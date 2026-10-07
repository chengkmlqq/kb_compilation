#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高频关键词 LLM 语义过滤（2026-08-29 新增）

背景：discover_terms.py 是纯统计方法（频次/PMI/左右熵/静态黑名单），只能识别
"凝固的高频片段"，无法判断"是否具有业务含义"。实测「完善」「规范」等通用动词
通过统计层进入候选池（频次高、熵高、不在 STOPWORDS）——它们无业务含义、可出现在
任何文档，不适合做高频关键词（高频关键词用途：① Top50 注入业务实体抽取作参考；
② Top15 建关键词页）。

本脚本插入 Step 8 新词发现之后：把 Top50 候选词分批送 LLM（每批带词在原文中的
出现片段作上下文），让 LLM 判断是否具有业务含义：
- 保留：法规术语/监管对象/业务事项/监管概念（如 药品/医疗器械/行政处罚/经营许可）
- 剔除：纯通用词（确定/完善/规范/加强/开展/落实/相关/主要）与功能词（特此/年月日）

设计原则：
- 只剔纯通用词（保守）：不碰「部门/单位/公司」等高频泛化名词（可能含真实实体如
  供应链管理部），不碰业务词
- fail-safe：LLM 调用失败/超时/输出异常 → 返回原 terms（不因过滤层丢失真实词）
- 幂等：输出 terms_filtered.json，build_full 已建页的跳过

用法：
    python3 filter_terms_llm.py --terms-file terms.json --ls-file ls_all.json \
        -o terms_filtered.json [--kb <kb_id>] [--model <model>] [--batch-size 15] [--dry-run]

输出：过滤后的 terms JSON（保留原统计字段，仅剔除 keep=false 的词）。
"""
import json, sys, os, argparse, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

# 纯通用词表（门禁/兜底用，与 LLM 判断标准一致；只列统计层 STOPWORDS 未覆盖的）
# 注意：只剔纯通用词，不碰业务词（药品/医疗器械/经营者/行政处罚/许可证等）
# 2026-09-04 市场监督域化：去掉公司专名（集团公司/中国移动等），补充法规文种通用词
#   （条例/办法/规定/细则/规章/法规/法律/施行/修订/公布/印发——任何法规正文都高频出现，
#   无领域专指义）；2026-09-02 供管域扩充的通用动词/管理词保留（确定/完善/规范/监督/审批等
#   在法规正文同样无专指义）——全部列入，LLM 判 keep 也由 apply_hard_filter 兜底剔除
COMMON_WORDS = set(
    '确定 明确 完善 规范 加强 开展 落实 推进 建立 健全 强化 提高 促进 实现 '
    '做好 抓好 严格 认真 积极 深入 全面 充分 不断 进一步 有关 相关 主要 '
    '重要 具体 有效 合理 科学 统一 直接 其他 各类 各种 部分 相应 一定 必要 '
    '特此 年月日 本细则 本通知 本办法 本实施细则 有关要求 相关要求 具体规定 '
    '遵照 印发 纳入 引入 专业 市场 决策 管理制度 业务 制度 '
    '条例 办法 规定 细则 规章 法规 法律 施行 修订 公布 实施 废止 之日起 '
    '使用 完成 增加 提交 支撑 负责 调整 变更 合作 计划 组织 监督 评审 审批 '
    '领导 框架 份额 媒介 级别 层级 分级 办公 电子 技术 程序 配置 预研 集体 '
    '集中 管理办法 实施细则 管理规定 相关管理 相关规定 工作计划 达到或超过 '
    '经营管理 负责部门'.split()
)

# 形态规则硬过滤（2026-09-02 新增）：不依赖 LLM 的程序兜底层。
# 背景：字符级 N-gram 会把数字/文号残片/介词粘连碎片统计成候选（0829 库「25」纯数字
# 关键词页来自 tokenize 补充通道）；LLM 过滤失败/漏判时这些垃圾会全量建页。
# 形态特征一旦命中即剔除——LLM 判断前后都执行（LLM 判 keep 也拦）。
JUNK_RE_LIST = [
    re.compile(r'^\d+$'),            # 纯数字（"25"）
    re.compile(r'^\d'),              # 数字开头（文号/序号残片）
    re.compile(r'^20\d{2}年'),       # 年份开头（"2025年"）
    re.compile(r'[〔〕\[\]（）()]'),  # 文号括号/括注
]


def is_junk_word(w):
    """形态垃圾判定（True=应剔除）：无汉字 / 含"的" / 数字文号特征。
    含"的"的候选几乎都是跨词粘连碎片（"决策的采购""招标的项目"——中文术语不含"的"）。"""
    if not w:
        return True
    if not re.search(r'[\u4e00-\u9fff]', w):
        return True
    if '的' in w:
        return True
    for r in JUNK_RE_LIST:
        if r.search(w):
            return True
    return False


def apply_hard_filter(terms):
    """程序硬过滤：剔除命中 COMMON_WORDS 或形态垃圾的词，保留原统计字段。
    用途：① LLM 语义过滤之后的兜底（判 keep 也拦）；② LLM 不可用/失败时的
    降级层（build_full 回退路径，不再裸保留统计层——坑 104）。"""
    return [t for t in terms
            if not is_junk_word(t.get('word', ''))
            and t.get('word', '') not in COMMON_WORDS]

FILTER_PROMPT = """你是市场监管法规知识库的术语筛选专家。以下是文档中通过统计方法（频次/凝固度）发现的候选高频词，请判断每个词是否**具有业务含义**，值得作为关键词/术语保留。

【判断标准】
- 保留（keep=true）：具有业务含义的制度术语、业务对象、业务事件、业务概念（如"行政处罚""药品经营许可""进口药材""医疗器械网络销售""食品生产许可"）
- 剔除（keep=false）：**纯通用词**——没有业务含义、可出现在任何文档中的通用动词/修饰词（如"确定""完善""规范""加强""开展""落实""相关""主要""重要""明确""部分""遵照""印发""纳入""引入"），以及无业务含义的功能词（如"特此""年月日"）；泛化名词/单字动词/通知套语/公司泛称/年份数字若无制度专指义也剔除
- **剔除以下高频统计碎片（即使频次/凝固度很高——它们只是统计噪音，不是术语）**：
  ① **跨词切碎残片**：原文中从不以该词独立成词、只出现在更长词/搭配内部的片段（如"理办"来自"管理办法"、"一部门"来自"同一部门"、"公司部"来自"公司部室"、"关业务"来自"相关业务"、"部决策"来自"部门决策"、"领导审"来自"领导审批"）——结合原文片段判断该词是否有独立成词出现；
  ② **纯数字/文号/年份**（如"25""2025年"）；
  ③ **制度文种词**（如"管理办法""实施细则""管理规定"——文件名后缀，非业务术语）；
  ④ **介词/动词粘连片段**（如"经本单位""根据集团公司""文件明确""达到或超过""用信息化系统"）；
  ⑤ **单字/双字泛化词**（如"使用""完成""提交""业务""电子""程序"——无采购领域专指义）

每个词附其在原文中的出现片段（上下文），请结合上下文判断该词在本文档中是否承载业务含义。
注意：只剔除纯通用词/统计碎片；部门/单位/公司等泛化名词若在本制度语境中指代特定业务实体（如"供应链管理部"）可保留；公司泛称/年份/单字动词优先剔除。

只输出JSON数组，格式：[{{"word":"词","keep":true,"reason":"一句话理由"}}]，不要输出任何其他文字。

候选词及上下文：
{items}
"""


def _ctx_snippet(ls_all, word, pad=20):
    """在长句列表中找包含 word 的原文片段（±pad 字，取第一条）。"""
    for item in ls_all:
        t = item.get('text', '') or ''
        idx = t.find(word)
        if idx >= 0:
            s = t[max(0, idx - pad):idx + len(word) + pad]
            s = s.replace('\n', ' ').replace('|', '\\|')
            return s[:80]
    return ''


def filter_terms_llm(terms, ls_all, model=None, batch_size=15, max_tokens=3000):
    """LLM 语义过滤。返回保留的 terms 列表（fail-safe：异常时返回原列表）。"""
    if not terms:
        return terms
    kept = []
    failed = False
    for i in range(0, len(terms), batch_size):
        batch = terms[i:i + batch_size]
        items = []
        for t in batch:
            w = t.get('word', '')
            if not w:
                continue
            ctx = _ctx_snippet(ls_all, w)
            items.append(f"{len(items)+1}. 词：{w}（频次 {t.get('freq','?')}）| 原文片段：{ctx or '（无上下文）'}")
        if not items:
            continue
        try:
            result = wr.llm_call(
                [{'role': 'system', 'content': '你是术语筛选专家，只输出JSON。'},
                 {'role': 'user', 'content': FILTER_PROMPT.format(items='\n'.join(items))}],
                model=model, max_tokens=max_tokens, temperature=0.0)
            result = wr.ensure_full_json(result)
            parsed = json.loads(result)
            # 归一化（防嵌套结构，坑 83）
            decisions = {}
            def _collect(x):
                if isinstance(x, dict):
                    w = (x.get('word') or '').strip()
                    if w:
                        decisions[w] = x.get('keep', True)
                elif isinstance(x, list):
                    for y in x:
                        _collect(y)
            _collect(parsed)
            for t in batch:
                w = t.get('word', '')
                if w and decisions.get(w, True):
                    kept.append(t)
            print(f"  [批 {i//batch_size+1}] {len(batch)} 词 → 保留 {sum(1 for t in batch if decisions.get(t.get('word',''), True))}")
        except Exception as e:
            print(f"  ⚠️ 批 {i//batch_size+1} LLM 过滤失败（{e}），该批全部保留（fail-safe）")
            kept.extend(batch)
            failed = True
    if failed:
        print("  ⚠️ 部分批次失败，已按 fail-safe 保留原词")
    # 兜底：LLM 判 keep 也过程序硬过滤（COMMON_WORDS + 形态垃圾，2026-09-02）
    return apply_hard_filter(kept)


# ---- 第二轮：整库级全局过滤（2026-08-31 新增）----
# 第一轮按单文档上下文判断业务含义；合并后同一词出现在多篇文档时，
# 需再判断「该词是否放哪都有、无特殊业务含义」（如 管理/工作/要求/相关），
# 用跨文档上下文综合判断。命中已有页面的词才需要跑第二轮（新词第一轮已判）。

GLOBAL_FILTER_PROMPT = """你是市场监管法规知识库的术语筛选专家。以下候选高频词出现在**多篇市场监管法规文档**中，请判断该词是否**具有特殊业务含义**，值得作为跨文档关键词保留。

【判断标准】
- 保留（keep=true）：在至少一篇文档中承载明确业务含义——制度术语、业务对象、业务事件、业务概念（如"行政处罚""药品经营许可""进口药材""医疗器械网络销售""食品生产许可"）；或虽为泛化名词但在本领域语境下指代特定业务实体（如"部门"在该库指药品监管部门）
- 剔除（keep=false）：**放哪都有、无特殊业务含义的纯通用词**——在所有出现文档中都不指向特定业务概念（如"管理""工作""要求""相关""主要""内容""情况""事项""进行""有关"），只作一般性表述

每个词附其在各篇文档中的出现片段（多个上下文），请综合判断：只要有任何一篇文档的语境赋予它明确业务含义就保留；全部文档都是通用用法才剔除。

只输出JSON数组，格式：[{{"word":"词","keep":true,"reason":"一句话理由"}}]，不要输出任何其他文字。

候选词及多文档上下文：
{items}
"""


def _multi_ctx_snippet(ls_all, word, pad=20, max_ctx=3):
    """在长句列表中收集 word 的出现片段（最多 max_ctx 条，覆盖不同文档语境）。"""
    ctxs = []
    for item in ls_all:
        t = item.get('text', '') or ''
        idx = t.find(word)
        if idx >= 0:
            s = t[max(0, idx - pad):idx + len(word) + pad]
            s = s.replace('\n', ' ').replace('|', '\\|')
            ctxs.append(s[:80])
            if len(ctxs) >= max_ctx:
                break
    return ctxs


def filter_terms_global(terms, ls_all, extra_ctx=None, model=None, batch_size=10, max_tokens=3000):
    """第二轮整库级过滤：词 + 多文档上下文 → 剔除「放哪都有、无特殊业务含义」的通用词。

    与第一轮区别：第一轮按单文档判断业务含义（保业务词）；本轮用该词在
    当前文档 + 其他文档（extra_ctx，来自注册表已有页面片段）的综合上下文判断，
    只有全部语境都是通用用法才剔除。fail-safe：LLM 失败返回原列表。

    terms: [{word, freq, ...}, ...]（本文件候选词，命中注册表跨文档出现的词）
    ls_all: 当前文档长句列表（提供本文件语境）
    extra_ctx: {word: [片段, ...]}——其他文档的上下文片段（注册表页面解析），
              可选；提供则 prompt 标注「其他文档片段」。
    """
    if not terms:
        return terms
    kept = []
    failed = False
    for i in range(0, len(terms), batch_size):
        batch = terms[i:i + batch_size]
        items = []
        for t in batch:
            w = t.get('word', '')
            if not w:
                continue
            ctxs = _multi_ctx_snippet(ls_all, w)
            extra = (extra_ctx or {}).get(w) or []
            ctx_str = ' | '.join(ctxs) if ctxs else '（无上下文）'
            if extra:
                ctx_str += '；其他文档片段：' + ' | '.join(extra[:2])
            items.append(f"{len(items)+1}. 词：{w}（本文件频次 {t.get('freq','?')}）| 多文档片段：{ctx_str[:250]}")
        if not items:
            continue
        try:
            result = wr.llm_call(
                [{'role': 'system', 'content': '你是术语筛选专家，只输出JSON。'},
                 {'role': 'user', 'content': GLOBAL_FILTER_PROMPT.format(items='\n'.join(items))}],
                model=model, max_tokens=max_tokens, temperature=0.0)
            result = wr.ensure_full_json(result)
            parsed = json.loads(result)
            decisions = {}
            def _collect(x):
                if isinstance(x, dict):
                    w = (x.get('word') or '').strip()
                    if w:
                        decisions[w] = x.get('keep', True)
                elif isinstance(x, list):
                    for y in x:
                        _collect(y)
            _collect(parsed)
            for t in batch:
                w = t.get('word', '')
                if w and decisions.get(w, True):
                    kept.append(t)
            print(f"  [全局批 {i//batch_size+1}] {len(batch)} 词 → 保留 {sum(1 for t in batch if decisions.get(t.get('word',''), True))}")
        except Exception as e:
            print(f"  ⚠️ 全局批 {i//batch_size+1} LLM 过滤失败（{e}），该批全部保留（fail-safe）")
            kept.extend(batch)
            failed = True
    if failed:
        print("  ⚠️ 部分全局批次失败，已按 fail-safe 保留原词")
    # 兜底：LLM 判 keep 也过程序硬过滤（COMMON_WORDS + 形态垃圾，2026-09-02）
    return apply_hard_filter(kept)


def main():
    ap = argparse.ArgumentParser(description='高频关键词 LLM 语义过滤（剔除纯通用词）')
    ap.add_argument('--terms-file', required=True, help='discover_terms.py 输出 JSON')
    ap.add_argument('--ls-file', required=True, help='长句列表 JSON（提供上下文）')
    ap.add_argument('-o', '--output', default='/tmp/terms_filtered.json')
    ap.add_argument('--kb', default=None, help='知识库 ID（LLM 配置来源）')
    ap.add_argument('--model', default=None, help='LLM 模型（默认 config.yaml）')
    ap.add_argument('--batch-size', type=int, default=15)
    ap.add_argument('--max-tokens', type=int, default=3000)
    ap.add_argument('--dry-run', action='store_true', help='只打印判断不写文件')
    args = ap.parse_args()

    wr.load_config()
    wr.set_phase('filter_terms')
    if args.kb:
        wr.KB = args.kb

    with open(args.terms_file, encoding='utf-8') as f:
        terms = json.load(f)
    with open(args.ls_file, encoding='utf-8') as f:
        ls_all = json.load(f)
    print(f"候选词: {len(terms)} 个")

    kept = filter_terms_llm(terms, ls_all, model=args.model,
                            batch_size=args.batch_size, max_tokens=args.max_tokens)
    # 兜底：COMMON_WORDS/形态垃圾命中剔除已内置于 filter_terms_llm（apply_hard_filter）
    removed = [t.get('word', '') for t in terms if t not in kept]
    print(f"\n过滤结果: 保留 {len(kept)} / 剔除 {len(removed)}")
    if removed:
        print("剔除词:", '、'.join(removed))
    print("保留词:", '、'.join(t.get('word','') for t in kept))

    if args.dry_run:
        return
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)
    print(f"已写入 {args.output}")


if __name__ == '__main__':
    main()
