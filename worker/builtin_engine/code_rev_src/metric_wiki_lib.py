#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量指标 wiki 构建 —— 纯函数库（无 argparse/无 main，可被编排 script 节点安全 import）。

被 metric_wiki_demo.py（CLI）与服务编排（wiki_build_指标wiki 的 n05 script 节点）
共用。含静态 SQL 分析、SQL LLM 分析 prompt、页面渲染。
"""
import json
import re

# ---------- SQL 静态分析（不依赖 LLM）----------
def static_sql_analysis(sql):
    """规则化提取 SQL 骨架：取数表/过滤条件/聚合/分组/口径转换/计算痕迹。"""
    s = sql or ""
    out = {}
    tables = re.findall(r"\bFROM\s+([A-Za-z_][\w]*\.[A-Za-z_][\w]*)", s, re.I)
    out["tables"] = list(dict.fromkeys(tables))
    conds = []
    wm = re.search(r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|$)", s, re.I | re.S)
    if wm:
        seg = wm.group(1)
        for part in re.split(r"\s+AND\s+|\s+OR\s+", seg):
            part = part.strip()
            if len(part) > 3:
                conds.append(part)
    out["where"] = conds
    gm = re.search(r"\bGROUP\s+BY\b(.*?)(?:\bORDER\b|$)", s, re.I | re.S)
    out["group_by"] = [g.strip() for g in gm.group(1).split(",")] if gm else []
    out["aggregates"] = list(dict.fromkeys(re.findall(r"\b(SUM|COUNT|MAX|MIN|AVG|COUNT\s+DISTINCT)\s*\(", s.upper())))
    out["params"] = list(dict.fromkeys(re.findall(r"\$\{(\w+)\}", s)))
    out["case_when"] = len(re.findall(r"\bCASE\s+WHEN\b", s.upper()))
    out["arith"] = list(dict.fromkeys(re.findall(r"[\w\)]+\s*(/|\\*)\s*\d+(?!\d*\.\d*\s*[:,)])", s)))[:6]
    return out


SQL_PROMPT = """你是一名数据仓库 SQL 专家。下面是一个电信经营分析指标的取数 SQL 脚本及其元信息。
请做严谨分析，输出 JSON（不要任何解释文字、不要 markdown 代码块），字段如下：

1. "calculation_logic": 该 SQL 的计算逻辑，用中文简明分步骤说明（每步一行，"1. xxx" 格式），要说清：取哪张表、怎么过滤、怎么聚合、最终字段怎么算出来的。
2. "filters": 关键过滤条件的业务含义，逐条中文解释（例如 STATIS_YM=调度月份参数 表示取调度传入的月份）。
3. "dimensions": 输出粒度/维度说明（GROUP BY 出来的维度分别是什么）。
4. "caveats": 口径风险与注意点（例如除以2的原因推测、排除10000汇总行、空值处理等），没有就给 []。
5. "plain_summary": 一句话（40字内）说明这个 SQL 算的是什么。

指标名称：{name}
指标编码：{code}
业务口径：{biz}
技术口径：{tech}
指标SQL：{expr}
SQL脚本：
{sql}
"""


def llm_sql_analysis(wr, meta, sql, no_llm=False):
    """LLM 结构化分析 SQL，返回 dict；失败返回 {}。wr 为 kb 版 weknora_rpc 模块。"""
    if no_llm or not sql:
        return {}
    prompt = SQL_PROMPT.format(
        name=meta.get("指标名称") or "", code=meta.get("指标编码") or "",
        biz=(meta.get("业务口径") or "")[:800], tech=(meta.get("技术口径") or "")[:400],
        expr=meta.get("指标SQL") or "",
        sql=sql.replace("{", "{{").replace("}", "}}"),
    )
    try:
        raw = wr.llm_call(
            [{"role": "system", "content": "你是数据仓库SQL分析专家，只输出JSON，不要思考过程。"},
             {"role": "user", "content": prompt}],
            max_tokens=3000,
        )
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0)) if m else {}

        def _fix(v):
            if isinstance(v, list):
                return [_fix(x) for x in v]
            if isinstance(v, str):
                return v.replace("${{", "${")
            return v
        return {k: _fix(v) for k, v in d.items()}
    except Exception:
        return {}


# ---------- 页面渲染 ----------
def esc(t):
    return (t or "").replace("|", "丨").replace("\n", " ").strip() or "—"


def _render_list_or_str(prefix, v):
    """把 str 或 list 统一渲染成 markdown 列表/段落，返回行 list。"""
    lines = []
    if isinstance(v, list):
        for item in v:
            lines.append(f"{prefix} {item}")
    elif v:
        lines.append(str(v))
    return lines


def build_page(meta, sql, folder_id, analysis):
    """渲染单个指标 wiki 页内容，返回 create_wiki_pages_batch 的 page dict。"""
    st = static_sql_analysis(sql)
    a = analysis or {}
    code = meta["指标编码"]
    name = meta["指标名称"]
    c1 = meta.get("指标分类-I") or "未分类"
    c2 = meta.get("指标分类-II") or "未分类"

    L = []
    L.append(f"# {name}\n")
    L.append(f"> 指标编码 `{code}` ｜ 分类：{c1} / {c2} ｜ 单位：{meta.get('单位') or '—'} ｜ 粒度：{meta.get('指标粒度') or '—'} ｜ 等级：{meta.get('指标等级') or '—'}\n")
    if a.get("plain_summary"):
        L.append(f"**一句话说明**：{a['plain_summary']}\n")

    L.append("## 一、指标基本信息\n")
    L.append("| 项 | 内容 |")
    L.append("|---|---|")
    for label, key in [
        ("指标编码", "指标编码"), ("指标名称", "指标名称"), ("应用系统展示名", "应用系统展示名"),
        ("指标类型", "指标类型"), ("指标粒度", "指标粒度"), ("单位", "单位"),
        ("指标用途", "指标用途"), ("需求类型", "需求类型"), ("指标等级", "指标等级"),
        ("指标下沉粒度", "指标下沉粒度"), ("指标开放度", "指标开放度"), ("指标及时性", "指标及时性"),
        ("所属分析主题", "所属分析主题"), ("指标分类", None), ("开发方", "开发方"),
        ("开发人", "开发人"), ("大数据负责人", "大数据负责人"), ("需求部门", "需求部门"),
    ]:
        if key is None:
            v = f"{c1} / {c2}"
        else:
            v = esc(meta.get(key))
        L.append(f"| {label} | {v} |")
    L.append("")

    L.append("## 二、口径定义\n")
    L.append("**业务口径**\n")
    L.append("```text\n" + (meta.get("业务口径") or "—") + "\n```\n")
    L.append("**技术口径**\n")
    L.append("```text\n" + (meta.get("技术口径") or "—") + "\n```\n")
    L.append(f"**指标说明**：{meta.get('指标说明') or '—'}\n")

    L.append("## 三、数据来源\n")
    L.append("| 项 | 内容 |")
    L.append("|---|---|")
    L.append(f"| 归属应用报表名 | {esc(meta.get('归属应用报表名'))} |")
    L.append(f"| 指标所在底层表名 | `{meta.get('指标所在底层表名') or '—'}` |")
    L.append(f"| 底层数据字段名 | `{meta.get('底层数据字段名') or '—'}` |")
    L.append(f"| 指标SQL（计算表达式） | `{meta.get('指标SQL') or '—'}` |")
    L.append("")

    L.append("## 四、SQL 脚本分析\n")
    L.append("### 4.1 脚本原文\n")
    L.append("```sql\n" + (sql or "—") + "\n```\n")

    L.append("### 4.2 计算逻辑\n")
    if a.get("calculation_logic"):
        L.extend(_render_list_or_str("-", a["calculation_logic"]))
        L.append("")
    else:
        L.append("```text\n未启用 LLM 分析\n```\n")

    L.append("### 4.3 关键过滤条件\n")
    fl = a.get("filters") or st["where"]
    if fl:
        L.extend(_render_list_or_str("-", fl))
        L.append("")

    L.append("### 4.4 维度与聚合\n")
    if a.get("dimensions"):
        L.extend(_render_list_or_str("-", a["dimensions"]))
        L.append("")
    if st["group_by"]:
        L.append("- GROUP BY：" + ", ".join(f"`{g}`" for g in st["group_by"]))
    if st["aggregates"]:
        L.append("- 聚合函数：" + ", ".join(st["aggregates"]))
    if st["tables"]:
        L.append("- 取数表：" + ", ".join(f"`{t}`" for t in st["tables"]))
    L.append("")

    L.append("### 4.5 口径风险与注意点\n")
    if a.get("caveats"):
        L.extend(_render_list_or_str("-", a["caveats"]))
        L.append("")
    else:
        L.append("- —\n")

    content = "\n".join(L)
    return {
        "slug": f"{_slug(c1)}-{_slug(c2)}-indicator-{code}",
        "title": f"{name}（{code}）",
        "content": content,
        "page_type": "original_sentence",
        "folder_id": folder_id,
        "source_refs": [{"type": "metric_row", "file": "全量指标20260814.xlsx", "row_key": code}],
    }


def _slug(name):
    return re.sub(r"[^0-9A-Za-z一-鿿]+", "-", name or "").strip("-").lower()