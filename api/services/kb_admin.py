"""Knowledge base management service — CRUD + documents + wiki browsing.

Owns the read/write paths the frontend needs beyond RAG QA:
- KB CRUD (kb_datasource), with a delete guard (a KB that still has
  documents cannot be deleted — mirrors WeKnora's vectorstore guard)
- document listing / delete (kb_document + doc_chunk cleanup)
- wiki browsing (folder tree + page detail with links)
- JSON hybrid search (non-streaming variant of the QA retrieval)

All functions take explicit sessions (knowledge store unless noted), so
Celery tasks and FastAPI routers reuse the same logic without globals.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import case, cast, delete, func, or_, select, update
from sqlalchemy import String as _String
from sqlalchemy.orm import Session

from api.config import get_settings
from api.models.framework import User
from api.models.knowledge import (
    DocChunk,
    DocFolder,
    KbAgent,
    KbDatasource,
    KbDocument,
    WIKI_FEEDBACK_PRESETS,
    WIKI_FEEDBACK_STATUSES,
    WIKI_FEEDBACK_TYPES,
    WikiFeedback,
    WikiFolder,
    WikiLink,
    WikiOperationLog,
    WikiPage,
    WikiPageIssue,
)
from api.services.retrieval import ChunkHit, hybrid_search
from api.services import storage
from api.services.wiki import slugify
from api.services.kb_config import (
    KB_TYPES,
    normalize_asr_config,
    normalize_extract_config,
    normalize_indexing_strategy,
    normalize_question_generation_config,
    normalize_storage_provider_config,
    normalize_vlm_config,
    normalize_wiki_config,
)

logger = logging.getLogger(__name__)


def _uuid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Knowledge base CRUD
# ---------------------------------------------------------------------------


def kb_visible_clauses(
    caller_user_id: str, caller_team_name: str, is_sys_admin: bool
) -> list:
    """SQL OR-clauses for KB visibility (personal=owner / team / system+admin)."""
    from api.services.scope import visible_clauses

    return visible_clauses(caller_user_id, caller_team_name, is_sys_admin, KbDatasource)


def list_kbs(
    db: Session,
    page: int = 1,
    page_size: int = 10,
    keyword: str = "",
    team_name: str | None = None,
    *,
    caller_user_id: str = "",
    caller_team_name: str = "",
    is_sys_admin: bool = False,
    scope: str | None = None,
) -> dict:
    """Paginated KB list with doc/page counts (state='1' only).

    With caller context, only KBs the caller may see are returned
    (personal=owner, team=team members, system=admins).
    """
    stmt = select(KbDatasource).where(KbDatasource.state == "1")
    if caller_user_id or caller_team_name or is_sys_admin:
        clauses = kb_visible_clauses(caller_user_id, caller_team_name, is_sys_admin)
        if clauses:
            stmt = stmt.where(clauses[0])
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(KbDatasource.name.ilike(like))
    if team_name:
        stmt = stmt.where(KbDatasource.team_name == team_name)
    if scope:
        stmt = stmt.where(KbDatasource.scope == scope)

    # 计数走 SQL count（原 len(...all()) 会把整表 ORM 对象物化出来，KB 越多越慢）
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    rows = (
        db.execute(
            stmt.order_by(KbDatasource.created_at.desc(), KbDatasource.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    # 文档/Wiki 页计数：原实现每个 KB 行 2 次 count 查询（一页 20 个 = 40 次额外查询），
    # 改为对当前页 kb_id 集合各做一次 group by 聚合（消除 N+1）。
    kb_ids = [kb.id for kb in rows]
    doc_counts: dict[str, int] = {}
    page_counts: dict[str, int] = {}
    keyword_counts: dict[str, int] = {}
    feedback_counts: dict[str, int] = {}
    if kb_ids:
        # 2026-10-23 性能审计：四组计数整块走 15s 进程内缓存（stale-while-revalidate：
        # 过期先返回旧值 + 后台刷新，页面请求恒定毫秒级）。计数 SQL 会全扫含
        # MEDIUMTEXT content 的大表（doc_chunk/wiki_page），远端库缓冲池小、冷
        # 索引页读取秒级（热时 100-300ms）；卡片计数（文件/Wiki/反馈数）容忍
        # 短时陈旧。fn 自建 session（后台刷新线程无请求上下文）。
        from api.services.agg_cache import agg_cached_swr

        def _load_counts(s) -> tuple[dict, dict, dict, dict]:
            c_doc = dict(
                s.execute(
                    select(KbDocument.kb_id, func.count())
                    .where(KbDocument.kb_id.in_(kb_ids))
                    .group_by(KbDocument.kb_id)
                ).all()
            )
            # 高频词页计数（page_type=frequent_keyword）+ 总页数：一次条件聚合产出
            # （2026-10-21 合并，原两次 group-by 各自全扫 wiki_page 命中集）。
            keyword_rows = s.execute(
                select(
                    WikiPage.kb_id,
                    func.count(),
                    func.coalesce(
                        func.sum(case((WikiPage.page_type == "frequent_keyword", 1), else_=0)),
                        0,
                    ),
                )
                .where(
                    WikiPage.kb_id.in_(kb_ids),
                    WikiPage.status == "active",
                )
                .group_by(WikiPage.kb_id)
            ).all()
            c_page = {kb_id: total for kb_id, total, _kw in keyword_rows}
            c_kw = {kb_id: int(kw) for kb_id, _total, kw in keyword_rows}
            # 反馈计数（未软删）：知识库卡片「反馈 N」统计 → 维护中心
            c_fb = dict(
                s.execute(
                    select(WikiFeedback.kb_id, func.count())
                    .where(WikiFeedback.kb_id.in_(kb_ids), WikiFeedback.deleted_at.is_(None))
                    .group_by(WikiFeedback.kb_id)
                ).all()
            )
            return c_doc, c_page, c_kw, c_fb

        doc_counts, page_counts, keyword_counts, feedback_counts = agg_cached_swr(
            "kbs_counts:" + ",".join(sorted(kb_ids)), 15.0, _load_counts, db=db
        )

    items = [
        _kb_dict(
            kb,
            doc_count=doc_counts.get(kb.id, 0),
            page_count=page_counts.get(kb.id, 0),
            keyword_count=keyword_counts.get(kb.id, 0),
            feedback_count=feedback_counts.get(kb.id, 0),
        )
        for kb in rows
    ]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def get_kb(db: Session, kb_id: str) -> KbDatasource | None:
    return db.execute(
        select(KbDatasource).where(KbDatasource.id == kb_id, KbDatasource.state == "1")
    ).scalars().first()


def _validate_bound_model(
    db: Session,
    model_id: str | None,
    label: str,
    caller_user_id: str | None,
    caller_team_name: str | None,
    is_sys_admin: bool,
) -> None:
    """KB 绑定的模型必须对调用方可见（personal=属主 / team=同队 / system=管理员），
    否则拒绝——防止跨用户/跨团队盗用他人模型凭据。"""
    if not model_id:
        return
    from api.services.models import get_model as get_mdl
    from api.services.scope import ResourceRow, can_see

    m = get_mdl(db, model_id)
    if m is None:
        raise ValueError(f"{label} 模型不存在: {model_id}")
    if not can_see(
        ResourceRow.from_obj(m),
        caller_user_id or "",
        caller_team_name or "",
        bool(is_sys_admin),
    ):
        raise ValueError(f"{label} 模型不可见，请选择自己有权限的模型")


def create_kb(
    db: Session,
    name: str,
    label: str | None = None,
    description: str | None = None,
    indexing_strategy: dict | None = None,
    team_name: str | None = None,
    created_by: str | None = None,
    scope: str = "system",
    owner_user_id: str | None = None,
    type: str = "document",
    custom_wiki_generation: bool = False,
    embedding_model_id: str | None = None,
    summary_model_id: str | None = None,
    storage_backend_id: str | None = None,
    vector_store_id: str | None = None,
    configs: dict | None = None,
    ontology_schema_name: str | None = None,
    build_tape_id: str | None = None,
    summary_strategy: str | None = None,
    caller_user_id: str | None = None,
    caller_team_name: str | None = None,
    is_sys_admin: bool = False,
    # ── 代码仓库知识库（2026-10，迁移 0029）──
    repo_type: str = "",
    repo_url: str = "",
    repo_branch: str = "",
    repo_auth_type: str = "none",
    repo_auth_secret: str = "",
    # ── 知识聚合配置（2026-10-25）：主题聚类/版本聚类开关 + 主题列表 + 定时频率
    aggregation_config: dict | None = None,
    # ── 切片智能增强配置（2026-10-25）：开启后解析完成触发增强编排
    enhancement_config: dict | None = None,
) -> KbDatasource:
    """Create a KB; name is required, id is generated if absent.

    scope: personal(owner_user_id) / team(team_name) / system(admin).
    indexing_strategy 归一为 WeKnora 四路开关（缺失键用 WeKnora 默认）。
    configs 为其余 JSON 配置（wiki_config/extract_config/question_generation_config 等）。
    build_tape_id: 构建引擎 = 服务编排中已发布的编排程序（kb_tape.id），
    文档解析成功后自动按该编排构建 wiki。
    """
    from api.services.kb_config import normalize_indexing_strategy

    cfg = dict(configs or {})
    if not name or not name.strip():
        raise ValueError("知识库名称不能为空")
    name = name.strip()
    if vector_store_id:
        _validate_vector_store_ref(db, vector_store_id)
    if build_tape_id:
        _validate_build_tape_ref(db, build_tape_id)
    strategy = str(summary_strategy or "auto").strip() or "auto"
    if strategy not in SUMMARY_STRATEGIES:
        raise ValueError(f"摘要策略不合法: {strategy}（可选 off/auto/stuff/mapreduce/refine）")
    # 2026-10-28：LLM 必须配置——知识库创建时强制绑定（摘要/增强/wiki 合成
    # 都从知识库 summary_model_id 取模型，不再回落注册表系统默认/env；未配置则
    # 运行时 LLM 全断）。
    if not summary_model_id:
        raise ValueError("创建知识库必须配置「大语言模型（LLM）」（summary_model_id）")
    _validate_bound_model(
        db, embedding_model_id, "向量化", caller_user_id, caller_team_name, is_sys_admin
    )
    _validate_bound_model(
        db, summary_model_id, "摘要", caller_user_id, caller_team_name, is_sys_admin
    )
    # 代码仓库知识库校验（2026-10）：repo_type=git 时仓库地址必填、凭证缺省 none
    repo_type = (repo_type or "").strip()
    repo_url_clean = (repo_url or "").strip()
    if repo_type == "git":
        if not repo_url_clean:
            raise ValueError("代码仓库知识库必须配置仓库地址")
        if repo_url_clean.startswith("git@"):
            pass  # ssh:// 或 git@host:path 形态交由 worker git 命令处理
        elif not repo_url_clean.startswith(("http://", "https://", "ssh://", "git://")):
            raise ValueError("仓库地址格式不合法（支持 http/https/ssh/git@）")
        auth_type = (repo_auth_type or "none").strip() or "none"
        if auth_type not in ("none", "token", "ssh_key", "user_pass"):
            raise ValueError(f"凭证类型不合法: {auth_type}")
        if auth_type != "none" and not (repo_auth_secret or "").strip():
            raise ValueError("已选择凭证类型但未提供凭证内容")
    # 凭证 AES 加密落库（payload 形状与 update 的 _apply_repo_fields 一致）
    auth_type = (repo_auth_type or "none").strip() or "none"
    secret_plain = (repo_auth_secret or "").strip()
    encrypted_secret = ""
    if secret_plain:
        from api.lib.crypto import aes_encrypt

        payload = {"type": auth_type, "secret": secret_plain}
        encrypted_secret = aes_encrypt(json.dumps(payload, ensure_ascii=False))
    kb = KbDatasource(
        id=_uuid(),
        name=name.strip(),
        label=label,
        description=description,
        type=type if type in KB_TYPES else "document",
        custom_wiki_generation=bool(custom_wiki_generation),
        embedding_model_id=embedding_model_id,
        summary_model_id=summary_model_id,
        storage_backend_id=storage_backend_id,
        vector_store_id=vector_store_id,
        scope=scope or "system",
        team_name=team_name,
        owner_team_name=team_name,  # 2026-10-28 修复: scope.py 的 can_see/can_manage/visible_clauses
        # 全部读 owner_team_name 列,此前 create_kb 只写 team_name 列 → team 库校验恒落空
        # (上传/详情/列表均 404/403)。两列同步写入,数据迁移见 2026-10-28 修复说明。
        owner_user_id=owner_user_id,
        created_by=created_by,
        build_tape_id=build_tape_id,
        summary_strategy=strategy,
        indexing_strategy=normalize_indexing_strategy(indexing_strategy),
        wiki_config=normalize_wiki_config(cfg.get("wiki_config")),
        extract_config=normalize_extract_config(cfg.get("extract_config")),
        question_generation_config=normalize_question_generation_config(
            cfg.get("question_generation_config")
        ),
        vlm_config=normalize_vlm_config(cfg.get("vlm_config")),
        asr_config=normalize_asr_config(cfg.get("asr_config")),
        storage_provider_config=normalize_storage_provider_config(
            cfg.get("storage_provider_config")
        ),
        image_processing_config=dict(cfg.get("image_processing_config") or {}),
        repo_type=repo_type,
        repo_url=repo_url_clean,
        repo_branch=(repo_branch or "").strip(),
        repo_auth_type=auth_type,
        repo_auth_secret=encrypted_secret,
        state="1",
    )
    if aggregation_config is not None:
        from api.services.aggregation import normalize_aggregation_config

        kb.aggregation_config = normalize_aggregation_config(aggregation_config)
    if enhancement_config is not None:
        from api.services.chunk_enhance import normalize_enhancement_config

        kb.enhancement_config = normalize_enhancement_config(enhancement_config)
    db.add(kb)
    db.commit()
    db.refresh(kb)
    if ontology_schema_name:
        from api.models.ontology import KbOntologySchema

        bind = db.get(KbOntologySchema, kb.id)
        if bind:
            bind.schema_name = ontology_schema_name
        else:
            db.add(KbOntologySchema(kb_id=kb.id, schema_name=ontology_schema_name))
        db.commit()
    # 知识聚合（2026-10-25）：开启开关 → 建每日定时任务 + 立即触发一次编排
    if kb.aggregation_config:
        _sync_and_trigger_aggregation(db, kb, kb.aggregation_config, trigger=True)
    return kb


def _validate_vector_store_ref(db: Session, ds_id: str) -> None:
    """Check a datasource id is a usable vector backend (ES / PostgreSQL)."""
    from api.models.framework import Datasource
    from sqlalchemy import select

    row = db.execute(select(Datasource).where(Datasource.id == ds_id)).scalars().first()
    if row is None:
        raise ValueError(f"向量库资源不存在: {ds_id}")
    ds_type = (row.ds_type or "").strip().lower()
    if ds_type not in (
        "elasticsearch", "vector_es", "es",
        "postgresql", "pg", "postgres",
    ):
        raise ValueError(
            f"资源 {row.label or row.name or row.id} 的类型不支持作为向量库: {ds_type}"
        )


def _validate_build_tape_ref(db: Session, tape_id: str) -> None:
    """Check a tape exists and is published (构建引擎 = 服务编排中已发布的编排)."""
    from api.models.orchestration import Tape

    tape = db.get(Tape, tape_id)
    if tape is None:
        raise ValueError(f"构建引擎编排不存在: {tape_id}")
    if tape.status != "effective":
        raise ValueError(
            f"编排「{tape.tape_label or tape.tape_name}」未发布（status={tape.status}），"
            "请在服务编排中发布后再绑定"
        )


def update_kb(
    db: Session,
    kb_id: str,
    fields: dict,
    caller_user_id: str | None = None,
    caller_team_name: str | None = None,
    is_sys_admin: bool = False,
) -> KbDatasource | None:
    kb = get_kb(db, kb_id)
    if not kb:
        return None
    _validate_bound_model(
        db,
        fields.get("embedding_model_id"),
        "向量化",
        caller_user_id,
        caller_team_name,
        is_sys_admin,
    )
    _validate_bound_model(
        db,
        fields.get("summary_model_id"),
        "摘要",
        caller_user_id,
        caller_team_name,
        is_sys_admin,
    )
    if "name" in fields:
        kb.name = str(fields["name"]).strip() or kb.name
    if "label" in fields:
        kb.label = str(fields.get("label") or "")
    if "description" in fields:
        kb.description = str(fields.get("description") or "")
    if "type" in fields:
        t = str(fields.get("type") or "document")
        kb.type = t if t in KB_TYPES else kb.type
    if "custom_wiki_generation" in fields:
        kb.custom_wiki_generation = bool(fields["custom_wiki_generation"])
    if "build_tape_id" in fields:
        new_tape = str(fields.get("build_tape_id") or "") or None
        if new_tape:
            _validate_build_tape_ref(db, new_tape)
        kb.build_tape_id = new_tape
    if "summary_strategy" in fields:
        strategy = str(fields.get("summary_strategy") or "auto").strip() or "auto"
        if strategy not in SUMMARY_STRATEGIES:
            raise ValueError(f"摘要策略不合法: {strategy}（可选 off/auto/stuff/mapreduce/refine）")
        kb.summary_strategy = strategy
    if "summary_model_id" in fields:
        # 2026-10-28：LLM 绑定不可清空——摘要/增强/wiki 合成都依赖知识库模型，
        # 无绑定时运行时 LLM 全断（不回落系统默认/env）。允许更换，禁止置空。
        new_model = str(fields.get("summary_model_id") or "").strip() or ""
        if not new_model:
            raise ValueError("大语言模型（LLM）为必配项，不能清空；如要更换请选择其他模型")
        fields["summary_model_id"] = new_model
    for f in ("embedding_model_id", "summary_model_id", "storage_backend_id"):
        if f in fields:
            setattr(kb, f, str(fields.get(f) or "") or None)
    if "vector_store_id" in fields:
        new_store = str(fields.get("vector_store_id") or "") or None
        if new_store != kb.vector_store_id:
            # 向量库来源在创建时确定；非空库禁止切换（避免存量向量悬空）
            has_docs = (
                db.execute(
                    select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb_id)
                ).scalar()
                or 0
            )
            if has_docs > 0:
                raise ValueError("非空知识库不允许切换向量库，请在创建时确定向量库来源")
            if new_store:
                _validate_vector_store_ref(db, new_store)
            kb.vector_store_id = new_store
    if "indexing_strategy" in fields and isinstance(fields["indexing_strategy"], dict):
        kb.indexing_strategy = normalize_indexing_strategy(fields["indexing_strategy"])
    if "aggregation_config" in fields and isinstance(fields["aggregation_config"], dict):
        from api.services.aggregation import normalize_aggregation_config

        kb.aggregation_config = normalize_aggregation_config(fields["aggregation_config"])
    if "enhancement_config" in fields and isinstance(fields["enhancement_config"], dict):
        from api.services.chunk_enhance import normalize_enhancement_config

        kb.enhancement_config = normalize_enhancement_config(fields["enhancement_config"])
    raw_cfg = fields.get("configs")
    cfg: dict = raw_cfg if isinstance(raw_cfg, dict) else {}
    normalizers = {
        "wiki_config": normalize_wiki_config,
        "extract_config": normalize_extract_config,
        "question_generation_config": normalize_question_generation_config,
        "vlm_config": normalize_vlm_config,
        "asr_config": normalize_asr_config,
        "storage_provider_config": normalize_storage_provider_config,
    }
    for key, fn in normalizers.items():
        if key in cfg:
            setattr(kb, key, fn(cfg.get(key)))
        elif key in fields:  # 顶层平铺也接受
            setattr(kb, key, fn(fields.get(key)))
    if "image_processing_config" in cfg:
        kb.image_processing_config = dict(cfg["image_processing_config"] or {})
    elif "image_processing_config" in fields:
        kb.image_processing_config = dict(fields["image_processing_config"] or {})
    # 代码仓库配置更新（2026-10）：凭证留空=保持不变；改地址/分支/类型时按需重加密
    repo_fields = ("repo_type", "repo_url", "repo_branch", "repo_auth_type", "repo_auth_secret")
    if any(f in fields for f in repo_fields):
        _apply_repo_fields(db, kb, fields)
    db.commit()
    db.refresh(kb)
    # 知识聚合（2026-10-25）：配置在 payload 才同步 cron + 立即触发
    if "aggregation_config" in fields and isinstance(fields["aggregation_config"], dict):
        _sync_and_trigger_aggregation(db, kb, kb.aggregation_config or {}, trigger=True)
    return kb


def _apply_repo_fields(db: Session, kb: KbDatasource, fields: dict) -> None:
    """应用代码仓库配置字段（update_kb 内部）。

    - repo_type/repo_url/repo_branch/repo_auth_type 直接更新；
    - repo_auth_secret 非空（新明文）→ 重加密覆盖；空 → 保持不变。
    """
    from api.lib.crypto import aes_encrypt

    if "repo_type" in fields:
        rt = str(fields.get("repo_type") or "").strip()
        kb.repo_type = rt if rt in ("", "git") else kb.repo_type
    if "repo_url" in fields:
        kb.repo_url = str(fields.get("repo_url") or "").strip()
    if "repo_branch" in fields:
        kb.repo_branch = str(fields.get("repo_branch") or "").strip()
    if "repo_auth_type" in fields:
        at = str(fields.get("repo_auth_type") or "none").strip() or "none"
        if at in ("none", "token", "ssh_key", "user_pass"):
            kb.repo_auth_type = at
    new_secret = str(fields.get("repo_auth_secret") or "").strip()
    if new_secret:
        payload = {"type": kb.repo_auth_type or "none", "secret": new_secret}
        kb.repo_auth_secret = aes_encrypt(json.dumps(payload, ensure_ascii=False))
    # 仓库配置变更后清空拉取状态（下次同步重新拉取）
    kb.repo_pull_status = ""
    kb.repo_pull_error = None


def _agents_bound_to_kb(db: Session, kb_id: str) -> list[dict]:
    """返回显式绑定该知识库的启用智能体（kb_selection_mode=selected 且 kb_id 在列表中）。

    判定口径（对齐 AgentEditorModal「知识库范围」）：
    - selected：config.knowledge_bases / knowledge_ids 里含该 kb_id → 显式绑定；
    - all：智能体检索全部知识库，删除后 resolve_kb_scope 按 state=="1" 自动
      排除软删库，不构成悬空引用 —— 不拦截，避免「存在一个 all 智能体就
      删不掉任何知识库」；
    - none：不绑定。

    返回 [{id, name, kb_selection_mode}, ...]，仅含 state=="1"（启用）智能体；
    已删除智能体（state=="0"）不参与拦截。
    """
    agents = db.execute(
        select(KbAgent).where(KbAgent.state == "1")
    ).scalars().all()
    bound: list[dict] = []
    for ag in agents:
        cfg = ag.config or {}
        if cfg.get("kb_selection_mode") != "selected":
            continue
        kb_ids = set(cfg.get("knowledge_bases") or []) | set(
            cfg.get("knowledge_ids") or []
        )
        if kb_id in kb_ids:
            bound.append(
                {"id": ag.id, "name": ag.name, "kb_selection_mode": "selected"}
            )
    return bound


def delete_kb(db: Session, kb_id: str) -> dict:
    """级联删除知识库（对齐 WeKnora：KB 记录软删 + 内容一并清理）。

    删除前守卫（2026-10-20）：被启用智能体显式绑定（kb_selection_mode=selected
    且 kb_id 在 knowledge_bases/knowledge_ids 中）的知识库拒绝删除，返回
    code=AGENT_BOUND；all 模式智能体不拦截（软删后其检索范围自动排除）。

    删除流程（同步清理，本库数据量小，语义与 WeKnora 一致）：

      1. KB 软删（state=0）
      2. wiki 链接 → 页面 → 文件夹（先删依赖，避免孤儿链接）
      3. 文档 chunks → 文档记录 → 该库 embedding 记录
      4. wiki 操作日志 / 反馈
      5. Neo4j 图谱节点（best-effort：失败不阻塞删除）

    返回 data 含删除计数，供前端确认框展示。
    """
    kb = get_kb(db, kb_id)
    if not kb:
        return {"success": False, "message": f"知识库不存在: {kb_id}"}

    # 0) 智能体绑定校验（2026-10-20：删除前校验是否绑定了智能体）
    bound_agents = _agents_bound_to_kb(db, kb_id)
    if bound_agents:
        names = "、".join(a["name"] for a in bound_agents)
        return {
            "success": False,
            "code": "AGENT_BOUND",
            "message": f"知识库已被智能体「{names}」绑定，请先解除绑定后再删除",
            "data": {"bound_agents": bound_agents},
        }

    # 快照计数（前端确认框「将一并删除 N 文档 / M Wiki 页」用）
    doc_count = (
        db.execute(
            select(func.count()).select_from(KbDocument).where(KbDocument.kb_id == kb_id)
        ).scalar()
        or 0
    )
    wiki_count = (
        db.execute(
            select(func.count()).select_from(WikiPage).where(WikiPage.kb_id == kb_id)
        ).scalar()
        or 0
    )

    # 1) KB 软删
    kb.state = "0"
    # 1.1) 清空代码仓库凭证（敏感信息不随软删残留；2026-10 代码仓库知识库）
    try:
        kb.repo_auth_secret = None
        kb.repo_url = ""
        kb.repo_pull_error = None
    except Exception:  # noqa: BLE001 — 旧库无 repo 列时忽略
        pass

    # 1.5) 级联取消该库未完成的转换/wiki 任务（2026-10-05 实测: 删库后
    #      关联任务行残留 PENDING, celery 仍会消费 -> agent 为已删库建页）
    _cancel_kb_tasks(db, kb_id)

    # 1.6) 级联删除知识聚合定时任务（2026-10-25：否则删除后定时任务仍按
    #      cron 触发编排 → 对已删知识库建 wiki 页）
    try:
        from api.services.aggregation import delete_aggregation_crons

        delete_aggregation_crons(db, kb_id)
    except Exception as exc:  # noqa: BLE001 — 清理失败不阻塞删除
        logging.getLogger(__name__).warning("聚合定时任务清理失败(kb=%s): %s", kb_id, exc)

    # 2) wiki 层：链接 → 页面 → 文件夹
    db.execute(delete(WikiLink).where(WikiLink.kb_id == kb_id))
    db.execute(delete(WikiPage).where(WikiPage.kb_id == kb_id))
    db.execute(delete(WikiFolder).where(WikiFolder.kb_id == kb_id))
    # 3) 文档层：chunks → 文档（先收集 storage_path，删除后清理对象存储）
    doc_paths = [
        str(p)
        for p in db.execute(
            select(KbDocument.storage_path).where(
                KbDocument.kb_id == kb_id, KbDocument.storage_path.isnot(None)
            )
        ).scalars()
        if p
    ]
    db.execute(delete(DocChunk).where(DocChunk.kb_id == kb_id))
    db.execute(delete(KbDocument).where(KbDocument.kb_id == kb_id))
    # 4) wiki 日志 / 反馈
    db.execute(delete(WikiOperationLog).where(WikiOperationLog.kb_id == kb_id))
    db.execute(delete(WikiFeedback).where(WikiFeedback.kb_id == kb_id))
    db.commit()

    # 4.2) 文档对象存储清理（minio:// 路径；本地路径文件一并删；best-effort）
    for sp in doc_paths:
        try:
            storage.delete(sp)
        except Exception as exc:  # noqa: BLE001 - 对象清理失败不阻塞
            logging.getLogger(__name__).warning("文档对象清理失败(%s): %s", sp, exc)

    # 4.5) 向量索引（kb_embedding 属知识引擎侧表，走 VectorStore；best-effort）
    try:
        from api.services.vector_store import get_vector_store

        get_vector_store(kb.vector_store_id).delete_by_kb(kb_id)
    except Exception as exc:  # pragma: no cover - 向量清理为尽力而为
        logging.getLogger(__name__).warning("向量索引清理失败(kb=%s): %s", kb_id, exc)

    # 5) Neo4j 图谱：best-effort，失败仅记录不阻塞（对齐 WeKnora 异步清理语义）
    graph_nodes = 0
    try:
        settings = get_settings()

        if settings.neo4j_enabled:
            from api.services.graph import Neo4jGraphStore

            graph_nodes = Neo4jGraphStore(
                settings.NEO4J_URI,
                settings.NEO4J_USERNAME,
                settings.NEO4J_PASSWORD,
                settings.NEO4J_DATABASE or None,
            ).delete_kb_graph(kb_id)
    except Exception as exc:  # pragma: no cover - 图谱清理为尽力而为
        logging.getLogger(__name__).warning("Neo4j 图谱清理失败(kb=%s): %s", kb_id, exc)

    return {
        "success": True,
        "data": {
            "id": kb_id,
            "doc_count": doc_count,
            "wiki_count": wiki_count,
            "graph_nodes": graph_nodes,
        },
    }


def _sync_and_trigger_aggregation(
    db: Session, kb: KbDatasource, cfg: dict, trigger: bool = True
) -> None:
    """同步知识聚合定时任务 + 立即触发一次编排（create/update 共用）。

    - sync_aggregation_crons：按开关 upsert/删除 modo_cron_task（每日定时）。
    - trigger=True 时对开启的聚合项各触发一次编排（幂等：同编排已有
      queued/running run 会跳过），满足「修改配置立即聚合」。
    触发失败不抛（best-effort，定时任务兜底每日执行）；cron 同步失败抛
    异常让保存失败（配置了开关却没有定时任务=功能缺失，应阻止保存）。
    """
    from api.services.aggregation import (
        TAPE_NAME_TOPIC_CLUSTER,
        TAPE_NAME_VERSION_EVOLUTION,
        enqueue_aggregation_run,
        sync_aggregation_crons,
    )

    _ = sync_aggregation_crons(db, kb.id, kb.name or kb.id, cfg)
    if not trigger:
        return
    if cfg.get("topic_cluster_enabled"):
        try:
            enqueue_aggregation_run(
                db, TAPE_NAME_TOPIC_CLUSTER, kb.id, cfg.get("topic_cluster_topics") or []
            )
        except Exception as exc:  # noqa: BLE001 — 触发失败不阻断保存
            logging.getLogger(__name__).warning(
                "主题聚合立即执行失败(kb=%s): %s", kb.id, exc
            )
    if cfg.get("version_cluster_enabled"):
        try:
            enqueue_aggregation_run(db, TAPE_NAME_VERSION_EVOLUTION, kb.id)
        except Exception as exc:  # noqa: BLE001 — 触发失败不阻断保存
            logging.getLogger(__name__).warning(
                "版本聚类立即执行失败(kb=%s): %s", kb.id, exc
            )


def _kb_dict(kb: KbDatasource, doc_count: int = 0, page_count: int = 0, keyword_count: int = 0, feedback_count: int = 0) -> dict:
    return {
        "id": kb.id,
        "name": kb.name,
        "label": kb.label,
        "description": kb.description,
        "scope": kb.scope or "system",
        "team_name": kb.team_name or "",
        "owner_user_id": kb.owner_user_id or "",
        "indexing_strategy": kb.indexing_strategy or {},
        "state": kb.state,
        "doc_count": doc_count,
        "page_count": page_count,
        "keyword_count": keyword_count,
        "feedback_count": feedback_count,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
        "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
        # ── WeKnora 对齐配置（详情页配置弹窗读取）──
        "type": kb.type or "document",
        "custom_wiki_generation": bool(getattr(kb, "custom_wiki_generation", False)),
        "build_tape_id": getattr(kb, "build_tape_id", None) or "",
        "summary_strategy": getattr(kb, "summary_strategy", None) or "auto",
        "embedding_model_id": getattr(kb, "embedding_model_id", None) or "",
        "summary_model_id": getattr(kb, "summary_model_id", None) or "",
        "wiki_config": getattr(kb, "wiki_config", None) or {},
        "extract_config": getattr(kb, "extract_config", None) or {},
        "question_generation_config": getattr(kb, "question_generation_config", None) or {},
        "vlm_config": getattr(kb, "vlm_config", None) or {},
        "asr_config": getattr(kb, "asr_config", None) or {},
        "storage_provider_config": getattr(kb, "storage_provider_config", None) or {},
        "storage_backend_id": getattr(kb, "storage_backend_id", None) or "",
        "vector_store_id": getattr(kb, "vector_store_id", None) or "",
        # ── 代码仓库知识库（凭证密文不回显）──
        "repo_type": getattr(kb, "repo_type", None) or "",
        "repo_url": getattr(kb, "repo_url", None) or "",
        "repo_branch": getattr(kb, "repo_branch", None) or "",
        "repo_auth_type": getattr(kb, "repo_auth_type", None) or "none",
        "repo_commit_sha": getattr(kb, "repo_commit_sha", None) or "",
        "repo_pull_status": getattr(kb, "repo_pull_status", None) or "",
        "repo_pull_error": getattr(kb, "repo_pull_error", None) or "",
        "repo_last_pull_at": kb.repo_last_pull_at.isoformat() if kb.repo_last_pull_at else None,
        "repo_file_count": getattr(kb, "repo_file_count", None) or 0,
        # ── 知识聚合配置（2026-10-25，配置弹窗回填）──
        "aggregation_config": getattr(kb, "aggregation_config", None) or {},
        # ── 切片智能增强配置（2026-10-25，配置弹窗回填）──
        "enhancement_config": getattr(kb, "enhancement_config", None) or {},
    }


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def list_documents(
    db: Session,
    kb_id: str,
    page: int = 1,
    page_size: int = 20,
    keyword: str = "",
    file_type: str = "",
    parse_status: str = "",
    folder_id: str = "",
) -> dict:
    stmt = select(KbDocument).where(KbDocument.kb_id == kb_id)
    if keyword:
        stmt = stmt.where(KbDocument.file_name.like(f"%{keyword}%"))
    if file_type:
        stmt = stmt.where(KbDocument.file_ext == file_type.lstrip(".").lower())
    if parse_status:
        stmt = stmt.where(KbDocument.parse_state == parse_status.upper())
    # 递归子树浏览：folder 及其全部子孙目录的文档都算
    if folder_id:
        subtree = _doc_subtree_ids(db, kb_id, folder_id)
        stmt = stmt.where(KbDocument.folder_id.in_(subtree))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    rows = (
        db.execute(
            stmt.order_by(KbDocument.created_at.desc(), KbDocument.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": d.id,
            "kb_id": d.kb_id,
            "file_name": d.file_name,
            "file_ext": d.file_ext,
            "file_size": d.file_size,
            "build_tape_id": getattr(d, "build_tape_id", None) or "",
            "parse_state": d.parse_state,
            "parse_error": d.parse_error,
            "chunk_count": d.chunk_count,
            "summary": d.summary,
            "summary_status": d.summary_status,
            "summary_error": d.summary_error,
            "folder_id": d.folder_id or "",
            "source_type": d.source_type or "file",
            "ds_source_name": d.ds_source_name,
            "ds_table_schema": d.ds_table_schema,
            "ds_table_name": d.ds_table_name,
            "row_count": d.row_count,
            "repo_commit_sha": getattr(d, "repo_commit_sha", None) or "",
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in rows
    ]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def create_document(
    db: Session,
    kb_id: str,
    file_name: str,
    file_ext: str | None = None,
    file_size: int | None = None,
    storage_path: str | None = None,
    sys_file_id: str | None = None,
    created_by: str | None = None,
    folder_id: str = "",
    process_config: str | None = None,
    source_type: str = "file",
    ds_source_name: str | None = None,
    ds_table_schema: str | None = None,
    ds_table_name: str | None = None,
    row_count: int | None = None,
    build_tape_id: str | None = None,  # 文档级构建引擎（空 = 用 KB 默认）
    repo_commit_sha: str = "",  # 代码仓库源：文件来源 commit（2026-10）
) -> KbDocument:
    if build_tape_id:
        _validate_build_tape_ref(db, build_tape_id)
    doc = KbDocument(
        id=_uuid(),
        kb_id=kb_id,
        file_name=file_name,
        file_ext=(file_ext or "").lstrip(".").lower() or None,
        file_size=file_size,
        storage_path=storage_path,
        sys_file_id=sys_file_id,
        process_config=process_config,
        source_type=source_type,
        ds_source_name=ds_source_name,
        ds_table_schema=ds_table_schema,
        ds_table_name=ds_table_name,
        row_count=row_count,
        build_tape_id=build_tape_id,
        repo_commit_sha=repo_commit_sha or "",
        parse_state="PENDING",
        chunk_count=0,
        created_by=created_by,
        folder_id=folder_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


# 上传摘要策略（2026-10-13）：off=不生成；auto=按规模+历史摘要自动路由
# （详情见 worker/tasks/doc_process.py::_resolve_summary_tape）；
# stuff/mapreduce/refine=指定模式强制用该编排。
SUMMARY_STRATEGIES: tuple[str, ...] = ("off", "auto", "stuff", "mapreduce", "refine")

# 注：直连 LLM 生成文档摘要的链路（generate_document_summary 及 POST
# /{kb_id}/documents/{document_id}/summary 端点、前端自动摘要）已于 2026-10-14
# 移除——文档摘要统一由摘要编排（Stuff/Map-Reduce/Refine）生成。
# 编排只产出 wiki 摘要页，不写 kb_document.summary 字段。


def delete_document(db: Session, kb_id: str, document_id: str) -> dict:
    """异步删除入口（2026-10-23 异步化改造）：标记 DELETING 并入队清理任务后立即返回。

    级联清理（wiki 页/链接/目录、chunks、向量、存储文件、Neo4j 图谱、任务
    监控残留）由 worker/tasks/doc_delete.purge_document 在后台执行——大文档
    删除不再占用 HTTP 请求线程（此前同步清理数秒起步，见下方 purge 函数的
    2026-10-21 性能注释）。broker 不可达时回退同步清理，删除语义不因队列
    故障丢失；同步清理也失败时保持 DELETING 状态，由 beat 定时扫描重投。

    幂等：文档已处于 DELETING/DELETE_FAILED 时重复调用无害（purge 幂等）。
    """
    doc = db.execute(
        select(KbDocument).where(
            KbDocument.id == document_id, KbDocument.kb_id == kb_id
        )
    ).scalars().first()
    if not doc:
        return {"success": False, "message": f"文档不存在: {document_id}"}
    doc.delete_state = "DELETING"
    doc.delete_error = None
    db.commit()

    try:
        from worker.tasks.doc_delete import enqueue_doc_delete

        enqueue_doc_delete(kb_id, document_id)
    except Exception as exc:  # noqa: BLE001 — broker 不可达：同步兜底，删除语义不丢
        logger.warning(
            "failed to enqueue async doc delete (doc=%s), falling back to sync purge: %s",
            document_id, exc,
        )
        try:
            purge_document_resources(db, kb_id, document_id)
        except Exception as exc2:  # noqa: BLE001
            logger.exception("sync purge fallback failed (doc=%s)", document_id)
            return {
                "success": False,
                "message": f"删除任务提交失败，同步清理也失败: {exc2}",
            }
    return {"success": True, "data": {"id": document_id, "async": True}}


def purge_document_resources(db: Session, kb_id: str, document_id: str) -> dict:
    """完整级联清理并物理删除文档（worker 异步任务 / 同步兜底共用）。

    幂等：各子步骤对已清理数据均为 no-op——wiki 粗筛找不到已删页、chunks
    为空、向量按 chunk_id 重复删除无效果、文件删除幂等、Neo4j 子图不存在
    即跳过、任务行为空。并发双跑（beat 重投）安全。
    """
    doc = db.execute(
        select(KbDocument).where(
            KbDocument.id == document_id, KbDocument.kb_id == kb_id
        )
    ).scalars().first()
    if not doc:
        return {"success": True, "data": {"id": document_id, "already_deleted": True}}
    # 2026-10-05 实测: 删文档后其 wiki 构建任务仍执行（假成功/白跑 LLM）——
    # 删除时先级联取消关联的未完成 wiki 任务（PENDING/RUNNING → STOPPED + revoke）。
    _cancel_related_wiki_tasks(db, document_id)

    # --- 级联清理（2026-10-08 修复：此前只删 chunks+文档行，wiki 页/目录/链接/
    #     向量/图谱全部残留成孤儿。对齐 delete_kb 语义逐层清理，先删依赖）---
    #
    # 1) wiki 层：该文档专属页（source_refs 仅含本文档）整页删除；共享页
    #    （跨文档合并实体页）只摘除本 kid 引用不删页——防误伤其他文档。
    #    页面删除后，指向这些页的链接一并清；不再被任何页引用的目录删除。
    # 2026-10-21 性能：原实现 select(WikiPage) 全量物化整库 wiki 页（含
    #    content 大字段）到 Python 再逐页 startswith——库页数上万时删除请求
    #    数秒起步。改为 SQL 层先用 source_refs JSON 文本 LIKE 粗筛出候选页
    #    （只取 id/folder_id/source_refs 三列），Python 侧仍做精判（startswith
    #    + 引用计数），语义不变但传输/物化量从「全库页」缩到「命中本文档的页」。
    #    注意：粗筛后的 pages 只有引用本文档的页，不能再用它收集「所有保留页
    #    的 folder」（会漏掉其他文档页 → 误删目录）；目录保留改由下方
    #    still_used 精确查询（affected folder 是否还有被删页之外的页引用）。
    doc_prefix = f"{document_id}|"
    like_pat = f"%{document_id}|%"
    pages = db.execute(
        select(WikiPage.id, WikiPage.folder_id, WikiPage.source_refs).where(
            WikiPage.kb_id == kb_id,
            WikiPage.source_refs.isnot(None),
            cast(WikiPage.source_refs, _String).like(like_pat),
        )
    ).all()
    page_ids_to_delete: list[str] = []
    shared_ref_updates: list[tuple[str, list]] = []  # (page_id, 摘除后的 source_refs)
    for p in pages:
        refs = p.source_refs if isinstance(p.source_refs, list) else []
        hits = [r for r in refs if isinstance(r, str) and r.startswith(doc_prefix)]
        if not hits:
            continue
        if len(refs) <= len(hits):
            # 专属页：本页所有 source_ref 都属于该文档 → 整页删除
            page_ids_to_delete.append(str(p.id))
        else:
            # 共享页：摘除本 kid 引用，保留其他文档引用
            shared_ref_updates.append(
                (str(p.id), [r for r in refs if not r.startswith(doc_prefix)])
            )
    for pid, new_refs in shared_ref_updates:
        db.execute(
            update(WikiPage)
            .where(WikiPage.kb_id == kb_id, WikiPage.id == pid)
            .values(source_refs=new_refs)
        )
    if page_ids_to_delete:
        db.execute(delete(WikiLink).where(WikiLink.from_page_id.in_(page_ids_to_delete)))
        db.execute(delete(WikiLink).where(WikiLink.to_page_id.in_(page_ids_to_delete)))
        db.execute(
            delete(WikiPage).where(WikiPage.id.in_(page_ids_to_delete))
        )
    # 该文档目录子树清理：从被删页所在 folder 向上收敛（该文档页专属目录），
    # 不删根目录（parent_id 为空，全库共用）也不误伤其他文档目录。
    # folder 是否可删 = 被删页的 folder 中，已无任何被删页之外的页引用
    # （含其他文档页、共享页、未引用本文档的页）→ 用 SQL 精确判定，避免
    # 全量拉页统计。
    affected_folder_ids = {
        str(p.folder_id)
        for p in pages
        if str(p.id) in set(page_ids_to_delete) and p.folder_id
    }
    # 子树内所有目录（对齐原实现：folder 必须真实存在于目录树才删）
    all_folder_ids = {
        str(f.id)
        for f in db.execute(
            select(WikiFolder).where(WikiFolder.kb_id == kb_id)
        ).scalars()
        if f.id
    }
    folder_ids_to_delete: list[str] = []
    if affected_folder_ids and page_ids_to_delete:
        still_used = set(
            db.execute(
                select(WikiPage.folder_id)
                .where(
                    WikiPage.kb_id == kb_id,
                    WikiPage.folder_id.in_(affected_folder_ids),
                    WikiPage.id.not_in(page_ids_to_delete),
                )
            ).scalars()
        )
        folder_ids_to_delete = [
            fid for fid in affected_folder_ids
            if fid not in still_used and fid in all_folder_ids
        ]
    if folder_ids_to_delete:
        db.execute(
            delete(WikiFolder).where(WikiFolder.id.in_(folder_ids_to_delete))
        )
    # 该文档操作日志 / 反馈（对齐 delete_kb 第 4 步；按 source 关联不可行则全量保日志）
    #   wiki_operation_log / wiki_feedback 无 document_id 列，按 kb 保留——不删。

    # 2) 文档层：chunks → 文档行
    # 注意 select(DocChunk.id) 单列查询 .scalars() 直接产出字符串值，
    # 不能对每个值再取 .id（曾导致删任何带 chunks 的文档 500）
    chunk_ids = list(
        db.execute(
            select(DocChunk.id).where(DocChunk.document_id == document_id)
        ).scalars()
    )
    db.execute(delete(DocChunk).where(DocChunk.document_id == document_id))
    db.delete(doc)

    # 3) 向量索引清理——挪到业务提交之前并 fail-closed（2026-10-20）：
    #    原实现先 commit 业务库、再 best-effort 清向量（失败仅 warning），
    #    曾致「切片已删、向量残留」的孤儿向量累积（kb_embedding 幽灵 id，
    #    检索命中空内容）。现在先清向量再提交业务删除；清理失败抛异常
    #    中止删除（业务事务回滚、文档保留），从源头杜绝孤儿产生。
    if chunk_ids:
        try:
            from api.services.vector_store import get_vector_store

            # vector_store_id 是 KB 级列（KbDatasource），文档行没有——
            # 对齐 ingest.py 的取法，否则 doc.vector_store_id 运行时
            # AttributeError 被吞 → 向量清理静默失效（曾实踩）
            kb_row = db.execute(
                select(KbDatasource).where(KbDatasource.id == kb_id)
            ).scalars().first()
            get_vector_store(
                kb_row.vector_store_id if kb_row else None
            ).delete_by_chunks(chunk_ids)
        except Exception as exc:  # fail-closed：向量清理失败则不删文档
            logging.getLogger(__name__).exception(
                "向量清理失败(doc=%s)，中止文档删除: %s", document_id, exc
            )
            raise
    db.commit()

    # 4) 本地/对象存储文件清理（_remove_local_file 内部已 try/except，幂等）
    _remove_local_file(doc.storage_path)

    # 5) Neo4j 图谱（kg = 该 kid 子图，best-effort 不阻塞）
    try:
        settings = get_settings()
        if settings.neo4j_enabled:
            from api.services.graph import Neo4jGraphStore

            Neo4jGraphStore(
                settings.NEO4J_URI,
                settings.NEO4J_USERNAME,
                settings.NEO4J_PASSWORD,
                settings.NEO4J_DATABASE or None,
            ).delete_doc_graph(document_id)
    except Exception as exc:  # pragma: no cover - 图谱清理尽力而为
        logging.getLogger(__name__).warning(
            "Neo4j 图谱清理失败(doc=%s): %s", document_id, exc
        )

    # 6) 任务监控残留清理（2026-10-21 用户反馈：删文档后任务监控仍有任务数据）。
    #    链头 DOC_{id}（含重解析轮次 DOC_{id}_R2）、其子任务、task_params 含
    #    该文档的 WIKI/VERIFY 任务、该文档的编排 run 一并删除——文档已删，
    #    这些任务行失去意义，留在任务监控里是噪音（审计价值 < 页面干净）。
    try:
        _cleanup_document_jobs(db, document_id)
    except Exception:  # noqa: BLE001 - 任务行清理失败不阻断文档删除
        logging.getLogger(__name__).warning(
            "任务行清理失败(doc=%s)", document_id, exc_info=True
        )

    return {"success": True, "data": {"id": document_id}}


def _cleanup_document_jobs(db: Session, document_id: str) -> None:
    """删除文档时清理其任务监控记录（链头/子任务/编排 run）。

    2026-10-21 用户反馈「删文档后任务监控还有任务数据残留」——此前只
    _cancel_related_wiki_tasks 置 STOPPED/failed，任务行全保留，文档已删的
    历史任务堆积在任务监控页。文档都删了任务行无审计意义，直接删。
    """
    from api.models.framework import Job
    from api.models.orchestration import TapeRun

    # 链头 DOC_{document_id}（含 DOC_{document_id}_R2 轮次）
    heads = list(
        db.execute(
            select(Job.id).where(Job.id.like(f"DOC_{document_id}%"))
        ).scalars()
    )
    if heads:
        # 子任务（parent_id 指向链头）先删，再删链头
        db.execute(delete(Job).where(Job.parent_id.in_(heads)))
        db.execute(delete(Job).where(Job.id.in_(heads)))
    # task_params 含该文档 id 的其他任务（WIKI_*/VERIFY_* 等）
    db.execute(delete(Job).where(Job.task_params.like(f"%{document_id}%")))
    # 该文档的编排 run（含被取消的 failed 残留）
    db.execute(delete(TapeRun).where(TapeRun.inputs.like(f"%{document_id}%")))
    db.commit()


def _cancel_related_wiki_tasks(db: Session, document_id: str) -> None:
    """删除文档时级联取消其关联的未完成 wiki 构建任务（避免删后假成功/孤儿页）。"""
    from api.models.framework import Job

    # 2026-10-17：编排化 wiki 构建（kb_tape_run）此前不会被取消 —— 文档删除后
    # run 仍继续执行，切片拉取 0 字 → 切片门禁 failed，任务监控/编排列表留失败记录。
    # 2026-10-21 追加：仅置 failed 不够 —— 已投递/正在执行的节点任务（run_node）
    # 不会因 run 状态变化而停止，写页节点照样把 keyword 页写库（实测 43 页孤儿）。
    # 因此除置 failed 外，还需 revoke 该 run 在途节点的 celery 任务。
    try:
        from sqlalchemy import text as _sql_text
        from api.models.orchestration import TapeRun

        # 先取该文档关联的 queued/running run（inputs 含 document_id）
        run_ids = list(
            db.execute(
                select(TapeRun.id).where(
                    TapeRun.status.in_(["queued", "running"]),
                    TapeRun.inputs.like(f"%{document_id}%"),
                )
            ).scalars()
        )
        _n = 0
        if run_ids:
            _res = db.execute(
                _sql_text(
                    "update kb_tape_run set status = 'failed', "
                    "error = '文档已删除，wiki 构建自动取消', updated_at = now() "
                    "where id in :ids and status in ('queued', 'running')"
                ),
                {"ids": tuple(run_ids)},
            )
            _n = getattr(_res, "rowcount", 0) or 0
        if _n:
            logger.info("cancel %s orchestration run(s) for deleted document %s", _n, document_id)
        # revoke 在途节点任务（terminate=True：杀正在执行的写页节点，防删后写页）
        if run_ids:
            _celery = None
            node_task_id = None
            try:
                from worker.celery_app import celery_app as _celery
                from worker.tasks.orch_runner import node_task_id
            except Exception:  # noqa: BLE001 - 无 celery 环境时跳过 revoke
                _celery = None
                node_task_id = None
            if _celery is not None and node_task_id is not None:
                # 在途节点 = step_results 中 status=running 的节点（已投递未结束）；
                # 未投递节点由 _advance_impl 的 run 状态守卫自然拦住。
                revoked: list[str] = []
                for rid in run_ids:
                    row = db.execute(
                        select(TapeRun.step_results).where(TapeRun.id == rid)
                    ).scalar()
                    for r in (row or []):
                        if r.get("status") == "running":
                            revoked.append(node_task_id(rid, str(r.get("step_id") or ""), int(r.get("round") or 0)))
                for tid in revoked:
                    try:
                        _celery.control.revoke(tid, terminate=True)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("revoke node %s failed: %s", tid, exc)
                if revoked:
                    logger.info("revoked %d in-flight node(s) for deleted document %s", len(revoked), document_id)
    except Exception:  # noqa: BLE001 - 取消失败不阻断删除
        logger.warning("cancel orchestration runs for document %s failed", document_id, exc_info=True)

    jobs = db.execute(
        select(Job).where(
            Job.id.like("WIKI_%"),
            Job.task_params.like(f"%{document_id}%"),
            Job.state.in_(["PENDING", "RUNNING"]),
        )
    ).scalars().all()
    if not jobs:
        return
    try:
        from worker.celery_app import celery_app
    except Exception:  # noqa: BLE001 — 环境无 celery 时仅置状态
        celery_app = None
    for j in jobs:
        j.state = "STOPPED"
        if celery_app is not None:
            try:
                celery_app.control.revoke(j.id, terminate=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("revoke wiki task %s failed: %s", j.id, exc)
    db.commit()


def _cancel_kb_tasks(db: Session, kb_id: str) -> None:
    """删除知识库时级联取消其未完成的转换/wiki 任务。

    2026-10-05 实测：删库后关联任务行残留 PENDING，celery 队列仍持有该
    任务 -> worker 照常消费 -> agent 为已删库建页（孤儿页）。
    仅置 STOPPED + revoke（保留任务行供审计）；限定 DOC_/WIKI_ 前缀防误伤。
    """
    from api.models.framework import Job

    jobs = db.execute(
        select(Job).where(
            Job.id.like("DOC_%"),
            Job.task_params.like(f"%{kb_id}%"),
            Job.state.in_(["PENDING", "RUNNING"]),
        )
    ).scalars().all()
    jobs += db.execute(
        select(Job).where(
            Job.id.like("WIKI_%"),
            Job.task_params.like(f"%{kb_id}%"),
            Job.state.in_(["PENDING", "RUNNING"]),
        )
    ).scalars().all()
    if not jobs:
        return
    try:
        from worker.celery_app import celery_app
    except Exception:  # noqa: BLE001 — 环境无 celery 时仅置状态
        celery_app = None
    for j in jobs:
        j.state = "STOPPED"
        if celery_app is not None:
            try:
                celery_app.control.revoke(j.id, terminate=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("revoke kb task %s failed: %s", j.id, exc)
    db.commit()


def _remove_local_file(storage_path: str | None) -> None:
    """删除物理文件（本地磁盘或 MinIO 对象，幂等）。"""
    if not storage_path:
        return
    try:
        from api.services.storage import delete as storage_delete

        storage_delete(storage_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to remove stored file %s: %s", storage_path, exc)


# ---------------------------------------------------------------------------
# Doc folders (文档多级目录, doc_folder + kb_document.folder_id)
# 决策（2026-10-10）：单归属 / 递归子树浏览 / 非空禁删 / 仅浏览不参与检索
# ---------------------------------------------------------------------------


def doc_folders(db: Session, kb_id: str) -> list[dict]:
    """全量文档目录元数据（轻量，管理面板/树构建用）。"""
    folders = db.execute(
        select(DocFolder).where(DocFolder.kb_id == kb_id).order_by(DocFolder.created_at)
    ).scalars().all()
    counts = _doc_child_counts(db, kb_id)
    totals = _subtree_file_totals(db, kb_id, DocFolder, _doc_direct_counts(db, kb_id))
    return [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id or "",
            "child_count": counts.get(f.id, 0),
            "doc_total": totals.get(f.id, 0),
        }
        for f in folders
    ]


def _doc_child_counts(db: Session, kb_id: str) -> dict[str, int]:
    """每个 folder 的直接子项数（子目录 + 子文档），懒加载树判断展开箭头用。"""
    sub_folders = db.execute(
        select(func.coalesce(DocFolder.parent_id, ""), func.count())
        .where(DocFolder.kb_id == kb_id)
        .group_by(func.coalesce(DocFolder.parent_id, ""))
    ).all()
    sub_docs = db.execute(
        select(func.coalesce(KbDocument.folder_id, ""), func.count())
        .where(KbDocument.kb_id == kb_id)
        .group_by(func.coalesce(KbDocument.folder_id, ""))
    ).all()
    counts: dict[str, int] = {}
    for fid, n in list(sub_folders) + list(sub_docs):
        counts[fid or ""] = counts.get(fid or "", 0) + int(n)
    return counts


def _doc_direct_counts(db: Session, kb_id: str) -> dict[str, int]:
    """每个 folder 的直属文档数（不含子目录），子树文件总数计算的直接层。"""
    rows = db.execute(
        select(func.coalesce(KbDocument.folder_id, ""), func.count())
        .where(KbDocument.kb_id == kb_id)
        .group_by(func.coalesce(KbDocument.folder_id, ""))
    ).all()
    return {str(fid or ""): int(n) for fid, n in rows}


def _subtree_file_totals(
    db: Session, kb_id: str, folder_cls: type, direct: dict[str, int]
) -> dict[str, int]:
    """每个 folder 的子树文件总数（直属文件 + 全部子孙目录下的文件，递归求和，不含目录本身）。

    2026-10-15：左侧树节点数量统计从「直属子项数/直属文件数」改为「子树文件总数」——
    用户在知识库详情左侧树上期望看到该目录（含子孙）下的全部文件数。folder_cls 传
    DocFolder / WikiFolder；direct 为每个 folder 的直属文件计数。memo 进入时先占位
    防 parent_id 成环死循环（环结构本身非法，结果不精确但不会挂）。
    """
    rows = db.execute(
        select(folder_cls.id, func.coalesce(folder_cls.parent_id, ""))
        .where(folder_cls.kb_id == kb_id)
    ).all()
    children: dict[str, list[str]] = {}
    for fid, pid in rows:
        children.setdefault(str(pid or ""), []).append(str(fid))
    totals: dict[str, int] = {}

    def _sum(fid: str) -> int:
        if fid in totals:
            return totals[fid]
        totals[fid] = 0  # 防环占位
        v = direct.get(fid, 0) + sum(_sum(c) for c in children.get(fid, []))
        totals[fid] = v
        return v

    for fid, _ in rows:
        _sum(str(fid))
    return totals


def doc_branch(
    db: Session, kb_id: str, folder_id: str = "", page: int = 1, page_size: int = 50
) -> dict:
    """懒加载分支（对齐 wiki_branch）：folder_id='' 返回根级直接子目录 + 直接子文档。

    每目录带 child_count（子目录+子文档数，前端据此显示展开箭头）。
    """
    fid = folder_id or ""
    folders = db.execute(
        select(DocFolder)
        .where(DocFolder.kb_id == kb_id, func.coalesce(DocFolder.parent_id, "") == fid)
        .order_by(DocFolder.created_at)
    ).scalars().all()
    docs_q = select(KbDocument).where(
        KbDocument.kb_id == kb_id,
        func.coalesce(KbDocument.folder_id, "") == fid,
    )
    # 2026-10-21 性能修复：计数走 SQL count，原 len(all()) 会把目录下全部文档
    # 全列（含 summary Text 大列）物化到 Python，大目录下每次分支展开都拖全量。
    total_docs = int(
        db.execute(select(func.count()).select_from(docs_q.subquery())).scalar() or 0
    )
    rows = db.execute(
        docs_q.order_by(KbDocument.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    counts = _doc_child_counts(db, kb_id)
    totals = _subtree_file_totals(db, kb_id, DocFolder, _doc_direct_counts(db, kb_id))
    return {
        "kb_id": kb_id,
        "folder_id": fid,
        "folders": [
            {
                "id": f.id,
                "name": f.name,
                "parent_id": f.parent_id or "",
                "child_count": counts.get(f.id, 0),
                "doc_total": totals.get(f.id, 0),
            }
            for f in folders
        ],
        "documents": [
            {
                "id": d.id,
                "file_name": d.file_name,
                "file_ext": d.file_ext,
                "file_size": d.file_size,
                "parse_state": d.parse_state,
                "folder_id": d.folder_id or "",
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in rows
        ],
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total_docs,
        "total_docs": total_docs,
        "total_folders": len(folders),
    }


def _doc_subtree_ids(db: Session, kb_id: str, folder_id: str) -> list[str]:
    """folder 及全部子孙目录的 id 集合（递归）。用于递归子树浏览。"""
    all_folders = db.execute(
        select(DocFolder).where(DocFolder.kb_id == kb_id)
    ).scalars().all()
    parent_of: dict[str, str] = {f.id: f.parent_id or "" for f in all_folders}
    ids = [folder_id]
    for f in all_folders:
        if f.id == folder_id:
            continue
        cur = f.parent_id or ""
        while cur:
            if cur == folder_id:
                ids.append(f.id)
                break
            cur = parent_of.get(cur, "")
    return ids


def doc_folder_exists(db: Session, kb_id: str, folder_id: str) -> bool:
    """目录是否属于该 KB（上传等写入路径的归属校验；空 folder_id = 根层级，视为合法）。"""
    if not folder_id:
        return True
    return (
        db.execute(
            select(DocFolder.id).where(
                DocFolder.kb_id == kb_id, DocFolder.id == folder_id
            )
        ).scalars().first()
        is not None
    )


def doc_create_folder(
    db: Session, kb_id: str, data: dict, user_id: str | None = None
) -> dict:
    """创建文档目录。parent_id 缺省为根。"""
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("name 不能为空")
    parent_id = str(data.get("parent_id") or "")
    if parent_id:
        parent = db.execute(
            select(DocFolder).where(
                DocFolder.kb_id == kb_id, DocFolder.id == parent_id
            )
        ).scalars().first()
        if not parent:
            raise ValueError(f"父目录不存在: {parent_id}")
    # 同级同名唯一
    dup = db.execute(
        select(DocFolder).where(
            DocFolder.kb_id == kb_id,
            func.coalesce(DocFolder.parent_id, "") == parent_id,
            DocFolder.name == name,
        )
    ).scalars().first()
    if dup:
        raise ValueError(f"同级下已存在同名目录: {name}")
    folder = DocFolder(
        id=_uuid(),
        kb_id=kb_id,
        name=name,
        parent_id=parent_id,
        created_by=user_id,
    )
    db.add(folder)
    db.commit()
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id or ""}


def doc_update_folder(db: Session, kb_id: str, folder_id: str, data: dict) -> dict:
    """重命名 / 移动文档目录。"""
    folder = db.execute(
        select(DocFolder).where(DocFolder.kb_id == kb_id, DocFolder.id == folder_id)
    ).scalars().first()
    if not folder:
        raise ValueError(f"目录不存在: {folder_id}")
    if "name" in data:
        n = str(data["name"]).strip()
        if not n:
            raise ValueError("name 不能为空")
        folder.name = n
    if "parent_id" in data:
        new_parent = str(data["parent_id"] or "")
        if new_parent == folder.id:
            raise ValueError("不能将目录移动到自身")
        # 防环：新父目录不能是自己的子孙
        subtree = _doc_subtree_ids(db, kb_id, folder.id)
        if new_parent in subtree:
            raise ValueError("不能将目录移动到自己的子目录下")
        folder.parent_id = new_parent
    db.commit()
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id or ""}


def doc_delete_folder(db: Session, kb_id: str, folder_id: str) -> dict:
    """删除文档目录：仅当目录下无子目录、无文档时允许（非空禁删）。"""
    folder = db.execute(
        select(DocFolder).where(DocFolder.kb_id == kb_id, DocFolder.id == folder_id)
    ).scalars().first()
    if not folder:
        raise ValueError(f"目录不存在: {folder_id}")
    child_folders = db.execute(
        select(DocFolder).where(
            DocFolder.kb_id == kb_id, DocFolder.parent_id == folder_id
        )
    ).scalars().all()
    if child_folders:
        raise ValueError(f"目录下仍有 {len(child_folders)} 个子目录，请先移走")
    child_docs = db.execute(
        select(KbDocument).where(
            KbDocument.kb_id == kb_id, KbDocument.folder_id == folder_id
        )
    ).scalars().all()
    if child_docs:
        raise ValueError(f"目录下仍有 {len(child_docs)} 个文档，请先移走")
    db.delete(folder)
    db.commit()
    return {"deleted": True, "folder_id": folder_id}


def doc_move_documents(
    db: Session, kb_id: str, document_ids: list[str], folder_id: str
) -> dict:
    """批量移动文档到目录（folder_id="" = 移出所有目录到根层级）。

    单归属语义：folder_id 整体替换文档原目录。所有 id 必须属于本 KB。
    """
    if not document_ids:
        return {"moved": 0}
    if folder_id:
        folder = db.execute(
            select(DocFolder).where(
                DocFolder.kb_id == kb_id, DocFolder.id == folder_id
            )
        ).scalars().first()
        if not folder:
            raise ValueError(f"目录不存在: {folder_id}")
    docs = db.execute(
        select(KbDocument).where(
            KbDocument.kb_id == kb_id, KbDocument.id.in_(document_ids)
        )
    ).scalars().all()
    if len(docs) != len(set(document_ids)):
        raise ValueError("部分文档不存在或不属于当前知识库")
    for d in docs:
        d.folder_id = folder_id
    db.commit()
    return {"moved": len(docs)}


# ---------------------------------------------------------------------------
# Wiki browsing
# ---------------------------------------------------------------------------


def wiki_tree(db: Session, kb_id: str) -> dict:
    """Folders + pages for a KB (folder_id '' = root). 全量（保留给管理面板等主动场景）。"""
    folders = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id).order_by(WikiFolder.created_at)
    ).scalars().all()
    pages = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
    ).scalars().all()
    totals = _subtree_file_totals(db, kb_id, WikiFolder, _wiki_page_counts(db, kb_id))
    folder_items = [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id or "",
            "page_count": sum(1 for p in pages if (p.folder_id or "") == f.id),
            "page_total": totals.get(f.id, 0),
        }
        for f in folders
    ]
    page_items = [
        {
            "id": p.id,
            "slug": p.slug,
            "title": p.title,
            "page_type": p.page_type,
            "folder_id": p.folder_id or "",
            "summary": p.summary,
        }
        for p in pages
    ]
    return {"kb_id": kb_id, "folders": folder_items, "pages": page_items}


def _wiki_child_counts(db: Session, kb_id: str) -> dict[str, int]:
    """每个 folder 的直接子项数（子目录 + 子页面），用于懒加载树判断展开箭头。"""
    sub_folders = db.execute(
        select(func.coalesce(WikiFolder.parent_id, ""), func.count())
        .where(WikiFolder.kb_id == kb_id)
        .group_by(func.coalesce(WikiFolder.parent_id, ""))
    ).all()
    sub_pages = db.execute(
        select(func.coalesce(WikiPage.folder_id, ""), func.count())
        .where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
        .group_by(func.coalesce(WikiPage.folder_id, ""))
    ).all()
    counts: dict[str, int] = {}
    for fid, n in list(sub_folders) + list(sub_pages):
        counts[fid or ""] = counts.get(fid or "", 0) + int(n)
    return counts


def _wiki_page_counts(
    db: Session, kb_id: str, page_types: list[str] | None = None
) -> dict[str, int]:
    """每个 folder 的直接 active 页面数（对齐 WeKnora 目录胶囊的页面数语义）。

    2026-10-18：支持 page_types 过滤 —— wiki 详情按 WeKnora 分「原始文档 Wiki /
    聚类知识 Wiki」两个 Tab，目录上的页数也要跟着 Tab 变，否则聚类 Tab 下会
    显示原始文档类别的大数字。
    """
    q = (
        select(func.coalesce(WikiPage.folder_id, ""), func.count())
        .where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
    )
    if page_types:
        q = q.where(WikiPage.page_type.in_(page_types))
    rows = db.execute(q.group_by(func.coalesce(WikiPage.folder_id, ""))).all()
    return {str(fid or ""): int(n) for fid, n in rows}


def wiki_branch(
    db: Session,
    kb_id: str,
    folder_id: str = "",
    page: int = 1,
    page_size: int = 50,
    page_types: list[str] | None = None,
) -> dict:
    """懒加载分支（对齐 WeKnora 侧栏按需展开）。

    folder_id='' → 根级。只返回该 folder 的直接子目录 + 直接子页面，
    每个目录带 child_count（子目录+子页面数，前端据此显示展开箭头）。
    页面分页，避免大知识库一次全量传输。
    """
    fid = folder_id or ""
    folders = db.execute(
        select(WikiFolder)
        .where(WikiFolder.kb_id == kb_id, func.coalesce(WikiFolder.parent_id, "") == fid)
        .order_by(WikiFolder.created_at)
    ).scalars().all()
    pages_q = select(WikiPage).where(
        WikiPage.kb_id == kb_id,
        WikiPage.status == "active",
        func.coalesce(WikiPage.folder_id, "") == fid,
    )
    if page_types:
        pages_q = pages_q.where(WikiPage.page_type.in_(page_types))
    # 2026-10-21 性能修复：计数走 SQL count，原 len(all()) 会把目录下全部页面
    # 全列（含 content Text 正文）物化到 Python，大知识库下主目录每次展开都变慢。
    total_pages = int(
        db.execute(select(func.count()).select_from(pages_q.subquery())).scalar() or 0
    )
    rows = db.execute(
        pages_q.order_by(WikiPage.created_at).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    counts = _wiki_child_counts(db, kb_id)
    page_counts = _wiki_page_counts(db, kb_id, page_types)
    totals = _subtree_file_totals(db, kb_id, WikiFolder, page_counts)
    folder_items = []
    for f in folders:
        # 按类型过滤时，子树内没有匹配页的目录直接隐藏（否则聚类 Tab 下满是空目录）
        if page_types and totals.get(f.id, 0) <= 0:
            continue
        folder_items.append(
            {
                "id": f.id,
                "name": f.name,
                "parent_id": f.parent_id or "",
                "child_count": counts.get(f.id, 0),
                "page_count": page_counts.get(f.id, 0),
                "page_total": totals.get(f.id, 0),
            }
        )
    return {
        "kb_id": kb_id,
        "folder_id": fid,
        "folders": folder_items,
        "pages": [
            {
                "id": p.id,
                "slug": p.slug,
                "title": p.title,
                "page_type": p.page_type,
                "folder_id": p.folder_id or "",
                "summary": p.summary,
            }
            for p in rows
        ],
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total_pages,
        "total_pages": total_pages,
        "total_folders": len(folders),
    }


def wiki_folders(db: Session, kb_id: str) -> list[dict]:
    """全量目录元数据（轻量，仅用于深链定位父链 / 管理面板目录下拉）。"""
    folders = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id).order_by(WikiFolder.created_at)
    ).scalars().all()
    counts = _wiki_child_counts(db, kb_id)
    page_counts = _wiki_page_counts(db, kb_id)
    totals = _subtree_file_totals(db, kb_id, WikiFolder, page_counts)
    return [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id or "",
            "child_count": counts.get(f.id, 0),
            "page_count": page_counts.get(f.id, 0),
            "page_total": totals.get(f.id, 0),
        }
        for f in folders
    ]


def get_wiki_page(db: Session, kb_id: str, slug: str) -> dict | None:
    page = db.execute(
        select(WikiPage).where(
            WikiPage.kb_id == kb_id,
            WikiPage.slug == slug,
            WikiPage.status == "active",
        )
    ).scalars().first()
    if not page:
        return None

    # 出链（本页 -> 其他页）
    links = db.execute(
        select(WikiLink).where(
            WikiLink.kb_id == kb_id, WikiLink.from_page_id == page.id
        )
    ).scalars().all()
    linked_pages = []
    if links:
        linked_ids = [l.to_page_id for l in links]
        linked = db.execute(
            select(WikiPage).where(WikiPage.id.in_(linked_ids), WikiPage.status == "active")
        ).scalars().all()
        linked_pages = [
            {"slug": lp.slug, "title": lp.title, "page_type": lp.page_type} for lp in linked
        ]

    # 反向链接（其他页 -> 本页）
    in_links_rows = db.execute(
        select(WikiLink).where(
            WikiLink.kb_id == kb_id, WikiLink.to_page_id == page.id
        )
    ).scalars().all()
    in_links = []
    if in_links_rows:
        in_ids = [l.from_page_id for l in in_links_rows]
        in_pages = db.execute(
            select(WikiPage).where(WikiPage.id.in_(in_ids), WikiPage.status == "active")
        ).scalars().all()
        in_links = [
            {"slug": ip.slug, "title": ip.title, "page_type": ip.page_type} for ip in in_pages
        ]

    return {
        "id": page.id,
        "slug": page.slug,
        "title": page.title,
        "page_type": page.page_type,
        "content": page.content,
        "summary": page.summary,
        "source_refs": page.source_refs or [],
        "folder_id": page.folder_id or "",
        "folder_ids": page.folder_ids or ([page.folder_id] if page.folder_id else []),
        "links": linked_pages,
        "in_links": in_links,
        "created_at": page.created_at.isoformat() if page.created_at else None,
        "updated_at": page.updated_at.isoformat() if page.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Wiki 知识图谱（数据源：wiki_page + wiki_link，不依赖 Neo4j）
# ---------------------------------------------------------------------------

PAGE_TYPE_LABELS = {
    "entity": "实体",
    "concept": "概念",
    "summary": "摘要",
    "synthesis": "综合",
    "comparison": "对比",
    "business_ontology": "业务本体",
    "rule_ontology": "规则本体",
    "original_sentence": "原句",
    "frequent_keyword": "高频关键词",
    "topic_cluster": "主题簇",
    "knowledge_graph_summary": "图谱摘要",
    "cross_document_insight": "跨文档洞察",
}

GRAPH_PAGE_TYPES = list(PAGE_TYPE_LABELS.keys())


def wiki_graph(
    db: Session,
    kb_id: str,
    mode: str = "overview",
    center: str = "",
    depth: int = 1,
    limit: int = 200,
    types: list[str] | None = None,
) -> dict:
    """图谱数据：nodes = wiki pages, edges = wiki_links。

    mode=overview 返回全库图（受 limit 截断）；mode=ego 以 center slug 为中心
    返回 depth 跳邻域。types 过滤 page_type。

    性能：只取图谱所需列（不加载 content 等大字段）；度数用 SQL 聚合；
    edges 在 SQL 侧按选中集过滤（避免全表读 + Python 逐条筛选）。
    """
    allowed_types = [t for t in (types or []) if t in GRAPH_PAGE_TYPES]
    type_filter = set(allowed_types) if allowed_types else set(GRAPH_PAGE_TYPES)

    pages = db.execute(
        select(
            WikiPage.id,
            WikiPage.slug,
            WikiPage.title,
            WikiPage.page_type,
            WikiPage.summary,
        ).where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
    ).all()
    total_pages = len(pages)
    page_ids = {p.id for p in pages}
    slug_by_id = {p.id: p.slug for p in pages}
    if not page_ids:
        return {"nodes": [], "edges": [], "meta": {"mode": mode, "total": 0, "returned": 0, "truncated": False}}

    # 度数（出+入）：SQL 分组聚合，避免把每条边都拉进 Python 再逐条判断。
    # join 保证另一端也是本 KB 的 active 页（与原 `frm in page_ids and to in page_ids` 语义一致）。
    link_counts: dict[str, int] = {}
    for pid, cnt in db.execute(
        select(WikiLink.from_page_id, func.count())
        .join(WikiPage, WikiPage.id == WikiLink.to_page_id)
        .where(
            WikiLink.kb_id == kb_id,
            WikiPage.kb_id == kb_id,
            WikiPage.status == "active",
        )
        .group_by(WikiLink.from_page_id)
    ).all():
        if pid in page_ids:
            link_counts[pid] = link_counts.get(pid, 0) + cnt
    for pid, cnt in db.execute(
        select(WikiLink.to_page_id, func.count())
        .join(WikiPage, WikiPage.id == WikiLink.from_page_id)
        .where(
            WikiLink.kb_id == kb_id,
            WikiPage.kb_id == kb_id,
            WikiPage.status == "active",
        )
        .group_by(WikiLink.to_page_id)
    ).all():
        if pid in page_ids:
            link_counts[pid] = link_counts.get(pid, 0) + cnt

    # 选择节点集
    if mode == "ego" and center:
        center_page = next((p for p in pages if p.slug == center), None)
        if not center_page:
            return {"nodes": [], "edges": [], "meta": {"mode": "ego", "total": 0, "returned": 0, "truncated": False}}
        selected: set[str] = {center_page.id}
        frontier: set[str] = {center_page.id}
        for _ in range(max(1, depth)):
            if not frontier:
                break
            # 只查与当前前沿相邻的边（SQL 侧过滤），不再遍历全量 link_rows
            rows = db.execute(
                select(WikiLink.from_page_id, WikiLink.to_page_id).where(
                    WikiLink.kb_id == kb_id,
                    or_(
                        WikiLink.from_page_id.in_(frontier),
                        WikiLink.to_page_id.in_(frontier),
                    ),
                )
            ).all()
            next_frontier: set[str] = set()
            for frm, to in rows:
                if frm in frontier and to in page_ids and to not in selected:
                    next_frontier.add(to)
                if to in frontier and frm in page_ids and frm not in selected:
                    next_frontier.add(frm)
            selected |= next_frontier
            frontier = next_frontier
    else:
        # overview：按链接数取 top-N（默认全库，limit 截断）
        ranked = sorted(
            [p for p in pages if p.page_type in type_filter],
            key=lambda p: link_counts.get(p.id, 0),
            reverse=True,
        )
        selected = {p.id for p in ranked[:limit]}

    nodes = [
        {
            "slug": p.slug,
            "title": p.title,
            "page_type": p.page_type,
            "link_count": link_counts.get(p.id, 0),
            "summary": p.summary,
        }
        for p in pages
        if p.id in selected and p.page_type in type_filter
    ]

    # edges 只在选中集之间取（选中集 ≤ limit，IN 参数规模可控）
    edges: list[dict] = []
    sel_ids = [p.id for p in pages if p.id in selected]
    if sel_ids:
        for frm, to in db.execute(
            select(WikiLink.from_page_id, WikiLink.to_page_id).where(
                WikiLink.kb_id == kb_id,
                WikiLink.from_page_id.in_(sel_ids),
                WikiLink.to_page_id.in_(sel_ids),
            )
        ).all():
            edges.append({"source": slug_by_id[frm], "target": slug_by_id[to]})

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "mode": mode,
            "total": total_pages,
            "returned": len(nodes),
            "truncated": total_pages > limit and mode != "ego",
        },
    }


# ---------------------------------------------------------------------------
# JSON search (non-streaming retrieval for the frontend search box)
# ---------------------------------------------------------------------------


def json_search(
    db: Session,
    kb_id: str,
    query: str,
    query_embedding: list[float] | None = None,
    top_k: int = 5,
    threshold: float = 0.2,
) -> list[dict]:
    """Hybrid search returning serializable hit dicts (no LLM involved)."""
    from api.services.retrieval import config_from_kb

    kb = get_kb(db, kb_id)
    if not kb:
        return []
    cfg = config_from_kb(kb, {"top_k": top_k, "threshold": threshold})
    hits: list[ChunkHit] = hybrid_search(db, kb_id, query, query_embedding, cfg)
    return [
        {
            "chunk_id": h.chunk_id,
            "content": h.content,
            "document_id": h.document_id,
            "kb_id": h.kb_id,
            "score": round(h.score, 4),
        }
        for h in hits
    ]


def wiki_search(db: Session, kb_id: str, query: str, limit: int = 20) -> dict:
    """Wiki 页面内搜索：按标题或内容 ilike 匹配（分页式、应用侧小结果集）。

    返回：{query, total, items:[{slug,title,page_type,summary,content_head}]}
    """
    q = (query or "").strip()
    if not q:
        return {"query": q, "total": 0, "items": []}
    like = f"%{q}%"
    stmt = (
        select(WikiPage)
        .where(
            WikiPage.kb_id == kb_id,
            WikiPage.status == "active",
            or_(WikiPage.title.like(like), WikiPage.content.like(like)),
        )
        .limit(limit)
    )
    pages = db.execute(stmt).scalars().all()
    items = [
        {
            "slug": p.slug,
            "title": p.title,
            "page_type": p.page_type,
            "summary": p.summary,
            "content_head": (p.content or "")[:200],
        }
        for p in pages
    ]
    return {"query": q, "total": len(items), "items": items}


def wiki_log_action(
    db: Session,
    kb_id: str,
    action: str,
    slug: str = "",
    title: str = "",
    detail: str = "",
    operator: str = "",
) -> None:
    """记录一条 wiki 操作日志（页面/目录 CRUD、链接重建等）。"""
    db.add(
        WikiOperationLog(
            id=uuid.uuid4().hex,
            kb_id=kb_id,
            action=action,
            slug=slug,
            title=title,
            detail=detail,
            operator=operator,
        )
    )


def wiki_list_logs(db: Session, kb_id: str, limit: int = 100) -> dict:
    """Wiki 操作日志（倒序，最近 limit 条）。"""
    rows = (
        db.execute(
            select(WikiOperationLog)
            .where(WikiOperationLog.kb_id == kb_id)
            .order_by(WikiOperationLog.created_at.desc(), WikiOperationLog.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return {
        "kb_id": kb_id,
        "total": len(rows),
        "items": [
            {
                "id": r.id,
                "action": r.action,
                "slug": r.slug,
                "title": r.title,
                "detail": r.detail,
                "operator": r.operator,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


def _wiki_page_title(db: Session, kb_id: str, slug: str) -> str:
    """反规范化页面标题：提交反馈时查一次，维护中心列表免 join。"""
    if not slug:
        return ""
    row = db.execute(
        select(WikiPage.title).where(
            WikiPage.kb_id == kb_id, WikiPage.slug == slug, WikiPage.status == "active"
        )
    ).scalar()
    return row or ""


def _wiki_user_name(db: Session, user_id: str) -> str:
    """反规范化上报人姓名：user_name 优先，其次 email；查不到退回空串。"""
    if not user_id:
        return ""
    row = db.execute(select(User.user_name).where(User.user_id == user_id)).scalar()
    if row:
        return row
    row = db.execute(select(User.email).where(User.user_id == user_id)).scalar()
    return row or ""


def wiki_submit_feedback(
    db: Session, kb_id: str, slug: str, user_id: str, feedback_type: str, content: str, preset: str = ""
) -> dict:
    """提交页面反馈（comment=评论 / question=问题；兼容旧 helpful/issue）。

    - preset 非空时按白名单校验；未填内容时自动回填预设中文文案（对齐 WeKnora）
    - page_title / reported_by_name 写入时反规范化（维护中心列表免 join）
    - 状态默认 pending（WeKnora 语义；旧 open 由迁移 0031 归并）
    """
    preset = (preset or "").strip()
    if preset:
        if preset == "custom":
            # 高频词「自定义」标注：内容必填，直接透传
            if not (content or "").strip():
                raise ValueError("自定义标注需填写内容")
        elif preset not in WIKI_FEEDBACK_PRESETS:
            raise ValueError(f"无效的预设标注: {preset}")
        else:
            if not (content or "").strip():
                content = WIKI_FEEDBACK_PRESETS[preset]
            if not feedback_type:
                feedback_type = "question"
    if feedback_type not in (*WIKI_FEEDBACK_TYPES, "helpful", "issue"):
        raise ValueError(f"无效的反馈类型: {feedback_type}")
    if not (content or "").strip():
        # 一键「有帮助」允许空内容（对齐 WeKnora 点赞语义），自动回填文案
        if feedback_type == "helpful":
            content = "有帮助"
        else:
            raise ValueError("反馈内容不能为空")
    fb = WikiFeedback(
        id=uuid.uuid4().hex,
        kb_id=kb_id,
        slug=slug,
        user_id=user_id,
        feedback_type=feedback_type,
        preset=preset,
        content=content,
        status="pending",
        page_title=_wiki_page_title(db, kb_id, slug),
        reported_by_name=_wiki_user_name(db, user_id),
    )
    db.add(fb)
    db.commit()
    return {"id": fb.id, "feedback_type": fb.feedback_type, "preset": fb.preset, "status": fb.status}


# ── 高频词管理（迁移自 WeKnora wiki/keywords/overview）──
# kb 模板开头句格式：「本关键词在 N 篇文档中高频出现（总频次：M）。」
# 分组标题：「**《来源文件名》**（频次：a）」。两条正则与 WeKnora wiki_page.go 逐字兼容。
_FREQ_HEAD_RE = re.compile(r"在\s*(\d+)\s*篇文档中高频出现（总频次：\s*(\d+)\s*）")
_FREQ_GROUP_RE = re.compile(r"（频次：(\d+)）")
# 释义段（2026-10-28 起模板支持）：`**释义**：xxx` / `**释义**: xxx`，LLM 生成的业务释义
_MEANING_RE = re.compile(r"\*\*释义\*\*[：:]\s*(.+?)(?:\n|$)")


def _parse_keyword_head(head: str) -> tuple[int, int]:
    """从高频词页 content 前段解析 (文档数, 总频次)。"""
    if m := _FREQ_HEAD_RE.search(head or ""):
        docs = int(m.group(1))
        freq = int(m.group(2))
        return docs, freq
    # 旧规范页面无标准开头句：按分组频次累加兜底
    freq = sum(int(x) for x in _FREQ_GROUP_RE.findall(head or ""))
    return (1 if freq else 0), freq


def wiki_keyword_overview(
    db: Session,
    kb_id: str,
    search: str = "",
    sort_by: str = "freq",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """知识库高频词概览：聚合 frequent_keyword 页面，按总频次降序排列。

    词频从页面 content 头部结构解析（不加载全文）。标注计数按 preset 聚合，
    供前端展示 ✅/❌/⚠️。
    """
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 50
    if page_size > 200:
        page_size = 200

    rows = (
        db.execute(
            select(WikiPage.slug, WikiPage.title, func.substr(WikiPage.content, 1, 500).label("content_head"))
            .where(WikiPage.kb_id == kb_id, WikiPage.page_type == "frequent_keyword", WikiPage.status == "active")
        )
        .all()
    )

    # 每词页的快捷标注计数（preset → 条数；软删标注不计入）
    fb_rows = (
        db.execute(
            select(WikiFeedback.slug, WikiFeedback.preset, func.count().label("cnt"))
            .where(
                WikiFeedback.kb_id == kb_id,
                WikiFeedback.preset != "",
                WikiFeedback.deleted_at.is_(None),
            )
            .group_by(WikiFeedback.slug, WikiFeedback.preset)
        )
        .all()
    )
    feedback_counts: dict[str, dict[str, int]] = {}
    for slug, preset, cnt in fb_rows:
        key = preset or ""
        if not key:
            continue
        feedback_counts.setdefault(slug or "", {})[key] = int(cnt)

    stats: list[dict] = []
    for slug, title, head in rows:
        keyword = (title or "").strip()
        if not keyword:
            keyword = (slug or "").strip()
        if search and search not in keyword:
            continue
        docs, freq = _parse_keyword_head(head or "")
        # 释义段解析（2026-10-28：市场域/code_rev 域模板均可能在开头句前
        # 插入 `**释义**：xxx` 行——LLM 生成的业务释义，供前端「含义」列展示；
        # 无释义行的存量页面为空串，前端显示占位符）
        meaning = ""
        if m := _MEANING_RE.search(head or ""):
            meaning = m.group(1).strip()
        stats.append(
            {
                "slug": slug,
                "keyword": keyword,
                "meaning": meaning,
                "total_freq": freq,
                "doc_count": docs,
                "feedback_counts": feedback_counts.get(slug, {}),
            }
        )

    asc = order == "asc"
    if sort_by == "keyword":
        stats.sort(key=lambda s: s["keyword"], reverse=not asc)
    else:  # freq
        stats.sort(
            key=lambda s: (s["total_freq"], s["doc_count"], -ord(s["keyword"][0]) if s["keyword"] else 0),
            reverse=not asc,
        )

    total = len(stats)
    start = (page - 1) * page_size
    end = start + page_size
    items = stats[start:end]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# 高频词标注预设白名单（独立通道，字面量——不与并行会话的 WIKI_FEEDBACK_PRESETS 耦合）
_KEYWORD_MARK_PRESETS = ("kw_meaningful", "kw_noise", "kw_mis_extracted", "custom")


def wiki_keyword_mark(
    db: Session, kb_id: str, slug: str, user_id: str, preset: str, content: str = ""
) -> dict:
    """高频词业务含义标注（迁移自 WeKnora WikiFeedbackPresetKeys）。

    独立于普通页面反馈通道：preset 白名单校验 + custom 必填内容，
    落 wiki_feedback 表（feedback_type=question，status 用模型默认）。
    """
    preset = (preset or "").strip()
    if preset not in _KEYWORD_MARK_PRESETS:
        raise ValueError(f"无效的预设标注: {preset}")
    if preset == "custom" and not (content or "").strip():
        raise ValueError("自定义标注需填写内容")
    fb = WikiFeedback(
        id=uuid.uuid4().hex,
        kb_id=kb_id,
        slug=slug,
        user_id=user_id,
        feedback_type="question",
        preset=preset,
        content=content,
    )
    db.add(fb)
    db.commit()
    return {"id": fb.id, "preset": fb.preset, "status": fb.status}


def _feedback_item(r: WikiFeedback) -> dict:
    """反馈行序列化（维护中心列表展示字段）。"""
    return {
        "id": r.id,
        "slug": r.slug,
        "page_title": r.page_title or "",
        "user_id": r.user_id or "",
        "reported_by_name": r.reported_by_name or "",
        "feedback_type": r.feedback_type,
        "preset": r.preset or "",
        "content": r.content or "",
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def wiki_list_feedback(
    db: Session,
    kb_id: str,
    slug: str = "",
    feedback_type: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """页面反馈列表（slug/feedback_type/status 过滤 + 分页，倒序；软删不可见）。"""
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 50
    if page_size > 200:
        page_size = 200
    stmt = select(WikiFeedback).where(
        WikiFeedback.kb_id == kb_id, WikiFeedback.deleted_at.is_(None)
    )
    if slug:
        stmt = stmt.where(WikiFeedback.slug == slug)
    if feedback_type:
        stmt = stmt.where(WikiFeedback.feedback_type == feedback_type)
    if status:
        stmt = stmt.where(WikiFeedback.status == status)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0
    rows = (
        db.execute(
            stmt.order_by(WikiFeedback.created_at.desc(), WikiFeedback.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    return {
        "kb_id": kb_id,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_feedback_item(r) for r in rows],
    }


def wiki_update_feedback_status(
    db: Session, kb_id: str, feedback_id: str, status: str
) -> dict:
    """更新反馈状态（pending/resolved/ignored；软删行不可操作）。"""
    if status not in WIKI_FEEDBACK_STATUSES:
        raise ValueError(f"无效的反馈状态: {status}")
    fb = db.execute(
        select(WikiFeedback).where(
            WikiFeedback.kb_id == kb_id,
            WikiFeedback.id == feedback_id,
            WikiFeedback.deleted_at.is_(None),
        )
    ).scalars().first()
    if not fb:
        raise ValueError(f"反馈不存在: {feedback_id}")
    fb.status = status
    db.commit()
    return {"id": fb.id, "status": fb.status}


def wiki_delete_feedback(db: Session, kb_id: str, feedback_id: str) -> dict:
    """软删反馈（对齐 WeKnora soft-delete；deleted_at 置位）。"""
    fb = db.execute(
        select(WikiFeedback).where(
            WikiFeedback.kb_id == kb_id,
            WikiFeedback.id == feedback_id,
            WikiFeedback.deleted_at.is_(None),
        )
    ).scalars().first()
    if not fb:
        raise ValueError(f"反馈不存在: {feedback_id}")
    fb.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"id": fb.id, "deleted": True}


def wiki_index(db: Session, kb_id: str) -> dict:
    """Wiki 索引：目录树 + 页面按类型统计 + 最近更新列表。"""
    folders = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id).order_by(WikiFolder.created_at)
    ).scalars().all()
    pages = db.execute(
        select(WikiPage)
        .where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
        .order_by(WikiPage.created_at.desc(), WikiPage.id.desc())
    ).scalars().all()

    by_type: dict[str, int] = {}
    for p in pages:
        by_type[p.page_type or "other"] = by_type.get(p.page_type or "other", 0) + 1

    recent = [
        {
            "slug": p.slug,
            "title": p.title,
            "page_type": p.page_type,
            "folder_id": p.folder_id or "",
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        }
        for p in pages[:30]
    ]

    folder_tree = [
        {"id": f.id, "name": f.name, "parent_id": f.parent_id or ""} for f in folders
    ]

    return {
        "kb_id": kb_id,
        "folder_tree": folder_tree,
        "pages_by_type": by_type,
        "recent_pages": recent,
        "total_pages": len(pages),
        "total_folders": len(folders),
    }


def wiki_stats(db: Session, kb_id: str) -> dict:
    """Wiki 统计：页数（按类型）/ 目录数 / 链接数 / 孤儿页数（无入链无出链）。"""
    pages = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
    ).scalars().all()
    folders = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id)
    ).scalars().all()
    links = db.execute(
        select(WikiLink).where(WikiLink.kb_id == kb_id)
    ).scalars().all()

    by_type: dict[str, int] = {}
    for p in pages:
        by_type[p.page_type or "other"] = by_type.get(p.page_type or "other", 0) + 1

    linked_page_ids = {
        pid
        for link in links
        for pid in (link.from_page_id, link.to_page_id)
    }
    orphan_count = sum(1 for p in pages if p.id not in linked_page_ids)

    return {
        "kb_id": kb_id,
        "total_pages": len(pages),
        "total_folders": len(folders),
        "total_links": len(links),
        "pages_by_type": by_type,
        "orphan_count": orphan_count,
    }


def _sync_page_folders(db: Session, kb_id: str, page: WikiPage, folder_ids: list) -> None:
    """同步页面多目录挂载（wiki_page_folder 关联表 + folder_ids JSON 冗余列）。

    主目录仍存 page.folder_id（取 folder_ids 首项）；关联表记全部挂载（含主目录）。
    folder_ids 为空/None → 清空挂载（仅保留 folder_id 主目录本身）。
    """
    from api.models.knowledge import WikiPageFolder

    db.execute(
        delete(WikiPageFolder).where(
            WikiPageFolder.kb_id == kb_id, WikiPageFolder.page_id == page.id
        )
    )
    if folder_ids:
        fids = [str(f) for f in folder_ids if str(f)]
        page.folder_ids = fids
        if not page.folder_id:
            page.folder_id = fids[0]
        for fid in fids:
            db.add(WikiPageFolder(
                id=uuid.uuid4().hex, kb_id=kb_id, page_id=page.id, folder_id=fid,
            ))
    else:
        page.folder_ids = [page.folder_id] if page.folder_id else []


def _filter_alive_doc_refs(db: Session, kb_id: str, refs: list) -> list:
    """过滤 source_refs：剔除引用的文档已被删除的条目。

    平台契约 source_refs 形如 ``{document_id}|{file_name}``。文档删除后编排
    在途节点仍可能写入该文档的专属页（实测：删完文档后 keyword 页继续落库
    43 页孤儿）——写页入口统一过本函数，引用文档全已删则返回空列表，
    调用方据此拒绝写入；部分已删则只保留存活引用（防残留引用死 id）。
    非 ``document_id|`` 形态的引用不参与校验（历史数据兼容）。
    """
    if not refs:
        return refs
    doc_ids = {
        str(r).split("|", 1)[0]
        for r in refs
        if isinstance(r, str) and "|" in r and str(r).split("|", 1)[0]
    }
    if not doc_ids:
        return refs
    alive = set(
        db.execute(
            select(KbDocument.id).where(
                KbDocument.kb_id == kb_id, KbDocument.id.in_(doc_ids)
            )
        ).scalars()
    )
    kept = [
        r
        for r in refs
        if not (isinstance(r, str) and "|" in r and str(r).split("|", 1)[0] in doc_ids
                and str(r).split("|", 1)[0] not in alive)
    ]
    return kept


def wiki_create_page(db: Session, kb_id: str, data: dict, user_id: str | None = None) -> dict:
    """创建 wiki 页面。slug 缺省由 title 生成（保证 kb 内唯一，重复加序号）。"""
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("title 不能为空")
    slug = str(data.get("slug") or "").strip() or slugify(title)
    # 2026-10-21：删文档后编排在途节点写页兜底——source_refs 引用的文档已全删
    # 则拒绝写入（否则删文档后仍生成孤儿页，删文档时的级联清理又查不到它）。
    refs_in = data.get("source_refs") or []
    if refs_in:
        refs_alive = _filter_alive_doc_refs(db, kb_id, refs_in)
        if not refs_alive:
            raise ValueError("source_refs 引用的文档已删除，跳过写页")
        data = {**data, "source_refs": refs_alive}
    existing = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.slug == slug)
    ).scalars().first()
    if existing:
        raise ValueError(f"slug 已存在: {slug}")
    page = WikiPage(
        id=uuid.uuid4().hex,
        kb_id=kb_id,
        slug=slug,
        title=title,
        page_type=str(data.get("page_type") or "entity"),
        content=str(data.get("content") or ""),
        summary=data.get("summary") or None,
        source_refs=data.get("source_refs") or [],
        folder_id=str(data.get("folder_id") or ""),
        status="active",
        created_by=user_id,
    )
    db.add(page)
    fids = data.get("folder_ids") or ([str(data.get("folder_id") or "")] if data.get("folder_id") else [])
    _sync_page_folders(db, kb_id, page, fids)
    wiki_log_action(
        db, kb_id, "page_create", slug=page.slug, title=page.title,
        detail=f"page_type={page.page_type}", operator=user_id or "",
    )
    db.commit()
    return {"id": page.id, "slug": page.slug, "title": page.title}


def wiki_update_page(db: Session, kb_id: str, slug: str, data: dict) -> dict:
    """更新 wiki 页面（标题/content/摘要/类型/目录/状态）。slug 本身不可改。"""
    page = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.slug == slug)
    ).scalars().first()
    if not page:
        raise ValueError(f"wiki 页不存在: {slug}")
    if "title" in data:
        t = str(data["title"]).strip()
        if not t:
            raise ValueError("title 不能为空")
        page.title = t
    if "content" in data:
        page.content = str(data["content"] or "")
    if "summary" in data:
        page.summary = data["summary"] or None
    if "page_type" in data:
        page.page_type = str(data["page_type"] or "entity")
    if "folder_id" in data:
        page.folder_id = str(data["folder_id"] or "")
    if "folder_ids" in data:
        _sync_page_folders(db, kb_id, page, data["folder_ids"] or [])
    if "source_refs" in data:
        # 合并式更新：保留既有引用 + 补齐新引用（多文档共享页不丢其他文档归属；
        # 单文档页首次补归属也不覆盖已有的同文档引用重复）。
        cur = page.source_refs if isinstance(page.source_refs, list) else []
        new = [str(r) for r in (data["source_refs"] or []) if str(r).strip()]
        merged = list(cur)
        for r in new:
            if r not in merged:
                merged.append(r)
        page.source_refs = merged
    if "status" in data:
        page.status = str(data["status"] or "active")
    wiki_log_action(
        db, kb_id, "page_update", slug=page.slug, title=page.title,
        detail=f"updated fields: {','.join(data.keys())}",
    )
    db.commit()
    return {"id": page.id, "slug": page.slug}


def wiki_delete_page(db: Session, kb_id: str, slug: str) -> dict:
    """删除 wiki 页面（软删：status=archived，保留数据便于恢复）。

    同时清理该页全部出入链（避免孤儿链接指向已归档页）。
    """
    page = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.slug == slug)
    ).scalars().first()
    if not page:
        raise ValueError(f"wiki 页不存在: {slug}")
    page.status = "archived"
    # 清理关联链接：以该页为端点（出/入）的全部 wiki_link
    from sqlalchemy import delete as sa_delete

    db.execute(
        sa_delete(WikiLink).where(
            WikiLink.kb_id == kb_id,
            (WikiLink.from_page_id == page.id) | (WikiLink.to_page_id == page.id),
        )
    )
    wiki_log_action(
        db, kb_id, "page_delete", slug=page.slug, title=page.title, detail="soft-delete",
    )
    db.commit()
    return {"deleted": True, "slug": slug}


def wiki_create_folder(
    db: Session, kb_id: str, data: dict, user_id: str | None = None
) -> dict:
    """创建目录。parent_id 缺省为根。"""
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("name 不能为空")
    folder = WikiFolder(
        id=uuid.uuid4().hex,
        kb_id=kb_id,
        name=name,
        parent_id=str(data.get("parent_id") or ""),
        created_by=user_id,
    )
    db.add(folder)
    wiki_log_action(
        db, kb_id, "folder_create", title=folder.name, operator=user_id or "",
    )
    db.commit()
    return {"id": folder.id, "name": folder.name}


def wiki_update_folder(db: Session, kb_id: str, folder_id: str, data: dict) -> dict:
    """重命名 / 移动目录。"""
    folder = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id, WikiFolder.id == folder_id)
    ).scalars().first()
    if not folder:
        raise ValueError(f"目录不存在: {folder_id}")
    if "name" in data:
        n = str(data["name"]).strip()
        if not n:
            raise ValueError("name 不能为空")
        folder.name = n
    if "parent_id" in data:
        folder.parent_id = str(data["parent_id"] or "")
    wiki_log_action(
        db, kb_id, "folder_update", title=folder.name,
        detail=f"updated: {','.join(data.keys())}",
    )
    db.commit()
    return {"id": folder.id, "name": folder.name}


def wiki_delete_folder(db: Session, kb_id: str, folder_id: str) -> dict:
    """删除目录：仅当目录下无页面时允许；有页面则抛错提示先移走。"""
    folder = db.execute(
        select(WikiFolder).where(WikiFolder.kb_id == kb_id, WikiFolder.id == folder_id)
    ).scalars().first()
    if not folder:
        raise ValueError(f"目录不存在: {folder_id}")
    child_pages = db.execute(
        select(WikiPage).where(
            WikiPage.kb_id == kb_id,
            WikiPage.status == "active",
            WikiPage.folder_id == folder_id,
        )
    ).scalars().all()
    if child_pages:
        raise ValueError(f"目录下仍有 {len(child_pages)} 个页面，请先移走")
    child_folders = db.execute(
        select(WikiFolder).where(
            WikiFolder.kb_id == kb_id, WikiFolder.parent_id == folder_id
        )
    ).scalars().all()
    for cf in child_folders:
        cf.parent_id = ""  # 子目录上移到根
    wiki_log_action(
        db, kb_id, "folder_delete", title=folder.name,
        detail=f"moved up {len(child_folders)} child folders",
    )
    db.delete(folder)
    db.commit()
    return {"deleted": True, "folder_id": folder_id}


def wiki_lint(db: Session, kb_id: str, limit: int = 200) -> dict:
    """Wiki 健康检查：空内容页 / 孤立页（无入链无出链）/ 断链目标。

    返回 issue 列表：{slug, title, issue_type, detail}。
    """
    pages = {
        p.slug: p
        for p in db.execute(
            select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
        ).scalars().all()
    }
    links = db.execute(
        select(WikiLink).where(WikiLink.kb_id == kb_id)
    ).scalars().all()

    page_ids = {p.id for p in pages.values()}
    slug_by_id = {p.id: p.slug for p in pages.values()}

    linked_ids = {pid for l in links for pid in (l.from_page_id, l.to_page_id)}
    issues: list[dict] = []

    # 空内容页
    for p in pages.values():
        if not (p.content or "").strip():
            issues.append(
                {"slug": p.slug, "title": p.title, "issue_type": "empty_content", "detail": "页面内容为空"}
            )

    # 孤立页（无入链且无出链）
    for p in pages.values():
        if p.id not in linked_ids:
            issues.append(
                {"slug": p.slug, "title": p.title, "issue_type": "orphan", "detail": "无任何双向链接"}
            )

    # 断链：content 中的 [[slug]] 目标不存在
    import re as _re

    broken = 0
    for p in pages.values():
        targets = _re.findall(r"\[\[([^\]|]+)", p.content or "")
        for t in targets:
            if t not in pages:
                broken += 1
        if broken > limit:
            break

    return {
        "kb_id": kb_id,
        "total_issues": len(issues),
        "issues": issues[:limit],
        "broken_link_count": broken,
    }


def wiki_rebuild_links(db: Session, kb_id: str) -> dict:
    """重建双向链接：清空该 kb 全部 wiki_link，按页面 content 中的 [[slug]]
    wikilink 重新生成（A→B 与 B→A 各一条）。"""
    pages = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
    ).scalars().all()
    slug_to_page = {p.slug: p for p in pages}

    # 清空旧链
    from sqlalchemy import delete as sa_delete

    db.execute(sa_delete(WikiLink).where(WikiLink.kb_id == kb_id))
    db.flush()

    import re as _re

    added = 0
    for p in pages:
        targets = _re.findall(r"\[\[([^\]|]+)", p.content or "")
        seen: set[str] = set()
        for t in targets:
            # 页面内去重：select 去重查不到本事务未 flush 的 pending 行，
            # 同页重复 wikilink 会插两条触发 uk_wiki_link_pair 冲突
            if t in seen:
                continue
            seen.add(t)
            target = slug_to_page.get(t)
            if not target or target.id == p.id:
                continue
            if (
                db.execute(
                    select(WikiLink).where(
                        WikiLink.kb_id == kb_id,
                        WikiLink.from_page_id == p.id,
                        WikiLink.to_page_id == target.id,
                    )
                ).scalars().first()
                is None
            ):
                db.add(
                    WikiLink(
                        id=uuid.uuid4().hex,
                        kb_id=kb_id,
                        from_page_id=p.id,
                        to_page_id=target.id,
                        link_type="related",
                    )
                )
                added += 1
    wiki_log_action(
        db, kb_id, "rebuild_links", detail=f"added {added} links",
    )
    db.commit()
    return {"added_links": added}


# ---------------------------------------------------------------------------
# MCP 对齐补充（2026-10-11）：WeKnora admin-kb 工具所需底层能力
# ---------------------------------------------------------------------------


def get_document_detail(db: Session, kb_id: str, document_id: str) -> dict | None:
    """单文档详情（对齐 WeKnora get_document）。"""
    doc = db.execute(
        select(KbDocument).where(KbDocument.id == document_id, KbDocument.kb_id == kb_id)
    ).scalars().first()
    if not doc:
        return None
    return {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "file_name": doc.file_name,
        "file_ext": doc.file_ext,
        "file_size": doc.file_size,
        "parse_state": doc.parse_state,
        "parse_error": doc.parse_error,
        "chunk_count": doc.chunk_count,
        "summary": doc.summary,
        "summary_status": doc.summary_status,
        "folder_id": doc.folder_id or "",
        "source_type": doc.source_type or "file",
        "ds_source_name": doc.ds_source_name,
        "ds_table_name": doc.ds_table_name,
        "row_count": doc.row_count,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


def list_wiki_pages(
    db: Session,
    kb_id: str,
    page: int = 1,
    page_size: int = 20,
    folder_id: str = "",
    page_type: str = "",
    status: str = "active",
) -> dict:
    """分页列出 wiki 页（对齐 WeKnora list_wiki_pages）。"""
    from api.models.knowledge import WikiPageFolder

    stmt = select(WikiPage).where(WikiPage.kb_id == kb_id)
    if status:
        stmt = stmt.where(WikiPage.status == status)
    if page_type:
        stmt = stmt.where(WikiPage.page_type == page_type)
    if folder_id:
        # 主目录或挂载目录命中均算（多目录挂载页面在任一挂载目录下可见）
        sub = (
            select(WikiPageFolder.page_id)
            .where(WikiPageFolder.kb_id == kb_id, WikiPageFolder.folder_id == folder_id)
            .scalar_subquery()
        )
        stmt = stmt.where((WikiPage.folder_id == folder_id) | (WikiPage.id.in_(sub)))
    # 2026-10-21 性能修复：计数走 SQL count，原 len(all()) 会把全部匹配页全列
    # （content Text 正文）物化到 Python，大知识库分页时一次请求拖回整库正文。
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0)
    rows = (
        db.execute(
            stmt.order_by(WikiPage.created_at.desc(), WikiPage.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": p.id,
            "slug": p.slug,
            "title": p.title,
            "page_type": p.page_type,
            "summary": p.summary,
            "content": p.content,
            "folder_id": p.folder_id or "",
            "folder_ids": p.folder_ids or ([p.folder_id] if p.folder_id else []),
            "status": p.status,
            "source_refs": p.source_refs or [],
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in rows
    ]
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def wiki_lookup_by_title(
    db: Session, kb_id: str, titles: list[str], page_types: list[str] | None = None
) -> dict:
    """按精确标题批量查页面（对齐 WeKnora wiki_lookup_by_title）。"""
    if not titles:
        return {"items": []}
    stmt = select(WikiPage).where(
        WikiPage.kb_id == kb_id, WikiPage.status == "active", WikiPage.title.in_(titles)
    )
    if page_types:
        stmt = stmt.where(WikiPage.page_type.in_(page_types))
    rows = db.execute(stmt).scalars().all()
    return {
        "items": [
            {
                "id": p.id,
                "slug": p.slug,
                "title": p.title,
                "page_type": p.page_type,
                "summary": p.summary,
                "status": p.status,
            }
            for p in rows
        ]
    }


def wiki_move_page(db: Session, kb_id: str, slug: str, folder_id: str) -> dict:
    """移动 wiki 页到指定目录（folder_id="" = 根；对齐 WeKnora move_wiki_page）。"""
    page = db.execute(
        select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.slug == slug)
    ).scalars().first()
    if not page:
        raise ValueError(f"wiki 页不存在: {slug}")
    if folder_id:
        folder = db.execute(
            select(WikiFolder).where(WikiFolder.kb_id == kb_id, WikiFolder.id == folder_id)
        ).scalars().first()
        if not folder:
            raise ValueError(f"目录不存在: {folder_id}")
    page.folder_id = folder_id
    wiki_log_action(db, kb_id, "page_move", slug=page.slug, title=page.title, detail=f"folder_id={folder_id}")
    db.commit()
    return {"id": page.id, "slug": page.slug, "folder_id": folder_id}


def wiki_auto_fix(db: Session, kb_id: str) -> dict:
    """自动修复可修复的 wiki 问题（对齐 WeKnora wiki_auto_fix）。

    当前可修复项：
    - 断链：content 中的 [[slug]] 目标不存在 → 若目标存在则保留，否则修复为空
      （与 wiki_rebuild_links 语义一致：重建时自动跳过断链）。
    - 空内容页：保留并记日志（无法自动生成内容，不做删除以免误伤）。
    """
    pages = {
        p.slug: p
        for p in db.execute(
            select(WikiPage).where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
        ).scalars().all()
    }
    import re as _re

    fixed = 0
    for p in pages.values():
        if not (p.content or "").strip():
            continue
        targets = _re.findall(r"\[\[([^\]|]+)\]\]", p.content)
        for t in targets:
            if t not in pages:
                # 断链：剥掉 [[...]] 只留目标文本，作为纯文本保留
                p.content = p.content.replace(f"[[{t}]]", t)
                fixed += 1
    wiki_log_action(db, kb_id, "auto_fix", detail=f"fixed {fixed} broken links")
    db.commit()
    return {"fixed": fixed}


def wiki_log_write(
    db: Session,
    kb_id: str,
    action: str,
    knowledge_id: str = "",
    doc_title: str = "",
    summary: str = "",
    page_slugs: list[str] | None = None,
    operator: str = "",
) -> dict:
    """写一条 wiki 操作日志（对齐 WeKnora wiki_log_write，外部 agent 进度上报）。"""
    if not action:
        raise ValueError("action 不能为空")
    detail = summary or ""
    if page_slugs:
        detail = (detail + " | pages: " + ", ".join(page_slugs)).strip(" |")
    wiki_log_action(
        db, kb_id, action[:32],
        slug=knowledge_id or "",
        title=doc_title or "",
        detail=detail[:2000],
        operator=operator,
    )
    db.commit()
    return {"success": True, "action": action, "kb_id": kb_id}


def wiki_list_issues(db: Session, kb_id: str, slug: str = "", status: str = "") -> dict:
    """列出 wiki 问题（对齐 WeKnora wiki_list_issues）。"""
    stmt = select(WikiPageIssue).where(WikiPageIssue.kb_id == kb_id)
    if slug:
        stmt = stmt.where(WikiPageIssue.slug == slug)
    if status:
        stmt = stmt.where(WikiPageIssue.status == status)
    rows = (
        db.execute(stmt.order_by(WikiPageIssue.created_at.desc(), WikiPageIssue.id.desc()))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": r.id,
                "slug": r.slug,
                "issue_type": r.issue_type,
                "description": r.description,
                "detail": r.detail,
                "status": r.status,
                "reported_by": r.reported_by,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


def wiki_update_issue_status(db: Session, kb_id: str, issue_id: str, status: str) -> dict:
    """更新 issue 状态（pending | ignored | resolved，对齐 WeKnora）。"""
    if status not in ("pending", "ignored", "resolved"):
        raise ValueError(f"status 必须是 pending/ignored/resolved，收到: {status}")
    issue = db.execute(
        select(WikiPageIssue).where(WikiPageIssue.kb_id == kb_id, WikiPageIssue.id == issue_id)
    ).scalars().first()
    if not issue:
        raise ValueError(f"issue 不存在: {issue_id}")
    issue.status = status
    db.commit()
    return {"id": issue.id, "slug": issue.slug, "status": status}


def wiki_sync_issues_from_lint(db: Session, kb_id: str) -> dict:
    """把 lint 检出的问题同步进 wiki_page_issue 表（幂等 upsert）。

    供 lint 调用方可选：WeKnora 的 wiki_lint 会把问题落库，MCP 工具
    wiki_list_issues 读这张表。这里按 (kb_id, slug, issue_type) 去重 upsert。
    """
    lint_result = wiki_lint(db, kb_id, limit=500)
    issues = lint_result.get("issues") or []
    inserted = updated = 0
    for it in issues:
        row = db.execute(
            select(WikiPageIssue).where(
                WikiPageIssue.kb_id == kb_id,
                WikiPageIssue.slug == it.get("slug", ""),
                WikiPageIssue.issue_type == it.get("issue_type", ""),
            )
        ).scalars().first()
        if row is None:
            db.add(
                WikiPageIssue(
                    id=uuid.uuid4().hex,
                    kb_id=kb_id,
                    slug=it.get("slug", ""),
                    issue_type=it.get("issue_type", ""),
                    description=it.get("detail", ""),
                    detail=it.get("detail", ""),
                    status="pending",
                    reported_by="wiki-lint",
                )
            )
            inserted += 1
        else:
            row.description = it.get("detail", "")
            row.detail = it.get("detail", "")
            row.status = "pending" if row.status in ("", None) else row.status
            updated += 1
    db.commit()
    return {"inserted": inserted, "updated": updated, "total_issues": len(issues)}


def tokenize_text(text: str, mode: str = "cut") -> dict:
    """分词（对齐 WeKnora tokenize）。

    无外部分词依赖：CJK 走 2-gram 滑窗（WeKnora 同款 tokenizeCJKBigram），
    ASCII 走单词切分。mode=cut_for_search 时对 CJK 2-gram 做去重。
    """
    import re as _re

    if not text:
        return {"words": [], "count": 0}

    def cjk_bigrams(s: str) -> list[str]:
        chars = _re.sub(r"\s+", "", s)
        if len(chars) < 2:
            return list(chars)
        return [chars[i : i + 2] for i in range(len(chars) - 1)]

    words: list[str] = []
    # 把 CJK 段和 ASCII 段分开处理
    for seg in _re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]+", text):
        if _re.fullmatch(r"[\u4e00-\u9fff]+", seg):
            words.extend(cjk_bigrams(seg))
        else:
            words.append(seg)
    if mode == "cut_for_search":
        seen: set[str] = set()
        deduped: list[str] = []
        for w in words:
            if w not in seen:
                seen.add(w)
                deduped.append(w)
        words = deduped
    return {"words": words, "count": len(words)}


__all__ = [
    "create_document",
    "create_kb",
    "delete_document",
    "delete_kb",
    "get_kb",
    "get_wiki_page",
    "json_search",
    "list_documents",
    "list_kbs",
    "update_kb",
    "wiki_tree",
    "wiki_stats",
    "wiki_search",
    "wiki_lint",
    "wiki_create_page",
    "wiki_update_page",
    "wiki_delete_page",
    "wiki_create_folder",
    "wiki_update_folder",
    "wiki_delete_folder",
    "wiki_rebuild_links",
    "wiki_log_action",
    "wiki_list_logs",
    "wiki_submit_feedback",
    "wiki_list_feedback",
    "wiki_update_feedback_status",
    "wiki_delete_feedback",
    "wiki_index",
    "get_document_detail",
    "list_wiki_pages",
    "wiki_lookup_by_title",
    "wiki_move_page",
    "wiki_auto_fix",
    "wiki_log_write",
    "wiki_list_issues",
    "wiki_update_issue_status",
    "wiki_sync_issues_from_lint",
    "tokenize_text",
    "_remove_local_file",
    "slugify",
]
