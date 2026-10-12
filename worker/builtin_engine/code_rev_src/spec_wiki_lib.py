#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规范分册 wiki 构建器（宿主运行）：spec_*.json → 页面 → 落库到「规范/」根下。

页面模型（内容领域划分）：
  规范/
  ├─ 数据模型/          ← 逻辑模型分册（基础数据模型 8 主题域 / 汇总数据模型 10 类 / 信息子层+统一视图 / 建模方法论）
  ├─ 外部接口/          ← 外部接口分册（总体说明 / 数据源接口 / 互动接口服务与XML报文 / 功能接口 / 附录A 单元明细）
  ├─ 版本修订/          ← 逻辑模型分册 42 张修订表
  └─ 术语/              ← 两分册的术语/缩略语
每页骨架：基本信息 + 结构化表格 + 出处（文档名+章节号）+ LLM 解读占位。
wr 用 wr_http 替身（宿主 cookie 直连）。用法：python3 -u build_spec_wiki.py
"""
import json
import os
import re
import sys
import time

HERE = "/home/ctyun/metric_build"
SKILL = "/home/ctyun/.hermes/skills/devops/dim-table-wiki-compiler/scripts"
for _p in (HERE, SKILL):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import weknora_rpc as wr  # worker 内真实模块
except Exception:  # noqa: BLE001 宿主环境 → wr_http 替身
    import wr_http  # noqa: E402
    sys.modules["weknora_rpc"] = wr_http
    wr = wr_http

KB = "4bfeebbca01a47278421cfe84b9b9b21"
SPEC_ROOT = "072314b093d3421293a85acc0eccdc50"  # 规范/ 根
LM_DOC = "d345f8caa39b4a9498bceec1d781af49"
IF_DOC = "32ef1cc3609e45b4931d6ca7cb2a47a5"
LM_FILE = "中国移动省级NG2-BASS(v5.0)技术规范逻辑模型分册.docx"
IF_FILE = "中国移动省级NG2-BASS(v5.0)技术规范外部接口分册.docx"

# ── 目录（幂等）──
_folder_cache = {}


def folder(name, parent=""):
    key = (name, parent)
    if key in _folder_cache:
        return _folder_cache[key]
    existing = wr.tool_call("list_wiki_folders", {"kb_id": KB}) or {}
    ext = existing.get("folders") or (existing.get("data") or {}).get("folders") or existing.get("data") or []
    ext = [f for f in ext if isinstance(f, dict)]
    hit = next((f for f in ext if f.get("name") == name and (f.get("parent_id") or "") == parent), None)
    if hit:
        _folder_cache[key] = hit["id"]
        return hit["id"]
    r = wr.tool_call("create_wiki_folder", {"kb_id": KB, "name": name, "parent_id": parent})
    d = r.get("data") or r
    fid = d.get("id") if isinstance(d, dict) else None
    _folder_cache[key] = fid
    return fid


def slugify(s):
    return re.sub(r"[^0-9A-Za-z一-鿿]+", "-", s or "").strip("-").lower()


def md_table(header, rows):
    L = ["| " + " | ".join(str(h) for h in header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for r in rows:
        L.append("| " + " | ".join(str(c).replace("|", "丨").replace("\n", " ") for c in r) + " |")
    return "\n".join(L)


def esc(v):
    return "—" if v is None or str(v).strip() == "" else str(v)


def sec_path(sec, parents):
    return ".".join(p["number"] for p in parents if p.get("number")) + ("." + sec["number"] if sec.get("number") else "")


def find_sections(secs, predicate, out=None):
    out = out if out is not None else []
    for s in secs:
        if predicate(s):
            out.append(s)
        find_sections(s.get("children", []), predicate, out)
    return out


def walk_all(secs, parents=None, out=None):
    parents = parents or []
    out = out if out is not None else []
    for s in secs:
        out.append((s, parents))
        walk_all(s.get("children", []), parents + [s], out)
    return out


# ── 页面渲染 ──
PAGES = []


def add(slug, title, page_type, content, folder_id, src_file, src_doc, section=""):
    PAGES.append({
        "slug": slug, "title": title, "page_type": page_type, "content": content,
        "folder_id": folder_id,
        "source_refs": [{"type": "spec_section", "file": src_file, "doc": src_doc, "section": section}],
    })


def render_overview(title, paras, tables, children_links, src_file, src_doc, section):
    L = [f"# {title}\n"]
    for t in tables:
        if t["rows"]:
            L.append(f"**{t['type']}**\n")
            L.append(md_table(t["header"], t["rows"]))
            L.append("")
    if paras:
        L.append("## 原文要点\n")
        for p in paras[:25]:
            L.append(f"- {p}")
        L.append("")
    if children_links:
        L.append("## 关联\n")
        for name, slug in children_links:
            L.append(f"- [[{slug}|{name}]]")
        L.append("")
    L.append("## 数据含义分析\n\n*由 LLM 生成*\n")
    return "\n".join(L)


def render_entity(title, code, meta_rows, attrs, paras, src_file, src_doc, section, kind="实体"):
    L = [f"# {title}\n"]
    L.append(f"> {kind}编码 `{esc(code)}` ｜ 出处：{src_file} 第 {section} 节\n")
    if meta_rows:
        L.append("## 基本信息\n")
        L.append(md_table(["项", "内容"], meta_rows))
        L.append("")
    if attrs:
        L.append(f"## 属性清单\n")
        L.append(md_table(attrs[0], attrs[1:]))
        L.append("")
    if paras:
        L.append("## 原文说明\n")
        for p in paras[:12]:
            L.append(f"- {p}")
        L.append("")
    L.append("## 数据含义分析\n\n*由 LLM 生成*\n")
    return "\n".join(L)


def render_rulepage(title, paras, tables, src_file, src_doc, section):
    L = [f"# {title}\n"]
    L.append(f"> 出处：{src_file} 第 {section} 节\n")
    for t in tables:
        if t["rows"]:
            L.append(md_table(t["header"], t["rows"]))
            L.append("")
    for p in paras:
        L.append(p)
        L.append("")
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════
# 一、逻辑模型分册 → 数据模型/
# ═══════════════════════════════════════════════════════════
def build_lm(spec):
    root_dm = folder("数据模型", SPEC_ROOT)
    allsec = walk_all(spec["sections"])

    def children(s):
        return s.get("children", [])

    # 1. 总览
    lm_sections = [s for s, _ in allsec if s["title"].startswith("数据仓库基础数据逻辑模型") or
                   s["title"].startswith("数据仓库汇总数据逻辑模型") or
                   s["title"].startswith("数据仓库信息子层逻辑模型")]
    total_entity = len(spec["records"]["ENTITY_DEF"]) + len(spec["records"]["ENTITY_ATTR"])
    ov = [f"# 数据模型总览（NG2-BASS v5.0 逻辑模型分册）\n",
          f"> 出处：{LM_FILE} ｜ 分册版本 1.0.0 ｜ 实体定义 {len(spec['records']['ENTITY_DEF'])} 条、属性表 {len(spec['records']['ENTITY_ATTR'])} 条、关系 {len(spec['records']['RELATION'])} 条、参照表 {len(spec['records']['REF_TABLE'])} 条、修订 {len(spec['records']['REVISION'])} 条\n",
          "## 三层结构\n",
          "- **基础数据模型**：8 个主题域（参与人/服务/资源/事件/帐务/营销/财务）——主题域 → 子类 → 实体/关系/参照表\n",
          "- **汇总数据模型**：10 类（清单/信令/帐务/个人用户/集团客户/他网用户/转售商/产品信息/渠道/终端）标准集+扩展集\n",
          "- **信息子层**：KPI、报表、13 类统一视图\n",
          "## 关联\n"]
    for nm, sl in [("基础数据模型", "数据模型-基础数据模型"), ("汇总数据模型", "数据模型-汇总数据模型"),
                   ("信息子层", "数据模型-信息子层"), ("建模方法论", "数据模型-建模方法论")]:
        ov.append(f"- [[{sl}|{nm}]]")
    ov.append("")
    ov.append("## 数据含义分析\n\n*由 LLM 生成*\n")
    add("数据模型-总览", "数据模型总览（NG2-BASS v5.0）", "summary", "\n".join(ov), root_dm, LM_FILE, LM_DOC, "目录")

    # 2. 基础数据模型 / 8 主题域
    base_f = folder("基础数据模型", root_dm)
    add("数据模型-基础数据模型", "基础数据模型总览", "summary",
        "# 基础数据模型总览\n\n## 主题域\n\n- 参与人主题域 / 服务主题域 / 资源主题域 / 事件主题域 / 帐务主题域 / 营销主题域 / 财务主题域\n",
        base_f, LM_FILE, LM_DOC, "第5章")
    base_sec = next((s for s, _ in allsec if s["title"].startswith("数据仓库基础数据逻辑模型")), None)
    if base_sec:
        for dom_sec in children(base_sec):
            t = dom_sec["title"]
            if not t.endswith("主题域"):
                continue
            dom_f = folder(t, base_f)
            # 域内收集实体/关系/参照表（来自子章节的 ENTITY_DEF / RELATION / REF_TABLE 表）
            entities, relations, reftables = [], [], []
            dom_all = walk_all(children(dom_sec))
            for s, pr in dom_all:
                for tb in s.get("tables", []):
                    if tb["type"] == "ENTITY_DEF":
                        for r in tb["rows"]:
                            entities.append((r, s["title"], sec_path(s, pr)))
                    elif tb["type"] == "RELATION":
                        relations.append((tb, s["title"], sec_path(s, pr)))
                    elif tb["type"] == "REF_TABLE":
                        reftables.append((tb, s["title"], sec_path(s, pr)))
            overview_sec = next((s for s in children(dom_sec) if s["title"] == "概述"), None)
            key_sec = next((s for s in children(dom_sec) if s["title"] == "关键问题说明"), None)
            links = []
            # 实体页（每行一页，slug 带实体编码防重名）
            for row, sub, secno in entities:
                r = (row + [""] * 6)[:6]
                code, name, attrs_, desc = r[0], r[1], r[2], r[3]
                if not name:
                    continue
                eslug = f"数据模型-{slugify(t[:4])}-实体-{slugify(code or 'x')}-{slugify(name[:16])}"
                c = render_entity(f"实体-{name}", code, [["实体编码", code], ["实体名称", name],
                                                          ["所属主题域", t], ["子类", sub]],
                                  None, [d for d in [desc] if d], LM_FILE, LM_DOC, secno)
                add(eslug, f"实体-{name}", "entity", c, dom_f, LM_FILE, LM_DOC, secno)
            # 关系页（每表一页，slug 带子类名）
            for tb, sub, secno in relations:
                rslug = f"数据模型-{slugify(t[:4])}-关系-{slugify(sub or 'none')}"
                c = render_entity(f"实体关系-{sub}", "", [["关系类型", sub], ["所属主题域", t]],
                                  [tb["header"], *tb["rows"]], [], LM_FILE, LM_DOC, secno)
                add(rslug, f"实体关系-{sub}", "entity", c, dom_f, LM_FILE, LM_DOC, secno)
            # 参照表页（每表一页，slug 带子类名）
            for tb, sub, secno in reftables:
                fslug = f"数据模型-{slugify(t[:4])}-参照表-{slugify(sub or 'none')}"
                c = render_entity(f"参照表-{sub}", "", [["参照表", sub], ["所属主题域", t]],
                                  [tb["header"], *tb["rows"]], [], LM_FILE, LM_DOC, secno)
                add(fslug, f"参照表-{sub}", "concept", c, dom_f, LM_FILE, LM_DOC, secno)
            # 主题域总览（最后渲染，链接实体页）
            ovl = [f"# {t}总览\n"]
            if overview_sec:
                ovl.append("## 概述\n")
                ovl.extend(f"- {p}" for p in overview_sec.get("paras", []))
                ovl.append("")
            ovl.append("## 实体\n")
            for row, sub, secno in entities:
                code, name = (row + [""] * 2)[:2]
                if name:
                    ovl.append(f"- [[数据模型-{slugify(t[:4])}-实体-{slugify(code or 'x')}-{slugify(name[:16])}|{name}]]（{sub}）")
            ovl.append("## 实体关系与参照表\n")
            for tb, sub, secno in relations:
                ovl.append(f"- 实体关系：{sub}（{secno}）")
            for tb, sub, secno in reftables:
                ovl.append(f"- 参照表：{sub}（{secno}）")
            if key_sec:
                ovl.append("## 关键问题说明\n")
                ovl.extend(f"- {p}" for p in key_sec.get("paras", []))
                ovl.append("")
            ovl.append("## 数据含义分析\n\n*由 LLM 生成*\n")
            add(f"数据模型-{slugify(t[:4])}-总览", f"{t}总览", "summary", "\n".join(ovl),
                dom_f, LM_FILE, LM_DOC, sec_path(dom_sec, [base_sec]))

    # 3. 汇总数据模型 / 10 类
    sum_f = folder("汇总数据模型", root_dm)
    add("数据模型-汇总数据模型", "汇总数据模型总览", "summary",
        "# 汇总数据模型总览\n\n## 汇总类别\n\n- 清单汇总 / 信令汇总 / 帐务汇总 / 个人用户汇总 / 集团客户汇总 / 他网用户汇总 / 转售商用户汇总 / 产品信息汇总 / 渠道汇总 / 终端信息汇总\n",
        sum_f, LM_FILE, LM_DOC, "第6章")
    sum_sec = next((s for s, _ in allsec if s["title"].startswith("数据仓库汇总数据逻辑模型")), None)
    if sum_sec:
        for cls_sec in children(sum_sec):
            t = cls_sec["title"]
            if not any(k in t for k in ("汇总",)):
                continue
            cls_f = folder(t, sum_f)
            sub_all = walk_all(children(cls_sec))
            entity_sections = [(s, pr) for s, pr in sub_all if s.get("tables") and
                               any(tb["type"] == "ENTITY_ATTR" for tb in s["tables"]) and s["level"] >= 5]
            # 汇总类总览（含实体导航）
            ovl = [f"# {t}总览\n"]
            ovl.append("## 实体\n")
            for s, pr in entity_sections:
                ovl.append(f"- [[数据模型-{slugify(t[:4])}-汇总实体-{slugify(s['title'][:20])}|{s['title']}]]")
            ovl.append("\n## 数据含义分析\n\n*由 LLM 生成*\n")
            add(f"数据模型-{slugify(t[:4])}-汇总总览", f"{t}总览", "summary", "\n".join(ovl), cls_f, LM_FILE, LM_DOC, "")
            for s, pr in entity_sections:
                attr_tb = next((tb for tb in s["tables"] if tb["type"] == "ENTITY_ATTR"), None)
                enum_tbs = [tb for tb in s["tables"] if tb["type"] == "ENUM"]
                secno = sec_path(s, pr)
                if attr_tb:
                    c = render_entity(s["title"], "", [["实体名称", s["title"]], ["所属汇总类", t], ["出处", secno]],
                                      [attr_tb["header"], *attr_tb["rows"]], s.get("paras", []), LM_FILE, LM_DOC, secno)
                    add(f"数据模型-{slugify(t[:4])}-汇总实体-{slugify(s['title'][:20])}", s["title"], "concept",
                        c, cls_f, LM_FILE, LM_DOC, secno)
                for et in enum_tbs:
                    en = et["header"][0] if et["header"] else "枚举"
                    c = render_entity(f"枚举-{s['title']}", "", [["枚举名称", s["title"]], ["所属汇总类", t]],
                                      [et["header"], *et["rows"]], [], LM_FILE, LM_DOC, secno)
                    add(f"数据模型-{slugify(t[:4])}-枚举-{slugify(s['title'][:16])}", f"枚举-{s['title']}", "concept",
                        c, cls_f, LM_FILE, LM_DOC, secno)
            # 扩展集 ENTITY_DEF（主要实体说明）→ 每张表一页
            for s, pr in sub_all:
                def_tb = next((tb for tb in s.get("tables", []) if tb["type"] == "ENTITY_DEF"), None)
                if def_tb and s["level"] >= 4:
                    secno = sec_path(s, pr)
                    c = render_entity(f"扩展实体-{s['title']}", "", [["来源", s["title"]], ["所属汇总类", t]],
                                      [def_tb["header"], *def_tb["rows"]], [], LM_FILE, LM_DOC, secno)
                    add(f"数据模型-{slugify(t[:4])}-扩展实体-{slugify(s['title'][:16])}",
                        f"扩展实体-{s['title']}", "concept", c, cls_f, LM_FILE, LM_DOC, secno)
    # 4. 信息子层（统一视图）
    info_f = folder("信息子层", root_dm)
    add("数据模型-信息子层", "信息子层总览", "summary",
        "# 信息子层总览\n\n## 视图\n\n- KPI / 报表 / 个人客户统一视图 / 集团客户统一视图 / 家庭客户统一视图 / 他网客户统一视图 / 转售商用户统一视图 / 终端信息统一视图 / 产品信息统一视图 / 校园客户统一视图 / 垃圾短信客户统一视图 / 渠道信息统一视图 / 网络信息统一视图 / 营销资源统一视图\n",
        info_f, LM_FILE, LM_DOC, "第7章")
    info_sec = next((s for s, _ in allsec if s["title"].startswith("数据仓库信息子层逻辑模型")), None)
    if info_sec:
        for v_sec in children(info_sec):
            t = v_sec["title"]
            if t in ("主题域说明",):
                continue
            vf = folder(t, info_f)
            sub_all = walk_all(children(v_sec))
            entity_sections = [(s, pr) for s, pr in sub_all if s.get("tables") and
                               any(tb["type"] == "ENTITY_ATTR" for tb in s["tables"])]
            ovl = [f"# {t}总览\n## 实体\n"]
            for s, pr in entity_sections:
                ovl.append(f"- [[数据模型-视图-{slugify(s['title'][:20])}|{s['title']}]]")
            ovl.append("\n## 数据含义分析\n\n*由 LLM 生成*\n")
            add(f"数据模型-视图-{slugify(t[:10])}-总览", f"{t}总览", "summary", "\n".join(ovl), vf, LM_FILE, LM_DOC, "")
            for s, pr in entity_sections:
                attr_tb = next((tb for tb in s["tables"] if tb["type"] == "ENTITY_ATTR"), None)
                secno = sec_path(s, pr)
                if attr_tb:
                    c = render_entity(s["title"], "", [["实体名称", s["title"]], ["所属视图", t]],
                                      [attr_tb["header"], *attr_tb["rows"]], s.get("paras", []), LM_FILE, LM_DOC, secno)
                    add(f"数据模型-视图-{slugify(s['title'][:20])}", s["title"], "concept", c, vf, LM_FILE, LM_DOC, secno)
    # 5. 建模方法论（条文页）
    meth_sec = next((s for s, _ in allsec if s["title"].startswith("建模方法论")), None)
    if meth_sec:
        mf = folder("建模方法论", root_dm)
        add("数据模型-建模方法论", "建模方法论总览", "summary",
            "# 建模方法论总览\n\n## 章节\n\n- 第三范式 / 实体-关系模型 / 建模的三个阶段 / 逻辑模型与业务需求的关系\n",
            mf, LM_FILE, LM_DOC, "建模方法论")
        for s in children(meth_sec):
            c = render_rulepage(s["title"], s.get("paras", []), s.get("tables", []), LM_FILE, LM_DOC, s["title"])
            add(f"数据模型-方法论-{slugify(s['title'][:12])}", s["title"], "concept", c, mf, LM_FILE, LM_DOC, s["title"])


def children(s):
    return s.get("children", [])


# ═══════════════════════════════════════════════════════════
# 二、外部接口分册 → 外部接口/
# ═══════════════════════════════════════════════════════════
def build_if(spec):
    root_if = folder("外部接口", SPEC_ROOT)
    allsec = walk_all(spec["sections"])
    total_units = len(spec["records"]["IF_UNIT"])
    ov = [f"# 外部接口总览（NG2-BASS v5.0 外部接口分册）\n",
          f"> 出处：{IF_FILE} ｜ 分册版本 1.0.0 ｜ 接口单元 {total_units} 个、XML 报文表 {len(spec['records']['IF_XML']) // 10} 组、枚举/错误码若干\n",
          "## 接口域\n",
          "- [[外部接口-总体说明|总体说明]] / [[外部接口-数据源接口|数据源接口]] / [[外部接口-互动接口|互动接口]] / [[外部接口-功能接口|功能接口]]\n",
          "## 数据含义分析\n\n*由 LLM 生成*\n"]
    add("外部接口-总览", "外部接口总览（NG2-BASS v5.0）", "summary", "\n".join(ov), root_if, IF_FILE, IF_DOC, "目录")

    # 总体说明
    ov_f = folder("总体说明", root_if)
    add("外部接口-总体说明", "总体说明总览", "summary",
        "# 总体说明总览\n\n- 总体目标 / 建设原则 / 接口数据流 / 接口内容\n",
        ov_f, IF_FILE, IF_DOC, "第4章")
    ov_sec = next((s for s, _ in allsec if s["title"] == "总体说明"), None)
    if ov_sec:
        for s in children_of(ov_sec):
            c = render_rulepage(s["title"], s.get("paras", []), s.get("tables", []), IF_FILE, IF_DOC, s["title"])
            add(f"外部接口-总体说明-{slugify(s['title'][:10])}", s["title"], "concept", c, ov_f, IF_FILE, IF_DOC, s["title"])

    # 数据源接口
    ds_f = folder("数据源接口", root_if)
    ds_sec = next((s for s, _ in allsec if s["title"] == "数据源接口"), None)
    if ds_sec:
        cat_tb = next((tb for s in children(ds_sec) for tb in s.get("tables", []) if tb["type"] == "IF_CATEGORY"), None)
        ovl = ["# 数据源接口总览\n"]
        if cat_tb:
            ovl.append("## 接口分类\n")
            ovl.append(md_table(cat_tb["header"], cat_tb["rows"]))
            ovl.append("")
        ovl.append("## 实现方式\n")
        for s in children(ds_sec):
            if s["title"] == "接口实现":
                for c in children(s):
                    ovl.append(f"- {c['title']}")
        ovl.append("## 数据含义分析\n\n*由 LLM 生成*\n")
        add("外部接口-数据源接口", "数据源接口总览", "summary", "\n".join(ovl), ds_f, IF_FILE, IF_DOC, "第5章")
        impl_sec = next((s for s in children(ds_sec) if s["title"] == "接口实现"), None)
        if impl_sec:
            impl_f = folder("接口实现", ds_f)
            for cs in children(impl_sec):
                content = render_rulepage(cs["title"], cs.get("paras", []), cs.get("tables", []),
                                          IF_FILE, IF_DOC, cs["title"])
                add(f"外部接口-数据源-{slugify(cs['title'][:10])}", cs["title"], "concept",
                    content, impl_f, IF_FILE, IF_DOC, cs["title"])
    # 附录A 接口单元明细
    apx_sec = next((s for s, _ in allsec if s["title"].startswith("附录A")), None)
    if apx_sec:
        unit_tb = next((tb for s in walk_secs(apx_sec) for tb in s.get("tables", []) if tb["type"] == "IF_UNIT"), None)
        if unit_tb:
            c = render_entity("数据源接口单元明细", "", [["接口单元数", len(unit_tb["rows"])]],
                              [unit_tb["header"], *unit_tb["rows"]], [], IF_FILE, IF_DOC, "附录A")
            add("外部接口-数据源接口单元明细", "数据源接口单元明细", "concept", c, ds_f, IF_FILE, IF_DOC, "附录A")

    # 互动接口
    int_f = folder("互动接口", root_if)
    int_sec = next((s for s, _ in allsec if s["title"] == "互动接口"), None)
    if int_sec:
        add("外部接口-互动接口", "互动接口总览", "summary",
            "# 互动接口总览\n\n- 营销服务互动服务 / 分析结果查询服务 / 获取客户统一视图服务\n",
            int_f, IF_FILE, IF_DOC, "第6章")
        for svc in children(int_sec):
            st = svc["title"]
            if not st.endswith("服务"):
                continue
            svc_f = folder(st, int_f)
            sub_all = walk_all(children(svc))
            # 方法区（带 IF_XML 请求/应答表的次级节）
            ovl = [f"# {st}总览\n## 方法\n"]
            for s, pr in sub_all:
                has_xml = any(tb["type"] == "IF_XML" for tb in s.get("tables", []))
                if has_xml:
                    m = pr[-1]["title"] if pr else s["title"]
                    ovl.append(f"- [[外部接口-方法-{slugify(m[:16])}-{slugify(s['title'][:12])}|{s['title']}（{m}）]]")
            ovl.append("\n## 数据含义分析\n\n*由 LLM 生成*\n")
            add(f"外部接口-{slugify(st[:8])}-总览", f"{st}总览", "summary", "\n".join(ovl), svc_f, IF_FILE, IF_DOC, st)
            for s, pr in sub_all:
                xml_tbs = [tb for tb in s.get("tables", []) if tb["type"] == "IF_XML"]
                if not xml_tbs:
                    continue
                method = pr[-1]["title"] if pr else s["title"]
                secno = sec_path(s, pr)
                L = [f"# {s['title']}（{method}）\n", f"> 出处：{IF_FILE} 第 {secno} 节\n",
                     "## 方法说明\n"]
                for p in s.get("paras", []):
                    L.append(f"- {p}")
                L.append("")
                for x in xml_tbs:
                    L.append("### 报文结构\n")
                    L.append(md_table(x["header"], x["rows"]))
                    L.append("")
                L.append("## 数据含义分析\n\n*由 LLM 生成*\n")
                add(f"外部接口-方法-{slugify(method[:16])}-{slugify(s['title'][:12])}",
                    f"{s['title']}（{method}）", "concept", "\n".join(L), svc_f, IF_FILE, IF_DOC, secno)

    # 功能接口
    fn_f = folder("功能接口", root_if)
    fn_sec = next((s for s, _ in allsec if s["title"] == "功能接口"), None)
    if fn_sec:
        cat_tb = next((tb for s in children(fn_sec) for tb in s.get("tables", []) if tb["type"] == "IF_CATEGORY"), None)
        ovl = ["# 功能接口总览\n"]
        if cat_tb:
            ovl.append("## 接口分类\n")
            ovl.append(md_table(cat_tb["header"], cat_tb["rows"]))
            ovl.append("")
        ovl.append("## 数据含义分析\n\n*由 LLM 生成*\n")
        add("外部接口-功能接口", "功能接口总览", "summary", "\n".join(ovl), fn_f, IF_FILE, IF_DOC, "第7章")
        for s in children(fn_sec):
            if s["title"] in ("接口分类",):
                continue
            c = render_rulepage(s["title"], collect_paras(s), s.get("tables", []), IF_FILE, IF_DOC, s["title"])
            add(f"外部接口-功能-{slugify(s['title'][:12])}", s["title"], "concept", c, fn_f, IF_FILE, IF_DOC, s["title"])


def children_of(s):
    return s.get("children", [])


def walk_secs(s):
    out = [s]
    for c in children_of(s):
        out.extend(walk_secs(c))
    return out


def collect_paras(s):
    return s.get("paras", [])


# ═══════════════════════════════════════════════════════════
# 三、版本修订 / 四、术语
# ═══════════════════════════════════════════════════════════
def build_extras(spec_lm, spec_if):
    rev_f = folder("版本修订", SPEC_ROOT)
    rev_secs = find_sections(spec_lm["sections"], lambda s: s.get("tables") and any(
        tb["type"] == "REVISION" for tb in s["tables"]))
    ovl = [f"# 版本修订总览\n\n共 {len(rev_secs)} 个修订清单（每清单一页）。\n"]
    for s in rev_secs:
        name = s["title"]
        ovl.append(f"- [[版本修订-{slugify(name[:20])}|{name}]]")
    ovl.append("")
    add("版本修订-总览", "版本修订总览", "summary", "\n".join(ovl), rev_f, LM_FILE, LM_DOC, "编制历史")
    for s in rev_secs:
        rev_tb = next((tb for tb in s["tables"] if tb["type"] == "REVISION"), None)
        c = render_entity(s["title"], "", [["修订清单", s["title"]]],
                          [rev_tb["header"], *rev_tb["rows"]], [], LM_FILE, LM_DOC, s["title"])
        add(f"版本修订-{slugify(s['title'][:20])}", s["title"], "concept", c, rev_f, LM_FILE, LM_DOC, s["title"])

    term_f = folder("术语", SPEC_ROOT)
    for name, rec_key, src in (("逻辑模型术语", "TERM", LM_FILE), ("外部接口术语", "TERM", IF_FILE)):
        pass
    # 合并两分册 TERM 记录
    term_all = spec_lm["records"]["TERM"] + spec_if["records"]["TERM"]
    if term_all:
        c = render_entity("术语与缩略语", "", [["术语数", len(term_all)]],
                          [["字母/缩写", "名词/专用名词", "说明"], *term_all], [], LM_FILE, LM_DOC, "第3章")
        add("术语-总览", "术语与缩略语", "concept", c, term_f, LM_FILE, LM_DOC, "第3章")


def build_from_specs(lm_json_path, if_json_path, kb_id=None, root_folder_id=None, page_limit=0):
    """编排入口：解析产物 spec_*.json → 目录 + 页面 → 批量落库。返回统计。"""
    global KB, SPEC_ROOT, PAGES
    if kb_id:
        KB = kb_id
    if root_folder_id:
        SPEC_ROOT = root_folder_id
    PAGES.clear()
    _folder_cache.clear()
    spec_lm = json.load(open(lm_json_path, encoding="utf-8"))
    spec_if = json.load(open(if_json_path, encoding="utf-8"))
    build_lm(spec_lm)
    build_if(spec_if)
    build_extras(spec_lm, spec_if)
    sel = PAGES if not page_limit else PAGES[:page_limit]
    print(f"[n04] 渲染 {len(PAGES)} 页（本次 {len(sel)}），开始落库…", flush=True)
    created = err = 0
    for i in range(0, len(sel), 50):
        batch = sel[i:i + 50]
        try:
            d = (wr.tool_call("create_wiki_pages_batch", {"kb_id": KB, "pages": batch}) or {}).get("data") or {}
            created += int(d.get("created") or 0)
            err += len(d.get("errors") or [])
        except Exception as e:  # noqa: BLE001
            print(f"  批次 {i//50} 失败: {e}", flush=True)
            err += len(batch)
    print(f"[n04] 落库完成：created={created} errors={err}", flush=True)
    return {"rendered": len(PAGES), "created": created, "errors": err}


if __name__ == "__main__":
    import sys as _s
    if len(_s.argv) >= 4:
        r = build_from_specs(_s.argv[1], _s.argv[2], _s.argv[3] if len(_s.argv) > 3 else None)
        print(r)
    else:
        print("用法: spec_wiki_lib.py <lm.json> <if.json> [kb_id]")