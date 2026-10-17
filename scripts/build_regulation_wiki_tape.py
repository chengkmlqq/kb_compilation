#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监管制度文档转wiki —— 服务编排（Tape）生成器。

把市场监督管理法规文档→wiki 的完整构建能力实现为服务编排节点，
业务逻辑全部内联在编排节点配置里（script 节点内联 Python + 通用原子
llm_extract / wiki_publish），仅保留 weknora_rpc 基础工具调用
（LLM 调用 / wiki 页面读写 / 目录 / 切片读取 / 分词）作为叶子。

节点 DAG（单文档构建，输入：kid / doc_name / source_file / kb_id）：
  n01 def 输入定义
  n02 def 派生变量（md5_kid / kid_prefix / 临时目录）
  n03 script 初始化 + 切片拉取清洗
  n04 script 门禁（chunks>0，否则阻断后继）
  n05 script 建法规目录树（根+三子目录+本体 9+12）
  n06 script 摘要生成 + 摘要页建页
  n07 script 长句抽取（切句+LLM 归组+回填+校验+渲染 content）
  n08 wiki_publish 长句建页（幂等）
  n09 script 高频词发现（统计层 N-gram/PMI/熵）
  n10 llm_extract 关键词 LLM 语义过滤
  n11 script 关键词过滤应用 + 关键词页建页（创建即合并）
  n12 script 实体+规则联合抽取（LLM 分批 + canonicalize 去重）
  n13 script 规则实体页建页
  n14 script 业务实体页建页（注册表合并 + 关键规则/关联实体）
  n15 script 图谱写入（Neo4j，平台无端点时跳过不中断）
  n16 script 合并重复实体 + 链接重建（批次末，skip_final 时跳过）
  n17 print 完成汇总

用法：
  python3 scripts/build_regulation_wiki_tape.py --out /tmp/tape.json
输出：可直接 POST /api/v1/orchestrations 创建的 payload（含 nodes/edges/exec_params）
"""

import argparse
import json
import sys

# ─────────────────────────────────────────────────────────────────────
# 内联代码模板 —— script 节点头部（注入 weknora_rpc，基础工具通道）
# ─────────────────────────────────────────────────────────────────────
WR_HEAD = '''import sys, os, json, re, hashlib, time, math
G = globals()
G.update({'sys': sys, 'os': os, 'json': json, 're': re,
          'hashlib': hashlib, 'time': time, 'math': math})
SD = '/srv/kb/worker/builtin_engine/scripts'
if SD not in sys.path:
    sys.path.insert(0, SD)
os.environ.setdefault('KB_API_BASE_URL', 'http://api-server:8000')
import weknora_rpc as wr
G['wr'] = wr
G['bindings'] = bindings
G['context'] = context
G['config'] = config
wr.load_config()
_kb = bindings.get('kb_id') or os.environ.get('WEKNORA_KB_ID', '')
if _kb:
    wr.KB = _kb
    os.environ['WEKNORA_KB_ID'] = _kb
wr.mcp_init()
_kid = bindings.get('kid', '')
_doc = bindings.get('doc_name', '')
_src = bindings.get('source_file', '')
G['_kid'] = _kid
G['_doc'] = _doc
G['_src'] = _src
'''

# ─────────────────────────────────────────────────────────────────────
# n03: 初始化 + 切片拉取清洗
# ─────────────────────────────────────────────────────────────────────
CODE_INIT_CHUNKS = WR_HEAD + '''
DIR = f"/tmp/reg_{_kid[:8]}"
os.makedirs(DIR, exist_ok=True)
if not _kid:
    raise ValueError("缺少 kid")
chunks = []
page, psize = 1, 100
while True:
    r = wr.list_chunks(_kid, kb_id=wr.KB, page=page, page_size=psize)
    data = (r or {}).get('data') or []
    if not data:
        break
    chunks.extend(data)
    if len(data) < psize:
        break
    page += 1
chunks.sort(key=lambda c: c.get('chunk_index', 0))
cleaned = []
for c in chunks:
    t = (c.get('content') or '')
    t = wr.clean_chunk_text(t)
    lines = [ln for ln in t.split('\\n') if ln.strip()]
    cleaned.append('\\n'.join(lines))
full_text = '\\n\\n'.join(cleaned).strip()
chunk_count = len(chunks)
with open(f"{DIR}/chunks.json", 'w', encoding='utf-8') as f:
    json.dump([{'content': c} for c in cleaned], f, ensure_ascii=False)
with open(f"{DIR}/fulltext.txt", 'w', encoding='utf-8') as f:
    f.write(full_text)
bindings['chunk_count'] = chunk_count
bindings['full_text_len'] = len(full_text)
bindings['reg_dir'] = DIR
print(f"[n03] chunks={chunk_count} full_text={len(full_text)}字 → {DIR}")
'''

# ─────────────────────────────────────────────────────────────────────
# n04: 门禁
# ─────────────────────────────────────────────────────────────────────
CODE_GATE = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
cc = int(bindings.get('chunk_count', 0) or 0)
if cc <= 0:
    raise RuntimeError(f"切片缺失（chunks={cc}），禁止自行恢复，阻断构建")
print(f"[n04] 门禁通过：chunks={cc}")
'''

# ─────────────────────────────────────────────────────────────────────
# n05: 建法规目录树
# ─────────────────────────────────────────────────────────────────────
CODE_DIRS = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
os.makedirs(DIR, exist_ok=True)

def _clean(fname):
    fn = re.sub(r'\\.(docx|doc|pdf|xlsx|xls)$', '', fname)
    fn = re.sub(r'^《|》$', '', fn)
    return fn.strip()

def ensure_folder(name, parent_id):
    try:
        for f in wr.list_all_folders_cached(kb_id=wr.KB):
            if f.get('name') == name and str(f.get('parent_id') or '') == str(parent_id or ''):
                return f['id']
    except Exception:
        pass
    r = wr.list_folders(kb_id=wr.KB, parent_id=parent_id)
    for f in (r.get('folders') or r.get('data') or []):
        if f.get('name') == name:
            return f['id']
    r = wr.create_folder(name, parent_id=parent_id, kb_id=wr.KB)
    if isinstance(r, dict) and 'error' in r:
        raise RuntimeError(f"create_folder 失败: {r['error']}")
    return r.get('folder', {}).get('id', r.get('id', ''))

core = _clean(_doc or '')
roots = []
try:
    for f in wr.list_all_folders_cached(kb_id=wr.KB):
        if _clean(f.get('name', '')) == core and not f.get('parent_id'):
            roots.append(f['id'])
except Exception:
    r = wr.list_folders(kb_id=wr.KB, parent_id='')
    for f in (r.get('folders') or r.get('data') or []):
        if _clean(f.get('name', '')) == core:
            roots.append(f['id'])
root_id = roots[0] if roots else ensure_folder(_doc or '法规', '')
leaf = {
    "基础实体": ensure_folder("基础实体", root_id),
    "长句原文": ensure_folder("长句原文", root_id),
    "高频关键词": ensure_folder("高频关键词", root_id),
}
biz_cats = ["1-法律法规", "2-监管对象", "3-许可事项", "4-违法行为", "5-行政处罚",
            "6-监管职责", "7-标准规范", "8-程序事项", "9-时限要求"]
rule_cats = ["1-定义规则", "2-条件规则", "3-约束规则", "4-职责规则", "5-审批规则",
             "6-流程规则", "7-引用规则", "8-例外规则", "9-处罚规则",
             "10-时限规则", "11-版本规则", "12-程序规则"]
cat_ids = {"biz": {}, "rule": {}, "biz_short": {}, "rule_short": {}}
base_id = leaf["基础实体"]
for oname, out_key, cats in [("业务本体", "biz", biz_cats), ("规则本体", "rule", rule_cats)]:
    onto_id = ensure_folder(oname, base_id)
    for cat in cats:
        cat_ids[out_key][cat] = ensure_folder(cat, onto_id)
cat_ids["biz_short"] = {c.split('-', 1)[1]: c for c in biz_cats}
cat_ids["rule_short"] = {c.split('-', 1)[1]: c for c in rule_cats}
with open(f"{DIR}/catids.json", 'w', encoding='utf-8') as f:
    json.dump(cat_ids, f, ensure_ascii=False, indent=2)
bindings['root_id'] = root_id
bindings['leaf'] = leaf
bindings['cat_ids'] = cat_ids
print(f"[n05] 目录树就绪 root={root_id} leaf={json.dumps(leaf, ensure_ascii=False)}")

# 2026-10-17: 清空重跑 —— 删除本文件旧专属页、共享页仅摘除本文件关联
try:
    import clear_file_pages as _cfp
    _all_folders = [v for v in leaf.values() if v]
    _all_folders.append(root_id)
    for _m in (cat_ids.get('biz') or {}).values():
        if _m:
            _all_folders.append(_m)
    for _m in (cat_ids.get('rule') or {}).values():
        if _m:
            _all_folders.append(_m)
    _cleared, _shared = _cfp.clear_file_pages(
        wr, wr.KB, _kid, leaf.get('基础实体', ''), file_label=_doc, all_folders=_all_folders)
    print(f"[n05] 清空重跑: 删除专属页 {len(_cleared)} 个, 共享页摘除关联 {len(_shared)} 个")
except Exception as _e:
    print(f"[n05] 清空旧页失败（不中断构建）: {_e}")

'''

# ─────────────────────────────────────────────────────────────────────
# n06: 摘要生成 + 摘要页
# ─────────────────────────────────────────────────────────────────────
CODE_SUMMARY = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
with open(f"{DIR}/fulltext.txt", encoding='utf-8') as f:
    full_text = f.read()
md5_kid = hashlib.md5(_kid.encode()).hexdigest()
slug = f"summary/{md5_kid}"
title = os.path.splitext(_src or _doc)[0]
# 幂等：已有摘要页直接跳过（不能用 raise SystemExit —— celery 序列化异常崩）
r = wr.read_page(slug, kb_id=wr.KB)
if 'error' not in r and (r.get('content') or '').strip():
    bindings['summary_slug'] = slug
    print(f"[n06] 摘要页已存在，跳过 {slug}")
elif len(full_text) > 14000:
    full_text = full_text[:10000] + '\\n\\n……（中段省略）……\\n\\n' + full_text[-4000:]
family_name = _doc or os.path.splitext(_src or '')[0]
prompt = f"""你是市场监管法律法规知识库的文档摘要专家。请根据下面给定的法规原文切片，生成一张「法规文档卡片」形式的 markdown 摘要。

【文档信息】
- 文件名：{_src or _doc}

【文档原文切片】
{full_text}

【输出要求——严格按以下 markdown 结构，只输出卡片本体，禁止任何问候/聊天内容】：
# {family_name}

<一句话定位>（本法规管什么，一句话）

## 基本信息

| 字段 | 内容 |
|---|---|
| 位阶 | <法律 / 行政法规 / 部门规章 / 规范性文件 / 标准等，按制定机关与文号判断> |
| 领域 | <药品、医疗器械、化妆品、食品、特种设备、市场监管综合等> |
| 制定机关 | <全国人大常委会 / 国务院 / 国家市场监督管理总局等，从原文提取> |
| 文号 | <如：国家市场监督管理总局令第9号；法律无文号填"无"> |
| 发布日期 / 施行日期 | <从原文提取> |
| 修订记录 | <历次修订，原文无则填"无"> |
| 状态 | <现行有效 / 已废止（原文判断，不编造）> |

## 法规定位

<本法规在法律体系中的位置：上位法/同位阶关系、效力层级、与相关法规的衔接，2~4 句>

## 重点内容概览

这部法规规定<监管领域>的规范，重点覆盖以下内容：

- <要点1>
- <要点2>
- <要点3>
- <要点4>

## 与其他法律法规的关系

| 相关法律法规 | 关系说明 | 原文依据 |
|---|---|---|
| <库内关联法规用 [[summary/<md5(关联kid)>|法规名]] 双链；库内无对应文档用纯文本 《引用名》> | <关系说明，基于原文概括> | <原文句子摘录，逐字来自切片；禁止"暂无引用原文"> |

要求：
1. 所有内容必须基于给定原文切片提炼，不编造。
2. 关系表只列原文中明确提到的法律法规关系。
3. 原文依据列必须是切片原文中的原句摘录（逐字），每条关系都要有。
4. 位阶判断依据：全国人大及其常委会制定=法律；国务院制定或"国务院令"发布=行政法规；部门令/总局令发布=部门规章；其他规范性文件=规范性文件。
"""
content = wr.llm_call([{"role": "user", "content": prompt}], max_tokens=8000, temperature=0.2)
content = wr.clean_llm_tail(content).strip()
sref = f"{_kid}|{_src or _doc}"
r = wr.create_page(slug=slug, title=title, content=content, folder_id=bindings.get('root_id', ''),
                   kb_id=wr.KB, page_type='summary', source_refs=[sref])
if 'error' in r and '500' in str(r['error']):
    pass  # slug 已存在
wr.update_page(slug, kb_id=wr.KB, title=title, content=content, folder_id=bindings.get('root_id', ''),
               page_type='summary', source_refs=[sref], status='published')
bindings['summary_slug'] = slug
print(f"[n06] 摘要页已建 {slug} ({len(content)}字)")
'''

# ─────────────────────────────────────────────────────────────────────
# n07: 长句抽取（程序切句 + LLM 归组 + 回填 + 校验 + 渲染 content）
# ─────────────────────────────────────────────────────────────────────
CODE_LONGSENT = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
with open(f"{DIR}/chunks.json", encoding='utf-8') as f:
    chunks = json.load(f)
full_text = '\\n'.join(c.get('content', '') for c in chunks)

def norm(t):
    t = re.sub(r'[*#\\s《》|—\\-，。；;：:、（）()]', '', t or '')
    for ch in '\\u201c\\u201d\\u2018\\u2019' + chr(34) + '\\u300c\\u300d':
        t = t.replace(ch, '')
    return t

def md5(s):
    return hashlib.md5(s.encode('utf-8')).hexdigest()

def clean_title(t):
    t = re.sub(r'[\\n\\r' + chr(34) + chr(39) + '《》【】#*“”‘’]', '', t or '').strip()
    t = re.sub(r'\\s+', ' ', t)
    return t[:30]

def split_sentences(text):
    sents, buf = [], ''
    open_q = False
    lines = text.split('\\n')
    for line in lines:
        ls = line.strip()
        if ls.startswith('|'):
            if buf.strip():
                sents.append(buf.strip()); buf = ''
            if ls:
                sents.append(ls)
            continue
        for ch in line:
            buf += ch
            if ch in '\\u201c\\u2018\\u300c"':
                open_q = True
            elif ch in '\\u201d\\u2019\\u300d"':
                open_q = False
            if ch in '。！？；' and not open_q:
                s = buf.strip()
                if s:
                    sents.append(s)
                buf = ''
    if buf.strip():
        sents.append(buf.strip())
    return sents

def split_blocks(sentences, max_sents=150):
    if len(sentences) <= max_sents:
        return [sentences]
    blocks, cur = [], []
    for s in sentences:
        is_heading = bool(re.match(r'^\\s*(第[一二三四五六七八九十百零]+[章节部分篇])', s))
        if is_heading and cur and len(cur) >= max_sents // 2:
            blocks.append(cur); cur = []
        cur.append(s)
        if len(cur) >= max_sents:
            blocks.append(cur); cur = []
    if cur:
        blocks.append(cur)
    return blocks

def llm_group(sentences):
    numbered = '\\n'.join(f"[{i+1}] {s}" for i, s in enumerate(sentences))
    sysp = ("你是法规知识单元提取专家。把句子清单归组为知识单元，输出 JSON 数组："
            "[{\\"title\\":\\"单元标题(≤15字概括)\\",\\"sentence_ids\\":[编号列表],\\"summary\\":\\"一段汇总描述(50-100字)\\"]}。"
            "编号必须来自输入清单；同一句子只能属于一个单元；跳过章节标题/目录/页码/无实义碎片。只输出JSON。")
    raw = wr.llm_call([{"role": "system", "content": sysp},
                       {"role": "user", "content": numbered}],
                      max_tokens=8000, temperature=0.2)
    import json as _j
    raw = wr.ensure_full_json(raw)
    try:
        items = _j.loads(raw)
    except Exception:
        items = None
    if isinstance(items, dict):
        for k in ('result', 'sentences', 'units', 'data'):
            if isinstance(items.get(k), list):
                items = items[k]; break
    return items if isinstance(items, list) else []

sents = split_sentences(full_text)
units_all = []
for blk in split_blocks(sents):
    units_all.extend(llm_group(blk))

# 回填未覆盖句子（非噪音的单句补建）
covered = set()
for u in units_all:
    for v in (u.get('sentence_ids') or []):
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(sents):
            covered.add(n)
added = []
for i, s in enumerate(sents, 1):
    if i in covered:
        continue
    nt = norm(s)
    if len(nt) <= 1:
        continue
    t = clean_title(s) or '未命名单元'
    added.append({'title': t[:15], 'summary': '', 'sentence_ids': [i]})
units_all = units_all + added

# 拼接 + 原文校验 + 去重
full_norm = norm(full_text)
results, seen = [], set()
for u in units_all:
    title = clean_title(u.get('title') or '')
    ids = []
    for v in (u.get('sentence_ids') or []):
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(sents) and n not in ids:
            ids.append(n)
    if not ids:
        continue
    ids.sort()
    parts = [sents[i-1].strip() for i in ids]
    text = '\\n'.join(parts)
    nt = norm(text)
    if not nt or nt in seen:
        continue
    seen.add(nt)
    if len(ids) == 1 and (len(nt) <= 1 or len(text) < 15):
        continue
    if not title:
        title = (text.split('\\n')[0])[:15]
    summary = re.sub(r'\\s+', ' ', (u.get('summary') or '')).strip()
    results.append({'text': text, 'title': title, 'summary': summary,
                    'md5_text': md5(text)[:8]})

# 渲染 content（render_unit_content：标题 + 汇总 + 两列表格）
def _cell(s):
    return s.replace('|', '\\\\|').replace('\\n', ' ')
ls_all = []
kid_prefix = hashlib.md5(_kid.encode()).hexdigest()[:10]
for c in results:
    rows = '\\n'.join(f"| {i} | {_cell(s)} |" for i, s in enumerate(c['text'].split('\\n'), 1))
    head = f"# {c['title']}\\n\\n" + (f"{c['summary'].strip()}\\n\\n" if c['summary'].strip() else '')
    content = f"{head}| 序号 | 原文 |\\n|---|---|\\n{rows}"
    slug = f"longsentence/{kid_prefix}-{c['md5_text']}"
    ls_all.append({'title': c['title'], 'text': c['text'], 'summary': c['summary'],
                   'slug': slug, 'content': content, 'source_refs': [f"{_kid}|{_src or _doc}"]})
with open(f"{DIR}/ls_all.json", 'w', encoding='utf-8') as f:
    json.dump(ls_all, f, ensure_ascii=False, indent=2)
bindings['ls_count'] = len(ls_all)
print(f"[n07] 长句抽取完成：{len(ls_all)} 个单元 → {DIR}/ls_all.json")
'''

# ─────────────────────────────────────────────────────────────────────
# n09: 高频词发现（统计层）
# ─────────────────────────────────────────────────────────────────────
CODE_TERMS = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
with open(f"{DIR}/ls_all.json", encoding='utf-8') as f:
    ls_all = json.load(f)
STOPWORDS = set('的 了 在 是 有 为 与 和 或 及 被 把 从 以 对 于 到 让 由 向 将 并 而 但 可 如 若 且 该 其 这 那 所 各 每 按 应 当 须 需 请 能 要 会 已 正 不 未 非 无 上 下 中 外 内 前 后 之 等 得 进行 相关 按照 根据 通过 以及 或者 应当 必须 不得 可以 用于 包括 属于 低于 高于 超过 以上 以下 法规 法律 行政法规 部门规章 办法 条例 规定 细则 规章 制度 施行 修订 公布 实施 废止 部门 单位 机关 机构 人员 工作 情况 内容 时间 期限 范围 结果 事项 活动 环节 流程 阶段 责任 义务 权利 行为 标准 要求 程序 监督管理 处罚'.split())
TAIL_STOP = set('的 了 是 在 和 与 及 或 之 所 等 以 于 对 为 从 到 向 将 应 当 须 需 能 要 会 已 未 非')
HEAD_STOP = set('如 该 此 若 且 其 这 那 所 各 每 按 应 当 须 需 请 能 要 会 已 正 不 未 非 无 上 下 中 外 内 前 后 之 以 为 由 于 而 但 可 将 并 被 把 让 向 从 对 到 是 有 和 与 或 及 也 又 才 就 都 还 再 只 即 则')
CORP_FRAG = ('有限公司', '人民共和国', '市场监督管理总局', '知识产权局')
SPLIT_RE = re.compile(r'[，。！？；、,.;:：\\n\\r\\t（）()《》<>〈〉「」『』“”‘’…—0-9\\-]')

def _clean_text(t):
    return re.sub(r'[^\\u4e00-\\u9fff]', '', t or '')

def _entropy(counter):
    total = sum(counter.values())
    if total <= 0:
        return 0.0
    return -sum((c / total) * math.log2(c / total) for c in counter.values())

import collections
sentences = []
for item in ls_all:
    if not item.get('text'):
        continue
    for sub in SPLIT_RE.split(item['text']):
        s = _clean_text(sub)
        if s:
            sentences.append(s)
max_len, min_freq = 6, 2 if len(ls_all) < 30 else 3
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
                    left_ctx[w][s[i-1]] += 1
                if i + L < n:
                    right_ctx[w][s[i+L]] += 1
N = sum(len(s) for s in sentences) or 1
candidates = []
for w, f in ngram_freq.items():
    if len(w) < 2 or f < min_freq:
        continue
    if w in STOPWORDS or w[-1] in TAIL_STOP or w[0] in HEAD_STOP:
        continue
    if any(x in w for x in CORP_FRAG):
        continue
    le = _entropy(left_ctx.get(w, {}))
    re_ = _entropy(right_ctx.get(w, {}))
    if min(le, re_) <= 0:
        continue
    if len(w) == 2 and f < 5 and min(le, re_) < 1.5:
        continue
    p_w = f / N
    min_pmi = None
    for cut in range(1, len(w)):
        l, r = w[:cut], w[cut:]
        pl, pr = ngram_freq.get(l, 0) / N, ngram_freq.get(r, 0) / N
        pmi = 0.0 if (pl <= 0 or pr <= 0) else (math.log2(p_w / (pl * pr)) if p_w / (pl * pr) > 0 else 0.0)
        min_pmi = pmi if min_pmi is None else min(min_pmi, pmi)
    len_bonus = {2: 0.0, 3: 2.0, 4: 3.0, 5: 3.5, 6: 4.0}.get(len(w), 3.0)
    score = math.log2(f) + (min_pmi or 0.0) + min(le, re_) + len_bonus
    candidates.append({'word': w, 'freq': f, 'pmi': round(min_pmi or 0.0, 3),
                       'left_entropy': round(le, 3), 'right_entropy': round(re_, 3),
                       'score': round(score, 3)})
candidates.sort(key=lambda x: (-x['freq'], -len(x['word'])))
keep = []
for c in candidates:
    w = c['word']
    if any(w in k['word'] and c['freq'] <= k['freq'] * 1.5 for k in keep):
        continue
    keep.append(c)
keep.sort(key=lambda x: -x['score'])
terms = keep[:50]
with open(f"{DIR}/terms.json", 'w', encoding='utf-8') as f:
    json.dump(terms, f, ensure_ascii=False, indent=2)
bindings['term_count'] = len(terms)
print(f"[n09] 新词发现：{len(terms)} 个候选")
'''

# ─────────────────────────────────────────────────────────────────────
# n10: 关键词 LLM 语义过滤（llm_extract 原子）
# ─────────────────────────────────────────────────────────────────────
FILTER_PROMPT = """你是市场监管法规知识库的术语筛选专家。以下是文档中通过统计方法发现的候选高频词，请判断每个词是否**具有业务含义**，值得作为关键词/术语保留。

【判断标准】
- 保留（keep=true）：具有业务含义的制度术语、业务对象、业务事件、业务概念（如"行政处罚""药品经营许可""进口药材""医疗器械网络销售""食品生产许可"）
- 剔除（keep=false）：**纯通用词**——没有业务含义、可出现在任何文档中的通用动词/修饰词（如"确定""完善""规范""加强""开展""落实""相关""主要""重要""明确""部分""遵照""印发""纳入""引入"），以及无业务含义的功能词（如"特此""年月日"）
- **剔除以下高频统计碎片（即使频次/凝固度很高——它们只是统计噪音，不是术语）**：
  ① 跨词切碎残片（如"理办"来自"管理办法"、"关业务"来自"相关业务"）
  ② 纯数字/文号/年份（如"25""2025年"）
  ③ 制度文种词（如"管理办法""实施细则""管理规定"——文件名后缀）
  ④ 介词/动词粘连片段（如"经本单位""文件明确""达到或超过"）
  ⑤ 单字/双字泛化词（如"使用""完成""提交""业务""电子""程序"）

每个词附其在原文中的出现片段（上下文），请结合上下文判断该词在本文档中是否承载业务含义。
只输出JSON数组，格式：[{"word":"词","keep":true,"reason":"一句话理由"}]，不要输出任何其他文字。

候选词及上下文：
{{items}}"""

# ─────────────────────────────────────────────────────────────────────
# n11: 关键词过滤应用 + 关键词页建页（创建即合并）
# ─────────────────────────────────────────────────────────────────────
CODE_KW_PAGES = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
with open(f"{DIR}/terms.json", encoding='utf-8') as f:
    terms = json.load(f)
decisions = {}
dec_path = f"{DIR}/terms_filtered_decisions.json"
if os.path.exists(dec_path):
    with open(dec_path, encoding='utf-8') as f:
        dec_list = json.load(f)
    for d in dec_list:
        w = (d.get('word') or '').strip()
        if w:
            decisions[w] = d.get('keep', True)
COMMON_WORDS = set('确定 明确 完善 规范 加强 开展 落实 推进 建立 健全 强化 提高 促进 实现 做好 抓好 严格 认真 积极 深入 全面 充分 不断 进一步 有关 相关 主要 重要 具体 有效 合理 科学 统一 直接 其他 各类 各种 部分 相应 一定 必要 特此 年月日 本细则 本通知 本办法 本实施细则 有关要求 相关要求 具体规定 遵照 印发 纳入 引入 专业 市场 决策 管理制度 业务 制度 条例 办法 规定 细则 规章 法规 法律 施行 修订 公布 实施 废止 之日起 使用 完成 增加 提交 支撑 负责 调整 变更 合作 计划 组织 监督 评审 审批 领导 框架 份额 媒介 级别 层级 分级 办公 电子 技术 程序 配置 预研 集体 集中 管理办法 实施细则 管理规定 相关管理 相关规定 工作计划 达到或超过 经营管理 负责部门'.split())

def is_junk_word(w):
    if not w or not re.search(r'[\\u4e00-\\u9fff]', w) or '的' in w:
        return True
    if re.match(r'^\\d+$', w) or re.match(r'^\\d', w) or re.match(r'^20\\d{2}年', w):
        return True
    if re.search(r'[〔〕\\[\\]（）()]', w):
        return True
    return False

kept = [t for t in terms
        if decisions.get(t.get('word', ''), True)
        and not is_junk_word(t.get('word', ''))
        and t.get('word', '') not in COMMON_WORDS]
top = kept[:15]
with open(f"{DIR}/terms_filtered.json", 'w', encoding='utf-8') as f:
    json.dump(top, f, ensure_ascii=False, indent=2)

def canonicalize(name):
    name = re.sub('[（(][^）)]*[）)]', '', name or '').strip()
    name = re.sub('\\\\s+', '', name)
    cn2ar = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}
    name = re.sub('([一二三四五六七八九十])$', lambda m: cn2ar.get(m.group(1), m.group(0)), name)
    return name

def kw_slug(word):
    c = canonicalize(word or '')
    return f"keyword/{hashlib.md5(c.encode('utf-8')).hexdigest()[:10]}" if c else ''

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
    s = s.replace('\\n', ' ').replace('|', '\\\\|')
    s = re.sub(r'#{1,6}', '', s)
    return s.replace(w, f'**{w}**')

def bookname(name):
    return name if name.startswith('《') and name.endswith('》') else f"《{name}》"

with open(f"{DIR}/ls_all.json", encoding='utf-8') as f:
    ls_all = json.load(f)
# 注册表：全量拉取新规范 frequent_keyword 页
reg = {}
page_no = 1
while True:
    r = wr.list_wiki_pages(kb_id=wr.KB, page=page_no, page_size=200)
    pages = (r or {}).get('pages') or []
    if not pages:
        break
    for p in pages:
        if p.get('page_type') != 'frequent_keyword':
            continue
        slug = p.get('slug') or ''
        m = re.match(r'^keyword/([0-9a-f]{10})$', slug)
        if not m:
            continue
        fids = p.get('folder_ids') or []
        reg[m.group(1)] = {'slug': slug, 'title': p.get('title') or '',
                           'folder_id': p.get('folder_id') or (fids[0] if fids else ''),
                           'folder_ids': fids, 'source_refs': p.get('source_refs') or [],
                           'content': p.get('content') or ''}
    if len(pages) < 200:
        break
    page_no += 1

def parse_groups_v2(content):
    groups = {}
    cur, cur_freq = None, 0
    for ln in (content or '').split('\\n'):
        s = ln.strip()
        m = re.match(r'^\\*\\*(.+?)\\*\\*（频次：(\\d+)）\\s*$', s)
        if m:
            cur, cur_freq = m.group(1), int(m.group(2))
            groups.setdefault(cur, (cur_freq, []))
            continue
        m3 = re.match(r'^\\|\\s*\\d+\\s*\\|\\s*\\[\\[([^\\]|]+)\\|([^\\]]*)\\]\\]\\s*\\|', s)
        if m3 and cur is not None and cur in groups:
            groups[cur][1].append((m3.group(1), m3.group(2)))
    return groups

def render_page_v2(title, groups_with_freq, total_docs):
    freq_total = sum(f for f, _ in groups_with_freq.values())
    lines = [f'# {title}', '', f'本关键词在 {total_docs} 篇文档中高频出现（总频次：{freq_total}）。',
             '', '## 原文出现位置', '']
    for doc, (freq, links) in groups_with_freq.items():
        lines.append(f'**{bookname(doc)}**（频次：{freq}）')
        lines.append('')
        lines.append('| 序号 | 关联原文 |')
        lines.append('|---|---|')
        if not links:
            lines.append('| - | 暂无关联长句 |')
        for i, (slug, snippet) in enumerate(links, 1):
            lines.append(f'| {i} | [[{slug}|{snippet}]] |')
        lines.append('')
    return '\\n'.join(lines).rstrip() + '\\n'

src_doc = _src or _doc
sref = f"{_kid}|{src_doc}"
kw_created = kw_merged = 0
for t in top:
    w = t.get('word', '')
    if not w:
        continue
    slug = kw_slug(w)
    if not slug:
        continue
    md5pre = slug.split('/')[-1]
    matched_ls = [ls for ls in ls_all if w in ls.get('text', '')]
    links = [(ls['slug'], _ls_snippet(ls, w)) for ls in matched_ls[:5]]
    hit = reg.get(md5pre)
    if hit:
        r = wr.read_page(slug=hit['slug'], kb_id=wr.KB)
        if 'error' not in r:
            fids = list(hit.get('folder_ids') or [])
            leaf = bindings.get('leaf', {})
            if leaf.get('高频关键词') not in fids:
                fids.append(leaf.get('高频关键词'))
            refs = list(hit.get('source_refs') or [])
            if sref not in refs:
                refs.append(sref)
            groups = parse_groups_v2(r.get('content') or '')
            groups[src_doc] = (t['freq'], links)
            content = render_page_v2(w, groups, len(groups))
            wr.update_page(slug=hit['slug'], kb_id=wr.KB, title=w, content=content,
                           folder_id=fids[0] if fids else '', page_type='frequent_keyword',
                           status=r.get('status') or 'published', source_refs=refs)
            kw_merged += 1
            reg[md5pre] = {'slug': hit['slug'], 'title': w, 'content': content,
                           'folder_ids': fids, 'source_refs': refs, 'folder_id': fids[0] if fids else ''}
    else:
        leaf = bindings.get('leaf', {})
        groups = {src_doc: (t['freq'], links)}
        content = render_page_v2(w, groups, 1)
        r = wr.read_page(slug=slug, kb_id=wr.KB)
        if 'error' not in r:
            continue
        r = wr.create_page(slug=slug, title=w, content=content,
                           folder_id=leaf.get('高频关键词', ''), page_type='frequent_keyword',
                           kb_id=wr.KB, source_refs=[sref])
        if 'error' not in r or '500' in str(r.get('error', '')):
            kw_created += 1
            wr.update_page(slug=slug, kb_id=wr.KB, title=w, content=content,
                           folder_id=leaf.get('高频关键词', ''), page_type='frequent_keyword',
                           source_refs=[sref])
            reg[md5pre] = {'slug': slug, 'title': w, 'content': content,
                           'folder_ids': [leaf.get('高频关键词', '')], 'source_refs': [sref],
                           'folder_id': leaf.get('高频关键词', '')}
bindings['kw_created'] = kw_created
bindings['kw_merged'] = kw_merged
print(f"[n11] 关键词页：created={kw_created} merged={kw_merged}（候选池 {len(top)}）")
'''

# ─────────────────────────────────────────────────────────────────────
# n12: 实体+规则联合抽取（LLM 分批 + canonicalize 去重）
# ─────────────────────────────────────────────────────────────────────
CODE_JOINT = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
with open(f"{DIR}/ls_all.json", encoding='utf-8') as f:
    ls_all = json.load(f)
terms_txt = ''
tf = f"{DIR}/terms_filtered.json"
if os.path.exists(tf):
    try:
        with open(tf, encoding='utf-8') as f:
            tlist = json.load(f)
        terms_txt = '\\n'.join(f"- {t.get('word', '')}" for t in tlist[:50] if t.get('word'))
    except Exception:
        terms_txt = ''
BIZ_CAT_SHORT = {'法律法规': '1-法律法规', '监管对象': '2-监管对象', '许可事项': '3-许可事项',
                 '违法行为': '4-违法行为', '行政处罚': '5-行政处罚', '监管职责': '6-监管职责',
                 '标准规范': '7-标准规范', '程序事项': '8-程序事项', '时限要求': '9-时限要求'}
RULE_CAT_SHORT = {'定义规则': '1-定义规则', '条件规则': '2-条件规则', '约束规则': '3-约束规则',
                  '职责规则': '4-职责规则', '审批规则': '5-审批规则', '流程规则': '6-流程规则',
                  '引用规则': '7-引用规则', '例外规则': '8-例外规则', '处罚规则': '9-处罚规则',
                  '时限规则': '10-时限规则', '版本规则': '11-版本规则', '程序规则': '12-程序规则'}
RO_NUM_MAP = {'定义规则': 1, '条件规则': 2, '约束规则': 3, '职责规则': 4, '审批规则': 5,
              '流程规则': 6, '引用规则': 7, '例外规则': 8, '处罚规则': 9, '时限规则': 10,
              '版本规则': 11, '程序规则': 12}
JOINT_PROMPT = ('你是市场监管法规解析专家，只输出JSON，不要任何思考过程。\\n'
'从下面长句列表抽取业务实体(name/category/definition)和规则(title/category/constraint/evidence)，\\n'
'category用9类之一：法律法规/监管对象/许可事项/违法行为/行政处罚/监管职责/标准规范/程序事项/时限要求；\\n'
'规则category用12类之一：定义规则/条件规则/约束规则/职责规则/审批规则/流程规则/引用规则/例外规则/处罚规则/时限规则/版本规则/程序规则。\\n'
'constraint必须逐字等于entities[].name之一；name/evidence必须逐字来自长句原文；rules[].structure只填相关字段(主体/条件/动作)，objects可选带role；\\n'
'禁止纯日期/第X条/本法本规定等指示定语；同一概念只输出一次。\\n'
'参考术语：{terms}\\n'
'只输出JSON对象 {"entities":[{"name":"实体名","category":"监管对象","definition":"一句话定义"}],'
'"rules":[{"title":"规则概括≤20字","category":"约束规则","constraint":"实体名","evidence":"原文片段","description":"...","structure":{"主体":"x"},"objects":[{"name":"实体名","role":"客体"}]}]}')

def canonicalize(name):
    name = re.sub('[（(][^）)]*[）)]', '', name or '').strip()
    name = re.sub('\\\\s+', '', name)
    cn2ar = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}
    name = re.sub('([一二三四五六七八九十])$', lambda m: cn2ar.get(m.group(1), m.group(0)), name)
    return name

JOINT_BATCH = 30
prompt = JOINT_PROMPT.replace('{terms}', terms_txt or '- （无）')
biz_by_canon = {}
rule_entities = []
failed = []
total_batches = (len(ls_all) + JOINT_BATCH - 1) // JOINT_BATCH
for bidx in range(total_batches):
    batch = ls_all[bidx * JOINT_BATCH:(bidx + 1) * JOINT_BATCH]
    ls_text = '\\n'.join(f"{i + 1}. {item['text'][:250]}" for i, item in enumerate(batch))
    obj = None
    for attempt in range(4):
        try:
            raw = wr.llm_call([{'role': 'system', 'content': '你是市场监管法规解析专家，只输出JSON，不要任何思考过程。'},
                               {'role': 'user', 'content': prompt + '\\n\\n长句列表：\\n' + ls_text},
                               {'role': 'assistant', 'content': '{"entities": ['}],
                              max_tokens=16000)
            try:
                obj = json.loads(wr.ensure_full_json('{"entities": [' + raw))
            except Exception:
                obj = json.loads(wr.ensure_full_json(raw))
            if isinstance(obj, dict):
                break
        except Exception as e:
            print(f"  联合批 {bidx + 1}/{total_batches} 第 {attempt + 1} 次失败: {e}")
            time.sleep(min(12 * (attempt + 1), 90))
    if obj is None:
        failed.append(bidx + 1)
        continue
    def _c(x, out):
        if isinstance(x, dict):
            out.append(x)
        elif isinstance(x, list):
            for y in x:
                _c(y, out)
    ents, ruls = [], []
    _c(obj.get('entities'), ents)
    _c(obj.get('rules'), ruls)
    for e in ents:
        nm = (e.get('name') or '').strip()
        if not nm:
            continue
        cc = canonicalize(nm)
        if cc in biz_by_canon:
            continue
        biz_by_canon[cc] = {'name': nm, 'category': (e.get('category') or '').strip(),
                            'definition': (e.get('definition') or '').strip(),
                            'description': (e.get('description') or '').strip()}
    for r_ in ruls:
        t = (r_.get('title') or '').strip()
        cat = (r_.get('category') or '').strip()
        if not t or cat not in RULE_CAT_SHORT:
            continue
        rule_entities.append({'title': t, 'category': cat, 'structure': r_.get('structure') or {},
                              'description': (r_.get('description') or '').strip(),
                              'evidence': (r_.get('evidence') or '').strip(),
                              'constraint': (r_.get('constraint') or '').strip(),
                              'objects': r_.get('objects') or []})
biz_entities = [{'name': v['name'], 'category': v['category'],
                 'definition': v['definition'], 'description': v['description']}
                for v in biz_by_canon.values()]
if failed:
    print(f"  ⚠️ 联合抽取 {len(failed)}/{total_batches} 批失败: {failed}（规则由 n12b 补抽兜底）")
with open(f"{DIR}/esm.json", 'w', encoding='utf-8') as f:
    json.dump({'biz': biz_entities, 'rules': rule_entities}, f, ensure_ascii=False, indent=2)
bindings['biz_count'] = len(biz_entities)
bindings['rule_count'] = len(rule_entities)
print(f"[n12] 联合抽取：业务实体 {len(biz_entities)} / 规则 {len(rule_entities)}")
'''

# ─────────────────────────────────────────────────────────────────────
# n12b: 规则为 0 时补抽（并入 n12 后置判断，若规则=0 单独补抽）
# ─────────────────────────────────────────────────────────────────────
CODE_RULES_BACKFILL = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
with open(f"{DIR}/ls_all.json", encoding='utf-8') as f:
    ls_all = json.load(f)
with open(f"{DIR}/esm.json", encoding='utf-8') as f:
    esm = json.load(f)
RULE_CAT_SHORT = {'定义规则': '1-定义规则', '条件规则': '2-条件规则', '约束规则': '3-约束规则',
                  '职责规则': '4-职责规则', '审批规则': '5-审批规则', '流程规则': '6-流程规则',
                  '引用规则': '7-引用规则', '例外规则': '8-例外规则', '处罚规则': '9-处罚规则',
                  '时限规则': '10-时限规则', '版本规则': '11-版本规则', '程序规则': '12-程序规则'}
RO_NUM_MAP = {'定义规则': 1, '条件规则': 2, '约束规则': 3, '职责规则': 4, '审批规则': 5,
              '流程规则': 6, '引用规则': 7, '例外规则': 8, '处罚规则': 9, '时限规则': 10,
              '版本规则': 11, '程序规则': 12}
rules = esm.get('rules') or []
if len(rules) > 0:
    print(f"[n12b] 规则实体 {len(rules)} 个，无需补抽")
else:
    RULE_PROMPT = ('你是市场监管法规解析专家。从以下长句中提取规则本体实体。\\n'
'规则本体共12类，每条规则一页：\\n'
'1. 定义规则：术语/概念定义  2. 条件规则：适用条件/前提/触发情形  3. 约束规则：禁止性/限制性规定  4. 职责规则：监管职责分工  5. 审批规则：许可/审批/备案事项及程序  6. 流程规则：流程步骤  7. 引用规则：引用其他法律法规/标准  8. 例外规则：例外/豁免/但书  9. 处罚规则：行政处罚规定  10. 时限规则：期限/期间规定  11. 版本规则：施行/修订/废止/替代  12. 程序规则：通用程序规定\\n'
'输出JSON：[{"title":"规则标题（≤20字概括）","category":"约束规则","structure":{"主体":"x","条件":"x","动作":"x"},"description":"规则具体内容","constraint":"约束对象业务实体名","evidence":"原文关键片段（逐字）","objects":[{"name":"其他业务对象名","role":"主体/条件/动作/客体"}]}]\\n'
'【硬性要求】title 用一句话概括（≤20字）；constraint 和 objects[].name 必须逐字来自原文；evidence 必须逐字来自长句原文；没有明确约束对象则 constraint 填空字符串""。')
added = 0
for batch_start in range(0, len(ls_all), 10):
    batch = ls_all[batch_start:batch_start + 10]
    ls_text = '\\n'.join(f"{i + 1}. {item['title']}: {item['text'][:250]}" for i, item in enumerate(batch))
    try:
        raw = wr.llm_call([{'role': 'system', 'content': '你是市场监管法规解析专家，只输出JSON。'},
                           {'role': 'user', 'content': RULE_PROMPT + '\\n\\n长句列表：\\n' + ls_text}],
                          max_tokens=8000)
        parsed = json.loads(wr.ensure_full_json(raw))
        norm_list = []
        def _collect(x):
            if isinstance(x, dict):
                norm_list.append(x)
            elif isinstance(x, list):
                for y in x:
                    _collect(y)
        _collect(parsed)
        for r_ in norm_list:
            t = (r_.get('title') or '').strip()
            cat = (r_.get('category') or '').strip()
            if not t or cat not in RULE_CAT_SHORT:
                continue
            rules.append({'title': t, 'category': cat, 'structure': r_.get('structure') or {},
                          'description': (r_.get('description') or '').strip(),
                          'evidence': (r_.get('evidence') or '').strip(),
                          'constraint': (r_.get('constraint') or '').strip(),
                          'objects': r_.get('objects') or []})
            added += 1
    except Exception as e:
        print(f"  规则补抽批 {batch_start//10 + 1} 失败: {e}")
esm['rules'] = rules
with open(f"{DIR}/esm.json", 'w', encoding='utf-8') as f:
    json.dump(esm, f, ensure_ascii=False, indent=2)
bindings['rule_count'] = len(rules)
print(f"[n12b] 规则补抽完成：+{added} → 共 {len(rules)} 条")
'''

# ─────────────────────────────────────────────────────────────────────
# n13: 规则实体页建页
# ─────────────────────────────────────────────────────────────────────
CODE_RULE_PAGES = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
with open(f"{DIR}/ls_all.json", encoding='utf-8') as f:
    ls_all = json.load(f)
with open(f"{DIR}/esm.json", encoding='utf-8') as f:
    esm = json.load(f)
with open(f"{DIR}/catids.json", encoding='utf-8') as f:
    cat_data = json.load(f)
RULE_CAT_SHORT = {'定义规则': '1-定义规则', '条件规则': '2-条件规则', '约束规则': '3-约束规则',
                  '职责规则': '4-职责规则', '审批规则': '5-审批规则', '流程规则': '6-流程规则',
                  '引用规则': '7-引用规则', '例外规则': '8-例外规则', '处罚规则': '9-处罚规则',
                  '时限规则': '10-时限规则', '版本规则': '11-版本规则', '程序规则': '12-程序规则'}
RO_NUM_MAP = {'定义规则': 1, '条件规则': 2, '约束规则': 3, '职责规则': 4, '审批规则': 5,
              '流程规则': 6, '引用规则': 7, '例外规则': 8, '处罚规则': 9, '时限规则': 10,
              '版本规则': 11, '程序规则': 12}
RULE_CAT_IDS = cat_data.get('rule', {})
KID, KB, SOURCE_FILE = _kid, wr.KB, _src or '（来源文件）'

def canonicalize(name):
    name = re.sub('[（(][^）)]*[）)]', '', name or '').strip()
    name = re.sub('\\\\s+', '', name)
    cn2ar = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}
    name = re.sub('([一二三四五六七八九十])$', lambda m: cn2ar.get(m.group(1), m.group(0)), name)
    return name

def rule_slug(ro_num, title, evidence=''):
    raw = (evidence or '').strip() if (evidence or '').strip() else (title or '').strip()
    h = hashlib.md5(canonicalize(raw).encode()).hexdigest()[:12]
    return f"entity-r-ro{ro_num:03d}-{h}"

def bookname(name):
    return name if name.startswith('《') and name.endswith('》') else f"《{name}》"

def find_ls_slugs(ls_all, entity_name, limit=3, text_only=True):
    hits, used = [], set()
    for item in ls_all:
        ok = entity_name in item.get('text', '')
        if text_only and not ok:
            ok = canonicalize(entity_name) in item.get('text', '')
        if ok:
            key = item['slug']
            if key not in used:
                hits.append((item['slug'], item['title']))
                used.add(key)
                if len(hits) >= limit:
                    return hits
    return hits

def render_ls_grouped(by_file):
    if not by_file:
        return ''
    parts = []
    for fname, links in by_file.items():
        if not links:
            continue
        rows = '\\n'.join(f"- [[{s}|{t}]]" for s, t in links)
        parts.append(f"**{bookname(fname)}**\\n{rows}")
    return '\\n\\n'.join(parts)

def build_rule_content(title, category, description, structure, constraint_link, by_file, source_file, objects=None, obj_links=None):
    struct_rows = []
    for k, v in (structure or {}).items():
        if v and str(v).strip():
            struct_rows.append(f"- {k}：{v}")
    struct_section = '\\n'.join(struct_rows) if struct_rows else '- （待补充）'
    if objects:
        cells = []
        for o in objects:
            name = (o.get('name') or '').strip() if isinstance(o, dict) else str(o or '').strip()
            role = (o.get('role') or '').strip() if isinstance(o, dict) else ''
            if not name:
                continue
            slug = (obj_links or {}).get(canonicalize(name), '')
            cell = f"[[{slug}|{name}]]" if slug else name
            cells.append(f"{cell}（{role}）" if role else cell)
        obj_row = '、'.join(cells) if cells else '-'
    else:
        obj_row = '-'
    ls_section = render_ls_grouped(by_file)
    ls_section = f"\\n## 原文关联\\n\\n{ls_section}" if ls_section else "\\n## 原文关联\\n\\n- （待补充）"
    return (f"# {title}\\n\\n**{category}**　{description}\\n\\n## 基本信息\\n\\n"
            f"| 字段 | 内容 |\\n|---|---|\\n| 规则类别 | {category} |\\n"
            f"| 约束对象 | {constraint_link} |\\n| 关联对象 | {obj_row} |\\n"
            f"| 来源制度 | {bookname(source_file)} |\\n\\n## 规则结构\\n\\n{struct_section}{ls_section}")

def create_page(KB, slug, title, content, folder_id, page_type, kid, source_file):
    r = wr.read_page(slug=slug, kb_id=KB)
    if 'error' not in r:
        return 'exists'
    r = wr.create_page(slug=slug, title=title, content=content, folder_id=folder_id,
                       page_type=page_type, kb_id=KB, source_refs=[f"{kid}|{source_file}"])
    if 'error' in r:
        err = str(r['error'])
        if '500' in err:
            r2 = wr.read_page(slug=slug, kb_id=KB)
            return 'exists' if 'error' not in r2 else 'error'
        return 'error'
    wr.update_page(slug=slug, kb_id=KB, title=title, content=content, folder_id=folder_id,
                   page_type=page_type, status='published', source_refs=[f"{kid}|{source_file}"])
    return 'created'

# 业务实体注册表（约束对象/关联对象链接用）
biz_reg = {}
page_no = 1
while True:
    r = wr.list_wiki_pages(kb_id=KB, page=page_no, page_size=200)
    ps = (r or {}).get('pages') or []
    if not ps:
        break
    for p in ps:
        if p.get('page_type') == 'business_ontology':
            biz_reg.setdefault(canonicalize(p.get('title', '')), []).append(p)
    if len(ps) < 200:
        break
    page_no += 1

rules = esm.get('rules') or []
created = skipped = errors = 0
entity_slug_map = {}
for ent in rules:
    title = (ent.get('title') or '').strip()
    category = (ent.get('category') or '').strip()
    structure = ent.get('structure', {}) or {}
    description = (ent.get('description') or '').strip()
    constraint = (ent.get('constraint') or '').strip()
    if not title or not category:
        continue
    cat_dir = RULE_CAT_SHORT.get(category)
    if not cat_dir:
        continue
    cat_id = RULE_CAT_IDS.get(cat_dir)
    if not cat_id:
        continue
    ro_num = RO_NUM_MAP.get(category, 0)
    evidence = (ent.get('evidence') or '').strip()
    slug = rule_slug(ro_num, title, evidence=evidence)
    constraint_link = constraint or '-'
    if constraint:
        for cand in biz_reg.get(canonicalize(constraint), []):
            constraint_link = f"[[{cand['slug']}|{constraint}]]"
            break
    ls_links = find_ls_slugs(ls_all, evidence, text_only=True) if evidence else []
    if not ls_links:
        ls_links = find_ls_slugs(ls_all, title, text_only=False)
    if not ls_links:
        entity_slug_map[title] = ''
        skipped += 1
        continue
    by_file = {SOURCE_FILE: ls_links}
    obj_links = {}
    for o in (ent.get('objects') or []):
        nm = (o.get('name') or '').strip() if isinstance(o, dict) else str(o or '').strip()
        if nm:
            for cand in biz_reg.get(canonicalize(nm), []):
                obj_links[canonicalize(nm)] = cand['slug']
                break
    content = build_rule_content(title, category, description, structure, constraint_link,
                                 by_file, SOURCE_FILE, objects=ent.get('objects') or [], obj_links=obj_links)
    st = create_page(KB, slug, title, content, cat_id, 'rule_ontology', KID, SOURCE_FILE)
    if st == 'created':
        created += 1
    elif st == 'exists':
        skipped += 1
    else:
        errors += 1
    entity_slug_map[title] = slug
bindings['rule_created'] = created
print(f"[n13] 规则页：created={created} skipped={skipped} errors={errors}")
'''

# ─────────────────────────────────────────────────────────────────────
# n14: 业务实体页建页（注册表合并 + 关键规则/关联实体）
# ─────────────────────────────────────────────────────────────────────
CODE_BIZ_PAGES = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
with open(f"{DIR}/ls_all.json", encoding='utf-8') as f:
    ls_all = json.load(f)
with open(f"{DIR}/esm.json", encoding='utf-8') as f:
    esm = json.load(f)
with open(f"{DIR}/catids.json", encoding='utf-8') as f:
    cat_data = json.load(f)
BIZ_CAT_SHORT = {'法律法规': '1-法律法规', '监管对象': '2-监管对象', '许可事项': '3-许可事项',
                 '违法行为': '4-违法行为', '行政处罚': '5-行政处罚', '监管职责': '6-监管职责',
                 '标准规范': '7-标准规范', '程序事项': '8-程序事项', '时限要求': '9-时限要求'}
RULE_CAT_SHORT = {'定义规则': '1-定义规则', '条件规则': '2-条件规则', '约束规则': '3-约束规则',
                  '职责规则': '4-职责规则', '审批规则': '5-审批规则', '流程规则': '6-流程规则',
                  '引用规则': '7-引用规则', '例外规则': '8-例外规则', '处罚规则': '9-处罚规则',
                  '时限规则': '10-时限规则', '版本规则': '11-版本规则', '程序规则': '12-程序规则'}
RO_NUM_MAP = {'定义规则': 1, '条件规则': 2, '约束规则': 3, '职责规则': 4, '审批规则': 5,
              '流程规则': 6, '引用规则': 7, '例外规则': 8, '处罚规则': 9, '时限规则': 10,
              '版本规则': 11, '程序规则': 12}
BIZ_CAT_IDS = cat_data.get('biz', {})
RULE_CAT_IDS = cat_data.get('rule', {})
KID, KB, SOURCE_FILE = _kid, wr.KB, _src or '（来源文件）'

def canonicalize(name):
    name = re.sub('[（(][^）)]*[）)]', '', name or '').strip()
    name = re.sub('\\\\s+', '', name)
    cn2ar = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}
    name = re.sub('([一二三四五六七八九十])$', lambda m: cn2ar.get(m.group(1), m.group(0)), name)
    return name

def entity_slug(cat, name):
    h = hashlib.md5(f'{cat}|{canonicalize(name)}'.encode()).hexdigest()[:10]
    return f'entity-b-{h}'

def rule_slug(ro_num, title, evidence=''):
    raw = (evidence or '').strip() if (evidence or '').strip() else (title or '').strip()
    h = hashlib.md5(canonicalize(raw).encode()).hexdigest()[:12]
    return f"entity-r-ro{ro_num:03d}-{h}"

def bookname(name):
    return name if name.startswith('《') and name.endswith('》') else f"《{name}》"

def find_ls_slugs(ls_all, entity_name, limit=3, text_only=True):
    hits, used = [], set()
    for item in ls_all:
        ok = entity_name in item.get('text', '')
        if text_only and not ok:
            ok = canonicalize(entity_name) in item.get('text', '')
        if ok:
            key = item['slug']
            if key not in used:
                hits.append((item['slug'], item['title']))
                used.add(key)
                if len(hits) >= limit:
                    return hits
    return hits

def render_ls_grouped(by_file):
    if not by_file:
        return ''
    parts = []
    for fname, links in by_file.items():
        if not links:
            continue
        rows = '\\n'.join(f"- [[{s}|{t}]]" for s, t in links)
        parts.append(f"**{bookname(fname)}**\\n{rows}")
    return '\\n\\n'.join(parts)

def build_biz_content(name, category, definition, description, by_file, key_rules, source_file, rels=None, name_to_slug=None):
    ls_section = render_ls_grouped(by_file)
    ls_section = f"\\n\\n## 原文关联\\n\\n{ls_section}" if ls_section else ''
    if key_rules:
        rules_rows = '\\n'.join(f"| [[{s}|{t}]] | {desc} | {bookname(src)} |" for s, t, desc, src in key_rules)
    else:
        rules_rows = f"| （待关联规则实体） | （待补充） | {bookname(source_file)} |"
    if rels:
        rows = []
        for rname, rtype, rdesc, rsrc in rels:
            rc = canonicalize(rname)
            slug = (name_to_slug or {}).get(rc, '')
            cell = f"[[{slug}|{rname}]]" if slug else rname
            rows.append(f"| {cell} | {rtype} | {rdesc} | {bookname(rsrc)} |")
        rel_rows = '\\n'.join(rows)
    else:
        rel_rows = f"| （待补充） | （待补充） | （待补充） | {bookname(source_file)} |"
    return (f"# {name}\\n\\n{definition or description}\\n\\n## 基本信息\\n\\n"
            f"| 字段 | 内容 |\\n|---|---|\\n| 所属本体 | 业务本体 > {category} > {name} |\\n"
            f"| 来源制度 | {bookname(source_file)} |\\n\\n## 关键规则\\n\\n"
            f"| 规则类型 | 内容 | 来源 |\\n|---|---|---|\\n{rules_rows}\\n\\n## 关联实体\\n\\n"
            f"| 关联实体 | 关系类型 | 关系说明 | 来源 |\\n|---|---|---|---|\\n{rel_rows}{ls_section}")

def create_page(KB, slug, title, content, folder_id, page_type, kid, source_file):
    r = wr.read_page(slug=slug, kb_id=KB)
    if 'error' not in r:
        return 'exists'
    r = wr.create_page(slug=slug, title=title, content=content, folder_id=folder_id,
                       page_type=page_type, kb_id=KB, source_refs=[f"{kid}|{source_file}"])
    if 'error' in r:
        err = str(r['error'])
        if '500' in err:
            r2 = wr.read_page(slug=slug, kb_id=KB)
            return 'exists' if 'error' not in r2 else 'error'
        return 'error'
    wr.update_page(slug=slug, kb_id=KB, title=title, content=content, folder_id=folder_id,
                   page_type=page_type, status='published', source_refs=[f"{kid}|{source_file}"])
    return 'created'

def append_ls_grouped(content, by_file):
    marker = '## 原文关联'
    if marker not in content:
        return content
    seg = content.split(marker, 1)[1]
    next_sec = seg.find('\\n## ')
    body = seg[:next_sec] if next_sec >= 0 else seg
    tail = seg[next_sec:] if next_sec >= 0 else ''
    for fname, links in by_file.items():
        if not links:
            continue
        gt = f"**{bookname(fname)}**"
        gi = body.find(gt)
        if gi >= 0:
            ge = body.find('\\n**', gi + len(gt))
            if ge < 0:
                ge = len(body)
            group = body[gi:ge]
            existing = set(re.findall('\\\\[\\\\[([^\\\\]|]+)', group))
            add = [f"- [[{s}|{t}]]" for s, t in links if s not in existing]
            if add:
                body = body[:ge] + '\\n' + '\\n'.join(add) + body[ge:]
        else:
            body = body.rstrip() + f"\\n\\n{gt}\\n" + '\\n'.join(f"- [[{s}|{t}]]" for s, t in links)
    return content.split(marker, 1)[0] + marker + body + tail

def append_key_rules(content, key_rules):
    if not key_rules:
        return content
    marker = '## 关键规则'
    if marker not in content:
        return content
    seg = content.split(marker, 1)[1]
    next_sec = seg.find('\\n## ')
    body = seg[:next_sec] if next_sec >= 0 else seg
    tail = seg[next_sec:] if next_sec >= 0 else ''
    lines = body.split('\\n')
    insert_at = None
    for i, ln in enumerate(lines):
        if '---' in ln and i + 1 < len(lines):
            insert_at = i + 1
    if insert_at is None:
        return content
    existing = set(re.findall('\\\\[\\\\[([^\\\\]|]+)', body))
    new_rows = [r for r in key_rules if r[0] not in existing]
    if not new_rows:
        return content
    rows = '\\n'.join(f"| [[{s}|{t}]] | {desc} | {bookname(src)} |" for s, t, desc, src in new_rows)
    lines.insert(insert_at, rows)
    new_body = '\\n'.join(lines)
    return content.split(marker, 1)[0] + marker + new_body + tail

def rewrite_source_line(content, refs):
    refs = list(dict.fromkeys(refs))
    src_text = '；'.join(f"《{r.split('|', 1)[-1]}》" if '|' in r else f"《{r}》" for r in refs)
    import re as _re
    return _re.sub(r'(\\|\\s*来源制度\\s*\\|\\s*).*(\\s*\\|)', lambda m: m.group(1) + src_text + m.group(2), content)

def merge_page(KB, p, new_folder_id, kid, source_file, by_file=None, key_rules=None):
    fids = list(p.get('folder_ids') or [p.get('folder_id')] or [])
    new_ref = f'{kid}|{source_file}'
    same_doc = new_ref in (p.get('source_refs') or [])
    if new_folder_id and new_folder_id not in fids and not same_doc:
        fids.append(new_folder_id)
    refs = list(p.get('source_refs') or [])
    if new_ref not in refs:
        refs.append(new_ref)
    content = p.get('content', '')
    if by_file:
        content = append_ls_grouped(content, by_file)
    if key_rules:
        content = append_key_rules(content, key_rules)
    content = rewrite_source_line(content, refs)
    wr.update_page(slug=p['slug'], kb_id=KB, title=p.get('title', ''), content=content,
                   folder_id=fids[0] if fids else '', page_type=p.get('page_type', ''),
                   status=p.get('status', 'published'), source_refs=refs)

# 注册表 + 规则驱动关联
biz_reg = {}
page_no = 1
while True:
    r = wr.list_wiki_pages(kb_id=KB, page=page_no, page_size=200)
    ps = (r or {}).get('pages') or []
    if not ps:
        break
    for p in ps:
        if p.get('page_type') == 'business_ontology':
            biz_reg.setdefault(canonicalize(p.get('title', '')), []).append(p)
    if len(ps) < 200:
        break
    page_no += 1

rules = esm.get('rules') or []
biz_entities = esm.get('biz') or []
# 规则对象程序兜底补建
biz_names = {canonicalize(e.get('name') or '') for e in biz_entities}
constraints = [e.get('constraint', '').strip() for e in rules if e.get('constraint', '').strip()]
rule_objects = []
for e in rules:
    for o in (e.get('objects') or []):
        name = (o.get('name') or '').strip() if isinstance(o, dict) else str(o or '').strip()
        if name:
            rule_objects.append(name)
for c in constraints + rule_objects:
    cc = canonicalize(c)
    if not cc or cc in biz_names:
        continue
    if not find_ls_slugs(ls_all, c, text_only=True):
        continue
    biz_entities.append({'name': c, 'category': '监管对象',
                         'definition': '规则对象（规则约束或涉及的业务对象，来自规则本体）', 'description': ''})
    biz_names.add(cc)

# 规则驱动关联实体
rels_map = {}
FWD_BY_ROLE = {'主体': {'职责规则': '管理职责', '审批规则': '审核职责', '流程规则': '执行职责'},
               '动作': {'*': '产出'}, '条件': {'*': '触发条件'}, '客体': {'*': '作用对象'}}
REV_BY_ROLE = {'主体': {'职责规则': '被管理', '审批规则': '被审核', '流程规则': '被执行'},
               '动作': {'*': '被产出'}, '条件': {'*': '被触发'}, '客体': {'*': '被作用'}}
for ent in rules:
    X = (ent.get('constraint') or '').strip()
    title = (ent.get('title') or '').strip()
    cat = (ent.get('category') or '').strip()
    desc = (ent.get('description') or '').strip()[:40]
    note = f'规则《{title}》规定：{desc}' if desc else f'规则《{title}》'
    if not X:
        continue
    for o in (ent.get('objects') or []):
        Y = (o.get('name') or '').strip() if isinstance(o, dict) else str(o or '').strip()
        role = (o.get('role') or '').strip() if isinstance(o, dict) else ''
        if not Y or canonicalize(Y) == canonicalize(X):
            continue
        fwd = FWD_BY_ROLE.get(role, {}).get(cat) or FWD_BY_ROLE.get(role, {}).get('*') or '规则绑定'
        rev = REV_BY_ROLE.get(role, {}).get(cat) or REV_BY_ROLE.get(role, {}).get('*') or '被约束'
        rels_map.setdefault(canonicalize(X), []).append((Y, fwd, note, SOURCE_FILE))
        rels_map.setdefault(canonicalize(Y), []).append((X, rev, note, SOURCE_FILE))
rels_map = {k: list({(i[0], i[1], i[2], i[3]): i for i in v}.values()) for k, v in rels_map.items()}

# 关键规则回填
rules_by_constraint = {}
for ent in rules:
    c = canonicalize((ent.get('constraint') or '').strip())
    if not c:
        continue
    t = (ent.get('title') or '').strip()
    cat = (ent.get('category') or '').strip()
    ev = (ent.get('evidence') or '').strip()
    if not t:
        continue
    rules_by_constraint.setdefault(c, []).append((rule_slug(RO_NUM_MAP.get(cat, 0), t, evidence=ev), t, (ent.get('description') or '')[:80]))

biz_cats = {canonicalize(e.get('name') or ''): (e.get('category') or '').strip() for e in biz_entities}
created = merged = skipped = errors = 0
for ent in biz_entities:
    name = (ent.get('name') or '').strip()
    category = (ent.get('category') or '').strip()
    definition = (ent.get('definition') or '').strip()
    description = (ent.get('description') or '').strip()
    if not name or not category:
        continue
    cat_dir = BIZ_CAT_SHORT.get(category)
    if not cat_dir:
        continue
    cat_id = BIZ_CAT_IDS.get(cat_dir)
    if not cat_id:
        continue
    slug = entity_slug(category, name)
    ls_links = find_ls_slugs(ls_all, name, text_only=True)
    if not ls_links:
        skipped += 1
        continue
    by_file = {SOURCE_FILE: ls_links}
    key_rules = [(s, t, desc, SOURCE_FILE) for s, t, desc in rules_by_constraint.get(canonicalize(name), [])]
    my_rels = rels_map.get(canonicalize(name), [])
    name_to_slug = {canonicalize(e.get('name') or ''): entity_slug(e.get('category') or '', e.get('name') or '')
                    for e in biz_entities if e.get('name') and e.get('category')}
    for cname, pages in biz_reg.items():
        if pages and cname not in name_to_slug:
            name_to_slug[cname] = pages[0]['slug']
    matches = biz_reg.get(canonicalize(name), [])
    if matches:
        p = matches[0]
        merge_page(KB, p, cat_id, KID, SOURCE_FILE, by_file=by_file, key_rules=key_rules)
        merged += 1
        continue
    content = build_biz_content(name, category, definition, description, by_file, key_rules,
                                SOURCE_FILE, rels=my_rels, name_to_slug=name_to_slug)
    st = create_page(KB, slug, name, content, cat_id, 'business_ontology', KID, SOURCE_FILE)
    if st == 'created':
        created += 1
        biz_reg.setdefault(canonicalize(name), []).append({'slug': slug, 'title': name, 'folder_id': cat_id,
                                                           'folder_ids': [cat_id], 'source_refs': [f"{KID}|{SOURCE_FILE}"],
                                                           'page_type': 'business_ontology', 'content': content})
    elif st == 'exists':
        skipped += 1
    else:
        errors += 1
bindings['biz_created'] = created
bindings['biz_merged'] = merged
print(f"[n14] 业务实体页：created={created} merged={merged} skipped={skipped} errors={errors}")
'''

# ─────────────────────────────────────────────────────────────────────
# n15: 图谱写入（Neo4j，平台无端点则跳过不中断）
# ─────────────────────────────────────────────────────────────────────
CODE_GRAPH = WR_HEAD + '''
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
try:
    import graph_export
    st = graph_export.export_doc(_kid, wr.KB, _src or None, quiet=True)
    print(f"[n15] 图谱写入: 实体页 {st.get('biz_pages',0)} 规则页 {st.get('rule_pages',0)} "
          f"→ 节点 {st.get('written_nodes',0)} 关系 {st.get('written_rels',0)}")
except Exception as e:
    print(f"[n15] ⚠️ Neo4j 图谱写入跳过/失败: {e}（wiki 构建不中断）")
'''

# ─────────────────────────────────────────────────────────────────────
# n16: 合并重复实体 + 链接重建（批次末，skip_final 时跳过）
# ─────────────────────────────────────────────────────────────────────
CODE_MERGE = WR_HEAD + '''
# 2026-10-17: 清理本文件根目录下的空目录（无页面且无子目录）
def _purge_empty_folders(_root):
    try:
        from api.db import get_sessionmaker as _gsm
        from sqlalchemy import text as _st2
        _d2 = _gsm()()
        try:
            _rows = _d2.execute(_st2("select id, parent_id from wiki_folder where kb_id = :k"), {"k": wr.KB}).all()
            _kids = {}
            _par = {}
            for _fid, _pid in _rows:
                _kids.setdefault(_pid or "", []).append(_fid)
                _par[_fid] = _pid or ""
            _sub, _depth = [], {}
            _stack = [(_root, 1)]
            while _stack:
                _cur, _dp = _stack.pop()
                for _c in _kids.get(_cur, []):
                    _sub.append(_c)
                    _depth[_c] = _dp
                    _stack.append((_c, _dp + 1))
            if not _sub:
                return 0
            _cnt = dict(_d2.execute(_st2("select folder_id, count(*) from wiki_page where kb_id = :k and folder_id is not null group by folder_id"), {"k": wr.KB}).all())
            _rm = 0
            for _fid in sorted(_sub, key=lambda x: -_depth.get(x, 0)):
                if _cnt.get(_fid, 0) == 0 and not _kids.get(_fid):
                    _d2.execute(_st2("delete from wiki_page_folder where folder_id = :i"), {"i": _fid})
                    _d2.execute(_st2("delete from wiki_folder where id = :i"), {"i": _fid})
                    _rm += 1
                    _p = _par.get(_fid)
                    if _p and _fid in _kids.get(_p, []):
                        _kids[_p].remove(_fid)
            _d2.commit()
            return _rm
        finally:
            _d2.close()
    except Exception as _e:
        print(f"[n16] 空目录清理跳过: {_e}")
        return 0
try:
    print(f"[n16] 空目录清理: 删除 {_purge_empty_folders(bindings.get('root_id') or '')} 个")
except Exception as _e2:
    print(f"[n16] 空目录清理异常: {_e2}")

if str(bindings.get('skip_final', 'false')).lower() == 'true':
    print("[n16] skip_final=true，跳过全库合并+链接重建（批次末统一执行）")
    raise SystemExit(0)
DIR = bindings.get('reg_dir', f"/tmp/reg_{_kid[:8]}")
try:
    import merge_duplicate_entities as mde
    import importlib
    mde.merge_entities(wr.KB, dry_run=False)
    print("[n16] 跨文档同名实体合并完成")
except Exception as e:
    print(f"[n16] 合并跳过/失败: {e}")
try:
    r = wr.rebuild_links(kb_id=wr.KB)
    print(f"[n16] 链接重建完成: {r}")
except Exception as e:
    print(f"[n16] 链接重建失败: {e}")
'''

# ─────────────────────────────────────────────────────────────────────
# n17: 汇总
# ─────────────────────────────────────────────────────────────────────
CODE_DONE = '''kid = bindings.get('kid', '')
doc = bindings.get('doc_name', '')
src = bindings.get('source_file', '')
summary = (
    f"监管制度文档转wiki完成: {doc or src}\\n"
    f"  chunks={bindings.get('chunk_count', 0)} "
    f"长句={bindings.get('ls_count', 0)} "
    f"关键词候选={bindings.get('term_count', 0)} (建页 created={bindings.get('kw_created', 0)} merged={bindings.get('kw_merged', 0)})\\n"
    f"  业务实体={bindings.get('biz_count', 0)} (页 created={bindings.get('biz_created', 0)} merged={bindings.get('biz_merged', 0)}) "
    f"规则={bindings.get('rule_count', 0)} (页 created={bindings.get('rule_created', 0)})"
)
print(summary)
bindings['build_summary'] = summary
'''


# ─────────────────────────────────────────────────────────────────────
# 节点/边装配
# ─────────────────────────────────────────────────────────────────────
def node(nid, inst, label, config, queue='build'):
    return {
        "id": nid,
        "data": {
            "label": label,
            "stepInst": inst,
            "config": {**(config or {}), "queue": queue},
        },
    }


def edge(eid, src, tgt):
    return {"id": eid, "source": src, "target": tgt}


def build_tape() -> dict:
    nodes = [
        # 输入定义
        node("n01", "def", "输入定义", {
            "assignments": json.dumps([
                {"variable": "kid", "expression": "bindings.get('kid', '')"},
                {"variable": "doc_name", "expression": "bindings.get('doc_name', '')"},
                {"variable": "source_file", "expression": "bindings.get('source_file', '')"},
                {"variable": "kb_id", "expression": "bindings.get('kb_id', '027f459712d1492aa789df1238eba642')"},
                {"variable": "skip_final", "expression": "bindings.get('skip_final', 'false')"},
            ], ensure_ascii=False),
        }),
        node("n02", "def", "派生变量", {
            "assignments": json.dumps([
                {"variable": "kid8", "expression": "bindings.get('kid', '')[:8]"},
            ], ensure_ascii=False),
        }),
        node("n03", "script", "初始化+切片拉取清洗", {"code": CODE_INIT_CHUNKS}),
        node("n04", "script", "切片门禁", {"code": CODE_GATE}),
        node("n05", "script", "建法规目录树", {"code": CODE_DIRS}),
        node("n06", "script", "摘要生成+建页", {"code": CODE_SUMMARY}),
        node("n07", "script", "长句抽取", {"code": CODE_LONGSENT}),
        node("n08", "wiki_publish", "长句建页", {
            "source": "{{reg_dir}}/ls_all.json",
            # 2026-10-17: 直接传本文档「长句原文」目录 ID（bindings.leaf）。
            # 此前只传目录名 → _resolve_folder 按名字取全库第一个同名目录，
            # 1586 个长句页全挤到最早那个目录，其余文档目录全空。
            "folder_id": "{{leaf['长句原文']}}",
            "page_type": "original_sentence",
            "slug_prefix": "longsentence",
            "slug_hash_field": "text",
            "title_field": "title",
            "content_field": "content",
            "source_ref": "{{kid}}|{{source_file}}",
            "env": {"WEKNORA_KB_ID": "{{kb_id}}", "KB_API_BASE_URL": "http://api-server:8000"},
        }),
        node("n09", "script", "高频词发现", {"code": CODE_TERMS}),
        node("n10", "llm_extract", "关键词LLM过滤", {
            "source": "{{reg_dir}}/terms.json",
            "source_key": "word",
            "prompt": FILTER_PROMPT,
            "output": "{{reg_dir}}/terms_filtered_decisions.json",
            "output_shape": "list",
            "batch_size": "15",
            "max_workers": "2",
            "max_tokens": "3000",
            "retries": "3",
            "dedupe_field": "word",
        }),
        node("n11", "script", "关键词过滤应用+建页", {"code": CODE_KW_PAGES}),
        node("n12", "script", "实体+规则联合抽取", {"code": CODE_JOINT}),
        node("n12b", "script", "规则补抽兜底", {"code": CODE_RULES_BACKFILL}),
        node("n13", "script", "规则实体页建页", {"code": CODE_RULE_PAGES}),
        node("n14", "script", "业务实体页建页", {"code": CODE_BIZ_PAGES}),
        node("n15", "script", "图谱写入", {"code": CODE_GRAPH}),
        node("n16", "script", "合并+链接重建", {"code": CODE_MERGE}),
        node("n17", "print", "完成汇总", {"text": "{{build_summary}}"}),
    ]
    edges = [
        edge("e01", "n01", "n02"),
        edge("e02", "n02", "n03"),
        edge("e03", "n03", "n04"),
        edge("e04", "n04", "n05"),
        edge("e05", "n05", "n06"),
        edge("e06", "n06", "n07"),
        edge("e07", "n07", "n08"),
        edge("e08", "n08", "n09"),
        edge("e09", "n09", "n10"),
        edge("e10", "n10", "n11"),
        edge("e11", "n11", "n12"),
        edge("e12", "n12", "n12b"),
        edge("e13", "n12b", "n13"),
        edge("e14", "n13", "n14"),
        edge("e15", "n14", "n15"),
        edge("e16", "n15", "n16"),
        edge("e17", "n16", "n17"),
    ]
    return {
        "tape_name": "wiki_build_监管制度_编排化",
        "tape_label": "监管制度文档转wiki（编排化全链路）",
        "tape_descr": "市场监督管理法规→wiki 全链路服务编排：目录→摘要→长句→关键词→实体/规则→图谱→合并→链接。业务逻辑全部在编排节点内实现，仅保留 weknora_rpc 基础工具调用（LLM/wiki读写/切片/分词）。输入：kid / doc_name / source_file / kb_id",
        "tape_type": "general",
        "nodes": nodes,
        "edges": edges,
        "exec_params": [
            {"paramName": "kid", "paramLabel": "文档 knowledge_id", "paramType": "input"},
            {"paramName": "doc_name", "paramLabel": "法规名", "paramType": "input"},
            {"paramName": "source_file", "paramLabel": "源文件名", "paramType": "input"},
            {"paramName": "kb_id", "paramLabel": "知识库ID", "paramType": "input", "defaultValue": "027f459712d1492aa789df1238eba642"},
            {"paramName": "skip_final", "paramLabel": "跳过批次末合并链接(true/false)", "paramType": "input", "defaultValue": "false"},
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/tape_regulation_wiki.json")
    args = ap.parse_args()
    tape = build_tape()
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(tape, f, ensure_ascii=False, indent=2)
    print(f"tape payload → {args.out}")
    print(f"nodes={len(tape['nodes'])} edges={len(tape['edges'])}")
    print(f"tape_name={tape['tape_name']}")


if __name__ == "__main__":
    main()
