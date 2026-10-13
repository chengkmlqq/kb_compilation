#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复用已建目录的文档构建（2026-08-20）
用法: python3 build_existing_dir.py <kid> <ls_dir> <kw_dir> <catids_path> <source_file>
流程：长句拆分 → 长句建页+关键词 → 实体抽取（含规则分批补抽）→ 合并 → 链接重建
"""
import json, sys, os, hashlib, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_wiki_rpc as wr

KB = wr.load_kb()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def tool(name, **kwargs):
    kwargs["kb_id"] = KB
    return wr.tool_call(name, kwargs)


def _render_unit(title, text, summary=''):
    """知识单元长句页 content：标题 + 汇总描述（如有）+ 两列表格（序号、原文）。
    数据层 text（纯 \n 拼接）不变——slug=md5(text)、实体抽取匹配均基于它。"""
    def _cell(s):
        return s.replace('|', '\\|').replace('\n', ' ')
    rows = '\n'.join(f'| {i} | {_cell(s)} |' for i, s in enumerate(text.split('\n'), 1))
    head = f'# {title}\n\n' + (f'{summary.strip()}\n\n' if summary.strip() else '')
    return f'{head}| 序号 | 原文 |\n|---|---|\n{rows}'


def build(kid, ls_dir, kw_dir, catids_path, source_file):
    wr.load_config()
    wr.mcp_init()
    md5_kid = hashlib.md5(kid.encode()).hexdigest()
    kid_prefix = md5_kid[:10]

    # 1. Chunks
    r = wr.list_chunks(kid, kb_id=KB, page_size=100)
    chunks = sorted(r.get('data', []), key=lambda c: c.get('chunk_index', 0))
    chunks_path = f'/tmp/chunks_{kid[:8]}.json'
    with open(chunks_path, 'w', encoding='utf-8') as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)
    print(f"[1] 切片: {len(chunks)}")

    # 2. Long sentences
    cand_path = f'/tmp/cand_{kid[:8]}.json'
    subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'extract_long_sentences_v2.py'),
                    chunks_path, '-o', cand_path, '--window', '1800', '--overlap', '300',
                    '--max-tokens', '4000'], check=True)
    with open(cand_path) as f:
        candidates = json.load(f)
    print(f"[2] 长句候选: {len(candidates)}")

    # 3. Long sentence pages + keywords
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
        content = _render_unit(title, text, c.get('summary') or '')
        sref = f"{kid}|{source_file}"
        r = tool('create_wiki_page', slug=slug, title=title, content=content,
                 folder_id=ls_dir, page_type='original_sentence', source_refs=[sref])
        if 'error' in r and '500' in str(r['error']):
            skipped += 1
        elif 'error' not in r:
            created += 1
            tool('update_wiki_page', slug=slug, title=title, content=content,
                 folder_id=ls_dir, source_refs=[sref])
        ls_all.append({"text": text, "title": title, "slug": slug, "source_refs": [sref]})
    ls_path = f'/tmp/ls_{kid[:8]}.json'
    with open(ls_path, 'w', encoding='utf-8') as f:
        json.dump(ls_all, f, ensure_ascii=False, indent=2)
    print(f"[3] 长句建页: created={created} skipped={skipped}")

    # 4. Keywords (MCP tokenizer)
    print("[4] 关键词 (MCP tokenizer)...")
    try:
        full_text = ' '.join(c.get('text', '') for c in candidates)
        token_r = tool('tokenize', text=full_text)
        data = token_r.get('data', {}) if isinstance(token_r, dict) else {}
        words = data.get('words', []) if isinstance(data, dict) else []
        stopwords = set('的 了 在 是 有 为 与 和 或 及 被 把 从 以 对 于 到 让 由 向 将 并 而 但 可 如 若 且 该 其 这 那 所 各 每 按 应 当 须 需 请 能 要 会 已 正 不 未 非 无 上 下 中 外 内 前 后 之 等 得 进行 相关 按照 根据 通过 以及 或者 应当 必须 不得 可以 用于 包括 属于 低于 高于 超过 以上 以下 采购 公司 管理 办法 细则 规定 制度 实施 执行 部门 单位 工作 情况 项目 方案 服务 货物 工程 合同 订单 协议 文件 信息 需求 产品 价格 数量 金额 模式 方式 内容 时间 期限 范围 结果 事项 活动 环节 流程 阶段 中国 移动 上海 有限 责任 集团 通信'.split())
        from collections import Counter
        freq = Counter(w for w in words if len(w) >= 2 and w not in stopwords)
        top_terms_n = 30  # 与 build_full.py KEYWORD_PAGES_PER_DOC 对齐（2026-09-20 由 15 调至 30）
        top15 = [(w, c) for w, c in freq.most_common(50) if c >= 2][:top_terms_n]
        kw_created = 0
        for w, c in top15:
            slug = f"keyword/{kid_prefix}-{w}"
            matched_ls = [ls for ls in ls_all if w in ls.get('text', '')]
            if matched_ls:
                rows = '\n'.join(f'| {i+1} | [[{ls["slug"]}|{ls["title"]}]] |' for i, ls in enumerate(matched_ls[:5]))
            else:
                rows = '| - | 暂无关联长句 |'
            content = f"# {w}\n\n本关键词在{wr.bookname(source_file)}中高频出现（频次：{c}）。\n\n## 原文出现位置\n\n| 序号 | 关联原文 |\n|------|----------|\n{rows}\n"
            r = tool('wiki_read_page', slug=slug)
            if 'error' not in r:
                continue
            sref = f"{kid}|{source_file}"
            r = tool('create_wiki_page', slug=slug, title=w, content=content,
                     folder_id=kw_dir, page_type='frequent_keyword', source_refs=[sref])
            if 'error' not in r or '500' in str(r.get('error', '')):
                kw_created += 1
                tool('update_wiki_page', slug=slug, title=w, content=content,
                     folder_id=kw_dir, source_refs=[sref])
        print(f"[4] 关键词: created={kw_created}")
    except Exception as e:
        print(f"[4] 关键词: 失败 ({e})")

    # 5. Entities (skip if --no-entities)
    no_entities = '--no-entities' in sys.argv
    if not no_entities:
        print("[5] 实体抽取...")
        subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'extract_entities_single.py'),
                        '--ls-file', ls_path, '--cat-ids', catids_path,
                        '--kid', kid, '--kb', KB, '--source-file', source_file,
                        '-o', f'/tmp/esm_{kid[:8]}.json',
                        '--max-tokens', '20000'], check=False)
        esm = {}
        esm_path = f'/tmp/esm_{kid[:8]}.json'
        if os.path.exists(esm_path):
            with open(esm_path) as f:
                esm = json.load(f)
        rule_count = sum(1 for v in esm.values() if str(v).startswith('entity-r'))
        if rule_count == 0:
            print("    规则实体为0，分批补抽...")
            subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'extract_rules_batch.py'),
                            '--ls-file', ls_path, '--cat-ids', catids_path,
                            '--kid', kid, '--kb', KB, '--source-file', source_file,
                            '--max-tokens', '20000'], check=False)
        print("    实体完成")
    else:
        print("[5] 实体: 跳过 (--no-entities)")

    # 6. Merge + rebuild
    subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'merge_duplicate_entities.py'),
                    '--kb', KB], check=False)
    tool('wiki_rebuild_links')
    print(f"\n=== 完成: {source_file} ===")

if __name__ == '__main__':
    if len(sys.argv) < 6:
        print("用法: build_existing_dir.py <kid> <ls_dir> <kw_dir> <catids_path> <source_file>")
        sys.exit(1)
    build(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
