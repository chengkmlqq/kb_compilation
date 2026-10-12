#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规范分册 docx 结构化解析（纯 stdlib：zip + ElementTree）。

输入：两个（或更多）规范 .docx
输出：spec.json —— 章节树 + 分类后的表格记录（实体/属性/关系/参照表/修订/接口单元/
      接口分类/XML报文/枚举/错误码/数据集/术语）+ 各章节正文段落（条文）。

标题层级：段落 pStyle → styles.xml 的 outlineLvl（0=heading1）。
表格识别：按表头签名分类（表类型字典）。
用法：python3 parse_spec_docx.py <docx> -o <out.json> [--volume <名称>] [--doc-id <kid>]
"""
import argparse
import json
import sys
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def para_text(p):
    return "".join(t.text or "" for t in p.iter(W + "t")).strip()


def cell_texts(tr):
    return [" ".join(para_text(tc).split()) for tc in tr.findall("./w:tc", NS)]


def parse(path):
    z = zipfile.ZipFile(path)
    # 样式 → outline 级别
    sty = ET.fromstring(z.read("word/styles.xml").decode("utf-8"))
    style_lvl = {}
    for s in sty.findall("w:style", NS):
        sid = s.get(W + "styleId")
        ol = s.find("./w:pPr/w:outlineLvl", NS)
        if ol is not None:
            style_lvl[sid] = int(ol.get(W + "val", "0"))
    doc = ET.fromstring(z.read("word/document.xml").decode("utf-8"))
    body = doc.find("w:body", NS)
    blocks = []
    for el in body:
        tag = el.tag.replace(W, "")
        if tag == "p":
            blocks.append(("p", el))
        elif tag == "tbl":
            blocks.append(("tbl", el))
    # 章节树 + 记录
    sections = []  # {number,title,level,paras[],tables[],records[]}
    stack = []
    records = {"ENTITY_DEF": [], "ENTITY_ATTR": [], "RELATION": [], "REF_TABLE": [],
               "REVISION": [], "IF_UNIT": [], "IF_CATEGORY": [], "IF_XML": [],
               "IF_ENUM": [], "IF_ERR": [], "IF_DATASET": [], "TERM": [], "ENUM": []}
    cur = None
    para_no = 0
    for kind, el in blocks:
        if kind == "p":
            txt = para_text(el)
            pst = el.find("./w:pPr/w:pStyle", NS)
            lvl = None
            if pst is not None:
                lvl = style_lvl.get(pst.get(W + "val"))
            if lvl is not None:
                sec = {"number": txt.split(None, 1)[0] if txt.split() else "",
                       "title": " ".join(txt.split(None, 1)[1:]) if " " in txt and txt.split() else txt,
                       "level": lvl + 1, "paras": [], "tables": []}
                while stack and stack[-1]["level"] >= sec["level"]:
                    stack.pop()
                if stack:
                    stack[-1].setdefault("children", []).append(sec)
                else:
                    sections.append(sec)
                stack.append(sec)
                cur = sec
            elif cur is not None and txt and not txt.startswith("目") and len(txt) > 1:
                # 跳过目录页码行与封面（正文段落按章节收集）
                if not (cur["title"] == "" and txt.isdigit()):
                    cur["paras"].append(txt)
            para_no += 1
        else:  # tbl
            rows = el.findall("./w:tr", NS)
            if not rows:
                continue
            hdr = cell_texts(rows[0])
            sig = "|".join(hdr[:6])
            cls = classify(sig, hdr)
            recs = [cell_texts(r) for r in rows[1:]]
            if cls and cls not in ("SKIP", "TOC", "OTHER"):
                records[cls].extend(recs)
                if cur is not None:
                    cur["tables"].append({"type": cls, "header": hdr, "rows": recs})
            elif cur is not None:
                cur["tables"].append({"type": "OTHER", "header": hdr, "rows": recs})
    return {"sections": sections, "records": records, "n_tables": sum(len(x) for x in records.values())}


def classify(sig, hdr):
    S = sig.replace(" ", "")
    if "序号|标准编号|标准名称" in S:
        return "SKIP"
    if "修订后实体名称" in S and "修订前实体名称" in S:
        return "REVISION"
    if "属性编码|属性名称|属性描述|属性类型" in S:
        return "ENTITY_ATTR"
    if "实体编号|" in S and ("实体主要属性" in S or "主要实体" in S) and "实体说明" in S:
        return "ENTITY_DEF"
    if "实体关系编号" in S:
        return "RELATION"
    if "参照表编号" in S or "参照表名称" in S:
        return "REF_TABLE"
    if "父元素名称|元素名称" in S:
        return "IF_XML"
    if "类别|编号|接口名称" in S or "类别代码|接口单元" in S:
        return "IF_CATEGORY"
    if "序号|编码|名称|取值说明" in S:
        return "IF_ENUM"
    if "编码|内容|说明" in S:
        return "IF_ENUM"
    if "参数标识" in S or "ErrRsp" in S:
        return "IF_ERR"
    if "字段名|类型|说明" in S:
        return "IF_DATASET"
    if "序号|编码|名称" in S:
        return "IF_UNIT"
    if "字母|名词|说明" in S or "缩写|专用名词" in S:
        return "TERM"
    if hdr and hdr[0].startswith("属性名称"):
        return "ENUM"
    return "OTHER"


NS = {"w": W[1:-1]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--volume", default="")
    ap.add_argument("--doc-id", default="")
    ap.add_argument("--file", default="")
    A = ap.parse_args()
    data = parse(A.docx)
    out = {"volume": A.volume or "", "doc_id": A.doc_id, "file": A.file or A.docx, **data}
    json.dump(out, open(A.out, "w", encoding="utf-8"), ensure_ascii=False)
    rec = out["records"]
    print(f"章节 {len(out['sections'])} | 表格总数 {out['n_tables']}")
    for k, v in rec.items():
        if v:
            print(f"  {k}: {len(v)} 条")
    print("→", A.out)


if __name__ == "__main__":
    main()
