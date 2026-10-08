#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单法规完整构建脚本（市场监督管理法规域版，2026-09-07 自供管版 build_full.py 域化）

与供管版差异：
  - 目录模型：法规库无家族/版本概念——根目录=法规名，三子目录直接挂根，
    无版本级目录（供管版：家族根→版本目录→三子目录）。
  - 本体：业务 9 类（法律法规/监管对象/许可事项/违法行为/行政处罚/监管职责/
    标准规范/程序事项/时限要求）+ 规则 12 类（…/处罚/时限/版本/程序）。
  - 摘要由 gen_summary.py 独立先生成（同供管架构），build_full 不建摘要。
  - 根目录索引页（update_family_index）：法规库无家族概念，不生成
    index/<家族名> 页（如需法规全景索引另行设计）。

流程：建目录 → 长句拆分 → 长句建页+关键词 → 实体抽取 → Neo4j 图谱写入(Step 9.5,
默认开启，--no-graph 跳过) → 合并 → 链接重建
用法：python3 build_full.py <kid> <family(法规名)> <> <source_file> [--kb <kb_id>] [--skip-final] [--no-graph]
"""
import json, sys, os, re, hashlib, subprocess, time, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
import keyword_registry as kr   # 2026-08-31：关键词整库合并（注册表 + 创建即合并）
import filter_terms_llm as ftl  # 2026-08-31：第二轮整库级语义过滤

KB = None  # will be set from --kb or wr.load_kb() in build()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _parse_args():
    global KB
    ap = argparse.ArgumentParser()
    ap.add_argument('args', nargs='+')
    ap.add_argument('--kb', default=None)
    ap.add_argument('--format', default=None)
    ap.add_argument('--skip-final', action='store_true')
    ap.add_argument('--no-graph', action='store_true',
                    help='构建后不写 Neo4j 图谱（默认开启，实体/规则页建完即写入该 kid 子图）')
    ns, _ = ap.parse_known_args()
    KB = ns.kb
    return ns


def _get_kb():
    return KB or wr.load_kb()


def _cell(s: str) -> str:
    """表格单元格转义：原文中的 | 转义为 \\|（防破表），句内换行 → 空格。"""
    return s.replace('|', '\\|').replace('\n', ' ')


def render_unit_content(title: str, text: str, summary: str = '') -> str:
    """知识单元长句页 content：标题 + 一段汇总描述（LLM 语义概括）+ 两列表格（序号、原文）。
    数据层 text（纯 \n 拼接）保持不变——slug=md5(text)、实体抽取/摘要回填匹配均基于它；
    页面呈现层用表格条目化，每条原文逐字保留，仅做 | 转义与句内换行拍平。"""
    rows = '\n'.join(f'| {i} | {_cell(s)} |' for i, s in enumerate(text.split('\n'), 1))
    head = f'# {title}\n\n' + (f'{summary.strip()}\n\n' if summary.strip() else '')
    return f'{head}| 序号 | 原文 |\n|---|---|\n{rows}'


# ---------- 法规根目录幂等归一（法规库：根目录名=法规名，无家族噪声词）----------
FAMILY_NOISE = []


def _family_core(fname: str) -> str:
    """法规根目录核心名：去扩展名、去《》（文件名一般不含）、去首尾空白。
    法规库无家族归并需求，直接返回法规原名。"""
    fn = re.sub(r'\.(docx|doc|pdf|xlsx|xls)$', '', fname)
    fn = re.sub(r'^《|》$', '', fn)
    return fn.strip()


def resolve_family_root(kb_id: str, family: str):
    """返回法规根目录 id。优先复用现有同名根目录，避免重复创建。"""
    core = _family_core(family)
    r = wr.list_folders(kb_id=kb_id, parent_id='')
    for f in r.get('folders', []):
        if _family_core(f.get('name', '')) == core:
            return f['id']
    return ensure_folder(family, '')


def tool(name, **kwargs):
    kwargs["kb_id"] = KB
    r = wr.tool_call(name, kwargs)
    return r


def ensure_folder(name, parent_id):
    # 全量目录缓存（2026-10-07）：direct 模式每 parent 查询 5.66s，24 层
    # ≈2.2min/篇——一次拉全量 + 本地查
    try:
        for f in wr.list_all_folders_cached(kb_id=KB):
            if f.get('name') == name and str(f.get('parent_id') or '') == str(parent_id or ''):
                return f['id']
    except Exception:
        r = wr.list_folders(kb_id=KB, parent_id=parent_id)
        for f in (r.get('data') or r.get('folders') or []):
            if f['name'] == name:
                return f['id']
    r = wr.create_folder(name, parent_id=parent_id, kb_id=KB)
    if isinstance(r, dict) and 'error' in r:
        raise RuntimeError(f"create_folder 失败: {r['error']}")
    return r.get('folder', {}).get('id', r.get('id', ''))


# 市场法规域 STOPWORDS（2026-09-07：供管词表的采购专词替代为法规通用词，
# 保留通用虚词/通用名词，剔除 采购/公司/合同/订单 等采购域词）
MARKET_STOPWORDS = set('的 了 在 是 有 为 与 和 或 及 被 把 从 以 对 于 到 让 由 向 将 并 而 但 可 如 若 且 该 其 这 那 所 各 每 按 应 当 须 需 请 能 要 会 已 正 不 未 非 无 上 下 中 外 内 前 后 之 等 得 进行 相关 按照 根据 通过 以及 或者 应当 必须 不得 可以 用于 包括 属于 低于 高于 超过 以上 以下 法规 法律 行政法规 部门规章 办法 条例 规定 细则 规章 制度 施行 修订 公布 实施 废止 部门 单位 机关 机构 人员 工作 情况 内容 时间 期限 范围 结果 事项 活动 环节 流程 阶段 责任 义务 权利 行为 标准 要求 程序 监督管理 处罚'.split())


def build(kid, family, version, source_file, file_format=None, skip_final=False, kb=None, no_graph=False):
    """Build a single regulation's wiki. Pass kb to override config.yaml's kb_id."""
    global KB
    if kb:
        KB = kb
    if not KB:
        KB = wr.load_kb()
    wr.load_config()
    wr.KB = KB
    wr.mcp_init()
    md5_kid = hashlib.md5(kid.encode()).hexdigest()

    wr.log_progress('agent_build_start', knowledge_id=kid, doc_title=source_file,
                    summary=f'开始为法规构建 wiki（{family}）', kb_id=KB)

    fmt_suffix = f"-{file_format}" if file_format else ""

    # 1. 法规根目录（无版本级：法规库一篇法规=一个根目录）
    root_id = resolve_family_root(KB, family)
    print(f"[1] 法规根: {root_id}")
    vid = root_id  # 法规库无版本级目录，三子目录直接挂根

    # 2. Subdirs — 三子目录直接挂根
    leaf = {}
    leaf["基础实体"] = ensure_folder("基础实体", vid)
    leaf["长句原文"] = ensure_folder("长句原文", vid)
    leaf["高频关键词"] = ensure_folder("高频关键词", vid)
    print(f"[2] 子目录: {json.dumps(leaf, ensure_ascii=False)}")

    # 3. Ontology subdirs — 市场法规域 9+12 类
    cat_ids = {"biz": {}, "rule": {}, "biz_short": {}, "rule_short": {}}
    base_id = leaf["基础实体"]
    biz_cats = ["1-法律法规", "2-监管对象", "3-许可事项", "4-违法行为", "5-行政处罚",
                "6-监管职责", "7-标准规范", "8-程序事项", "9-时限要求"]
    rule_cats = ["1-定义规则", "2-条件规则", "3-约束规则", "4-职责规则", "5-审批规则",
                 "6-流程规则", "7-引用规则", "8-例外规则", "9-处罚规则",
                 "10-时限规则", "11-版本规则", "12-程序规则"]
    for oname, out_key, cats in [("业务本体", "biz", biz_cats), ("规则本体", "rule", rule_cats)]:
        onto_id = ensure_folder(oname, base_id)
        for cat in cats:
            cat_ids[out_key][cat] = ensure_folder(cat, onto_id)
    cat_ids["biz_short"] = {c.split('-', 1)[1]: c for c in biz_cats}
    cat_ids["rule_short"] = {c.split('-', 1)[1]: c for c in rule_cats}
    with open(f'/tmp/catids_{kid[:8]}.json', 'w') as f:
        json.dump(cat_ids, f, ensure_ascii=False, indent=2)
    print(f"[3] 本体子目录: 完成")

    wr.log_progress('agent_build_dirs', knowledge_id=kid, doc_title=source_file,
                    summary=f'法规根/本体子目录已就绪（{family}）', kb_id=KB)

    # 4. Chunks
    r = wr.list_chunks(kid, kb_id=KB, page_size=100)
    chunks = sorted(r.get('data', []), key=lambda c: c.get('chunk_index', 0))
    chunks_path = f'/tmp/chunks_{kid[:8]}.json'
    with open(chunks_path, 'w', encoding='utf-8') as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"[4] 切片: {len(chunks)} chunks")

    # 5. Long sentence split (v3: 整篇理解 + 知识单元, LLM)
    cand_path = f'/tmp/cand_{kid[:8]}.json'
    print("[5] 长句拆分 (v3 知识单元, LLM)...")
    ls_cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'extract_long_sentences_v2.py'),
              chunks_path, '-o', cand_path, '--max-tokens', '8000']
    summary_slug = f"summary/{hashlib.md5(kid.encode()).hexdigest()}"
    if wr.read_page(summary_slug, kb_id=KB).get('content'):
        ls_cmd += ['--summary-slug', summary_slug]
    with wr.measure('long_sentences'):
        subprocess.run(ls_cmd, check=True)
    with open(cand_path) as f:
        candidates = json.load(f)
    print(f"    候选: {len(candidates)}")

    # 6. Build long sentence pages
    kid_prefix = md5_kid[:10]
    created = skipped = 0
    ls_all = []
    for c in candidates:
        text = c.get('text', '').strip()
        title = c.get('title', '').strip()
        if not text or not title:
            continue
        text_md5 = hashlib.md5(text.encode()).hexdigest()[:8]
        slug = f"longsentence/{kid_prefix}-{text_md5}"
        r = tool('wiki_read_page', slug=slug)
        if 'error' not in r:
            skipped += 1
            ls_all.append({"text": text, "title": title, "slug": slug, "source_refs": [f"{kid}|{source_file}"]})
            continue
        content = render_unit_content(title, text, c.get('summary') or '')
        sref = f"{kid}|{source_file}"
        r = tool('create_wiki_page', slug=slug, title=title, content=content,
                 folder_id=leaf['长句原文'], page_type='original_sentence', source_refs=[sref])
        if 'error' in r and '500' in str(r['error']):
            skipped += 1
        elif 'error' not in r:
            created += 1
            tool('update_wiki_page', slug=slug, title=title, content=content,
                 folder_id=leaf['长句原文'], source_refs=[sref])
        ls_all.append({"text": text, "title": title, "slug": slug, "source_refs": [sref]})
    ls_path = f'/tmp/ls_{kid[:8]}.json'
    with open(ls_path, 'w', encoding='utf-8') as f:
        json.dump(ls_all, f, ensure_ascii=False, indent=2)
    print(f"[6] 长句建页: created={created} skipped={skipped}")

    # 7. 高频短语新词发现 + 关键词页（先于实体抽取，兼作实体候选术语）
    print("[7] 高频短语新词发现 + 关键词页...")
    terms_path = f'/tmp/terms_{kid[:8]}.json'
    terms = []
    try:
        mf = 2 if len(ls_all) < 30 else 3  # 短文档自动降频次门槛
        with wr.measure('discover_terms'):
            rc = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'discover_terms.py'), ls_path,
                            '-o', terms_path, '--min-freq', str(mf), '--top', '50'], check=True)
        with open(terms_path) as f:
            terms = json.load(f)
        # 分词通道补充（并存）：2 字高频词未入 terms 的追加
        try:
            full_text = ' '.join(c.get('text', '') for c in candidates)
            token_r = tool('tokenize', text=full_text)
            data = token_r.get('data', {}) if isinstance(token_r, dict) else {}
            words = data.get('words', []) if isinstance(data, dict) else []
            from collections import Counter
            freq = Counter(w for w in words if len(w) == 2 and w not in MARKET_STOPWORDS)
            term_words = {t['word'] for t in terms}
            for w, c in freq.most_common(20):
                if w not in term_words and c >= 3 and re.search(r'[\u4e00-\u9fff]', w):
                    terms.append({'word': w, 'freq': c, 'pmi': 0, 'left_entropy': 0,
                                  'right_entropy': 0, 'score': 0, '_extra': True})
        except Exception as e:
            print(f"    分词补充失败 ({e})")
        # LLM 语义过滤
        try:
            filtered_path = f'/tmp/terms_filtered_{kid[:8]}.json'
            with wr.measure('filter_terms'):
                proc = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'filter_terms_llm.py'),
                                   '--terms-file', terms_path, '--ls-file', ls_path,
                                   '-o', filtered_path, '--kb', KB], check=False, timeout=600)
            if os.path.exists(filtered_path):
                with open(filtered_path) as f:
                    filtered = json.load(f)
                terms = filtered
                print(f"    LLM 语义过滤: {len(terms)} 词保留")
            elif proc.returncode != 0:
                print(f"    LLM 语义过滤退出码 {proc.returncode}（无产物），降级为程序硬过滤")
                hard = ftl.apply_hard_filter(terms)
                print(f"    硬过滤: 统计层 {len(terms)} 词 → {len(hard)} 词")
                terms = hard
        except Exception as e:
            print(f"    LLM 语义过滤失败 ({e})，降级为程序硬过滤")
            try:
                hard = ftl.apply_hard_filter(terms)
                print(f"    硬过滤: 统计层 {len(terms)} 词 → {len(hard)} 词")
                terms = hard
            except Exception as e2:
                print(f"    硬过滤也失败 ({e2})，保留统计层（不阻断构建）")
        # 关键词页（创建即合并：同词跨文档合并到同一页，多目录挂载，频次累加）
        def _ls_snippet(ls, w, pad=60):
            t = ls.get('text', '')
            idx = t.find(w)
            if idx < 0:
                return (t[:300] + '…') if len(t) > 300 else t
            start = max(0, idx - pad)
            end = min(len(t), idx + len(w) + pad)
            s = t[start:end]
            if start > 0:
                s = '…' + s
            if end < len(t):
                s = s + '…'
            s = s.replace('\n', ' ')
            s = re.sub(r'#{1,6}', '', s)
            s = re.sub(r'!\[[^\]]*\]\(resource://[^)]*\)', '', s)
            s = re.sub(r'^\s*[-—－]\s*[\d０-９0-9\s]{1,6}\s*[-—－]\s*$', '', s)
            s = s.replace('|', '\\|')
            return s.replace(w, f'**{w}**')
        try:
            kw_reg = kr.build_registry(wr, KB)
            print(f"    关键词注册表: {len(kw_reg)} 个既有规范页")
        except Exception as e:
            kw_reg = {}
            print(f"    关键词注册表加载失败 ({e})，本次按新建处理")
        # 第二轮整库级语义过滤
        top_terms = terms[:15]
        hit_terms = [t for t in top_terms if kr.lookup(kw_reg, t.get('word', ''))]
        if hit_terms:
            try:
                extra_ctx = {}
                for t in hit_terms:
                    w = t.get('word', '')
                    hit = kr.lookup(kw_reg, w)
                    if not hit:
                        continue
                    groups = kr.parse_groups_v2(hit.get('content') or '')
                    snips = []
                    for _freq, links in groups.values():
                        for _s, snip in links:
                            snips.append(snip)
                    extra_ctx[w] = snips[:3]
                kept_global = ftl.filter_terms_global(hit_terms, ls_all, extra_ctx=extra_ctx)
                kept_words = {t.get('word') for t in kept_global}
                dropped = [t.get('word') for t in hit_terms if t.get('word') not in kept_words]
                if dropped:
                    print(f"    第二轮全局过滤剔除通用词: {dropped}")
                    top_terms = [t for t in top_terms if t.get('word') not in dropped]
            except Exception as e:
                print(f"    第二轮全局过滤失败 ({e})，保留（fail-safe）")
        kw_created = kw_merged = 0
        for t in top_terms:
            w = t['word']
            slug = kr.kw_slug(w)
            if not slug:
                continue
            md5pre = slug.split('/')[-1]
            sref = f"{kid}|{source_file}"
            matched_ls = [ls for ls in ls_all if w in ls.get('text', '')]
            if matched_ls:
                links = [(ls['slug'], _ls_snippet(ls, w)) for ls in matched_ls[:5]]
            else:
                links = []
            hit = kw_reg.get(md5pre)
            if hit:
                r = tool('wiki_read_page', slug=hit['slug'])
                if 'error' not in r:
                    fids = list(hit.get('folder_ids') or [])
                    if leaf['高频关键词'] not in fids:
                        fids.append(leaf['高频关键词'])
                    refs = list(hit.get('source_refs') or [])
                    if sref not in refs:
                        refs.append(sref)
                    groups = kr.parse_groups_v2(r.get('content') or '')
                    groups[source_file] = (t['freq'], links)
                    content = kr.render_page_v2(w, groups, len(groups), bookname_fn=wr.bookname)
                    rr = tool('update_wiki_page', slug=hit['slug'], title=w, content=content,
                              folder_id=fids[0] if fids else '', folder_ids=fids,
                              page_type='frequent_keyword', status=r.get('status') or 'published',
                              source_refs=refs)
                    kw_merged += 1
                    hit['content'] = content
                    hit['folder_ids'] = fids
                    hit['source_refs'] = refs
            else:
                groups = {source_file: (t['freq'], links)}
                content = kr.render_page_v2(w, groups, 1, bookname_fn=wr.bookname)
                r = tool('wiki_read_page', slug=slug)
                if 'error' not in r:
                    continue
                r = tool('create_wiki_page', slug=slug, title=w, content=content,
                         folder_id=leaf['高频关键词'], page_type='frequent_keyword', source_refs=[sref])
                if 'error' not in r or '500' in str(r.get('error', '')):
                    kw_created += 1
                    tool('update_wiki_page', slug=slug, title=w, content=content,
                         folder_id=leaf['高频关键词'], source_refs=[sref])
                    kw_reg[md5pre] = {'slug': slug, 'title': w, 'content': content,
                                      'folder_ids': [leaf['高频关键词']], 'source_refs': [sref],
                                      'folder_id': leaf['高频关键词']}
        print(f"[7] 关键词: created={kw_created} merged={kw_merged}（候选池 {len(top_terms)} 个）")
    except Exception as e:
        print(f"[7] 关键词: 失败 ({e})")

    # 8. Entities (B2 主谓联合抽取)
    print("[8] 实体抽取 (LLM, max_tokens=20000, 注入高频候选术语)...")
    wr.log_progress('agent_build_entities', knowledge_id=kid, doc_title=source_file,
                    summary='长句/关键词完成，开始实体抽取', kb_id=KB)
    terms_ref = f'/tmp/terms_filtered_{kid[:8]}.json'
    if not os.path.exists(terms_ref):
        terms_ref = terms_path
    with wr.measure('extract_entities'):
        rc = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'extract_entities_single.py'),
        '--ls-file', ls_path, '--cat-ids', f'/tmp/catids_{kid[:8]}.json',
        '--kid', kid, '--kb', KB, '--source-file', source_file,
        '-o', f'/tmp/esm_{kid[:8]}.json',
        '--max-tokens', '16000', '--terms-file', terms_ref], check=False)
    # 检查规则实体是否抽取成功，失败则分批补抽
    esm_path = f'/tmp/esm_{kid[:8]}.json'
    if os.path.exists(esm_path):
        with open(esm_path) as f:
            esm = json.load(f)
    else:
        esm = {}
        print("    ⚠️⚠️ [ERROR] 实体抽取产物缺失（LLM 失败），esm={} —— 本文档实体页=0！"
              "构建不会中断但结果不完整，构建后必须重跑 extract_entities_single.py 补实体")
    rule_count = sum(1 for v in esm.values() if str(v).startswith('entity-r'))
    if rule_count == 0:
        print("    规则实体为0，分批补抽...")
        with wr.measure('extract_rules'):
            rc = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'extract_rules_batch.py'),
            '--ls-file', ls_path, '--cat-ids', f'/tmp/catids_{kid[:8]}.json',
            '--kid', kid, '--kb', KB, '--source-file', source_file,
            '--max-tokens', '16000'], check=False)
    print("    实体完成")

    # 9.5 Neo4j 图谱写入（2026-09-08 移植自供管版，默认开启；--no-graph 跳过）
    # 图模型：业务实体=节点、规则=边（边类型=规则类别动词），kg=该 kid（每文件独立子图）。
    # 数据源 = 本 kid 已建的 entity-b/entity-r 页（跨文档合并前的权威页面），先删该 kid
    # 旧子图再写（重建干净）。失败不中断 wiki 构建（打印修复命令）。
    if no_graph:
        print("[9.5] 跳过 Neo4j 图谱写入（--no-graph）")
    else:
        print("[9.5] Neo4j 图谱写入 (业务实体=节点, 规则=边, kg=该kid)...")
        try:
            import graph_export
            with wr.measure('graph_export'):
                st = graph_export.export_doc(kid, KB, source_file, quiet=True)
            print(f"    图: 实体页 {st.get('biz_pages',0)} 规则页 {st.get('rule_pages',0)} "
                  f"→ 节点 {st.get('written_nodes',0)} 关系 {st.get('written_rels',0)}"
                  + (f" 边型分布 {dict(st.get('edge_to_rule', {}))}" if st.get('edge_to_rule') else ''))
        except Exception as e:
            print(f"    ⚠️⚠️ [ERROR] Neo4j 图谱写入失败: {e}\n"
                  "    构建不中断（wiki 不受影响）；修复后可重跑：\n"
                  "    python3 scripts/graph_export.py doc --kid <kid> --kb <kb_id>")

    # 10. Merge duplicates + rebuild links (--skip-final: 全库操作集中到最后统一执行)
    if not skip_final:
        print("[10] 跨文档合并...")
        with wr.measure('merge_entities'):
            rc = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'merge_duplicate_entities.py'),
                        '--kb', KB], check=True)
        with wr.measure('rebuild_links'):
            tool('wiki_rebuild_links')
        print("[10] 链接重建完成")
    else:
        print("[10] 跳过跨文档合并（--skip-final，统一在批次末尾执行）")

    print(f"\n=== 完成: {source_file} ===")
    print(f"长句 {len(ls_all)} + 关键词 15 + 摘要 1 (+实体)")
    wr.log_progress('agent_build_done', knowledge_id=kid, doc_title=source_file,
                    summary=f'法规 wiki 构建完成（长句 {len(ls_all)}，法规 {family}）', kb_id=KB)


if __name__ == '__main__':
    ns = _parse_args()
    if len(ns.args) < 4:
        print("用法: build_full.py <kid> <family(法规名)> <> <source_file> [--kb <kb_id>] [--skip-final] [--no-graph]")
        sys.exit(1)
    build(ns.args[0], ns.args[1], ns.args[2], ns.args[3],
          file_format=ns.format, skip_final=ns.skip_final, kb=ns.kb, no_graph=ns.no_graph)