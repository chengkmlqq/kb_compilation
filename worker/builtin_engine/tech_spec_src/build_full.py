#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单文档完整构建脚本（2026-08-20 新增，批量构建用）
流程：建目录 → 摘要 → 长句拆分 → 长句建页+关键词 → 实体抽取 → 合并 → 链接重建
目录模型：规范体系根 → 分册 → 版本 → {基础实体/{知识对象,规范要求}, 长句原文, 高频关键词}
用法：python3 build_full.py <kid> <规范体系> <分册> <版本> <source_file> [--format pdf|doc]
"""
import json, sys, os, re, hashlib, subprocess, time, argparse
import domain

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
import keyword_registry as kr   # 2026-08-31：关键词整库合并（注册表 + 创建即合并）
import filter_terms_llm as ftl  # 2026-08-31：第二轮整库级语义过滤

KB = None  # will be set from --kb or wr.load_kb() in build()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 每篇文档建/合并的高频关键词页数（2026-09-20 由 15 调至 30，用户要求）。
# 候选池仍是 discover_terms 的 Top50，经 filter_terms_llm 过滤后取前 N 个建页；
# 过滤后不足 N 个就有几个建几个。改这个数只影响建页数量，不影响实体抽取参考的 Top50。
KEYWORD_PAGES_PER_DOC = 30


def fetch_chunks(kid, kb_id=None, retries=3, delay=3):
    """拉切片；list_chunks 偶发返回 0（索引缓存一致性抖动，843bc5e4/89b06bb5 假 abort），重试后再判死。"""
    import time
    for i in range(retries):
        r = wr.list_chunks(kid, kb_id=kb_id or _get_kb(), page_size=100)
        chunks = r.get('data', []) if isinstance(r, dict) else []
        if chunks:
            return chunks
        if i < retries - 1:
            time.sleep(delay)
    r = wr.list_chunks(kid, kb_id=kb_id or _get_kb(), page=1, page_size=20)
    chunks = r.get('data', []) if isinstance(r, dict) else []
    return chunks


def _parse_args():
    global KB
    ap = argparse.ArgumentParser()
    ap.add_argument('args', nargs='+')
    ap.add_argument('--kb', default=None)
    ap.add_argument('--format', default=None)
    ap.add_argument('--skip-final', action='store_true')
    ap.add_argument('--notice', action='store_true')
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


# ---------- 规范体系根目录（精确名匹配，不做模糊归并）----------
def resolve_family_root(kb_id: str, spec_system: str):
    """规范体系根目录 id：去空白归一后精确匹配，不存在则创建。
    技术规范体系名是明确给定的（如「中国移动省级NG2-BASS技术规范」），
    不做供管式的去噪归并——避免把不同体系误并到同一根。"""
    core = re.sub(r'\s+', '', spec_system)
    r = wr.list_folders(kb_id=kb_id, parent_id='')
    for f in r.get('folders', []):
        if re.sub(r'\s+', '', f.get('name', '')) == core:
            return f['id']
    return ensure_folder(spec_system, '')


def tool(name, **kwargs):
    kwargs["kb_id"] = KB
    r = wr.tool_call(name, kwargs)
    return r


def ensure_folder(name, parent_id):
    r = wr.list_folders(kb_id=KB, parent_id=parent_id)
    for f in r.get('folders', []):
        if f['name'] == name:
            return f['id']
    r = wr.create_folder(name, parent_id=parent_id, kb_id=KB)
    return r.get('folder', {}).get('id', r.get('id', ''))


def build(kid, spec_system, volume, version, source_file, is_notice=False, file_format=None, skip_final=False, kb=None, no_graph=False):
    """Build a single document's wiki. Pass kb to override config.yaml's kb_id."""
    global KB
    if kb:
        KB = kb
    if not KB:
        KB = wr.load_kb()
    wr.load_config()
    wr.KB = KB
    wr.mcp_init()
    md5_kid = hashlib.md5(kid.encode()).hexdigest()

    # Format suffix for pdf/doc dual-format separation (坑 44)
    fmt_suffix = f"-{file_format}" if file_format else ""

    # 1. 规范体系根（=家族根，如「中国移动省级NG2-BASS技术规范」）
    root_id = resolve_family_root(KB, spec_system)
    print(f"[1] 规范体系根: {root_id}")

    # 1.5 分册目录（分册在上、版本在下：体系根 → 分册 → 版本）
    vol_id = ensure_folder(volume, root_id)
    print(f"[1.5] 分册目录: {vol_id} ({volume})")

    # 2. 版本目录（无序号前缀，目录名=版本标识唯一命名，如 v5.0版）
    children = wr.list_folders(kb_id=KB, parent_id=vol_id)
    existing = None
    for c in children.get('folders', []):
        if c['name'] == version + fmt_suffix:
            existing = c
            break
    if existing:
        vid = existing['id']
        vname = existing['name']
        print(f"[2] 复用版本目录: {vid} ({vname})")
    else:
        vname = f"{version}{fmt_suffix}"
        vid = ensure_folder(vname, vol_id)
        print(f"[2] 版本目录: {vid} ({vname})")

    # 3. Subdirs — all doc types get full directories (坑 44: 通知/附件完整构建)
    leaf = {}
    leaf["基础实体"] = ensure_folder("基础实体", vid)
    leaf["长句原文"] = ensure_folder("长句原文", vid)
    leaf["高频关键词"] = ensure_folder("高频关键词", vid)
    print(f"[3] 子目录: {json.dumps(leaf, ensure_ascii=False)}")

    # 4. Ontology subdirs — ALL doc types get full ontology subdirs
    cat_ids = {"biz": {}, "rule": {}, "biz_short": {}, "rule_short": {}}
    base_id = leaf["基础实体"]
    biz_cats = domain.BIZ_CATS
    rule_cats = domain.RULE_CATS
    for oname, out_key, cats in [(domain.BIZ_DIR, "biz", biz_cats), (domain.RULE_DIR, "rule", rule_cats)]:
        onto_id = ensure_folder(oname, base_id)
        for cat in cats:
            cat_ids[out_key][cat] = ensure_folder(cat, onto_id)
    cat_ids["biz_short"] = {c.split('-',1)[1]: c for c in biz_cats}
    cat_ids["rule_short"] = {c.split('-',1)[1]: c for c in rule_cats}
    with open(f'/tmp/catids_{kid[:8]}.json', 'w') as f:
        json.dump(cat_ids, f, ensure_ascii=False, indent=2)
    print(f"[4] 本体子目录: 完成")

    # 5. Chunks（list_chunks 偶发返回 0 是索引一致性抖动——重试，勿直接判死）
    chunks = fetch_chunks(kid, KB)
    chunks = sorted(chunks, key=lambda c: c.get('chunk_index', 0))
    chunks_path = f'/tmp/chunks_{kid[:8]}.json'
    with open(chunks_path, 'w', encoding='utf-8') as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"[5] 切片: {len(chunks)} chunks")

    # 6. Long sentence split (v3: 整篇理解 + 知识单元, background)
    cand_path = f'/tmp/cand_{kid[:8]}.json'
    print("[6] 长句拆分 (v3 知识单元, LLM)...")
    # 传 --summary-slug：长句拆分 v3 读摘要页作整篇理解语境（读不到时优雅降级为无摘要）
    ls_cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'extract_long_sentences_v2.py'),
              chunks_path, '-o', cand_path, '--max-tokens', '8000']
    summary_slug = f"summary/{hashlib.md5(kid.encode()).hexdigest()}"
    if wr.read_page(summary_slug, kb_id=KB).get('content'):
        ls_cmd += ['--summary-slug', summary_slug]
    subprocess.run(ls_cmd, check=True)
    with open(cand_path) as f:
        candidates = json.load(f)
    print(f"    候选: {len(candidates)}")

    # 7. Build long sentence pages + keywords
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
    print(f"[7] 长句建页: created={created} skipped={skipped}")

    # 8. 高频短语新词发现（先于实体抽取，兼作实体候选术语）+ 关键词页
    # 2026-08-28：字符级 N-gram + PMI/左右熵（discover_terms.py），tokenize 分词通道并存补充
    print("[8] 高频短语新词发现 + 关键词页...")
    terms_path = f'/tmp/terms_{kid[:8]}.json'
    terms = []
    try:
        mf = 2 if len(ls_all) < 30 else 3  # 短文档（通知/附件）自动降频次门槛
        subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'discover_terms.py'), ls_path,
                        '-o', terms_path, '--min-freq', str(mf), '--top', '50'], check=True)
        with open(terms_path) as f:
            terms = json.load(f)
        # 分词通道补充（并存）：2 字高频词未入 terms 的追加（tokenize 仍参与统计）
        try:
            full_text = ' '.join(c.get('text', '') for c in candidates)
            token_r = tool('tokenize', text=full_text)
            data = token_r.get('data', {}) if isinstance(token_r, dict) else {}
            words = data.get('words', []) if isinstance(data, dict) else []
            from collections import Counter
            stopwords = set('的 了 在 是 有 为 与 和 或 及 被 把 从 以 对 于 到 让 由 向 将 并 而 但 可 如 若 且 该 其 这 那 所 各 每 按 应 当 须 需 请 能 要 会 已 正 不 未 非 无 上 下 中 外 内 前 后 之 等 得 进行 相关 按照 根据 通过 以及 或者 应当 必须 不得 可以 用于 包括 属于 低于 高于 超过 以上 以下 采购 公司 管理 办法 细则 规定 制度 实施 执行 部门 单位 工作 情况 项目 方案 服务 货物 工程 合同 订单 协议 文件 信息 需求 产品 价格 数量 金额 模式 方式 内容 时间 期限 范围 结果 事项 活动 环节 流程 阶段 中国 移动 上海 有限 责任 集团 通信'.split())
            freq = Counter(w for w in words if len(w) == 2 and w not in stopwords)
            term_words = {t['word'] for t in terms}
            for w, c in freq.most_common(20):
                # 补充词必须含汉字（2026-09-02：tokenize 会把文号数字"25"当 2 字词补充进来，
                # 0829 库实测建出纯数字关键词页——数字/字母/符号 token 一律不收）
                if w not in term_words and c >= 3 and re.search(r'[\u4e00-\u9fff]', w):
                    terms.append({'word': w, 'freq': c, 'pmi': 0, 'left_entropy': 0,
                                  'right_entropy': 0, 'score': 0, '_extra': True})
        except Exception as e:
            print(f"    分词补充失败 ({e})")
        # LLM 语义过滤（2026-08-29 新增：统计方法只能识别"高频凝固片段"，无法判断业务含义，
        # 「确定/完善/规范」等纯通用词会漏网——不适合做关键词/实体参考。filter_terms_llm.py
        # 分批送 LLM 判 keep，剔除纯通用词；fail-safe：LLM 失败保留原词）
        try:
            filtered_path = f'/tmp/terms_filtered_{kid[:8]}.json'
            proc = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'filter_terms_llm.py'),
                                   '--terms-file', terms_path, '--ls-file', ls_path,
                                   '-o', filtered_path, '--kb', KB], check=False, timeout=600)
            if os.path.exists(filtered_path):
                # 产物存在即采用（可为空列表=LLM 全剔——禁止回退统计层，坑 104）
                with open(filtered_path) as f:
                    filtered = json.load(f)
                terms = filtered
                print(f"    LLM 语义过滤: {len(terms)} 词保留")
            elif proc.returncode != 0:
                # LLM 主流程异常退出（rc!=0 且无产物）：降级为程序硬过滤，
                # 防裸统计层垃圾全量建页（0829 库 183 页实证，坑 104）
                print(f"    LLM 语义过滤退出码 {proc.returncode}（无产物），降级为程序硬过滤")
                hard = ftl.apply_hard_filter(terms)
                print(f"    硬过滤: 统计层 {len(terms)} 词 → {len(hard)} 词（剔除 {len(terms) - len(hard)} 个通用词/形态垃圾）")
                terms = hard
        except Exception as e:
            # 2026-09-02 坑 104：LLM 过滤失败不能回退裸统计层（0829 库实证——并行构建时
            # subprocess 超时→垃圾词全量建页）。降级为程序硬过滤（COMMON_WORDS + 形态规则）
            print(f"    LLM 语义过滤失败 ({e})，降级为程序硬过滤（不保留裸统计层）")
            try:
                hard = ftl.apply_hard_filter(terms)
                print(f"    硬过滤: 统计层 {len(terms)} 词 → {len(hard)} 词（剔除 {len(terms) - len(hard)} 个通用词/形态垃圾）")
                terms = hard
            except Exception as e2:
                print(f"    硬过滤也失败 ({e2})，保留统计层（不阻断构建）")
        # 关键词页（2026-08-31 起：创建即合并——同词跨文档合并到同一页，多目录挂载，
        # 频次累加；slug 新规范 keyword/<md5(canonical)[:10]>；旧规范 keyword/<kid>-<词> 不再生成）
        # 链接文本=关键词上下文片段（前后 pad 字）+加粗
        def _ls_snippet(ls, w, pad=60):
            """截取包含关键词的一段原文（2026-08-28 用户要求：不展示完整长句，
            只需包含高频关键词相关的截取片段；片段外以 … 标记省略）。"""
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
            # 片段内换行拍平为空格（v3 长句 text 多句拼接含 \n，表格单元格内换行会破坏表格）
            s = s.replace('\n', ' ')
            # 原文 markdown 标题标记 # 残留清理（2026-08-28 实测关键词页片段含 "### 关于印发…" 无效行）
            s = re.sub(r'#{1,6}', '', s)
            # doc 图片标记清理（2026-08-31：doc 转 markdown 的 ![..](resource://..) 残留，
            # 同 clean_chunk_text 口径，防关键词片段显示 raw 图片标记）
            s = re.sub(r'!\[[^\]]*\]\(resource://[^)]*\)', '', s)
            # 原文 markdown 表格残留 | 转义（2026-08-28 实测 A2 18 决策细则等表格原文）
            s = s.replace('|', '\\|')
            return s.replace(w, f'**{w}**')
        # 注册表：全量拉新规范关键词页（canonical → slug）
        try:
            kw_reg = kr.build_registry(wr, KB)
            print(f"    关键词注册表: {len(kw_reg)} 个既有规范页")
        except Exception as e:
            kw_reg = {}
            print(f"    关键词注册表加载失败 ({e})，本次按新建处理")
        # 第二轮整库级语义过滤（2026-08-31 新增）：仅对「注册表已存在（跨文档出现）」的词跑——
        # 判断该词是否放哪都有、无特殊业务含义（如 管理/工作/要求）；新词第一轮已判，不重复。
        # extra_ctx：从注册表已有页面解析其他文档的上下文片段，供 LLM 综合判断
        top_terms = terms[:KEYWORD_PAGES_PER_DOC]
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
                # 合并模式（2026-08-31）：update 现有页——folder_ids/source_refs 追加、
                # 原文出现位置按文档分组追加（同文档幂等：覆盖该文档分组）、频次累加
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
                    # 释义：新 LLM 产物优先，无则保留旧页面释义（存量兼容）
                    old_meaning = ''
                    m = re.search(r'\*\*释义\*\*[：:]\s*(.+)', r.get('content') or '')
                    if m:
                        old_meaning = m.group(1).strip()
                    content = kr.render_page_v2(w, groups, len(groups), bookname_fn=wr.bookname,
                                                meaning=t.get('meaning', '') or old_meaning)
                    rr = tool('update_wiki_page', slug=hit['slug'], title=w, content=content,
                              folder_id=fids[0] if fids else '', folder_ids=fids,
                              page_type='frequent_keyword', status=r.get('status') or 'published',
                              source_refs=refs)
                    kw_merged += 1
                    # 更新内存注册表（本批次后续词合并时用最新内容）
                    hit['content'] = content
                    hit['folder_ids'] = fids
                    hit['source_refs'] = refs
            else:
                # 新建模式（一页，挂本文档关键词目录）
                groups = {source_file: (t['freq'], links)}
                content = kr.render_page_v2(w, groups, 1, bookname_fn=wr.bookname,
                                            meaning=t.get('meaning', ''))
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
        print(f"[8] 关键词: created={kw_created} merged={kw_merged}（候选池 {len(top_terms)} 个，上限 {KEYWORD_PAGES_PER_DOC}）")
    except Exception as e:
        print(f"[8] 关键词: 失败 ({e})")

    # 9. Entities (all doc types — 通知/附件完整构建)
    print("[9] 实体抽取 (LLM, max_tokens=20000, 注入高频候选术语)...")
    # 2026-08-29：实体参考用 LLM 过滤后的词池（filter_terms_llm 产物），无则退回统计层
    terms_ref = f'/tmp/terms_filtered_{kid[:8]}.json'
    if not os.path.exists(terms_ref):
        terms_ref = terms_path
    subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'extract_entities_single.py'),
                    '--ls-file', ls_path, '--cat-ids', f'/tmp/catids_{kid[:8]}.json',
                    '--kid', kid, '--kb', KB, '--source-file', source_file,
                    '-o', f'/tmp/esm_{kid[:8]}.json',
                    '--max-tokens', '20000', '--terms-file', terms_ref], check=False)
    # 检查规范要求是否抽取成功，失败则分批补抽（2026-08-20：单次大 prompt 因 LLM reasoning 占用导致规则=0）
    esm_path = f'/tmp/esm_{kid[:8]}.json'
    if os.path.exists(esm_path):
        with open(esm_path) as f:
            esm = json.load(f)
    else:
        esm = {}
        print("    ⚠️⚠️ [ERROR] 实体抽取产物缺失（LLM 失败），esm={} —— 本文档实体页=0！"
              "构建不会中断但结果不完整，构建后必须重跑 extract_entities_single.py 补实体"
              "（坑 107：aiaaa.cc 大输出易超时，llm_call timeout=300 后重试）")
    rule_count = sum(1 for v in esm.values() if str(v).startswith('entity-r'))
    if rule_count == 0:
        print("    规范要求为0，分批补抽...")
        subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'extract_rules_batch.py'),
                        '--ls-file', ls_path, '--cat-ids', f'/tmp/catids_{kid[:8]}.json',
                        '--kid', kid, '--kb', KB, '--source-file', source_file,
                        '--max-tokens', '20000'], check=False)
    print("    实体完成")

    # 9.5 Neo4j 图谱写入（2026-09-07 新增，默认开启；--no-graph 跳过）
    # 数据源 = 本 kid 已建的 entity-b/entity-r 页（跨文档合并前的权威页面）。
    # 先删该 kid 旧子图再写（重建干净），kg=该文档 kid（每文件独立子图）。
    if no_graph:
        print("[9.5] 跳过 Neo4j 图谱写入（--no-graph）")
    else:
        print("[9.5] Neo4j 图谱写入 (知识对象=节点, 规则=边, kg=该kid)...")
        try:
            import graph_export
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
        subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'merge_duplicate_entities.py'),
                        '--kb', KB], check=True)
        tool('wiki_rebuild_links')
        print("[10] 链接重建完成")
    else:
        print("[10] 跳过跨文档合并（--skip-final，统一在批次末尾执行）")

    # 10.5 已删除（2026-09-02 B2 改造后废弃）：长句桥接（backfill_related_by_ls.py）
    # 使命由 B2 主谓联合抽取覆盖——规则 constraint/objects 同批闭包配对后，规则驱动
    # 通道（约束对象→规范要求、objects→关联对象）已能建立绝大多数规则-实体关联。

    # 11. 分册索引页维护（每篇构建完更新其所在分册的索引页）
    try:
        subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'update_index.py'),
                        '--kb', KB, '--family', volume, '--root', spec_system], check=False, timeout=180)
    except Exception as e:
        print(f"[11] 索引页维护失败: {e}")

    print(f"\n=== 完成: {source_file} ===")
    print(f"长句 {len(ls_all)} + 关键词 ≤{KEYWORD_PAGES_PER_DOC} + 摘要 1 (+实体)")

if __name__ == '__main__':
    ns = _parse_args()  # 2026-08-29 修复：原手写解析只认 --format=/--kb= 等号形式，空格形式被吞导致 file_format=None 建出无后缀目录（坑 44 违规）
    if len(ns.args) < 5:
        print("用法: build_full.py <kid> <规范体系> <分册> <版本> <源文件> [--format pdf|doc] [--kb <kb_id>] [--skip-final] [--no-graph]")
        sys.exit(1)
    build(ns.args[0], ns.args[1], ns.args[2], ns.args[3], ns.args[4],
          file_format=ns.format, skip_final=ns.skip_final, kb=ns.kb, no_graph=ns.no_graph)
