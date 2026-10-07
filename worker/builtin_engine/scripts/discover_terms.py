#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高频短语新词发现（2026-08-28 新增，零依赖纯 Python）。

原理：字符级滑动窗口 N-gram + 统计过滤——绕开分词器碎词缺陷，发现真实术语。
评分三要素：
  1. 频次 freq：出现次数（>= min_freq）
  2. 凝固度 PMI：内部结合强度（P(w) / P(左)*P(右)，取所有切分点最小值）
  3. 左右熵：边界自由度（左右邻字集合的信息熵，取 min）
综合分 score = log2(freq) + min_pmi + min(left_entropy, right_entropy)

用法：
    python discover_terms.py <ls_all.json> -o terms.json [--min-freq 3] [--top 50] [--max-len 6]

输入：长句列表 JSON（[{text, title, slug}]，每个 text 视为一个句子）
输出：[{word, freq, pmi, left_entropy, right_entropy, score}]（按 score 降序 Top N）

下游：
  - 注入业务实体抽取 prompt（作候选术语，优先覆盖）
  - 建高频关键词页
"""
import json, re, math, sys, collections, argparse

STOPWORDS = set('的 了 在 是 有 为 与 和 或 及 被 把 从 以 对 于 到 让 由 向 将 并 而 但 可 如 若 且 该 其 这 那 所 各 每 按 应 当 须 需 请 能 要 会 已 正 不 未 非 无 上 下 中 外 内 前 后 之 等 得 进行 相关 按照 根据 通过 以及 或者 应当 必须 不得 可以 用于 包括 属于 低于 高于 超过 以上 以下 采购 公司 管理 办法 细则 规定 制度 实施 执行 部门 单位 工作 情况 项目 方案 服务 货物 工程 合同 订单 协议 文件 信息 需求 产品 价格 数量 金额 模式 方式 内容 时间 期限 范围 结果 事项 活动 环节 流程 阶段 责任'.split())

# 以虚词结尾的往往是搭配碎片
TAIL_STOP = set('的 了 是 在 和 与 及 或 之 所 等 以 于 对 为 从 到 向 将 应 当 须 需 能 要 会 已 未 非')
# 以虚词开头的往往是搭配碎片（"如有需求归口"→"如有"）
HEAD_STOP = set('如 该 此 若 且 其 这 那 所 各 每 按 应 当 须 需 请 能 要 会 已 正 不 未 非 无 上 下 中 外 内 前 后 之 以 为 由 于 而 但 可 将 并 被 把 让 向 从 对 到 是 有 和 与 或 及 也 又 才 就 都 还 再 只 即 则')
# 公司名/机构名残片（非制度术语）
# 公司名残片过滤（市场监督域：法规标题残片/机关名残片，与 STOPWORDS 去公司名同思路）
CORP_FRAG = ('有限公司', '人民共和国', '市场监督管理总局', '知识产权局')


def _clean_text(text):
    """只保留中文字符（数字/字母/标点丢弃，避免混入统计）。"""
    return re.sub(r'[^\u4e00-\u9fff]', '', text or '')


# 子句切分标点集：N-gram 滑窗不跨这些标点（2026-08-29 新增，防跨标点残片词）
# 2026-08-29 补充：数字也作切分边界——「2025年9月1日」删除数字后塌缩成「年月日」残片词（实测 55号通知频次 3 通过过滤）
# 注意：- 必须放字符类末尾（坑 36：range 陷阱）
SPLIT_RE = re.compile(r'[，。！？；、,.;:：\n\r\t（）()《》<>〈〉「」『』“”‘’…—0-9\-]')


def _entropy(counter):
    total = sum(counter.values())
    if total <= 0:
        return 0.0
    return -sum((c / total) * math.log2(c / total) for c in counter.values())


def discover(ls_all, min_freq=3, max_len=6, top_n=50):
    """核心新词发现。返回 [{word, freq, pmi, left_entropy, right_entropy, score}]（score 降序）。"""
    # 2026-08-29：N-gram 前先按标点切子句（子句内滑窗，不跨标点）。
    # 原逻辑把整句去标点后滑窗，导致「事项，遵照」→「事项遵照」这类跨标点残片词
    # （72号通知实测：残片频次 5 通过 PMI/熵过滤，原文却从不连续出现）。
    sentences = []
    for item in ls_all:
        if not item.get('text'):
            continue
        for sub in SPLIT_RE.split(item['text']):
            s = _clean_text(sub)
            if s:
                sentences.append(s)

    # 1. N-gram 频次（子句内滑窗，不跨句/不跨标点；1 字也统计，供 PMI 分母）
    ngram_freq = collections.Counter()
    left_ctx = collections.defaultdict(collections.Counter)
    right_ctx = collections.defaultdict(collections.Counter)
    for s in sentences:
        n = len(s)
        for L in range(1, max_len + 1):
            for i in range(n - L + 1):
                w = s[i:i + L]
                ngram_freq[w] += 1
                if L >= 2:
                    if i > 0:
                        left_ctx[w][s[i - 1]] += 1
                    if i + L < n:
                        right_ctx[w][s[i + L]] += 1
    N = sum(len(s) for s in sentences) or 1

    # 2. 过滤 + 评分
    candidates = []
    for w, f in ngram_freq.items():
        if len(w) < 2:
            continue
        if f < min_freq:
            continue
        if w in STOPWORDS:
            continue
        if w[-1] in TAIL_STOP or w[0] in HEAD_STOP:
            continue
        if any(x in w for x in CORP_FRAG):
            continue
        le = _entropy(left_ctx.get(w, {}))
        re_ = _entropy(right_ctx.get(w, {}))
        # 边界熵为 0 = 邻字完全固定 = 长词残片（如"估采购金额属"），丢弃
        if min(le, re_) <= 0:
            continue
        # 2 字词门槛：频次>=5，或（频次>=3 且边界熵>=1.5）——过滤"最终/其他"类通用词
        if len(w) == 2 and f < 5 and min(le, re_) < 1.5:
            continue
        # 凝固度：所有切分点中 P(w)/(P(l)*P(r)) 的最小值（对数值）
        p_w = f / N
        min_pmi = None
        for cut in range(1, len(w)):
            l, r = w[:cut], w[cut:]
            pl = ngram_freq.get(l, 0) / N
            pr = ngram_freq.get(r, 0) / N
            if pl <= 0 or pr <= 0:
                pmi = 0.0
            else:
                ratio = p_w / (pl * pr)
                pmi = math.log2(ratio) if ratio > 0 else 0.0
            min_pmi = pmi if min_pmi is None else min(min_pmi, pmi)
        # 长度加权：3+ 字术语更可能是有价值的业务短语
        len_bonus = {2: 0.0, 3: 2.0, 4: 3.0, 5: 3.5, 6: 4.0}.get(len(w), 3.0)
        score = math.log2(f) + (min_pmi or 0.0) + min(le, re_) + len_bonus
        candidates.append({
            'word': w, 'freq': f,
            'pmi': round(min_pmi or 0.0, 3),
            'left_entropy': round(le, 3),
            'right_entropy': round(re_, 3),
            'score': round(score, 3),
        })

    # 3. 子串去冗：短词是长词子串且频次不显著高于长词 → 视为长词碎片，丢弃
    candidates.sort(key=lambda x: (-x['freq'], -len(x['word'])))
    keep = []
    for c in candidates:
        w = c['word']
        dominated = False
        for k in keep:
            if w in k['word'] and c['freq'] <= k['freq'] * 1.5:
                dominated = True
                break
        if not dominated:
            keep.append(c)

    # 4. 按综合分排序，Top N
    keep.sort(key=lambda x: -x['score'])
    return keep[:top_n]


def main():
    ap = argparse.ArgumentParser(description='高频短语新词发现（字符级 N-gram + PMI + 左右熵）')
    ap.add_argument('ls_file', help='长句列表 JSON (ls_all.json)')
    ap.add_argument('-o', '--output', default='/tmp/terms.json')
    ap.add_argument('--min-freq', type=int, default=3, help='最低频次（默认3）')
    ap.add_argument('--top', type=int, default=50, help='输出 Top N（默认50）')
    ap.add_argument('--max-len', type=int, default=6, help='最长短语字数（默认6）')
    args = ap.parse_args()

    with open(args.ls_file, encoding='utf-8') as f:
        ls_all = json.load(f)
    terms = discover(ls_all, min_freq=args.min_freq, max_len=args.max_len, top_n=args.top)
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(terms, f, ensure_ascii=False, indent=2)
    print(f"新词发现完成: {len(terms)} 个（min_freq={args.min_freq}, top={args.top}）")
    for t in terms[:20]:
        print(f"  {t['word']}  freq={t['freq']} pmi={t['pmi']} L熵={t['left_entropy']} R熵={t['right_entropy']} score={t['score']}")


if __name__ == '__main__':
    main()
