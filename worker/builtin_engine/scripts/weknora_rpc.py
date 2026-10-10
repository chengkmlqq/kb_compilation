#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_compilation 平台 HTTP 直调版 weknora_rpc（2026-10-06 由 WeKnora MCP 网关版适配）。

上层 30+ 脚本（build_full.py / gen_summary.py / extract_entities_single.py 等）**零改动**：
本模块保留原有全部函数名与签名，把原 WeKnora MCP 网关调用（rpc/tool_call）改写为
kb_compilation 平台 HTTP 直调（登录 cookie + REST），并按各脚本既有预期对齐返回结构。

连接配置（环境变量 > 技能根 config.yaml > 默认）
  - API 入口:  KB_API_BASE_URL            > config.yaml kb.api_base_url > http://api-server:8000
  - 服务账号:  KB_API_USER / KB_API_PWD    > config.yaml kb.api_user/api_pwd > admin/sys
  - 目标知识库: WEKNORA_KB_ID（agent 任务注入） > config.yaml kb.kb_id
  - LLM:       WEKNORA_LLM_*（任务级）> LLM_* / AI_CHAT_*（容器注入）> config.yaml model 段
               > 默认 https://inferaiapi.com/v1 + deepseek-v4-pro（key 兜底读
                 /home/ctyun/kb_compilation/deploy/.env 的 AI_CHAT_API_KEY）

MCP 工具名 → HTTP 端点映射
  list_documents      GET  /api/v1/kbs/{kb}/documents?page&page_size            → {'data': [items]}
  get_document        同上翻页找 id（平台无单文档元数据端点）                     → {'data': doc}
  list_chunks         GET  /api/v1/kbs/{kb}/documents/{did}/chunks               → {'data': [{content, chunk_index}]}（seq→chunk_index，按页切片）
  list_wiki_folders   GET  /api/v1/kbs/{kb}/wiki                                 → {'folders': [...]}（按 parent_id 过滤直子目录）
  list_wiki_pages     GET  /api/v1/kbs/{kb}/wiki + 逐页 GET wiki/pages/{slug}     → {'pages': [...]}（补 content/source_refs，进程内缓存）
  wiki_search         GET  /api/v1/kbs/{kb}/wiki/search?q&limit                  → {'data': {...}}
  wiki_read_page      GET  /api/v1/kbs/{kb}/wiki/pages/{slug}                    → 页面字典（404 → {'error'}）
  create_wiki_page    POST /api/v1/kbs/{kb}/wiki/pages                           → {'data': {...}}（400 slug 冲突 → {'error'}）
  update_wiki_page    PUT  /api/v1/kbs/{kb}/wiki/pages/{slug}
  delete_wiki_page    DELETE /api/v1/kbs/{kb}/wiki/pages/{slug}
  create_wiki_folder  POST /api/v1/kbs/{kb}/wiki/folders                         → {'id', 'folder', 'data'}
  update/delete_wiki_folder  PUT/DELETE /api/v1/kbs/{kb}/wiki/folders/{fid}
  move_wiki_page      PUT  /api/v1/kbs/{kb}/wiki/pages/{slug}（folder_id）
  wiki_rebuild_links  POST /api/v1/kbs/{kb}/wiki/rebuild-links
  wiki_stats          GET  /api/v1/kbs/{kb}/wiki/stats
  wiki_lint           GET  /api/v1/kbs/{kb}/wiki/lint
  wiki_log_write      本地进度日志（平台无写端点）
  tokenize            本地分词（平台无端点）：jieba 优先，缺失回退 CJK 单字+相邻二字组
  graph_add/delete    no-op（平台无 Neo4j 写入端点；WikiBrowser 图谱走 wiki_link，两套数据）
  list_knowledge_bases GET /api/v1/kbs

已知语义差异（适配记录，均在「不改上层脚本」前提下处理）
  1) slug 扁平化：平台路由 /wiki/pages/{slug} 是单路径段路由，本技能的命名空间 slug
     （summary/<md5>、longsentence/…、keyword/…）能建但读/改/删一律 404。入站 slug
     一律 '/'→'-' 扁平化后访问；list_wiki_pages/read_page 出站按已知前缀
     （summary-/longsentence-/keyword-）还原为命名空间形态，使脚本侧 KW_SLUG_RE、
     startswith('summary/') 等识别照常生效。entity-b-/entity-r- 本就无 '/'，不变。
  2) status 词表：平台仅 status='active' 的页面出现在 /wiki 树与详情（published/其他值
     会被过滤成"消失"）。脚本普遍传 status='published'（WeKnora 词表）→ 映射
     published/draft→active，archived 保留。
  3) source_refs/folder_ids/aliases 不可写：平台 WikiPageCreate/Update 入参不含这三项
     （DB 列存在但 API 未暴露）。传入被静默丢弃：跨文档合并的归属判定退化为按页内容，
     正文「来源制度」行仍由 rewrite_source_line 维护；graph_export 的 _ref_has 依赖
     source_refs → 图谱导出为空图（no-op，不阻断构建）。
  4) 正文 wikilink 不改写：[[longsentence/xxx|标题]] 仍是命名空间形态，库内页 slug 为扁平
     形态 → rebuild_links 无法解析（链接图缺失，前端双链显示为纯文本）。属展示层问题。
  5) list_wiki_pages 补 content 的成本：平台无批量带正文端点，首轮全量扫描 = N 次单页 GET
     （进程内缓存；create/update 写穿缓存，后续扫描与读回命中）。
"""
import ast
import json
import os
import contextlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# 兼容旧调用（MCP 网关版遗留）：KEY 不再使用；URL 复用为 API 入口；KB 不变
# ---------------------------------------------------------------------------
KEY = None
URL = None
KB = None  # 调用前设置：wr.KB = '<目标知识库 id>'；或从环境变量 WEKNORA_KB_ID 自动读取

_ID = [0]
SESSION = {'id': None}
_COOKIE = ""

# 页面详情缓存（list_wiki_pages 的 content 补齐 + create/update 写穿）：
# key = (kb_id, flat_slug) -> page dict（含 content/source_refs 等完整字段）
_PAGE_CACHE = {}

# 命名空间 slug 前缀（脚本侧识别用）与扁平形态的互转
_NS_PREFIXES = (('summary-', 'summary/'), ('longsentence-', 'longsentence/'),
                ('keyword-', 'keyword/'))

# 平台 status 词表（active/draft/archived）与 WeKnora 词表（published/draft/...）互转：
# 非 active 值会被平台 wiki 树/详情过滤成"页面消失"，故 published 等一律归 active
_STATUS_MAP = {'published': 'active', 'publish': 'active', 'active': 'active',
               'draft': 'active', 'archived': 'archived', 'deleted': 'archived'}


def _env(name, fallback=''):
    """环境变量 -> 回退 ~/.hermes/.env 文件读取"""
    v = os.environ.get(name, '').strip()
    if v:
        return v
    try:
        for line in open(os.path.expanduser('~/.hermes/.env'), encoding='utf-8'):
            line = line.strip()
            if line.startswith(name + '='):
                return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return fallback


def _env_file(path, name):
    """从指定 .env 文件读取变量（不存在/异常返回 ''）。"""
    try:
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if line.startswith(name + '='):
                return line.split('=', 1)[1].strip()
    except Exception:
        pass
    return ''


def _skill_config():
    """技能公共配置：技能根 config.yaml（优先）→ ~/.hermes/config.yaml。返回完整 dict。"""
    import yaml
    skill_cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')
    for path in [skill_cfg, os.path.expanduser('~/.hermes/config.yaml')]:
        try:
            cfg = yaml.safe_load(open(path, encoding='utf-8')) or {}
            if cfg.get('model') or cfg.get('kb'):
                return cfg
        except Exception:
            continue
    return {}


def _default_model():
    """模型名优先级：任务级 WEKNORA_LLM_MODEL > 容器 LLM_MODEL/AI_CHAT_MODEL
    > 技能 config.yaml model.default > 内置默认 deepseek-v4-pro。"""
    for env in ('WEKNORA_LLM_MODEL', 'LLM_MODEL', 'AI_CHAT_MODEL'):
        v = os.environ.get(env, '').strip()
        if v:
            return v
    m = (_skill_config().get('model', {}) or {}).get('default', '') or ''
    return m if m else 'deepseek-v4-pro'


DEFAULT_MODEL = _default_model()  # 全技能 LLM 统一默认模型


def llm_config():
    """LLM 统一配置来源：返回 (base_url, api_key)。
    优先级：任务级 WEKNORA_LLM_BASE_URL/WEKNORA_LLM_API_KEY > 容器 LLM_*/AI_CHAT_*
    > 技能 config.yaml model 段 > 默认 https://inferaiapi.com/v1（key 兜底
    /home/ctyun/kb_compilation/deploy/.env 的 AI_CHAT_API_KEY）。"""
    cfg = _skill_config().get('model', {}) or {}
    base = (_env('WEKNORA_LLM_BASE_URL') or _env('LLM_BASE_URL')
            or _env('AI_CHAT_API_ENDPOINT') or str(cfg.get('base_url') or '').strip()
            or 'https://inferaiapi.com/v1')
    key = (_env('WEKNORA_LLM_API_KEY') or _env('LLM_API_KEY')
           or _env('AI_CHAT_API_KEY')
           or os.path.expandvars(str(cfg.get('api_key') or '')).strip())
    if not key or '${' in key:
        # 平台部署 .env（宿主机路径；容器内不存在时静默跳过）
        key = (_env_file('/home/ctyun/kb_compilation/deploy/.env', 'AI_CHAT_API_KEY')
               or _env_file(os.path.expanduser('~/kb_compilation/deploy/.env'), 'AI_CHAT_API_KEY')
               or _env('HERMES_CUSTOM_AIAAA_CC_API_KEY') or '')
    if '${' in base or '${' in key:
        base = 'https://inferaiapi.com/v1'
        key = ''
    return base.rstrip('/'), key


def api_base_url():
    """平台 API 入口：env KB_API_BASE_URL > config.yaml kb.api_base_url > 默认。"""
    cfg = _skill_config().get('kb', {}) or {}
    base = _env('KB_API_BASE_URL') or str(cfg.get('api_base_url') or '').strip() \
        or 'http://api-server:8000'
    return base.rstrip('/')


def api_credentials():
    cfg = _skill_config().get('kb', {}) or {}
    # 2026-10-09 fix: 默认服务账号由 huqiang 改为 admin——huqiang 已删除，
    # 沿用旧默认值会让编排业务原子组件(llm_extract/wiki_publish)登录 KB
    # 报「用户不存在」。与 seed_framework_db.SEED_USER_ID 默认值保持一致。
    user = _env('KB_API_USER') or str(cfg.get('api_user') or 'admin')
    pwd = _env('KB_API_PWD') or str(cfg.get('api_pwd') or 'sys')
    return user, pwd


# ---------------------------------------------------------------------------
# HTTP 客户端（登录 cookie + 401 重登 + 5xx 退避；失败带状态码+响应体）
# ---------------------------------------------------------------------------
def _login():
    """登录 kb_compilation 平台，返回 identity cookie。

    并发 4 构建时 api-server 偶发过载（登录读超时）——2026-10-07 实证 9 篇
    任务因单次登录超时整篇失败。这里做 4 次重试 + 递增退避（3/6/12s），
    过载是瞬时的，重试即可恢复；4 次仍失败才抛（真故障）。
    """
    user, pwd = api_credentials()
    body = json.dumps({'userId': user, 'pwd': pwd}).encode()
    last_err = None
    for attempt in range(4):
        if attempt:
            time.sleep(3 * (2 ** (attempt - 1)))
        req = urllib.request.Request(
            f'{api_base_url()}/api/v1/auth/login', data=body, method='POST',
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}"
            continue
        except Exception as e:
            last_err = str(e)
            continue
        if not data.get('success'):
            last_err = str(data.get('message') or data)
            continue
        ck = (data.get('identity_cookie') or '').strip()
        if ck:
            return ck
        last_err = f'响应无 identity_cookie（{json.dumps(data, ensure_ascii=False)[:200]}）'
    raise RuntimeError(f"kb 登录失败（重试 4 次）: {last_err}")



# ---------------------------------------------------------------------------
# 内部直连模式（KB_INTERNAL_DIRECT=1，worker 内置技能）：跳过 HTTP/登录认证，
# 进程内直接调平台 services（api.services.kb_admin + DB）。返回结构与 REST 同构
# （{"success": true, "data": ...}），技能侧 api_get/api_post 无感知。
# ---------------------------------------------------------------------------
_DIRECT = os.environ.get("KB_INTERNAL_DIRECT") == "1"


def _direct_call(method, path, payload=None):
    """进程内直连平台 services（无认证）。path 形如 /api/v1/kbs/{kb}/wiki/...。"""
    import sys as _sys

    if "/srv/kb" not in _sys.path:
        _sys.path.insert(0, "/srv/kb")
    from sqlalchemy import text as _text

    from api.db import get_sessionmaker
    from api.services import kb_admin

    _qstr = path.split("?", 1)[1] if "?" in path else ""
    parts = [p for p in path.split("?", 1)[0].strip("/").split("/") if p]
    if len(parts) < 4 or parts[0] != "api" or parts[1] != "v1" or parts[2] != "kbs":
        raise RuntimeError(f"内部直连不支持的路径: {path}")
    kb = parts[3]
    rest = parts[4:]
    db = get_sessionmaker()()
    try:
        # ---- wiki 页面 ----
        if rest[:2] == ["wiki", "pages"]:
            slug = rest[2] if len(rest) > 2 else ""
            if method == "GET" and slug:
                r = kb_admin.get_wiki_page(db, kb, slug)
                return {"success": True, "data": r} if r is not None else {"success": False, "error": "页面不存在"}
            if method == "POST":
                if slug == "batch":
                    pages = payload or []
                    created = 0
                    for p in pages:
                        try:
                            kb_admin.wiki_create_page(db, kb, p or {})
                            created += 1
                        except Exception:  # noqa: BLE001
                            pass
                    return {"success": True, "data": {"created": created, "total": len(pages)}}
                r = kb_admin.wiki_create_page(db, kb, payload or {})
                return {"success": True, "data": r}
            if method == "PUT" and slug:
                r = kb_admin.wiki_update_page(db, kb, slug, payload or {})
                return {"success": True, "data": r}
            if method == "DELETE" and slug:
                r = kb_admin.wiki_delete_page(db, kb, slug)
                return {"success": True, "data": r}
        # ---- wiki 目录 ----
        if rest[:2] == ["wiki", "folders"]:
            fid = rest[2] if len(rest) > 2 else ""
            if method == "GET":
                return {"success": True, "data": kb_admin.wiki_folders(db, kb)}
            if method == "POST":
                r = kb_admin.wiki_create_folder(db, kb, payload or {})
                return {"success": True, "data": r}
            if method == "PUT" and fid:
                r = kb_admin.wiki_update_folder(db, kb, fid, payload or {})
                return {"success": True, "data": r}
            if method == "DELETE" and fid:
                r = kb_admin.wiki_delete_folder(db, kb, fid)
                return {"success": True, "data": r}
        # ---- wiki 其他 ----
        if rest[:2] == ["wiki", "rebuild-links"]:
            return {"success": True, "data": kb_admin.wiki_rebuild_links(db, kb)}
        if rest[:2] == ["wiki", "search"]:
            q = ""
            limit = 20
            for seg in _qstr.split("&") if _qstr else []:
                if seg.startswith("q="):
                    q = seg[2:]
                if seg.startswith("limit="):
                    try:
                        limit = int(seg.split("=")[1])
                    except ValueError:
                        pass
            import urllib.parse as _up

            q = _up.unquote(q)
            return {"success": True, "data": kb_admin.wiki_search(db, kb, q, limit)}
        if rest[:2] == ["wiki", "stats"]:
            return {"success": True, "data": kb_admin.wiki_stats(db, kb)}
        if rest[:2] == ["wiki", "lint"]:
            return {"success": True, "data": kb_admin.wiki_lint(db, kb)}
        if rest == ["wiki"]:
            return {"success": True, "data": kb_admin.wiki_tree(db, kb)}
        if rest == ["ontology-schema"]:
            from api.models.ontology import KbOntologySchema, OntologySchema

            b = db.execute(
                _text("SELECT schema_name FROM kb_ontology_schema WHERE kb_id=:k"), {"k": kb}
            ).fetchone()
            name = str(b[0]) if b else ""
            rows = []
            if name:
                rows = db.execute(
                    _text("SELECT dimension, cat_no, cat_name, cat_label, prompt_hint, neo4j_edge "
                          "FROM ontology_schema WHERE schema_name=:n ORDER BY dimension, cat_no"),
                    {"n": name},
                ).fetchall()
            return {
                "success": True,
                "data": {
                    "schema_name": name,
                    "schemas": [
                        {"dimension": r[0], "cat_no": r[1], "cat_name": r[2], "cat_label": r[3],
                         "prompt_hint": r[4], "neo4j_edge": r[5]} for r in rows
                    ],
                },
            }
        # ---- 文档 ----
        if rest[0] == "documents":
            if len(rest) == 1:
                page = 1
                ps = 100
                for seg in path.split("?")[1].split("&") if "?" in path else []:
                    if seg.startswith("page="):
                        page = int(seg.split("=")[1])
                    if seg.startswith("page_size="):
                        ps = int(seg.split("=")[1])
                r = kb_admin.list_documents(db, kb, page=page, page_size=ps)
                return {"success": True, "data": r}
            if len(rest) == 3 and rest[2] == "chunks":
                doc_id = rest[1]
                rows = db.execute(
                    _text("SELECT id, seq, content, meta FROM doc_chunk WHERE document_id=:d ORDER BY seq"),
                    {"d": doc_id},
                ).fetchall()
                items = []
                for rid, seq, content, meta in rows:
                    items.append({"id": rid, "seq": seq, "content": content, "meta": meta or {}})
                return {"success": True, "data": {"total": len(items), "items": items}}
        # ---- 其他 ----
        if rest == [] and method == "GET":
            return {"success": True, "data": kb_admin.list_kbs(db)}
        raise RuntimeError(f"内部直连未覆盖: {method} {path}")
    finally:
        db.close()


def _request(method, path, payload=None, retries=3):
    """统一平台请求。KB_INTERNAL_DIRECT=1 时进程内直连 services（无认证）。"""
    if _DIRECT:
        return _direct_call(method, path, payload)
    """HTTP 路径：统一请求。失败抛 RuntimeError（含状态码+响应体）。"""
    global _COOKIE
    url = f'{api_base_url()}{path}'
    last = None
    for attempt in range(retries):
        if not _COOKIE:
            _COOKIE = _login()
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        headers = {'Accept': 'application/json', 'Cookie': f'x-next-identity={_COOKIE}'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode('utf-8', 'replace')
            return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')
            if e.code == 401:
                _COOKIE = ''  # 强制重登
                last = RuntimeError(f'kb API {method} {path} 失败: HTTP 401')
                time.sleep(1)
                continue
            if e.code in (500, 502, 503, 504) and attempt < retries - 1:
                last = RuntimeError(f'kb API {method} {path} 失败: HTTP {e.code} {body[:200]}')
                time.sleep(1 + attempt)
                continue
            raise RuntimeError(f'kb API {method} {path} 失败: HTTP {e.code} {body[:300]}')
        except (urllib.error.URLError, TimeoutError, ConnectionResetError,
                ConnectionAbortedError, OSError) as e:
            last = RuntimeError(f'kb API {method} {path} 网络错误: {e}')
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise last
    raise last


def api_get(path, retries=3):
    return _request('GET', path, retries=retries)


def api_post(path, payload, retries=3):
    return _request('POST', path, payload, retries=retries)


def api_put(path, payload, retries=3):
    return _request('PUT', path, payload, retries=retries)


def api_delete(path, retries=3):
    return _request('DELETE', path, retries=retries)


# ---------------------------------------------------------------------------
# slug 扁平化（平台路由单路径段限制）+ 命名空间还原 + 页面详情（带缓存）
# ---------------------------------------------------------------------------
def _flat_slug(slug):
    """脚本 slug（含 '/' 命名空间）→ 平台可寻址扁平 slug（'/'→'-'）。
    entity-b-/entity-r- 等本就无 '/'，原样返回。"""
    return (slug or '').replace('/', '-')


def _normalize_wikilinks(content):
    """把页面内容中 wikilink 目标的 '/' 归一为 '-'（与 _flat_slug 对称）。

    只处理技能 slug 前缀形态（summary/longsentence/keyword/entity-b/entity-r），
    标题态链接（[[制度名|文本]]）不动。否则平台按 slug 匹配建 wiki_link
    全断链（双链显示纯文本），前端无法跳原文。
    """
    import re
    if not content:
        return content
    pattern = re.compile(r"\[\[((?:summary|longsentence|keyword|entity-b|entity-r)/[^\]|]+)")
    return pattern.sub(lambda m: "[[" + m.group(1).replace("/", "-"), content)


def _script_slug(flat):
    """DB 扁平 slug → 脚本约定命名空间 slug（list_wiki_pages/read_page 出站用）。
    仅还原已知前缀（summary-/longsentence-/keyword-），其余原样返回。"""
    s = flat or ''
    for pre, ns in _NS_PREFIXES:
        if s.startswith(pre):
            return ns + s[len(pre):]
    return s


def _page_url(kb, flat_slug):
    return f'/api/v1/kbs/{kb}/wiki/pages/{urllib.parse.quote(flat_slug, safe="")}'


def _page_detail(kb, flat_slug, use_cache=True):
    """拉单页完整字段（content/source_refs/folder_id...）。404 → None。带缓存。"""
    key = (kb, flat_slug)
    if use_cache and key in _PAGE_CACHE:
        return _PAGE_CACHE[key]
    try:
        r = api_get(_page_url(kb, flat_slug))
    except RuntimeError as e:
        if 'HTTP 404' in str(e):
            _PAGE_CACHE.pop(key, None)
            return None
        raise
    data = r.get('data') or {}
    if not data:
        # 空响应 = 页面不存在（direct 模式 get_wiki_page 返回 None → data={}）。
        # 必须返回 None 让 read_page 判定 error，否则幂等查重误判「已存在」→
        # 实体/长句页全部 SKIP（2026-10-08 实测：清库后构建只建 summary，
        # 其余全 [SKIP] 已存在，因为 {} 被当成了存在的页面）。
        _PAGE_CACHE.pop(key, None)
        return None
    _PAGE_CACHE[key] = data
    return data


def _cache_write(kb, flat_slug, page):
    if page:
        _PAGE_CACHE[(kb, flat_slug)] = page


def _map_status(status):
    """WeKnora status 词表 → 平台词表（published/draft→active，archived 保留）。"""
    s = (status or '').strip().lower()
    if not s:
        return None
    return _STATUS_MAP.get(s, 'active')


# ---------------------------------------------------------------------------
# 配置加载（与旧版同名的兼容入口）
# ---------------------------------------------------------------------------
def load_config():
    """初始化平台连接：URL=API 入口；KB 取 WEKNORA_KB_ID（任务注入）> config.yaml kb.kb_id。"""
    global KEY, URL, KB
    env_kb = os.environ.get('WEKNORA_KB_ID', '').strip()
    if env_kb:
        KB = env_kb
    if not KB:
        KB = str((_skill_config().get('kb', {}) or {}).get('kb_id') or '').strip() or None
    URL = api_base_url()
    if not URL:
        raise RuntimeError('未配置 kb_compilation API 入口：请设置环境变量 KB_API_BASE_URL')
    return URL


def load_kb(kb_id=None):
    """返回知识库 id：显式传参 > 环境变量 WEKNORA_KB_ID（agent 任务注入）> 报错。"""
    global KB
    if kb_id:
        KB = kb_id
    env_kb = os.environ.get('WEKNORA_KB_ID', '').strip()
    if env_kb and (KB is None or env_kb != (KB if KB else '')):
        KB = env_kb
    if KB is None:
        load_config()
    if KB is None:
        raise RuntimeError('未指定目标知识库：请设置环境变量 WEKNORA_KB_ID')
    return KB


# ---------------------------------------------------------------------------
# 本体 Schema（DB 驱动分类结构）——多领域扩展核心
# ---------------------------------------------------------------------------
_ONT_CACHE: dict = {}
_ONT_FAILED: set = set()


# 全量目录进程缓存（list_wiki_folders 高频调用：gen_summary 建目录 25+ 次/篇，
# 每次拉全量 1752 目录在过载时 44s/次 → 缓存 60s 后 1 次拉取本地复用）
_FOLDER_CACHE: dict[str, tuple[float, list]] = {}
_FOLDER_TTL = 60.0


def _cached_wiki_folders(kb):
    now = time.time()
    hit = _FOLDER_CACHE.get(kb)
    if hit and now - hit[0] < _FOLDER_TTL:
        return hit[1]
    # 轻量端点 /wiki/folders（仅目录，无 6974 页全量 → 秒回）；旧 /wiki 返回
    # folders+pages 全量（51s+，gen_summary 建目录 25+ 次调用必超时）
    r = api_get(f'/api/v1/kbs/{kb}/wiki/folders')
    folders = r.get('data') or []
    if isinstance(folders, dict):
        folders = folders.get('folders') or []
    _FOLDER_CACHE[kb] = (now, folders)
    return folders


def load_ontology_schema(kb_id=None, refresh=False):
    """从平台读取 KB 绑定的本体 schema（业务/规则分类 + 提示词段）。

    返回值：{'schema_name','schema_label','business':[分类dict], 'rule':[分类dict]}；
    平台不可达/未绑定时返回 None（调用方 fallback 内置硬编码）。
    进程内缓存（构建期 KB 不变）；refresh=True 强制重读。
    """
    kb = load_kb(kb_id)
    if not refresh and kb in _ONT_CACHE:
        return _ONT_CACHE[kb]
    if kb in _ONT_FAILED and not refresh:
        return None
    try:
        resp = api_get(f'/api/v1/kbs/{kb}/ontology-schema')
        data = (resp or {}).get('data') or {}
        schema = data.get('schema')
        if not schema or not schema.get('business') or not schema.get('rule'):
            raise ValueError('schema 为空或维度缺失')
        out = {
            'schema_name': schema.get('schema_name'),
            'schema_label': schema.get('schema_label'),
            'business': schema.get('business', []),
            'rule': schema.get('rule', []),
        }
        _ONT_CACHE[kb] = out
        return out
    except Exception:
        _ONT_FAILED.add(kb)
        return None


def ontology_cat_map(schema):
    """schema -> {类别名: '序号-类别名'} 映射（BIZ_CAT_SHORT/RULE_CAT_SHORT 动态版）。"""
    m = {}
    for r in (schema or {}).get('business', []):
        m[r['cat_name']] = f"{r['cat_no']}-{r['cat_name']}"
    for r in (schema or {}).get('rule', []):
        m[r['cat_name']] = f"{r['cat_no']}-{r['cat_name']}"
    return m


_ALL_FOLDERS_CACHE = {"ts": 0.0, "data": None}


def list_all_folders_cached(kb_id=None, ttl=600):
    """全量目录缓存（一次拉全库目录，本地按 parent/name 查）。

    ensure_folder 在建目录树时对每个 parent 都调 list_folders——direct 模式
    每次 5.66s（子页面计数 join），24 个层级 ≈ 2.2 分钟/篇（2026-10-07 实证
    卡在 [2]→[3]）。这里一次拉全量 + 10 分钟 TTL，后续本地 O(1) 查。
    """
    import time as _t

    kb = kb_id or KB
    now = _t.time()
    if _ALL_FOLDERS_CACHE["data"] is not None and now - _ALL_FOLDERS_CACHE["ts"] < ttl:
        return _ALL_FOLDERS_CACHE["data"]
    r = api_get(f"/api/v1/kbs/{kb}/wiki/folders")
    data = r.get("data") or []
    _ALL_FOLDERS_CACHE["ts"] = now
    _ALL_FOLDERS_CACHE["data"] = data
    return data


def mcp_init():
    """（历史命名）登录平台并初始化 HTTP 会话——**不走 MCP**。
    技能与平台交互 = HTTP 直调 + Neo4j 直连（无 MCP 服务）。
    平台适配版：等价于「确保已登录」（预热 cookie，失败早抛便于排查）。
    """
    global _COOKIE
    if _DIRECT:
        # 内部直连模式：平台调用走进程内 services，无需 HTTP cookie——
        # 跳过登录（2026-10-07 实证：api-server 过载时登录重试 4x60s 卡数分钟）
        return True
    if not _COOKIE:
        _COOKIE = _login()
    return True


def rpc(method, params, retries=5):
    raise RuntimeError(
        'weknora_rpc 已适配为 kb_compilation 平台 HTTP 直调（2026-10-06），不再使用 '
        'WeKnora MCP 网关；请改用 wr.tool_call(name, arguments) 或本模块快捷函数。')


def parse_result(res):
    """兼容旧调用（MCP 版遗留），保留定义。"""
    if isinstance(res, dict) and 'result' in res and isinstance(res['result'], str):
        return json.loads(res['result'])
    return res


def parse_text(data):
    """兼容旧调用（MCP 版遗留），保留定义。"""
    return ast.literal_eval(data)


def tool_text(res):
    txt = res.get('content', [{}])[0].get('text', '{}')
    try:
        return parse_text(txt)
    except Exception:
        return {'error': txt}


def _kb():
    if KB is None:
        raise RuntimeError('请先设置 wr.KB = <目标知识库 id>（或调用 wr.load_kb()）')
    return KB


# ---------------------------------------------------------------------------
# 核心分发：MCP 工具名 → kb_compilation HTTP 端点（返回结构对齐上层脚本预期）
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Neo4j 直连（graph_add/graph_delete：平台无写入端点，worker 容器直连 kb-neo4j）
# ---------------------------------------------------------------------------
def _graph_conn():
    import os

    from neo4j import GraphDatabase

    uri = os.getenv('NEO4J_URI', 'bolt://kb-neo4j:7687')
    user = os.getenv('NEO4J_USERNAME', 'neo4j')
    pwd = os.getenv('NEO4J_PASSWORD', '')
    return GraphDatabase.driver(uri, auth=(user, pwd))


def _graph_call(name, args):
    """graph_add/graph_delete 直连 Neo4j（此前适配为 no-op，图谱从未写入）。"""
    import os

    kb = (args.get('kb_id') or '').strip()
    if not kb:
        return {'error': 'graph_add 缺少 kb_id'}
    label = 'ENTITY' + kb.replace('-', '_')
    try:
        driver = _graph_conn()
        try:
            with driver.session() as sess:
                if name == 'graph_delete':
                    kid = str(args.get('knowledge_id') or args.get('kid') or '').strip()
                    if not kid:
                        return {'error': 'graph_delete 缺少 knowledge_id'}
                    kg = 'ENTITY' + kid.replace('-', '_')
                    sess.run(f"MATCH (n:{kg}) DETACH DELETE n")
                    return {'data': {'deleted': True, 'kg': kg}}
                # graph_add
                graphs = args.get('graphs') or [args]
                kid = str(args.get('knowledge_id') or args.get('kid') or '').strip()
                kg = 'ENTITY' + kid.replace('-', '_') if kid else ''
                total_nodes = 0
                total_rels = 0
                for g in graphs:
                    nodes = g.get('node') or g.get('nodes') or []
                    rels = g.get('relation') or g.get('relations') or []
                    for nd in nodes:
                        name = str(nd.get('name') or '').strip()
                        if not name:
                            continue
                        attrs = [str(a) for a in (nd.get('attributes') or []) if a]
                        # 节点挂双 label：kb 级（全库查询）+ 文档子图级（kg 删/查）
                        if kg:
                            sess.run(
                                f"MERGE (n:{label}:{kg} {{name: $name}}) SET n.attributes = $attrs",
                                name=name, attrs=attrs,
                            )
                        else:
                            sess.run(
                                f"MERGE (n:{label} {{name: $name}}) SET n.attributes = $attrs",
                                name=name, attrs=attrs,
                            )
                        total_nodes += 1
                    for rl in rels:
                        n1 = str(rl.get('node1') or rl.get('source') or '').strip()
                        n2 = str(rl.get('node2') or rl.get('target') or '').strip()
                        typ = str(rl.get('type') or 'related')[:60]
                        if not n1 or not n2:
                            continue
                        total_rels += 1
                        # 2026-10-09 边证据链：写关系属性（rule_title/source_slugs），
                        # 让边能回溯规则卡与原文。Neo4j 关系属性只接受标量/标量数组，
                        # 不能存 Map → 摊平成独立 SET 子句（此前只 MERGE 不 SET，
                        # 导致全库边属性为空、无法回溯证据）。
                        props = rl.get('properties') or rl.get('attributes') or {}
                        flat = {
                            str(k): v for k, v in props.items()
                            if isinstance(v, (str, int, float, bool, list))
                        }
                        if flat:
                            set_clause = " SET " + ", ".join(
                                f"r.{k} = ${k}" for k in flat)
                            sess.run(
                                f"MATCH (a:{label} {{name: $n1}}), (b:{label} {{name: $n2}}) "
                                f"MERGE (a)-[r:{typ}]->(b){set_clause}",
                                n1=n1, n2=n2, **flat,
                            )
                        else:
                            sess.run(
                                f"MATCH (a:{label} {{name: $n1}}), (b:{label} {{name: $n2}}) "
                                f"MERGE (a)-[r:{typ}]->(b)",
                                n1=n1, n2=n2,
                            )
                return {'data': {'nodes': total_nodes, 'relations': total_rels}}
        finally:
            driver.close()
    except Exception as exc:  # noqa: BLE001 - 图写失败不中断构建（打印即可）
        print(f'  ⚠️ Neo4j 直连写入失败: {exc}', flush=True)
        return {'error': str(exc)[:160]}


def tool_call(name, arguments):
    """调用平台等价端点，返回与 WeKnora MCP 同构的 dict。
    ALWAYS check 'error' key —— 异常不抛出（除网络/5xx 经 _request 重试后仍失败）。"""
    args = dict(arguments or {})
    kb = args.get('kb_id') or _kb()

    if name == 'create_wiki_page':
        payload = {
            'slug': _flat_slug(args.get('slug') or ''),
            'title': args.get('title') or '',
            'content': _normalize_wikilinks(args.get('content') or ''),
            'page_type': str(args.get('page_type') or 'entity'),
            'folder_id': args.get('folder_id') or '',
            'summary': args.get('summary') or '',
            'source_refs': args.get('source_refs') or [],
        }
        r = api_post(f'/api/v1/kbs/{kb}/wiki/pages', payload)
        return {'data': r.get('data') or {}}

    if name == 'create_wiki_pages_batch':
        pages = []
        for it in (args.get('pages') or []):
            pages.append({
                'slug': _flat_slug(it.get('slug') or ''),
                'title': it.get('title') or '',
                'content': _normalize_wikilinks(it.get('content') or ''),
                'page_type': str(it.get('page_type') or 'entity'),
                'folder_id': it.get('folder_id') or '',
                'summary': it.get('summary') or '',
                'source_refs': it.get('source_refs') or [],
            })
        if not pages:
            return {'data': {'created': 0, 'errors': []}}
        # 分批（每批 40 页，避免单请求过大）
        results = {'created': 0, 'errors': []}
        for i in range(0, len(pages), 40):
            r = api_post(f'/api/v1/kbs/{kb}/wiki/pages/batch', pages[i:i + 40])
            d = r.get('data') or {}
            results['created'] += int(d.get('created') or 0)
            results['errors'].extend(d.get('errors') or [])
        return {'data': results}

    if name in ('graph_add', 'graph_delete'):
        return _graph_call(name, args)

    if name == 'list_documents':
        page = int(args.get('page', 1))
        ps = int(args.get('page_size', 100))
        r = api_get(f'/api/v1/kbs/{kb}/documents?page={page}&page_size={ps}')
        return {'data': (r.get('data') or {}).get('items') or []}

    if name == 'get_document':
        kid = (args.get('knowledge_id') or args.get('document_id') or args.get('id') or '').strip()
        page = 1
        while True:
            r = api_get(f'/api/v1/kbs/{kb}/documents?page={page}&page_size=100')
            items = (r.get('data') or {}).get('items') or []
            for d in items:
                if d.get('id') == kid:
                    return {'data': d}
            if len(items) < 100:
                break
            page += 1
        return {'error': f'文档不存在: {kid} (kb={kb})'}

    if name == 'list_chunks':
        kid = (args.get('knowledge_id') or args.get('document_id') or '').strip()
        page = int(args.get('page', 1))
        ps = int(args.get('page_size', 100))
        r = api_get(f'/api/v1/kbs/{kb}/documents/{urllib.parse.quote(kid, safe="")}/chunks')
        items = (r.get('data') or {}).get('items') or []
        # 平台返回全量（无分页）；按 page/page_size 切片保持 MCP 分页语义，
        # 避免 fetch_all_chunks 等翻页循环对 >100 切片文档死循环/重复
        start = (page - 1) * ps
        out = []
        for c in items[start:start + ps]:
            d = dict(c)
            d['chunk_index'] = c.get('seq', 0)  # seq → chunk_index（上层脚本排序键）
            out.append(d)
        return {'data': out}

    if name == 'list_wiki_folders':
        parent = args.get('parent_id', '') or ''
        folders = _cached_wiki_folders(kb)
        # MCP list_wiki_folders(parent_id) 返回直子目录；平台 /wiki 返回全量 → 本地过滤
        folders = [f for f in folders if (f.get('parent_id') or '') == parent]
        return {'folders': folders}

    if name == 'list_wiki_pages':
        return _list_wiki_pages(kb, args)

    if name == 'wiki_search':
        q = (args.get('query') or '').strip()
        limit = min(int(args.get('limit', 100)), 100)
        r = api_get(f'/api/v1/kbs/{kb}/wiki/search?q={urllib.parse.quote(q)}&limit={limit}')
        return {'data': r.get('data') or {'query': q, 'total': 0, 'items': []}}

    if name == 'wiki_read_page':
        slug = (args.get('slug') or '').strip()
        return read_page(slug, kb_id=kb)

    if name == 'create_wiki_page':
        return _create_page(kb, args)

    if name == 'update_wiki_page':
        return _update_page(kb, args)

    if name == 'delete_wiki_page':
        slug = (args.get('slug') or '').strip()
        flat = _flat_slug(slug)
        try:
            r = api_delete(_page_url(kb, flat))
            _PAGE_CACHE.pop((kb, flat), None)
            return {'data': r.get('data') or {}, 'slug': slug}
        except RuntimeError as e:
            return {'error': str(e)}

    if name == 'create_wiki_folder':
        payload = {'name': (args.get('name') or '').strip()}
        if args.get('parent_id'):
            payload['parent_id'] = str(args['parent_id'])
        try:
            r = api_post(f'/api/v1/kbs/{kb}/wiki/folders', payload)
            d = r.get('data') or {}
            return {'data': d, 'folder': d, 'id': d.get('id', '')}
        except RuntimeError as e:
            return {'error': str(e)}

    if name == 'update_wiki_folder':
        fid = (args.get('folder_id') or '').strip()
        payload = {}
        if args.get('name') is not None:
            payload['name'] = str(args['name'])
        if args.get('parent_id') is not None:
            payload['parent_id'] = str(args['parent_id'])
        try:
            r = api_put(f'/api/v1/kbs/{kb}/wiki/folders/{urllib.parse.quote(fid, safe="")}', payload)
            return {'data': r.get('data') or {}}
        except RuntimeError as e:
            return {'error': str(e)}

    if name == 'delete_wiki_folder':
        fid = (args.get('folder_id') or '').strip()
        try:
            r = api_delete(f'/api/v1/kbs/{kb}/wiki/folders/{urllib.parse.quote(fid, safe="")}')
            return {'data': r.get('data') or {}}
        except RuntimeError as e:
            return {'error': str(e)}

    if name == 'move_wiki_page':
        slug = (args.get('slug') or '').strip()
        fid = args.get('folder_id', '') or ''
        try:
            r = api_put(_page_url(kb, _flat_slug(slug)), {'folder_id': str(fid)})
            return {'data': r.get('data') or {}}
        except RuntimeError as e:
            return {'error': str(e)}

    if name == 'wiki_rebuild_links':
        try:
            r = api_post(f'/api/v1/kbs/{kb}/wiki/rebuild-links', {})
            return {'data': r.get('data') or {}}
        except RuntimeError as e:
            return {'error': str(e)}

    if name == 'wiki_stats':
        try:
            r = api_get(f'/api/v1/kbs/{kb}/wiki/stats')
            return {'data': r.get('data') or {}}
        except RuntimeError as e:
            return {'error': str(e)}

    if name == 'wiki_lint':
        try:
            r = api_get(f'/api/v1/kbs/{kb}/wiki/lint')
            return {'data': r.get('data') or {}}
        except RuntimeError as e:
            return {'error': str(e)}

    if name == 'tokenize':
        return tokenize(args.get('text', ''), kb_id=kb)

    if name == 'wiki_log_write':
        return log_progress(
            action=(args.get('action') or ''), knowledge_id=(args.get('knowledge_id') or ''),
            doc_title=(args.get('doc_title') or ''), summary=(args.get('summary') or ''),
            kb_id=kb, page_slugs=args.get('page_slugs'))

    if name in ('graph_add', 'graph_delete'):
        # 平台无 Neo4j 写入 HTTP 端点（WikiBrowser 图谱是 wiki_link 数据）→ no-op
        print('  ⚠️ kb_compilation 无 graph 写入端点，graph_add/graph_delete 为 no-op'
              '（2026-10-06 适配注记，不阻断 wiki 构建）')
        return {'success': True, 'note': 'no-op: kb_compilation 无 Neo4j 写入端点'}

    if name == 'list_knowledge_bases':
        r = api_get('/api/v1/kbs')
        return {'data': (r.get('data') or {}).get('items') or []}

    if name == 'wiki_create_pages':
        # 批量建页：逐条走 create_wiki_page（平台无批量端点）
        created = []
        for pg in args.get('pages') or []:
            sub = dict(args)
            sub.update(pg)
            created.append(tool_call('create_wiki_page', sub))
        return {'data': created}

    raise RuntimeError(f'weknora_rpc 适配版未映射的 MCP 工具名: {name}（arguments={json.dumps(args, ensure_ascii=False)[:200]}）')


def _list_wiki_pages(kb, args):
    """分页拉取 wiki 页面并补齐 content。平台 /wiki 全量无正文 → 逐页 GET 补齐（缓存）。"""
    page = max(1, int(args.get('page', 1)))
    ps = int(args.get('page_size', 200))
    r = api_get(f'/api/v1/kbs/{kb}/wiki')
    all_pages = (r.get('data') or {}).get('pages') or []
    start = (page - 1) * ps
    slice_pages = all_pages[start:start + ps]
    out = []
    for p in slice_pages:
        flat = (p.get('slug') or '')
        detail = _page_detail(kb, flat) or {}
        merged = dict(detail)
        for k, v in p.items():          # 树列表项字段兜底（summary/folder_id/...）
            merged.setdefault(k, v)
        merged['slug'] = _script_slug(flat)
        merged.setdefault('status', 'active')
        out.append(merged)
    return {'pages': out, 'total': len(all_pages)}


def _create_page(kb, args):
    slug = (args.get('slug') or '').strip()
    title = (args.get('title') or '').strip()
    payload = {
        'slug': _flat_slug(slug),
        'title': title,
        'content': str(args.get('content') or ''),
        'page_type': str(args.get('page_type') or 'entity'),
        'folder_id': str(args.get('folder_id') or ''),
    }
    if args.get('summary'):
        payload['summary'] = str(args['summary'])
    # source_refs：平台 API 未暴露该字段（DB 列存在），静默丢弃（见模块 docstring 差异 3）
    try:
        r = api_post(f'/api/v1/kbs/{kb}/wiki/pages', payload)
        d = r.get('data') or {}
        page = dict(d)
        page.update({'title': title, 'content': payload['content'],
                     'page_type': payload['page_type'], 'folder_id': payload['folder_id'],
                     'status': 'active', 'source_refs': []})
        _cache_write(kb, _flat_slug(slug), page)
        return {'data': d, 'slug': slug, 'id': d.get('id', '')}
    except RuntimeError as e:
        return {'error': str(e)}


def _update_page(kb, args):
    slug = (args.get('slug') or '').strip()
    flat = _flat_slug(slug)
    payload = {}
    if args.get('title') is not None:
        payload['title'] = str(args['title'])
    if args.get('content') is not None:
        payload['content'] = str(args['content'])
    if args.get('summary') is not None:
        payload['summary'] = args['summary']
    if args.get('page_type') is not None:
        payload['page_type'] = str(args['page_type'])
    if args.get('folder_id') is not None:
        payload['folder_id'] = str(args['folder_id'])
    st = _map_status(args.get('status'))
    if st:
        payload['status'] = st
    if not payload:
        return {'data': {}, 'slug': slug, 'note': 'no-op: 无可更新字段'}
    try:
        r = api_put(_page_url(kb, flat), payload)
        d = r.get('data') or {}
        # 写穿缓存（合并已缓存字段）
        key = (kb, flat)
        if key in _PAGE_CACHE:
            _PAGE_CACHE[key].update(payload)
        return {'data': d, 'slug': slug}
    except RuntimeError as e:
        return {'error': str(e)}


# ---------------------------------------------------------------------------
# 常用工具快捷函数（kb 用模块级 KB，或显式传参覆盖）—— 签名与 MCP 版完全一致
# ---------------------------------------------------------------------------
def list_docs(kb_id=None, page=1, page_size=100):
    return tool_call('list_documents', {"kb_id": kb_id or _kb(), "page": page, "page_size": page_size})


def list_chunks(kid, kb_id=None, page=1, page_size=100):
    return tool_call('list_chunks', {"kb_id": kb_id or _kb(), "knowledge_id": kid,
                                     "page": page, "page_size": page_size})


def list_folders(kb_id=None, parent_id=''):
    return tool_call('list_wiki_folders', {"kb_id": kb_id or _kb(), "parent_id": parent_id})


def list_wiki_pages(kb_id=None, page=1, page_size=200):
    """分页拉取 wiki 页面（content 已补齐）。注意：wiki_search 只返回前 N 条，
    全量扫描必须用 list_wiki_pages 分页！"""
    return tool_call('list_wiki_pages', {"kb_id": kb_id or _kb(), "page": page, "page_size": page_size})


def wiki_search(query, kb_id=None, limit=100):
    return tool_call('wiki_search', {"kb_id": kb_id or _kb(), "query": query, "limit": limit})


def walk_folders(kb_id=None, parent_id='', collect=None):
    """递归遍历目录树（逐层展开交叉核对）。collect: dict 收集 {folder_id: name}。"""
    if collect is None:
        collect = {}
    r = list_folders(kb_id=kb_id, parent_id=parent_id)
    for f in r.get('folders', []):
        collect[f['id']] = f['name']
        walk_folders(kb_id=kb_id, parent_id=f['id'], collect=collect)
    return collect


def create_folder(name, parent_id='', kb_id=None):
    """建目录。注意参数顺序：parent_id 在第二位（kb_id 用关键字或模块 KB）。"""
    return tool_call('create_wiki_folder', {"kb_id": kb_id or _kb(), "name": name, "parent_id": parent_id})


def update_folder(folder_id, kb_id=None, name=None, parent_id=None):
    """更新目录（重命名/重挂父目录）。"""
    args = {"kb_id": kb_id or _kb(), "folder_id": folder_id}
    if name is not None:
        args['name'] = name
    if parent_id is not None:
        args['parent_id'] = parent_id
    return tool_call('update_wiki_folder', args)


def delete_folder(folder_id, kb_id=None):
    return tool_call('delete_wiki_folder', {"kb_id": kb_id or _kb(), "folder_id": folder_id})


def move_page(slug, kb_id=None, folder_id=''):
    return tool_call('move_wiki_page', {"kb_id": kb_id or _kb(), "slug": slug, "folder_id": folder_id})


def create_page(slug, title, content, folder_id, kb_id=None, page_type=None, summary='', source_refs=None):
    """Create a wiki page. page_type is REQUIRED — pass 'business_ontology', 'rule_ontology',
    'summary', 'original_sentence', or 'frequent_keyword' 等（本技能约定）。"""
    if page_type is None:
        raise ValueError("page_type is required for create_page. Use 'business_ontology' for entity-b, "
                         "'rule_ontology' for entity-r.")
    args = {"kb_id": kb_id or _kb(), "slug": slug, "title": title, "content": _normalize_wikilinks(content),
            "folder_id": folder_id, "page_type": page_type, "summary": summary}
    if source_refs:
        args['source_refs'] = source_refs  # 平台不可写，tool_call 内静默丢弃
    return tool_call('create_wiki_page', args)


def update_page(slug, kb_id=None, content=None, folder_id=None, summary=None,
                source_refs=None, page_type=None, aliases=None,
                title=None, status=None, folder_ids=None):
    """更新 wiki 页面（slug 不可改）。CRITICAL：始终传 folder_id（平台 PUT 仅更新传入字段，
    不传 folder_id 不会重置目录——与 MCP 版行为相反但更安全）。
    source_refs/folder_ids/aliases 平台 API 未暴露 → 静默丢弃（见 docstring 差异 3）。"""
    args = {"kb_id": kb_id or _kb(), "slug": slug}
    if content is not None:
        args['content'] = _normalize_wikilinks(content)
    if folder_id is not None:
        args['folder_id'] = folder_id
    if summary is not None:
        args['summary'] = summary
    if page_type is not None:
        args['page_type'] = page_type
    if title is not None:
        args['title'] = title
    if status is not None:
        args['status'] = status
    return tool_call('update_wiki_page', args)


def read_page(slug, kb_id=None):
    """读页面：返回页面 dict（含 content/title/source_refs/folder_id/status）。
    不存在 → {'error': ...}（维持「页面不存在」语义，幂等查重靠 'error' not in r）。"""
    kb = kb_id or _kb()
    flat = _flat_slug(slug)
    data = _page_detail(kb, flat, use_cache=False)  # 读回核验要新鲜数据
    if data is None:
        return {'error': f'404 page not found: {slug} (kb={kb})'}
    p = dict(data)
    p['slug'] = _script_slug(p.get('slug') or flat)
    p.setdefault('status', 'active')
    return p


def delete_page(slug, kb_id=None):
    return tool_call('delete_wiki_page', {"kb_id": kb_id or _kb(), "slug": slug})


def rebuild_links(kb_id=None):
    return tool_call('wiki_rebuild_links', {"kb_id": kb_id or _kb()})


def log_progress(action, knowledge_id='', doc_title='', summary='', kb_id=None, page_slugs=None):
    """写入一条 wiki 构建进度日志。平台无 wiki_log 写端点 → 写本地 JSONL + stdout。
    失败仅告警不阻断。action 建议值：agent_build_start/dirs/summary/entities/keywords/done。"""
    from datetime import datetime
    rec = {'ts': datetime.now().isoformat(timespec='seconds'), 'kb_id': kb_id or KB,
           'action': action, 'knowledge_id': knowledge_id, 'doc_title': doc_title,
           'summary': summary, 'page_slugs': list(page_slugs or [])}
    line = json.dumps(rec, ensure_ascii=False)
    print(f'  [wiki-log] {line}')
    tried = []
    for d in (os.environ.get('KB_WIKI_LOG_DIR', '/data/kb_documents/logs/jobs'), '/tmp/kb_wiki_logs'):
        tried.append(d)
        try:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, 'wiki_build_progress.jsonl'), 'a', encoding='utf-8') as f:
                f.write(line + '\n')
            return {'success': True, 'logged': line, 'dir': d}
        except Exception as e:
            print(f'  ⚠️ 进度日志写 {d} 失败(不阻断): {e}')
    return {'success': False, 'error': f'日志目录不可写: {tried}', 'logged': line}


def find_file(file_name, kb_id=None):
    """按 file_name 找文件，返回 doc dict 或 None（支持精确/包含匹配）。"""
    kb = kb_id or _kb()
    page = 1
    while True:
        r = list_docs(kb_id=kb, page=page)
        data = r.get('data') or []
        for d in data:
            if d.get('file_name') == file_name:
                return d
        if len(data) < 100:
            break
        page += 1
    page = 1
    while True:
        r = list_docs(kb_id=kb, page=page)
        data = r.get('data') or []
        for d in data:
            if file_name in (d.get('file_name') or ''):
                return d
        if len(data) < 100:
            break
        page += 1
    return None


def fetch_all_chunks(kid, kb_id=None):
    """分页拉全切片，按 chunk_index 排序，返回 [{'content','chunk_index'}, ...]"""
    chunks, page = [], 1
    while True:
        r = list_chunks(kid, kb_id=kb_id, page=page)
        data = r.get('data') or []
        chunks.extend([{'content': c.get('content', ''), 'chunk_index': c.get('chunk_index', 0)}
                       for c in data])
        if len(data) < 100:
            break
        page += 1
    chunks.sort(key=lambda c: c['chunk_index'])
    return chunks


def clean_chunk_text(text):
    """清洗切片：去 markdown 锚点链接、图片 resource 标记、纯页码行与 markdown 标题标记。"""
    text = re.sub(r'\[([^\]]*)\]\(#[^)]*\)', r'\1', text)  # [..](#_Toc..) -> ..
    text = re.sub(r'!\[[^\]]*\]\(resource://[^)]*\)', '', text)  # 图片 resource 标记
    # PDF 页码线（2026-09-07：法规 PDF 页脚「— ８４３ —」，行级删除）
    text = re.sub(r'^\s*[-—－]\s*[\d０-９0-9\s]{1,6}\s*[-—－]\s*$', '', text, flags=re.MULTILINE)
    # markdown 标题标记（docx 转 markdown 的 # 残留）
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'#{2,6}', '', text)
    lines = [ln for ln in text.splitlines() if ln.strip() and not re.fullmatch(r'\[\d+\]', ln.strip())]
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# LLM 调用（统一重试+截断处理，与 MCP 版同构；端点默认 inferaiapi）
# ---------------------------------------------------------------------------
def tokenize(text, kb_id=None):
    """分词工具快捷函数。平台无 tokenize 端点 → 本地实现：
    jieba 优先（容器 venv 无则尝试系统 site-packages），缺失回退 CJK 单字+相邻二字组。
    返回 {'data': {'words': [...]}}（build_full 第 7 步只取 2 字词做频次补充）。"""
    words = _local_tokens(text or '')
    return {'data': {'words': words, 'count': len(words)}}


def _local_tokens(text):
    tok = None
    try:
        import jieba as _jb
        tok = _jb.lcut
    except Exception:
        for sp in ('/usr/local/lib/python3.11/site-packages', '/usr/local/lib/python3/dist-packages'):
            if os.path.isdir(os.path.join(sp, 'jieba')) and sp not in sys.path:
                sys.path.append(sp)
        try:
            import jieba as _jb
            tok = _jb.lcut
        except Exception:
            tok = None
    if tok:
        try:
            return [w.strip() for w in tok(text) if w and w.strip()]
        except Exception:
            pass
    # 无分词器回退：CJK 连续段 → 单字 + 相邻二字组（build_full 只取 len==2 的词）
    out = []
    for seg in re.findall(r'[\u4e00-\u9fff]+|[A-Za-z0-9]+', text):
        if re.fullmatch(r'[\u4e00-\u9fff]+', seg):
            out.extend(seg)
            out.extend(seg[i:i + 2] for i in range(len(seg) - 1))
        else:
            out.append(seg)
    return out


_last_llm_ts = [0.0]

# ---------------------------------------------------------------------------
# LLM 调用事件埋点（2026-10-06 可观测性）：每次调用/重试写结构化 JSONL
# ---------------------------------------------------------------------------
_phase_ctx = {"name": ""}
_ev_log_path_cache = [None]


def set_phase(name):
    """标记当前构建阶段（long_sentences/terms/filter_terms/extract_joint/rules/…）"""
    _phase_ctx["name"] = name


def measure(step):
    """构建步骤计时器：with wr.measure('gen_summary'): ...  → 写 start/done/fail + 耗时 ms 事件

    与 LLM 调用事件同通道（WIKI_EVENTS_LOG），kind=step 区分；平台聚合后
    任务详情页展示「构建步骤时间线」（步骤/耗时/状态）。幂等嵌套安全。
    """
    import contextlib

    @contextlib.contextmanager
    def _cm():
        t0 = time.time()
        _log_step(step, "start", 0)
        try:
            yield
            _log_step(step, "done", int((time.time() - t0) * 1000))
        except BaseException:
            _log_step(step, "fail", int((time.time() - t0) * 1000))
            raise

    return _cm()


def _log_step(step, status, ms):
    ev = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "step",
          "step": step, "status": status, "ms": ms}
    try:
        with open(_event_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _event_log_path():
    if _ev_log_path_cache[0] is None:
        _ev_log_path_cache[0] = (
            os.environ.get("WIKI_EVENTS_LOG")
            or f"/tmp/wiki_events_{os.getpid()}.jsonl"
        )
    return _ev_log_path_cache[0]


def _log_llm_event(**kw):
    ev = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "phase": _phase_ctx["name"]}
    ev.update(kw)
    try:
        with open(_event_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass


@contextlib.contextmanager
def measure(step):
    """构建步骤计时埋点：写 step 事件（start/done/fail + wall-clock 耗时 ms）。

    用法：with wr.measure('gen_summary'): ...   —— 步骤耗时进 WIKI_EVENTS_LOG，
    平台聚合后任务详情页展示「构建步骤时间线」。
    """
    t0 = time.time()
    _log_llm_event(kind="step", step=step, status="start")
    try:
        yield
        _log_llm_event(kind="step", step=step, status="done",
                       ms=int((time.time() - t0) * 1000))
    except Exception:
        _log_llm_event(kind="step", step=step, status="fail",
                       ms=int((time.time() - t0) * 1000))
        raise


def llm_call(messages, model=None, max_tokens=2048, temperature=0.1,
             retries=5, base_delay=3, timeout=600):
    """调用 LLM API 的通用函数，带重试和 JSON 截断处理。

    返回 content 文本；全部重试失败抛 RuntimeError。
    自动处理 403/429/5xx 退避重试；finish_reason=length 0 字符截断内部重试；
    enable_thinking=false 避免 reasoning 耗尽 max_tokens。
    每次调用/重试写入事件 JSONL（WIKI_EVENTS_LOG 或 /tmp/wiki_events_<pid>.jsonl）。
    """
    base_url, api_key = llm_config()
    model = model or DEFAULT_MODEL

    # inferaiapi group 限流 ~3 次/分：距上次调用不足 15s 则等待补齐（全局节流，
    # 2026-10-07 25→15s 提速；限流由 llm_call 重试收敛兜底）
    _now = time.time()
    _gap = _now - _last_llm_ts[0]
    wait_ms = 0
    if _gap < 15:
        wait_ms = int((15 - _gap) * 1000)
        time.sleep(15 - _gap)
        _now = time.time()
    _last_llm_ts[0] = _now

    url = f'{base_url}/chat/completions'
    payload = {
        'model': model,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
        'enable_thinking': False,
    }
    data = json.dumps(payload).encode()
    # inferaiapi.com 前置 Cloudflare 拦截 urllib 默认 UA（403/1010）→ 浏览器 UA
    req = urllib.request.Request(url, data, {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'})

    last_err = None
    t_start = time.time()
    retry_events = []

    def _llm_raw(req, sock_to):
        """单次 HTTP 请求：返回响应体文本。socket 超时只作用于单次 recv 等待——
        服务器慢流持续发字节时 recv 永不超时（2026-10-07 实测 8h+ hang）。
        """
        with urllib.request.urlopen(req, timeout=sock_to) as r:
            return r.read().decode()

    for attempt in range(retries):
        # 总时长守卫：daemon 线程执行 + Queue.get(总超时)——无论 read 怎么挂起
        # （慢流/死连接），到点抛 TimeoutError 走网络重试；挂起线程成为 daemon
        # 孤儿无害回收。__import__ 幂等，避免重复 import。
        q = __import__("queue").Queue()
        wt = __import__("threading").Thread(
            target=lambda: q.put(_llm_raw(req, timeout)), daemon=True)
        wt.start()
        total_to = min(timeout + 60, 900)
        try:
            raw = q.get(timeout=total_to)
        except __import__("queue").Empty:
            # 慢流不重试（2026-10-07）：660s 已是极慢——重试只再等一个 660s，
            # 且会把批级 pool.shutdown(wait=True) 拖入 55min 级阻塞被 watchdog 杀。
            # 直接抛走网络重试（上层批循环可换批继续）。
            _log_llm_event(kind="error", model=model, llm_ms=int((time.time() - t_start) * 1000),
                           error=f"slow_stream_timeout >{total_to}s")
            print(f"  ⛔ LLM 总时长超限 {total_to}s（慢流挂起），抛错")
            raise TimeoutError(f"LLM 响应超过 {total_to}s 未完整")
        try:
            resp = json.loads(raw)
            choice = resp['choices'][0]
            content = (choice['message'].get('content') or '').strip()
            finish = choice.get('finish_reason', '')

            if finish == 'length' and not content:
                print('  ⚠️ LLM 输出为空 (finish_reason=length, 0 chars)，内部重试')
                time.sleep(base_delay)
                retry_events.append({"reason": "empty_length", "backoff_s": base_delay})
                last_err = RuntimeError('empty content (finish_reason=length)')
                continue
            if finish == 'length':
                print(f'  ⚠️ LLM 输出被截断 (finish_reason=length, {len(content)} chars)')
            _log_llm_event(
                kind="ok", model=model, llm_ms=int((time.time() - t_start) * 1000),
                wait_ms=wait_ms, retries=len(retry_events), retry_events=retry_events,
                content_len=len(content), finish=finish)
            return content

        except urllib.error.HTTPError as e:
            code = e.code
            if code in (403, 429, 500, 502, 503, 520, 521, 522, 524):
                # 退避上限 45s + 重试次数 5：最长退避 ~3 分钟 < run_one 阶段超时，
                # 避免 LLM 限流重试耗尽 450s 被判 gen_summary 超时（2026-10-07 实证）
                delay = min(max(15, base_delay * (2 ** attempt)), 45)
                _log_llm_event(kind="retry", model=model, attempt=attempt + 1,
                               reason=f"http_{code}", backoff_s=delay)
                print(f'  ⏳ LLM {code} (rate limit), 等待 {delay}s 重试 ({attempt + 1}/{retries})')
                time.sleep(delay)
                retry_events.append({"reason": f"http_{code}", "backoff_s": delay})
                last_err = e
                continue
            raise  # 其他 HTTP 错误直接抛出

        except (urllib.error.URLError, TimeoutError, ConnectionResetError) as e:
            delay = base_delay * (attempt + 1)
            print(f'  ⏳ LLM 网络错误: {e}, 等待 {delay}s 重试 ({attempt + 1}/{retries})')
            time.sleep(delay)
            retry_events.append({"reason": "network", "backoff_s": delay})
            last_err = e
            continue

        except json.JSONDecodeError as e:
            delay = base_delay * (attempt + 1)
            print(f'  ⏳ LLM 响应解析失败: {e}, 等待 {delay}s 重试 ({attempt + 1}/{retries})')
            time.sleep(delay)
            retry_events.append({"reason": "json_decode", "backoff_s": delay})
            last_err = e
            continue

    _log_llm_event(
        kind="error", model=model, llm_ms=int((time.time() - t_start) * 1000),
        wait_ms=wait_ms, retries=len(retry_events), retry_events=retry_events,
        error=str(last_err)[:200])
    raise RuntimeError(f'LLM 调用失败，已重试 {retries} 次: {last_err}')


def ensure_full_json(text):
    """尝试修复截断的 JSON 数组（LLM 输出被 max_tokens 截断时使用）。"""
    text = text.strip()
    if '```json' in text:
        text = text.split('```json')[1].split('```')[0].strip()
    elif '```' in text:
        text = text.split('```')[1].split('```')[0].strip()
    try:
        json.loads(text)
        return text  # 完整有效
    except json.JSONDecodeError:
        pass
    depth = 0
    last_complete = 0
    for i, ch in enumerate(text):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                last_complete = i + 1
    if last_complete > 0:
        candidate = text[:last_complete]
        if not candidate.endswith(']'):
            if candidate.rstrip().endswith(','):
                candidate = candidate.rstrip()[:-1]
            candidate += '\n]'
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    return text  # 无法修复，返回原文


# ---------------------------------------------------------------------------
# 展示/文本工具（与 MCP 版完全一致）
# ---------------------------------------------------------------------------
_CHAT_TAIL_LINE_RE = re.compile(r'^\s*(?:😊[^\n]*|有什么(?:我可以|需要我)帮[^\n]*)\s*$', re.MULTILINE)


def clean_llm_tail(text):
    """清理 LLM 自由文本输出的尾部聊天残留。只删除文尾的独立问候行。"""
    if not text:
        return text
    matches = list(_CHAT_TAIL_LINE_RE.finditer(text))
    if matches:
        last = matches[-1]
        if not text[last.end():].strip():
            text = text[:last.start()].rstrip() + '\n'
    return text


def bookname(name):
    """文件名/制度名 → 展示用书名号包装（防书名号嵌套）。文件已含《》→ 原样返回。"""
    name = (name or '').strip()
    if not name:
        return ''
    return name if ('《' in name and '》' in name) else f'《{name}》'


def rewrite_source_line(content, refs):
    """重写 entity 页基本信息表「来源制度」行为来源文档并集。幂等。"""
    if not refs or '来源制度' not in (content or ''):
        return content
    names = []
    for r in refs:
        fname = r.split('|', 1)[1] if '|' in r else r
        n = bookname(fname)
        if n and n not in names:
            names.append(n)
    if not names:
        return content
    joined = '；'.join(names)
    m = re.search(r'## 基本信息(.*?)(\n## |\Z)', content, re.S)
    if not m:
        return content
    seg = m.group(1)
    new_seg = re.sub(r'^\| 来源制度 \|.*\|$', f'| 来源制度 | {joined} |', seg, count=1, flags=re.M)
    if new_seg == seg:
        return content
    return content[:m.start(1)] + new_seg + content[m.end(1):]


if __name__ == '__main__':
    load_config()
    mcp_init()
    print(f'API 入口: {URL}')
    print(f'知识库: {KB}')
    st = tool_call('wiki_stats', {'kb_id': KB})
    print(f'wiki stats: {json.dumps(st.get("data", st), ensure_ascii=False)[:300]}')
