#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长句原文拆分 v3（整篇理解 + 知识单元抽取版，2026-08-28 方案 B 改造）。

与 v2（extract_long_sentences.py v1 标点硬切 / v2 窗口语义拆分）的区别：
  v2 按固定字符窗口（1800 字）让 LLM 拆"逻辑自包含最小单元"，text 必须单条连续原文。
  问题：单句脱离上下文丢失语义——如「我方在投标/应答文件中确定了集成商、产品品牌、
  规格型号的说明文件」单看不知道它是哪部法规中的哪条规定的
  **投标场景的依据文件**。用户要求：整篇理解之后抽取知识单元，综合多个原文句子。

v3 两阶段管线：
  阶段 1（程序切句）：全文按句末标点切句并编号（引号内句号不切），每句记录原文。
  阶段 2（LLM 整篇理解归组）：把【文档摘要】+【编号句子清单】一次性交给 LLM，
      LLM 通读全篇后把句子归组为"知识单元"（一条完整语义 = 场景+依据 / 规则+条件
      / 流程+例外…，可跨段落挑句，句子引用编号，不复述原文）→ 输出
      [{title, sentence_ids:[...]}]。
  阶段 3（程序拼接校验）：按 sentence_ids 取原句、按原文顺序拼接（句间 \n）、
      逐句原文校验、norm 去重、噪音过滤 → candidates.json（格式与 v2 一致，
      下游 build_full.py / 实体抽取 / 摘要回填全部兼容）。

句子锚定完全由程序保证（切句 + 编号引用），LLM 不输出任何原文文本 → 杜绝
"LLM 复述原文有偏差"导致的锚定匹配问题。跨段落/跨小节挑句由 sentence_ids 支持。

超长文档（>150 句）自动按句子数分块（`split_sentence_blocks`，块边界优先落在
章节标题句前，块内独立编号，单次 LLM 调用 ~150 句输出稳定不截断）；
跨块语义单元优先归入首句所在块。

Usage:
    python extract_long_sentences_v2.py <chunks.json> -o candidates.json \
        [--summary-file <摘要.txt>] [--summary-slug <summary页slug>] \
        [--max-tokens 8000] [--model pro]

输出: candidates.json
    [{text, title, md5_text, len, keep: true}]
    text 可能为多句原文拼接（句间 \n 分隔），每句逐字来自原文。
"""
import hashlib
import json
import os
import re
import sys
import time

# ---- LLM 配置（统一从 weknora_rpc.llm_config() 获取，2026-08-28）----
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
BASE_URL, API_KEY = wr.llm_config()
# relay API flash/pro 默认带 reasoning，必须显式关掉（否则 reasoning 耗尽 max_tokens 致 content 空）
THINKING_FALSE = {'enable_thinking': False, 'thinking': {'type': 'disabled'}}

# 断句碎片前缀（doc 解析拦腰截断残留；市场库第一批试点前留空，实测后再补充）
FRAGMENT_PREFIXES = []

# 阶段 2 prompt：整篇理解 + 句子归组为知识单元
SYSTEM_PROMPT = """你是市场监管法规解析专家。我会提供一份市场监管法律法规文档的【文档摘要】和【全文句子清单】。
全文已被程序按句切分并编号，每行格式：[编号] 句子原文（编号连续，可能有跨行内容，忽略换行符影响）。

你的任务：**通读全文、理解整部法规后**，把句子归组为"知识单元"。

知识单元定义：
1. 一个知识单元 = 一条完整的语义（一条规则/定义/监管流程/罚则及其适用条件、例外、后续处理等）。
2. 允许综合多个原文句子：单句无法完整表达的语义，用多句组合表达。
   例如一个罚则条文单元由"违法行为情形句 + 处罚幅度句 + 例外/但书句"组成——单拆任何一句都语义残缺。
3. 单元内的句子按原文顺序排列（sentence_ids 升序），可以跨段落挑句。
4. 每个句子只能归属一个单元；章节标题、目录、页码、纯编号残留、无实义碎片不归属任何单元。
5. 单元粒度：有效句子（非标题/目录/编号）的 1/2 到 1/3 个单元为宜；
   不要把完整语义硬拆成单句单元，也不要把互不相关的内容硬拼成一个单元。
6. 输出按单元首句编号升序排列。

只输出 JSON 数组，格式（不要任何其他文字/代码块标记）：
[{"title": "知识单元标题（15字以内，概括完整语义，保留关键法规概念：监管对象、义务主体、许可/处罚事项、时限、流程环节等）", "summary": "知识单元汇总描述（50-100字的一段话，概括该单元整体语义，说明这些原文句子共同表达的内容；不是原文摘录，是语义概括）", "sentence_ids": [3, 5, 6]}]

sentence_ids 必须来自清单中的真实编号；每个编号最多出现一次（跨单元不重复）。"""


def clean_chunk_text(text):
    """与 weknora_rpc.clean_chunk_text 一致的清洗。"""
    text = re.sub(r'\[([^\]]*)\]\(#[^)]*\)', r'\1', text)  # [..](#_Toc..) -> ..
    # doc 转 markdown 图片标记（2026-08-31：![..](resource://..) 残留会切出噪音长句页
    # 并截断正文，整段删除）
    text = re.sub(r'!\[[^\]]*\]\(resource://[^)]*\)', '', text)
    # PDF 页码线（2026-09-07 市场法规库实测：黄河保护法 PDF 页脚「— ８４３ —」，行级删除）
    text = re.sub(r'^\s*[-—－]\s*[\d０-９0-9\s]{1,6}\s*[-—－]\s*$', '', text, flags=re.MULTILINE)
    # markdown 标题标记清理（2026-08-28 实测 docx 转 markdown 的 #/### 标题残留：
    # 如 "### 关于印发《…》的通知"、"沪移★…## 中国移动…文件"——行首 # 保留标题文字去标记，
    # 文本中间混入的 # 序列直接删除，防片段/长句表格被 # 污染）
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'#{2,6}', '', text)
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not re.fullmatch(r'\[\d+\]', ln.strip())]
    return '\n'.join(lines)


def load_chunks(path):
    data = json.load(open(path, encoding='utf-8'))
    if isinstance(data, dict):
        data = data.get('chunks', [])
    # 按 chunk_index 排序（缺字段按 0）
    for i, c in enumerate(data):
        c.setdefault('chunk_index', c.get('seq_id', i))
    data.sort(key=lambda c: c.get('chunk_index', 0))
    return data


def md5(s):
    return hashlib.md5(s.encode('utf-8')).hexdigest()


def norm(t):
    """归一化：去 markdown 标记、书名号、空白、标点。
    注意：字符类里 — 和 - 必须分开写（[—\-]），连写成 [—-] 会被 Python re
    解释为范围 U+2014..U+002D（覆盖全部汉字），导致整句被删空。"""
    t = re.sub(r'[*#\s《》|—\-，。；;：:、（）()]', '', t)
    for ch in '\u201c\u201d\u2018\u2019"\'\u300c\u300d':
        t = t.replace(ch, '')
    return t


def auto_noise_reason(s):
    """噪音兜底（复用 v1/v2 规则，LLM 漏网的再滤一遍）。"""
    t = s.strip()
    nt = norm(t)
    if len(nt) <= 1:
        return '单字符/纯标点'
    if t.startswith('**') and ('章' in t[:20]) and len(nt) < 60:
        return '章节标题/封面混合行'
    if t.strip().startswith('#') and len(nt) < 30:
        return '章节标题'
    if re.fullmatch(r'\d+[\.、．]?[一二三四五六七八九十]?[\u4e00-\u9fff]{0,6}[。；;]?', nt) and len(nt) <= 8:
        return '纯编号枚举项'
    if t.strip().startswith('**') and len(nt) < 30:
        return '标题混合短行'
    for frag in FRAGMENT_PREFIXES:
        if t.strip().startswith(frag) or nt == frag:
            return '断句碎片'
    if len(t) < 15 and not re.search(r'[。！？；;]$', t):
        return '残句（短且无句末标点）'
    if re.search(r'(的|了|在|与|和|或|等|为|以|按|按照|根据)$', nt) and len(t) < 30:
        return '连接词结尾残句'
    return None


def clean_title(t):
    """标题清洗：去引号/标点/超长。"""
    t = re.sub(r'[\n\r"\'《》【】#*“”‘’]', '', t or '').strip()
    t = re.sub(r'\s+', ' ', t)
    if len(t) > 30:
        t = t[:30]
    return t


# ---------- 阶段 1：程序切句 ----------

# xlsx/CSV 列前缀表格模式（2026-08-30 新增）：识别从 Excel 表格转出的文本
# （修订说明/授权清单/一览表等，chunks 格式 "A: 序号,B: 修订章节,C: 修订内容,D: 修订性质,
# E: 现条款,F: 原条款,G: 修订理由"）。一行 = 一个表格条目（跨多列语义），E/F/G 等长文本列
# 会被 chunk 按长度截断成多行（无前缀续行 或 ",F:" 续行前缀）——必须把整个条目合并为一个
# 原子句：若按句号切散，LLM 无法从碎片还原"同一修订条目的多列语义"（实测 812 切句中 301 条
# 是现条款/原条款/修订理由列互相复制内容产生的重复碎片）。
TABLE_ROW_RE = re.compile(r'^[A-G][:：]')            # 新表格条目行（如 "A: 1,B: ..."）
TABLE_CONT_RE = re.compile(r'^[,，;；]\s*[A-G][:：]')  # 条目续行前缀（如 ",G: 根据集团制度..."）

def _table_mode_trigger(ls):
    """表格模式激活判定（2026-08-30，2026-08-31 扩展授权清单型）：
    ① 修订说明型：A 列纯数字序号或表头"序号"（如 "A: 1,B: 第一章,..."）
    ② 授权清单型：行内含 ≥3 个 ",X:" 列前缀（如 "A: 采购方案,B: 200万以下,C: 200万以下,D: /,G: 审批..."，
       A 列是文字分类名而非数字——修订说明/授权清单共用同一表格模式，防止授权清单按 prose 切散成巨型句子）
    防误触发：中文制度正文里偶发的 "A: 采购方式包括..." 行（无连续列前缀）不激活。
    """
    m = re.match(r'^A[:：]\s*([^,，;；]+)', ls)
    if m:
        a_col = m.group(1).strip()
        if re.fullmatch(r'\d+', a_col) or a_col == '序号':
            return True
    # 授权清单型：≥3 个列前缀（含行首 A:）
    return len(re.findall(r'(?:^|[,，;；])\s*[A-I][:：]', ls)) >= 3

def split_sentences(text):
    """把全文切成"句子"（切句粒度 = LLM 归组的最小单位）：
    - 表格行（行首以 | 开头）→ 整行作为一个原子句子（表格行内部无句末标点，
      且一行 = 一条语义单元，如"适用条件表格"的每个场景行；2026-08-28 实测
      若按标点切，整张表会因无句号被吞成一个巨型句子，LLM 无法做场景级归组）
    - xlsx 列前缀表格行（2026-08-30 新增）→ "A: 序号,B: ..." 整条目作为一个
      原子句子（含被 chunk 截断的续行，程序跨行合并，LLM 不读 Excel 结构）
    - prose 行 → 按句末标点（。！？；）切，引号（“ ” 「 」）内不切；
      换行符保留在句内（原文逐字），拼接时句间用 \n 分隔
    """
    sents, buf = [], ''
    open_q = False
    in_table = False
    table_buf = []
    lines = text.split('\n')
    for line in lines:
        ls = line.strip()
        if ls.startswith('|'):
            # markdown 表格行：原子单元，即使含句末标点也不切（保持整行语义）
            if in_table and table_buf:
                sents.append('\n'.join(table_buf))
                table_buf = []
            in_table = False
            if buf.strip():
                sents.append(buf.strip())
                buf = ''
            if ls:
                sents.append(ls)
            continue
        if _table_mode_trigger(ls):
            # xlsx 表格条目行（A: 数字/序号）：结算上一个条目，开启新条目
            if in_table and table_buf:
                sents.append('\n'.join(table_buf))
                table_buf = []
            in_table = True
            table_buf = [ls]
            continue
        if in_table:
            # 表格模式内续行：",F:/,G:" 续行前缀 或 无前缀的单元格长文本续文
            # （chunk 截断导致 E/F/G 列内容拆到多行）→ 并入当前条目，不切散
            if ls or TABLE_CONT_RE.match(ls):
                if ls:
                    table_buf.append(ls)
                continue
            # 空行等表格外内容 → 结算当前条目，退出表格模式
            if table_buf:
                sents.append('\n'.join(table_buf))
                table_buf = []
            in_table = False
        for ch in line:
            buf += ch
            if ch in '\u201c\u2018\u300c"':
                open_q = True
            elif ch in '\u201d\u2019\u300d"':
                open_q = False
            if ch in '。！？；' and not open_q:
                s = buf.strip()
                if s:
                    sents.append(s)
                buf = ''
    if in_table and table_buf:
        sents.append('\n'.join(table_buf))
        table_buf = []
    if buf.strip():
        sents.append(buf.strip())
    return sents


def split_sentence_blocks(sentences, max_sents=150):
    """按句子数分块（每块 ≤ max_sents 句，块内独立编号）。

    为什么按句子数而非字数：单次 LLM 调用对 100+ 句的归组输出会超过
    max_tokens 截断（2026-08-28 实测：353 句、176 句均截断），触发对半切递归
    后单次调用仍需 300s+，串行太慢。150 句/块 → 单次输出 ~50 单元，稳定。
    块边界优先落在章节标题句（"第X章/第X节"开头）前，尽量保持语义边界。
    跨块语义单元优先归入首句所在块（可接受降级）。"""
    if len(sentences) <= max_sents:
        return [sentences]
    blocks, cur = [], []
    for s in sentences:
        is_heading = bool(re.match(r'^\s*(第[一二三四五六七八九十百零]+[章节部分篇])', s))
        if is_heading and cur and len(cur) >= max_sents // 2:
            blocks.append(cur)
            cur = []
        cur.append(s)
        if len(cur) >= max_sents:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


# ---------- 阶段 2：LLM 整篇理解 + 句子归组 ----------

def llm_extract_units(sentences, summary, model=None, max_tokens=8000, retries=8):
    """调用 LLM 通读句子清单，输出知识单元骨架 [{title, sentence_ids}]。
    返回 (units, truncated)：units=[{title, sentence_ids}] 或 None；truncated=输出是否被截断。"""
    numbered = '\n'.join(f'[{i + 1}] {s}' for i, s in enumerate(sentences))
    user_msg = ''
    if summary:
        user_msg += f'【文档摘要】\n{summary}\n\n'
    user_msg += f'【全文句子清单】\n{numbered}'
    payload = {
        "model": model or wr.DEFAULT_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": user_msg}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        **THINKING_FALSE,
    }
    import urllib.request
    # inferaiapi group 限流 ~3 次/分：与 wr.llm_call 共享全局节流（25s 间隔）
    _now = time.time()
    _gap = _now - wr._last_llm_ts[0]
    if _gap < 25:
        time.sleep(25 - _gap)
        _now = time.time()
    wr._last_llm_ts[0] = _now
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
        method="POST")
    last = None
    for a in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = json.loads(resp.read().decode())
            finish = body['choices'][0].get('finish_reason', '')
            content = (body['choices'][0]['message'].get('content') or '').strip()
            if not content:
                content = (body['choices'][0]['message'].get('reasoning_content') or '').strip()
            # 容错解析：剥 markdown 代码块
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                content = content.split('```')[1].split('```')[0].strip()
            content = content.strip()
            # 容错解析（2026-08-28 v3）：用 raw_decode 解析第一个完整 JSON 值——
            # 兼容：① 数组 + 尾部多余文字（Extra data）；② {"result":[...]} 包装；
            # ③ 正常纯数组。禁止用"找第一个 ]"截断（会误切 sentence_ids 内部的 ]）。
            dec = json.JSONDecoder()
            try:
                items, _ = dec.raw_decode(content)
            except json.JSONDecodeError:
                items = None
            if isinstance(items, dict):
                for k in ('result', 'sentences', 'units', 'data'):
                    if isinstance(items.get(k), list):
                        items = items[k]
                        break
            if not isinstance(items, list):
                return None, finish == 'length'
            return items, finish == 'length'
        except urllib.error.HTTPError as e:
            last = e
            # inferaiapi group 限流 ~3 次/分：等待必须覆盖限流窗口
            if e.code in (403, 429):
                time.sleep(min(max(30, 5 * (a + 1)), 90))
            else:
                time.sleep(3 * (a + 1))
        except Exception as e:
            last = e
            time.sleep(2 * (a + 1))
    print(f'LLM失败: {last}')
    return None, False


def extract_units_recursive(sentences, summary, model, max_tokens,
                            min_count=15, depth=0):
    """句子归组，带对半切降级重试：LLM 输出被截断（finish_reason=length）时，
    把句子列表对半切（每块重新编号）再递归处理，直到输出不再截断或句子过少。
    截断的原始输出会被丢弃，只保留递归切半后的完整结果。"""
    if len(sentences) <= min_count:
        units, truncated = llm_extract_units(sentences, summary, model=model, max_tokens=max_tokens)
        return (units or []), truncated
    units, truncated = llm_extract_units(sentences, summary, model=model, max_tokens=max_tokens)
    if units is not None and not truncated:
        return units, False
    prefix = '  ' * depth
    print(f'{prefix}  句子 {len(sentences)} 条 → 输出截断，对半切重试...')
    if depth > 3:
        return [], True
    mid = len(sentences) // 2
    left_units, left_trunc = extract_units_recursive(
        sentences[:mid], summary, model, max_tokens, min_count, depth + 1)
    right_units, right_trunc = extract_units_recursive(
        sentences[mid:], summary, model, max_tokens, min_count, depth + 1)
    print(f'{prefix}  左 {len(left_units)} 单元, 右 {len(right_units)} 单元')
    return left_units + right_units, (left_trunc or right_trunc)


# ---------- 阶段 3：程序拼接 + 校验 ----------

def backfill_missing_sentences(block, units):
    """阶段 3.5：LLM 归组未覆盖句子兜底补建（2026-08-30 新增，修长句漏切）。

    根因：SYSTEM_PROMPT 允许 LLM 跳过"章节标题/目录/页码/无实义碎片"，但 LLM
    会误判有效句子为噪音跳过；且原管线没有任何"未覆盖检测"，漏掉的句子直接消失
    （实测 0829 库 13/186 关系行的原文依据在 chunks 有、长句无）。

    逻辑：
    1. 统计所有单元已覆盖的句子编号（有效编号 1..len(block)）
    2. 对每个未覆盖句子跑 auto_noise_reason 复核：
       - 真噪音（标题/目录/残句等）→ 跳过（符合设计，不补）
       - 非噪音（LLM 漏归组的有效句子）→ 补建单句单元（sentence_ids=[i]）
    3. 补建单元并入 units，交给 build_units 走正常拼接/校验/去重（单句必过
       verify_in_original，因为句子本身来自程序切句）。

    返回补建后的 units 列表。
    """
    covered = set()
    for u in units:
        for v in (u.get('sentence_ids') or []):
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(block):
                covered.add(n)
    added = []
    for i, s in enumerate(block, 1):
        if i in covered:
            continue
        if auto_noise_reason(s):
            continue  # 真噪音（标题/目录/页码/残句），LLM 不归组符合设计
        # 表格条目行：A 列必须纯数字（表头"A: 序号"行不补建，2026-08-30）
        if TABLE_ROW_RE.match(s.strip()) and not table_entry_valid(s):
            continue
        # 表格列前缀残留（2026-08-30 实测修订说明 xlsx：chunk 截断残留的续行碎片，
        # 如 ",G: /A: 61,B: 第七章"——注意只拦 ",X:" 续行前缀，不拦 "A: 序号" 完整条目行：
        # 完整条目行由 split_sentences 表格模式合并后是合法原子句，须保留）
        if re.match(r'^[,，;；]\s*[A-G]:', s.strip()):
            continue
        t = clean_title(s) or '未命名单元'
        added.append({'title': t[:15], 'summary': '', 'sentence_ids': [i]})
    if added:
        print(f'  补建 {len(added)} 个 LLM 未归组的有效句子（单句单元）: '
              f'{[a["sentence_ids"][0] for a in added]}')
    return units + added


def verify_in_original(text, full_norm):
    """原文包含校验：句子必须能在原文中找到（防 LLM 改写/编造，对拼接单元的每个分句调用）。
    对首尾 5 字放宽（容忍剥掉编号前缀）。"""
    nt = norm(text)
    if not nt:
        return False
    if nt in full_norm:
        return True
    for k in range(1, 6):
        if len(nt) > k * 2 and nt[k:-k] in full_norm:
            return True
    return False


def table_entry_valid(s):
    """xlsx 列前缀表格条目行校验（2026-08-30 新增，2026-08-31 适配授权清单型）：
    合法数据条目 = ① A 列纯数字序号（修订说明型，如 "A: 1,B: 第一章"）
    或 ② 行内含 ≥3 个列前缀且 A 列非空非《》（授权清单型，A 列是文字分类名）。
    过滤：标题行（A 列《》书名）、空行。返回 True=合法数据条目。"""
    s = s.strip()
    m = re.match(r'^[A-GI][:：]\s*([^,，;；]+)', s)
    if not m:
        return False
    a_col = m.group(1).strip()
    if not a_col:
        return False
    if re.fullmatch(r'\d+', a_col) or a_col == '序号':
        return True
    if a_col.startswith('《'):
        return False
    # 表头行判定（2026-08-31 实测授权清单）：A 列="决策事项/分类"且后续含列名性片段
    # （B: 分类 / B: 授权层级 / C: 实施单位 等）→ 整行是表头，非数据条目
    if a_col in ('决策事项', '分类', '决策', '事项'):
        b_col = re.search(r'[,，;；]\s*B[:：]\s*([^,，;；]{1,12})', s)
        if b_col and re.search(r'分类|层级|实施单位|事项|决策', b_col.group(1)):
            return False
    # 授权清单型：≥3 个列前缀
    return len(re.findall(r'(?:^|[,，;；])\s*[A-I][:：]', s)) >= 3


def build_units(sentences, units, full_norm):
    """按 sentence_ids 拼接原句（升序、\n 分隔）→ 逐句原文校验 → 去重 → 噪音过滤。
    返回 candidates 列表 [{text, title, md5_text, len, keep:true}]。"""
    results, seen = [], set()
    for u in units:
        title = clean_title(u.get('title') or '')
        raw_ids = u.get('sentence_ids') or []
        ids = []
        for v in raw_ids:
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(sentences) and n not in ids:
                ids.append(n)
        if not ids:
            continue
        ids.sort()
        # 拼接：每句 strip 后 \n 连接（保留原文逐字；句内换行原样保留）
        parts = [sentences[i - 1].strip() for i in ids]
        # 分句去重：同 norm 的分句只保留首次出现（原文跨块/chunk 重叠导致的内容重复）
        seen_s, kept = set(), []
        for p in parts:
            np = norm(p)
            if np and np not in seen_s:
                seen_s.add(np)
                kept.append(p)
        text = '\n'.join(kept)
        if not text:
            continue
        # 逐句原文校验（程序切句天然满足，此处兜底防数据异常）。
        # 注意：必须按"句子"粒度校验（句子内部可含换行/表格行），不能按 \n 拆段——
        # 否则表格行等行级片段 norm 后为空/过短，误杀整个单元（2026-08-28 实测）
        if not all(verify_in_original(s, full_norm) for s in kept):
            print(f'  [DISCARD] 单元含无法在原文中校验的分句: {title} ids={ids[:5]}...')
            continue
        nt = norm(text)
        if nt in seen:
            continue
        seen.add(nt)
        # 噪音过滤：多句单元直接保留（拼接 text 长度足以排除残句误判）；
        # 单句单元仍走 auto_noise_reason 兜底
        if len(ids) == 1:
            # xlsx 表格条目行：A 列必须纯数字（过滤标题行/表头行，2026-08-30）
            if TABLE_ROW_RE.match(text.strip()) and not table_entry_valid(text):
                continue
            reason = auto_noise_reason(text)
            if reason:
                continue
        if not title:
            title = (text.split('\n')[0])[:15]
        # summary：LLM 生成的知识单元汇总描述（语义概括，非原文；只去换行合并空白，不截断）
        summary = re.sub(r'\s+', ' ', (u.get('summary') or '')).strip()
        results.append({
            'text': text,
            'title': title,
            'summary': summary,
            'md5_text': md5(text)[:8],
            'len': len(text),
            'keep': True,
        })
    return results


# ---------- 摘要读取（复用 v2 逻辑） ----------

def _read_summary_by_slug(slug):
    """读 wiki summary 页内容作为拆分输入。
    优先 psql 直连本机 WeKnora-postgres；失败回退 weknora_rpc。
    查询必须带 knowledge_base_id 过滤（历史库被替换后旧库 wiki_pages
    数据仍留在表里，不带过滤会命中旧库的同名页面）。"""
    import shutil
    import subprocess
    if shutil.which('docker') and shutil.which('psql') is None:
        try:
            kb = _KB_ID_FROM_CONFIG()
            q = ("SELECT content FROM wiki_pages "
                 "WHERE slug='%s' AND deleted_at IS NULL%s LIMIT 1;"
                 % (slug.replace("'", "''"),
                    f" AND knowledge_base_id='{kb}'" if kb else ""))
            r = subprocess.run(['docker', 'exec', 'WeKnora-postgres', 'psql',
                                '-U', 'weknora', '-d', 'weknora', '-t', '-A', '-c', q],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip() and 'ERROR' not in r.stdout:
                content = r.stdout.strip()
                print(f'摘要: psql summary页 {slug} ({len(content)} 字 → 取前 1200, kb={kb})')
                return content[:1200]
        except Exception as e:
            print(f'  摘要 psql 读取失败: {e}')
    try:
        wr.load_config()
        wr.mcp_init()
        KB = wr.load_kb()
        p = wr.read_page(slug, kb_id=KB)
        if 'error' not in p:
            content = p.get('content', '') or ''
            print(f'摘要: wr summary页 {slug} ({len(content)} 字 → 取前 1200)')
            return content[:1200]
    except Exception:
        pass
    return ''


def _KB_ID_FROM_CONFIG():
    """从技能 config.yaml 读当前 kb_id（避免查询命中历史库的同名页面）。"""
    import yaml
    try:
        skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for path in (os.path.join(skill_root, 'config.yaml'),
                     os.path.expanduser('~/.hermes/config.yaml')):
            if os.path.exists(path):
                cfg = yaml.safe_load(open(path, encoding='utf-8'))
                if cfg and 'weknora' in cfg and cfg['weknora'].get('kb_id'):
                    return cfg['weknora']['kb_id']
                if cfg and cfg.get('mcp_servers', {}).get('weknora', {}).get('kb_id'):
                    return cfg['mcp_servers']['weknora']['kb_id']
    except Exception:
        pass
    return ''


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    wr.set_phase('long_sentences')
    chunks_path = sys.argv[1]
    out_path = 'candidates.json'
    summary_file = None
    summary_slug = None
    max_full_len = 25000
    max_tokens = 8000
    model = wr.DEFAULT_MODEL
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == '-o' and i + 1 < len(args):
            out_path = args[i + 1]
        elif args[i] == '--summary-file' and i + 1 < len(args):
            summary_file = args[i + 1]
        elif args[i] == '--summary-slug' and i + 1 < len(args):
            summary_slug = args[i + 1]
        elif args[i] == '--max-full-len' and i + 1 < len(args):
            max_full_len = int(args[i + 1])
        elif args[i] == '--max-tokens' and i + 1 < len(args):
            max_tokens = int(args[i + 1])
        elif args[i] == '--model' and i + 1 < len(args):
            model = args[i + 1]
        # --window/--overlap 为 v2 遗留参数，v3 按整篇/分块理解，忽略并提示
        elif args[i] in ('--window', '--overlap') and i + 1 < len(args):
            print(f'提示: {args[i]} 为 v2 遗留参数，v3 已忽略（整篇理解无需窗口）')
            i += 1
        i += 1

    # 摘要输入：优先文件，其次 wiki summary 页
    summary = ''
    if summary_file and os.path.exists(summary_file):
        summary = open(summary_file, encoding='utf-8').read().strip()
        print(f'摘要: {summary_file} ({len(summary)} 字)')
    elif summary_slug:
        summary = _read_summary_by_slug(summary_slug)
        if not summary:
            print('警告: 读 summary 页失败，将无摘要输入')

    chunks = load_chunks(chunks_path)
    print(f'chunks={len(chunks)}')
    cleaned = [clean_chunk_text(c.get('content', '')) for c in chunks]
    full = '\n'.join(x for x in cleaned if x.strip())
    full_norm = norm(full)
    print(f'全文 {len(full)} 字')

    sentences_all = split_sentences(full)
    print(f'切句: {len(sentences_all)} 条')
    blocks = split_sentence_blocks(sentences_all, max_sents=150)
    print(f'分块: {len(blocks)} 块 ({"整篇" if len(blocks) == 1 else "按句子数，块内独立编号"})')

    results = []
    for bi, block in enumerate(blocks, 1):
        print(f'块 {bi}/{len(blocks)}: 句子 {len(block)} 条 → LLM 整篇理解归组...')
        units, truncated = extract_units_recursive(
            block, summary, model=model, max_tokens=max_tokens)
        if truncated:
            print(f'  警告: 块 {bi} 仍存在输出截断（可能丢单元）')
        if not units:
            print(f'  块 {bi} 失败（LLM 无输出）')
            continue
        # 阶段 3.5：未覆盖句子兜底补建（2026-08-30，修长句漏切）
        units = backfill_missing_sentences(block, units)
        block_results = build_units(block, units, full_norm)
        NL = '\n'
        print(f'  块 {bi}: LLM 单元 {len(units)} 个 → 拼接保留 {len(block_results)} 个'
              f'（其中多句单元 {sum(1 for r in block_results if NL in r["text"])} 个）')
        results.extend(block_results)
        time.sleep(0.3)

    # 跨块 norm 去重
    uniq, seen = [], set()
    for r in results:
        if r['md5_text'] in seen:
            continue
        seen.add(r['md5_text'])
        uniq.append(r)
    results = uniq

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    multi = sum(1 for r in results if '\n' in r['text'])
    print(f'\n完成: 分块={len(blocks)}, 最终候选={len(results)}（多句综合单元 {multi} 个, '
          f'占比 {multi / max(len(results), 1):.0%}）')
    print(f'saved: {out_path}')
    print('下一步：直接按 longsentence/<md5(kid)[:10]>-<md5(text)[:8]> 建页，title 用 LLM 标题')


if __name__ == '__main__':
    main()
