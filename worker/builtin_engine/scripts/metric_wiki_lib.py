"""指标 wiki 页构建库——「指标WIKI构建」编排 seq4「批量建页」依赖（2026-10-12 补齐）。

build_page(meta, sql, folder_id, analysis) 返回与
wr.create_wiki_pages_batch 契约一致的页 dict：
{slug, title, content, page_type, folder_id, summary, source_refs}

页面模板：指标实体页（entity），含 指标编码/分类、业务口径、技术口径、
SQL 脚本（代码块）、SQL 分析（LLM 结果按 dict 递归渲染）。
"""
from __future__ import annotations

import re


def _clean_slug(s: str) -> str:
    """与平台 _flat_slug 语义相近：非法字符折叠为下划线，截断。"""
    s = re.sub(r"[\s/\\:#*?\"<>|]+", "_", s or "")
    return s.strip("._")[:120] or "page"


def _render_value(v, _depth: int = 0):
    """递归渲染 LLM 分析结果 dict/list/标量 → markdown 文本。"""
    if v is None:
        return ""
    if isinstance(v, dict):
        lines = []
        for k, val in v.items():
            sub = _render_value(val, _depth + 1)
            if sub:
                lines.append(f"- **{k}**：{sub}")
        return "\n".join(lines)
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if str(x).strip())
    return str(v)


def build_page(meta: dict, sql: str, folder_id: str, analysis: dict) -> dict:
    """构造一个指标 wiki 实体页。

    meta:     指标行（xlsx 解析 dict）：指标编码/指标名称/指标分类-I/II/业务口径/技术口径
    sql:      指标 SQL 脚本原文
    folder_id: 二级分类目录 id（seq2 门禁+建两级目录产出）
    analysis: seq3 LLM SQL 分析结果（dict，不定字段，递归渲染）
    """
    code = str(meta.get("指标编码") or "").strip()
    name = str(meta.get("指标名称") or "").strip()
    title = name or code
    slug = _clean_slug(f"{code} {name}" if name else code)
    c1 = str(meta.get("指标分类-I") or "未分类").strip()
    c2 = str(meta.get("指标分类-II") or "未分类").strip()

    lines = [f"# {title}"]
    lines.append(f"- **指标编码**：{code}")
    lines.append(f"- **指标分类**：{c1} / {c2}")

    biz = str(meta.get("业务口径") or "").strip()
    tech = str(meta.get("技术口径") or "").strip()
    if biz:
        lines.append("")
        lines.append("## 业务口径")
        lines.append(biz)
    if tech:
        lines.append("")
        lines.append("## 技术口径")
        lines.append(tech)

    sql_txt = (sql or "").strip()
    if sql_txt:
        lines.append("")
        lines.append("## SQL 脚本")
        lines.append("```sql")
        lines.append(sql_txt)
        lines.append("```")

    if analysis:
        body = _render_value(analysis)
        if body:
            lines.append("")
            lines.append("## SQL 分析")
            lines.append(body)

    return {
        "slug": slug,
        "title": title,
        "content": "\n".join(lines),
        "page_type": "entity",
        "folder_id": folder_id,
        "summary": f"{name or code}（{c1}/{c2}）指标",
        "source_refs": [],
    }
