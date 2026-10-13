#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_compilation 版 weknora_rpc —— 供管技能移植适配层（2026-10-13）。

与技能 scripts/weknora_rpc.py **同名同签名**，技能脚本 import 替换即用（上层零改动）。
底层从 WeKnora MCP 网关改为 kb_compilation REST 直调（服务账号 huqiang/sys 登录拿 cookie）。

平台语义差异处理（迁移指南 skill-migration-weknora-to-kbcompilation.md）：
1. slug 含 `/` 不可寻址（/wiki/pages/{slug} 单路径段路由）→ 入站统一 /→-，
   出站按前缀还原（summary|longsentence|keyword|index|entity）——技能脚本侧形态不变。
2. wikilink 归一化与 slug 扁平化对称（content 写库前转 [[x/y|名]] → [[x-y|名]]，读回还原）。
3. status 词表：published/draft → active（平台仅 active 进 wiki 树）。
4. source_refs："kid|文件名" ↔ 平台 [{"doc": kid, "file": 名}] 双向转换。
5. 图谱 graph_add/graph_delete/graph_search_node 直连 kb-neo4j
   （NEO4J_URI 默认 bolt://kb-neo4j:7687，worker 已注入 NEO4J_* env；双 label MERGE + attributes）。
6. llm_call 直连 inferaiapi（504/429/403 入重试表；同步调用避免 daemon 线程异常丢失）。
7. 多目录挂载 folder_ids：平台已支持（wiki_page_folder 表，2026-10-13 新增）。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get("KB_API_BASE_URL", "http://api-server:8000").rstrip("/") + "/api/v1"
KB: str | None = None
DEFAULT_MODEL = os.environ.get("LLM_MODEL") or "deepseek-v4-pro"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# ── 配置（环境变量优先；LLM 端点/模型可被编排 def 覆盖）──
LLM_BASE_URL = os.environ.get("LLM_BASE_URL") or os.environ.get("INFERAI_BASE_URL") or "https://inferaiapi.com/v1"
LLM_API_KEY = os.environ.get("LLM_API_KEY") or ""

# 技能源文档 slug 前缀（含 / 的形态，入站转换用到）
_SLUG_FWD = ("summary/", "longsentence/", "keyword/", "index/", "entity/")
_SLUG_REV = ("summary-", "longsentence-", "keyword-", "index-", "entity-")
_WIKILINK_RE = re.compile(r"\[\[((?:summary|longsentence|keyword|index|entity)/[^\]|]+)(?:\|([^\]]*))?\]\]")

_cookie: str | None = None


# ═══════════════════════════════════════════════════════════
# 基础：配置 / 登录 / HTTP
# ═══════════════════════════════════════════════════════════
def load_config(config_path=None):
    """读取配置（兼容技能签名；实际配置走环境变量/参数，编排 def 注入）。"""
    global KB, LLM_BASE_URL, LLM_API_KEY, DEFAULT_MODEL
    if KB is None and os.environ.get("WEKNORA_KB_ID"):
        KB = os.environ["WEKNORA_KB_ID"]
    return None


def load_kb(kb_id=None):
    global KB
    if kb_id:
        KB = kb_id
    return KB


def llm_config():
    return LLM_BASE_URL, LLM_API_KEY or ""


def mcp_init():
    """REST 直调无需 MCP 握手；登录拿 cookie（幂等）。"""
    _ensure_cookie()
    return True


def _ensure_cookie() -> str:
    global _cookie
    if _cookie:
        return _cookie
    env_cookie = os.environ.get("IDENTITY_COOKIE", "")
    if env_cookie:
        _cookie = env_cookie
        return _cookie
    user_id = os.environ.get("KB_LOGIN_USER", "huqiang")
    pwd = os.environ.get("KB_LOGIN_PWD", "sys")
    body = json.dumps({"userId": user_id, "pwd": pwd}).encode()
    req = urllib.request.Request(API_BASE + "/auth/login", body,
                                 {"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"kb 登录失败: {e}") from e
    ck = (d.get("identity_cookie") or (d.get("data") or {}).get("identity_cookie") or "")
    if not ck:
        raise RuntimeError(f"kb 登录未返回 cookie: {str(d)[:200]}")
    _cookie = ck
    return ck


def _http(method: str, path: str, body=None, timeout=120, retries=3) -> dict:
    ck = _ensure_cookie()
    data = json.dumps(body).encode() if body is not None else None
    hdr = {"Cookie": f"x-next-identity={ck}", "Content-Type": "application/json"}
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(API_BASE + path, data, hdr, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:  # noqa: BLE001
                pass
            if e.code in (408, 429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(min(30, 2 ** attempt * 2))
                continue
            if e.code == 404:
                return {"error": f"HTTP 404: {detail or path}"}
            raise RuntimeError(f"HTTP {e.code} {method} {path}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            break
    raise RuntimeError(f"请求失败（重试 {retries} 次）: {last}")


def rpc(method, params, retries=5):
    """兼容技能签名：tool_call 的底层。"""
    return tool_call(method, params)


def parse_result(res):
    return res


def parse_text(data):
    return data


def tool_text(res):
    return res or {}


# ═══════════════════════════════════════════════════════════
# slug / wikilink / status / source_refs 转换
# ═══════════════════════════════════════════════════════════
def _slug_to_kb(slug: str) -> str:
    for p in _SLUG_FWD:
        if slug.startswith(p):
            return p[:-1] + "-" + slug[len(p):]
    return slug


def _slug_to_native(slug: str) -> str:
    for p in _SLUG_REV:
        if slug.startswith(p):
            return p[:-1] + "/" + slug[len(p):]
    return slug


def _content_to_kb(content: str) -> str:
    return _WIKILINK_RE.sub(
        lambda m: "[[" + m.group(1).replace("/", "-", 1) + (("|" + m.group(2)) if m.group(2) else "") + "]]",
        content or "",
    )


def _content_to_native(content: str) -> str:
    return re.sub(
        r"\[\[((?:summary|longsentence|keyword|index|entity)-[^\]|]+)(?:\|([^\]]*))?\]\]",
        lambda m: "[[" + m.group(1).replace("-", "/", 1) + (("|" + m.group(2)) if m.group(2) else "") + "]]",
        content or "",
    )


def _refs_to_kb(refs) -> list:
    """["kid|文件名", ...] → [{"doc": kid, "file": 名}, ...]"""
    out = []
    for r in refs or []:
        if isinstance(r, dict):
            out.append(r)
            continue
        s = str(r)
        if "|" in s:
            kid, fname = s.split("|", 1)
            out.append({"doc": kid, "file": fname})
        elif s:
            out.append({"doc": s, "file": ""})
    return out


def _refs_to_native(refs) -> list[str]:
    """[{"doc":kid,"file":名}, ...] → ["kid|文件名", ...]（兼容 dict 直传）"""
    out = []
    for r in refs or []:
        if isinstance(r, str):
            out.append(r)
        elif isinstance(r, dict):
            kid = str(r.get("doc") or r.get("knowledge_id") or "")
            fname = str(r.get("file") or r.get("file_name") or "")
            out.append(f"{kid}|{fname}" if fname else kid)
    return out


def _status_to_kb(status: str | None) -> str | None:
    if status in ("published", "draft"):
        return "active"
    return status


# ═══════════════════════════════════════════════════════════
# 文档 / 切片
# ═══════════════════════════════════════════════════════════
def list_docs(kb_id=None, page=1, page_size=100):
    kb = kb_id or _kb()
    d = _http("GET", f"/kbs/{kb}/documents?page={page}&page_size={page_size}")
    data = d.get("data") or {}
    items = data.get("items") if isinstance(data, dict) else data
    return {"data": items or []}


def list_chunks(kid, kb_id=None, page=1, page_size=100):
    """kb 的 chunks 接口全量返回（无分页）；忽略 page 参数取全部。"""
    kb = kb_id or _kb()
    d = _http("GET", f"/kbs/{kb}/documents/{kid}/chunks")
    data = d.get("data") or []
    if isinstance(data, dict):
        data = data.get("items") or data.get("chunks") or []
    rows = [{"content": c.get("content", ""), "chunk_index": c.get("seq") or c.get("chunk_index") or 0}
            for c in data]
    return {"data": rows}


def fetch_all_chunks(kid, kb_id=None):
    r = list_chunks(kid, kb_id=kb_id)
    chunks = [{"content": c["content"], "chunk_index": c.get("chunk_index", 0)} for c in (r.get("data") or [])]
    chunks.sort(key=lambda c: c["chunk_index"])
    return chunks


def find_file(file_name, kb_id=None):
    kb = kb_id or _kb()
    page = 1
    while True:
        r = list_docs(kb_id=kb, page=page, page_size=100)
        data = r.get("data") or []
        for d in data:
            if d.get("file_name") == file_name:
                return d
        if len(data) < 100:
            break
        page += 1
    page = 1
    while True:
        r = list_docs(kb_id=kb, page=page, page_size=100)
        data = r.get("data") or []
        for d in data:
            if file_name in (d.get("file_name") or ""):
                return d
        if len(data) < 100:
            break
        page += 1
    return None


def clean_chunk_text(text):
    """清洗切片（与技能同款）：锚点/图片 resource/# 标记/纯页码行。"""
    text = re.sub(r"\[([^\]]*)\]\(#[^)]*\)", r"\1", text or "")
    text = re.sub(r"!\[[^\]]*\]\(resource://[^)]*\)", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"#{2,6}", "", text)
    lines = [ln for ln in text.splitlines() if ln.strip() and not re.fullmatch(r"\[\d+\]", ln.strip())]
    return "\n".join(lines)


def tokenize(text, kb_id=None):
    """kb 无 REST tokenize——降级：返回空补充集（discover_terms 统计通道是主链路）。"""
    return {"data": []}


def clean_llm_tail(text):
    """清理 LLM 尾部聊天残留（技能同款）。"""
    if not text:
        return text
    m = re.search(r"^\s*(?:😊[^\n]*|有什么(?:我可以|需要我)帮[^\n]*)\s*$", text, re.MULTILINE | re.DOTALL)
    matches = list(re.finditer(r"^\s*(?:😊[^\n]*|有什么(?:我可以|需要我)帮[^\n]*)\s*$", text, re.MULTILINE))
    if matches:
        last = matches[-1]
        if not text[last.end():].strip():
            text = text[:last.start()].rstrip() + "\n"
    return text


def bookname(name):
    name = (name or "").strip()
    if not name:
        return ""
    return name if ("《" in name and "》" in name) else f"《{name}》"


def rewrite_source_line(content, refs):
    """按 refs 并集重写 entity 页基本信息表「来源制度」行（技能同款）。"""
    if not refs or "来源制度" not in (content or ""):
        return content
    names = []
    for r in refs:
        fname = r.split("|", 1)[1] if "|" in r else r
        n = bookname(fname)
        if n and n not in names:
            names.append(n)
    if not names:
        return content
    joined = "；".join(names)
    m = re.search(r"## 基本信息(.*?)(\n## |\Z)", content, re.S)
    if not m:
        return content
    seg = m.group(1)
    new_seg = re.sub(r"^\| 来源制度 \|.*\|$", f"| 来源制度 | {joined} |", seg, count=1, flags=re.M)
    if new_seg == seg:
        return content
    return content[:m.start(1)] + new_seg + content[m.end(1):]


# ═══════════════════════════════════════════════════════════
# Wiki 目录 / 页面
# ═══════════════════════════════════════════════════════════
def _kb():
    if KB is None:
        raise RuntimeError("请先设置 wr.KB = <目标知识库 id>")
    return KB


def list_folders(kb_id=None, parent_id=""):
    kb = kb_id or _kb()
    d = _http("GET", f"/kbs/{kb}/wiki/folders")
    data = d.get("data") or []
    if isinstance(data, dict):
        data = data.get("folders") or data.get("items") or []
    folders = []
    for f in data:
        folders.append({
            "id": f.get("id", ""),
            "name": f.get("name", ""),
            "parent_id": f.get("parent_id") or "",
            "created_at": f.get("created_at"),
        })
    return {"folders": folders}


def walk_folders(kb_id=None, parent_id="", collect=None):
    if collect is None:
        collect = {}
    r = list_folders(kb_id=kb_id)
    for f in r.get("folders", []):
        collect[f["id"]] = f["name"]
    return collect


def create_folder(name, parent_id="", kb_id=None):
    kb = kb_id or _kb()
    d = _http("POST", f"/kbs/{kb}/wiki/folders", {"name": name, "parent_id": parent_id or ""})
    data = d.get("data") or d
    return data if isinstance(data, dict) else {"id": data.get("id", ""), "name": name}


def update_folder(folder_id, kb_id=None, name=None, parent_id=None):
    kb = kb_id or _kb()
    body = {}
    if name is not None:
        body["name"] = name
    if parent_id is not None:
        body["parent_id"] = parent_id or ""
    d = _http("PUT", f"/kbs/{kb}/wiki/folders/{folder_id}", body)
    return d.get("data") or d


def delete_folder(folder_id, kb_id=None):
    kb = kb_id or _kb()
    return _http("DELETE", f"/kbs/{kb}/wiki/folders/{folder_id}")


def move_page(slug, kb_id=None, folder_id=""):
    kb = kb_id or _kb()
    return update_page(slug, kb_id=kb, folder_id=folder_id or "")


def _page_to_native(p: dict) -> dict:
    p = dict(p)
    p["slug"] = _slug_to_native(p.get("slug", ""))
    if p.get("content"):
        p["content"] = _content_to_native(p["content"])
    if p.get("source_refs"):
        p["source_refs"] = _refs_to_native(p["source_refs"])
    if not p.get("folder_ids") and p.get("folder_id"):
        p["folder_ids"] = [p["folder_id"]]
    return p


def list_wiki_pages(kb_id=None, page=1, page_size=200):
    kb = kb_id or _kb()
    d = _http("GET", f"/kbs/{kb}/wiki/pages?page={page}&page_size={min(page_size, 200)}")
    data = d.get("data") or {}
    items = data.get("items") if isinstance(data, dict) else data
    return {"pages": [_page_to_native(p) for p in (items or [])]}


def wiki_search(query, kb_id=None, limit=100):
    kb = kb_id or _kb()
    d = _http("GET", f"/kbs/{kb}/wiki/search?q={urllib.parse.quote(query)}&limit={limit}")
    data = d.get("data") or {}
    items = data.get("items") if isinstance(data, dict) else data
    pages = []
    for p in items or []:
        pages.append(_page_to_native({**p, "content": p.get("content_head") or ""}))
    return {"pages": pages}


def create_page(slug, title, content, folder_id, kb_id=None, page_type=None, summary="", source_refs=None):
    if page_type is None:
        raise ValueError("page_type is required for create_page.")
    kb = kb_id or _kb()
    body = {
        "slug": _slug_to_kb(slug),
        "title": title,
        "content": _content_to_kb(content),
        "page_type": page_type,
        "folder_id": folder_id or "",
        "summary": summary,
    }
    if source_refs:
        body["source_refs"] = _refs_to_kb(source_refs)
    d = _http("POST", f"/kbs/{kb}/wiki/pages", body)
    return d.get("data") or d


def update_page(slug, kb_id=None, content=None, folder_id=None, summary=None,
                source_refs=None, page_type=None, aliases=None, title=None,
                status=None, folder_ids=None):
    kb = kb_id or _kb()
    body = {}
    if content is not None:
        body["content"] = _content_to_kb(content)
    if folder_id is not None:
        body["folder_id"] = folder_id or ""
    if folder_ids is not None:
        body["folder_ids"] = folder_ids or []
    if summary is not None:
        body["summary"] = summary
    if source_refs is not None:
        body["source_refs"] = _refs_to_kb(source_refs)
    if page_type is not None:
        body["page_type"] = page_type
    if title is not None:
        body["title"] = title
    if status is not None:
        body["status"] = _status_to_kb(status)
    d = _http("PUT", f"/kbs/{kb}/wiki/pages/{urllib.parse.quote(_slug_to_kb(slug), safe='')}", body)
    return d.get("data") or d


def read_page(slug, kb_id=None):
    kb = kb_id or _kb()
    try:
        d = _http("GET", f"/kbs/{kb}/wiki/pages/{urllib.parse.quote(_slug_to_kb(slug), safe='')}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": f"404 page not found: {slug}"}
        raise
    if isinstance(d, dict) and d.get("error"):
        return d
    page = d.get("data") or d
    return _page_to_native(page) if isinstance(page, dict) else page


def delete_page(slug, kb_id=None):
    kb = kb_id or _kb()
    return _http("DELETE", f"/kbs/{kb}/wiki/pages/{urllib.parse.quote(_slug_to_kb(slug), safe='')}")


def rebuild_links(kb_id=None):
    kb = kb_id or _kb()
    d = _http("POST", f"/kbs/{kb}/wiki/rebuild-links", {})
    return {"success": True, **d}


# ═══════════════════════════════════════════════════════════
# 图谱直连（graph_add / graph_delete / graph_search_node）
# ═══════════════════════════════════════════════════════════
def _graph_conn():
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://kb-neo4j:7687")
    user = os.environ.get("NEO4J_USERNAME", "neo4j")
    pwd = os.environ.get("NEO4J_PASSWORD", "")
    database = os.environ.get("NEO4J_DATABASE") or None
    return GraphDatabase.driver(uri, auth=(user, pwd)), database


def _graph_call(kb_id, cypher, params=None):
    """直连 kb-neo4j 执行 Cypher（先删后写由调用方控制）。"""
    driver, database = _graph_conn()
    try:
        with driver.session(database=database) as session:
            return session.run(cypher, params or {}).consume()
    finally:
        driver.close()


def tool_call(name, arguments):
    """graph_add / graph_delete / graph_search_node → Neo4j 直连；其余走 REST。"""
    kb_id = arguments.get("kb_id") or _kb()
    if name == "graph_add":
        return _graph_add(kb_id, arguments)
    if name == "graph_delete":
        return _graph_delete(kb_id, arguments)
    if name == "graph_search_node":
        return _graph_search(kb_id, arguments)
    raise NotImplementedError(f"kb_wiki_rpc 未实现 tool_call: {name}")


def _graph_add(kb_id, arguments):
    graph = (arguments.get("graphs") or [{}])[0]
    nodes = graph.get("node") or []
    rels = graph.get("relation") or []
    kg = str(arguments.get("knowledge_id") or arguments.get("kg") or "")
    kb_label = "ENTITY" + kb_id.replace("-", "_")
    kg_label = ("ENTITY" + kg.replace("-", "_")) if kg else kb_label
    node_idx = {}
    for n in nodes:
        name = str(n.get("name") or n.get("title") or "")
        if not name:
            continue
        node_idx[name] = True
        attrs = n.get("attributes") or []
        if not isinstance(attrs, list):
            attrs = [str(attrs)]
        page_id = n.get("page_id") or ""
        page_slug = str(n.get("page_slug") or "")
        _cypher(
            f"MERGE (n:`{kb_label}`:`{kg_label}` {{name: $name}}) "
            f"SET n.name = $name, n.attributes = $attrs, n.kb_id = $kb, n.kg = $kg, "
            f"n.page_id = $page_id, n.page_slug = $page_slug",
            {"name": name, "attrs": attrs, "kb": kb_id, "kg": kg or kb_id,
             "page_id": page_id or None, "page_slug": page_slug or None},
        )
    for r in rels:
        s = str(r.get("node1") or "")
        t = str(r.get("node2") or "")
        typ = str(r.get("type") or "related")[:60]
        if not s or not t:
            continue
        props = r.get("properties") or {}
        for ep in (s, t):
            if ep not in node_idx:
                _cypher(
                    f"MERGE (n:`{kb_label}`:`{kg_label}` {{name: $name}}) SET n.kb_id = $kb, n.kg = $kg, n.attributes = []",
                    {"name": ep, "kb": kb_id, "kg": kg or kb_id},
                )
        set_clause = "SET r.note = $note, r.rule_slug = $rule_slug, r.predicate = $predicate"
        _cypher(
            f"MATCH (a:`{kb_label}` {{name: $s}}), (b:`{kb_label}` {{name: $t}}) "
            f"MERGE (a)-[r:`{typ}`]->(b) {set_clause}",
            {"s": s, "t": t, "note": str(props.get("note") or "")[:200],
             "rule_slug": str(props.get("rule_slug") or ""), "predicate": str(props.get("predicate") or "")},
        )
    return {"success": True, "entities": len(nodes), "relationships": len(rels)}


def _graph_delete(kb_id, arguments):
    kg = str(arguments.get("knowledge_id") or "")
    if kg:
        label = "ENTITY" + kg.replace("-", "_")
        _cypher(f"MATCH (n:`{label}`) DETACH DELETE n")
    else:
        label = "ENTITY" + kb_id.replace("-", "_")
        _cypher(f"MATCH (n:`{label}`) DETACH DELETE n")
    return {"success": True, "deleted": True}


def _graph_search(kb_id, arguments):
    q = str(arguments.get("query") or arguments.get("name") or "")
    limit = min(int(arguments.get("limit") or 100), 200)
    label = "ENTITY" + kb_id.replace("-", "_")
    if q:
        rows = _query(
            f"MATCH (n:`{label}`) WHERE n.name CONTAINS $q RETURN n.name AS name, n.attributes AS attributes, n.kg AS kg LIMIT {limit}",
            {"q": q},
        )
    else:
        rows = _query(
            f"MATCH (n:`{label}`) RETURN n.name AS name, n.attributes AS attributes, n.kg AS kg LIMIT {limit}"
        )
    return {"success": True, "data": [dict(r) for r in rows]}


def _cypher(cypher, params=None):
    driver, database = _graph_conn()
    try:
        with driver.session(database=database) as session:
            session.run(cypher, params or {})
    finally:
        driver.close()


def _query(cypher, params=None):
    driver, database = _graph_conn()
    try:
        with driver.session(database=database) as session:
            return list(session.run(cypher, params or {}))
    finally:
        driver.close()


# ═══════════════════════════════════════════════════════════
# LLM
# ═══════════════════════════════════════════════════════════
def llm_call(messages, model=None, max_tokens=4096, temperature=0.1,
             retries=5, base_delay=3, timeout=300):
    """直连 inferaiapi（同步调用 + 退避重试；504/429/403/5xx 入重试表）。

    迁移指南坑：① 504 必须显式加进重试列表（InferAI 大 max_tokens 常见）；
    ② 用同步调用（不走 daemon 线程），避免 HTTPError 在线程里丢失致重试分支死代码。
    """
    import urllib.request as _ur

    base_url = LLM_BASE_URL.rstrip("/")
    key = LLM_API_KEY
    if not key:
        # 环境兜底（worker 注入 / .env）
        env = {}
        try:
            for ln in open(os.path.expanduser("~/.hermes/.env"), encoding="utf-8"):
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k, v = ln.split("=", 1)
                    env[k.strip()] = v.strip()
        except Exception:  # noqa: BLE001
            pass
        key = (os.environ.get("AI_CHAT_API_KEY") or env.get("LLM_API_KEY")
               or env.get("AI_CHAT_API_KEY") or env.get("HERMES_CUSTOM_INFERAIAPI_COM_API_KEY") or "")
    model = model or DEFAULT_MODEL
    payload = json.dumps({
        "model": model, "messages": messages, "max_tokens": max_tokens,
        "temperature": temperature, "enable_thinking": False,
    }).encode()
    req = _ur.Request(base_url + "/chat/completions", payload, {
        "Content-Type": "application/json", "Authorization": f"Bearer {key}",
        "User-Agent": UA,
    })
    last = None
    for attempt in range(retries):
        try:
            resp = json.loads(_ur.urlopen(req, timeout=timeout).read().decode())
            choice = resp["choices"][0]
            content = (choice["message"].get("content") or "").strip()
            if choice.get("finish_reason") == "length":
                print(f"  ⚠️ LLM 输出被截断 (finish_reason=length, {len(content)} chars)", flush=True)
            return content
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 429, 500, 502, 503, 504, 520, 521, 522, 524):
                delay = min(base_delay * (2 ** attempt), 60)
                print(f"  ⏳ LLM {e.code}, 等待 {delay}s 重试 ({attempt + 1}/{retries})", flush=True)
                time.sleep(delay)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            delay = base_delay * (attempt + 1)
            print(f"  ⏳ LLM 网络错误: {e}, 等待 {delay}s 重试", flush=True)
            time.sleep(delay)
        except (json.JSONDecodeError, KeyError) as e:
            last = e
            delay = base_delay * (attempt + 1)
            print(f"  ⏳ LLM 响应解析失败: {e}, 重试", flush=True)
            time.sleep(delay)
    raise RuntimeError(f"LLM 调用失败，已重试 {retries} 次: {last}")


def ensure_full_json(text):
    """LLM 截断 JSON 修复（技能同款）。"""
    text = (text or "").strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    depth = 0
    last_complete = 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                last_complete = i + 1
    if last_complete > 0:
        candidate = text[:last_complete]
        if not candidate.endswith("]"):
            if candidate.rstrip().endswith(","):
                candidate = candidate.rstrip()[:-1]
            candidate += "\n]"
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    return text