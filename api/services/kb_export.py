"""知识库导出/导入（2026-10-21）。

导出：单个知识库打包 zip —— manifest.json（配置快照 + 文档清单 + 目录树）
      + files/（文档原文件，按目录结构摆放）。不携带切片/向量/wiki 二进制：
      wiki 由导入后的构建链（构建引擎编排 / 领域引导链）重新生成，与上传链路一致。

导入：解包 zip → 建库（同名可覆盖或报错）→ 重建文档目录 → 文档逐篇入上传链
      （storage.put_bytes + kb_document(PENDING) + _enqueue_document_process），
      后续解析/摘要/构建全部走既有链路的编排自动恢复。

权限由路由层负责（导出=读可见 _kb_visible；导入=平台建库权限，复用 post_kb 的
scope 校验语义）。模型/构建引擎引用在目标环境不存在时置空回退（记录 warnings），
不阻断导入。
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.models.knowledge import DocFolder, KbDatasource, KbDocument, WikiPage
from api.services import storage as storage_svc
from api.services.parser_registry import ext_of

EXPORT_FORMAT_VERSION = 1

# 导入导出共用：单 zip 上限与解包文件数（防 zip 炸弹，对齐 upload-archive 口径）
MAX_IMPORT_UNCOMPRESSED_BYTES = 2 * 600 * 1024 * 1024  # 1.2GB
MAX_IMPORT_ENTRIES = 3000


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def _manifest_config(kb: KbDatasource) -> dict:
    """KB 配置快照（剔除运行时/敏感字段：state/时间戳/repo 凭证等）。"""
    return {
        "id": kb.id,
        "name": kb.name,
        "label": kb.label or "",
        "description": kb.description or "",
        "scope": kb.scope or "system",
        "team_name": kb.team_name or "",
        "owner_user_id": kb.owner_user_id or "",
        "type": kb.type or "document",
        "custom_wiki_generation": bool(kb.custom_wiki_generation),
        "indexing_strategy": kb.indexing_strategy or {},
        "embedding_model_id": kb.embedding_model_id or "",
        "summary_model_id": kb.summary_model_id or "",
        "summary_strategy": kb.summary_strategy or "auto",
        "build_tape_id": kb.build_tape_id or "",
        "wiki_config": kb.wiki_config or {},
        "extract_config": kb.extract_config or {},
        "vlm_config": kb.vlm_config or {},
        "asr_config": kb.asr_config or {},
        "image_processing_config": kb.image_processing_config or {},
        "storage_provider_config": kb.storage_provider_config or {},
        "repo_type": kb.repo_type or "",
        "repo_url": kb.repo_url or "",
        "repo_branch": kb.repo_branch or "",
    }


def _folder_rel_paths(db: Session, kb_id: str) -> tuple[list[dict], dict[str, str]]:
    """文档目录树 → [{id,name,parent_id}] + folder_id → 相对路径（'' = 根）。"""
    folders = db.execute(
        select(DocFolder).where(DocFolder.kb_id == kb_id).order_by(DocFolder.created_at)
    ).scalars().all()
    by_id = {f.id: f for f in folders}
    paths: dict[str, str] = {}

    def _path(fid: str) -> str:
        if fid in paths:
            return paths[fid]
        f = by_id.get(fid)
        if f is None or not f.parent_id:
            p = f.name if f else ""
        else:
            p = f"{_path(f.parent_id)}/{f.name}"
        paths[fid] = p
        return p

    tree = [
        {"id": f.id, "name": f.name, "parent_id": f.parent_id or ""}
        for f in folders
    ]
    for f in folders:
        _path(f.id)
    return tree, paths


def _read_doc_bytes(db: Session, doc: KbDocument) -> bytes | None:
    """读取文档原文件字节（SysFile 优先，storage_path 兜底，缺失返回 None）。"""
    from api.models.framework import SysFile

    if doc.sys_file_id:
        f = db.execute(select(SysFile).where(SysFile.id == doc.sys_file_id)).scalars().first()
        if f:
            from api.routers.files import _local_fallback_path, _read_stored_bytes

            local_path = _local_fallback_path(f.storage_path)
            if local_path and Path(local_path).exists():
                return Path(local_path).read_bytes()
            try:
                return _read_stored_bytes(f)
            except Exception:  # noqa: BLE001
                pass
    if doc.storage_path:
        try:
            return storage_svc.get_bytes(doc.storage_path)
        except Exception:  # noqa: BLE001
            return None
    return None


def export_kb_zip(db: Session, kb_id: str) -> bytes:
    """导出单个知识库为 zip 字节（内存组装；超大库由路由层决定是否流式）。

    返回 zip：manifest.json + files/<folder_rel>/<file_name> + wiki/<slug>.md
    （wiki 快照仅供参考/审计，导入不恢复——wiki 由构建链重建）。
    """
    kb = db.get(KbDatasource, kb_id)
    if kb is None:
        raise ValueError("知识库不存在")

    docs = db.execute(
        select(KbDocument)
        .where(KbDocument.kb_id == kb_id, KbDocument.source_type == "file")
        .order_by(KbDocument.created_at)
    ).scalars().all()
    tree, folder_paths = _folder_rel_paths(db, kb_id)

    manifest = {
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": __import__("datetime").datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "kb": _manifest_config(kb),
        "folders": tree,
        "documents": [
            {
                "file_name": d.file_name,
                "folder_path": folder_paths.get(d.folder_id or "", ""),
                "process_config": d.process_config or "",
                "build_tape_id": d.build_tape_id or "",
                "file_size": d.file_size,
            }
            for d in docs
        ],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        for d in docs:
            data = _read_doc_bytes(db, d)
            if data is None:
                continue  # 物理文件缺失跳过（manifest 仍列出，导入侧可跳过）
            rel = folder_paths.get(d.folder_id or "", "")
            arc = f"files/{rel}/{d.file_name}" if rel else f"files/{d.file_name}"
            zf.writestr(arc, data)
        # wiki 快照（只读参考；slug 转安全文件名）
        pages = db.execute(
            select(WikiPage)
            .where(WikiPage.kb_id == kb_id, WikiPage.status == "active")
            .order_by(WikiPage.slug)
        ).scalars().all()
        for p in pages:
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in p.slug)
            zf.writestr(f"wiki/{safe}.md", p.content or "")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------


def _resolve_ref_or_none(db: Session, model_cls, ref_id: str) -> str | None:
    """目标环境存在该引用（模型/编排）才保留，否则置空回退（记录 warning）。"""
    if not ref_id:
        return None
    if db.get(model_cls, ref_id) is not None:
        return ref_id
    return None


def import_kb_zip(
    db: Session,
    zip_bytes: bytes,
    *,
    caller_user_id: str,
    caller_team_name: str,
    is_sys_admin: bool,
    replace: bool = False,
) -> dict:
    """导入知识库 zip → 建库（同名覆盖或报错）+ 文档入上传链。

    返回：{"kb_id","kb_name","docs_created","docs_skipped","warnings":[...]}
    """
    from api.models.model import KbModel
    from api.models.orchestration import Tape
    from api.services.kb_admin import (
        create_document,
        create_kb,
        delete_kb,
        doc_create_folder,
    )
    from api.routers.kbs import _enqueue_document_process
    from api.services.scope import validate_scope_request

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("压缩包无法解压（不是有效的 zip 或文件已损坏）") from exc

    infos = [i for i in zf.infolist() if not (i.filename or "").endswith("/")]
    if len(infos) > MAX_IMPORT_ENTRIES:
        raise ValueError(
            f"导入包内文件过多（{len(infos)} 个，上限 {MAX_IMPORT_ENTRIES} 个），请拆分后导入"
        )
    total_uncompressed = sum(i.file_size for i in infos)
    if total_uncompressed > MAX_IMPORT_UNCOMPRESSED_BYTES:
        raise ValueError(
            f"导入包解压后过大（约 {total_uncompressed // (1024 * 1024)}MB，上限 "
            f"{MAX_IMPORT_UNCOMPRESSED_BYTES // (1024 * 1024)}MB），请拆分后导入"
        )

    try:
        raw = zf.read("manifest.json")
    except KeyError as exc:
        raise ValueError("压缩包缺少 manifest.json（不是知识库导出包）") from exc

    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("manifest.json 解析失败") from exc

    if manifest.get("format_version") != EXPORT_FORMAT_VERSION:
        raise ValueError(
            f"不支持的导出格式版本: {manifest.get('format_version')}（当前支持 v{EXPORT_FORMAT_VERSION}）"
        )

    kb_cfg = manifest.get("kb") or {}
    name = str(kb_cfg.get("name") or "").strip()
    if not name:
        raise ValueError("导出包缺少知识库名称")

    warnings: list[str] = []
    existing = db.execute(
        select(KbDatasource).where(KbDatasource.name == name, KbDatasource.state == "1")
    ).scalars().first()
    if existing:
        if not replace:
            raise ValueError(
                f"已存在同名知识库「{name}」，如确认覆盖请勾选「覆盖同名库」后重试"
            )
        # 覆盖：级联删旧库（软删+内容清理），再按导出配置重建
        res = delete_kb(db, existing.id)
        if not res.get("success"):
            raise ValueError(res.get("message") or f"删除旧知识库失败: {name}")

    # 引用存在性回退（目标环境模型/编排可能不同）
    embedding_model_id = _resolve_ref_or_none(
        db, KbModel, kb_cfg.get("embedding_model_id") or ""
    )
    summary_model_id = _resolve_ref_or_none(db, KbModel, kb_cfg.get("summary_model_id") or "")
    build_tape_id = _resolve_ref_or_none(db, Tape, kb_cfg.get("build_tape_id") or "")
    if (kb_cfg.get("embedding_model_id") or "") and not embedding_model_id:
        warnings.append("Embedding 模型在目标环境不存在，已回退为系统默认")
    if (kb_cfg.get("summary_model_id") or "") and not summary_model_id:
        warnings.append("摘要模型在目标环境不存在，已回退为系统默认")
    if (kb_cfg.get("build_tape_id") or "") and not build_tape_id:
        warnings.append("构建引擎编排在目标环境不存在，已回退为内置构建链")

    try:
        scope, owner_user_id, owner_team_name = validate_scope_request(
            kb_cfg.get("scope") or "system",
            caller_user_id=caller_user_id,
            caller_team_name=caller_team_name,
            is_sys_admin=is_sys_admin,
        )
    except Exception as exc:  # noqa: BLE001（权限/归属异常 → 回退 system 语义）
        scope, owner_user_id, owner_team_name = "system", None, None
        warnings.append(f"导入库归属按系统库处理（{exc}）")

    try:
        kb = create_kb(
            db,
            name=name,
            label=kb_cfg.get("label") or None,
            description=kb_cfg.get("description") or None,
            indexing_strategy=kb_cfg.get("indexing_strategy"),
            scope=scope,
            team_name=owner_team_name or None,
            owner_user_id=owner_user_id or None,
            created_by=caller_user_id or None,
            type=kb_cfg.get("type") or "document",
            custom_wiki_generation=bool(kb_cfg.get("custom_wiki_generation")),
            embedding_model_id=embedding_model_id,
            summary_model_id=summary_model_id,
            build_tape_id=build_tape_id,
            summary_strategy=kb_cfg.get("summary_strategy") or None,
            configs={
                "wiki_config": kb_cfg.get("wiki_config"),
                "extract_config": kb_cfg.get("extract_config"),
                "vlm_config": kb_cfg.get("vlm_config"),
                "asr_config": kb_cfg.get("asr_config"),
                "image_processing_config": kb_cfg.get("image_processing_config"),
                "storage_provider_config": kb_cfg.get("storage_provider_config"),
            },
            caller_user_id=caller_user_id or None,
            caller_team_name=caller_team_name or None,
            is_sys_admin=is_sys_admin,
            repo_type=kb_cfg.get("repo_type") or "",
            repo_url=kb_cfg.get("repo_url") or "",
            repo_branch=kb_cfg.get("repo_branch") or "",
        )
    except ValueError as exc:
        raise ValueError(f"导入建库失败: {exc}") from exc

    # 重建文档目录（按 manifest 树，父先子后；id 重映射旧 → 新）
    folders_meta = manifest.get("folders") or []
    # 旧 folder id → 相对路径（与导出侧 _folder_rel_paths 同款算法）
    old_by_id = {str(f.get("id") or ""): f for f in folders_meta}
    old_paths: dict[str, str] = {}

    def _old_path(fid: str) -> str:
        if fid in old_paths:
            return old_paths[fid]
        f = old_by_id.get(fid)
        if f is None or not f.get("parent_id"):
            p = str(f.get("name") or "") if f else ""
        else:
            p = f"{_old_path(str(f['parent_id']))}/{f['name']}"
        old_paths[fid] = p
        return p

    for f in folders_meta:
        _old_path(str(f.get("id") or ""))
    path_to_folder: dict[str, str] = {  # 相对路径 → 新目录 id
        "": ""
    }
    folder_map: dict[str, str] = {}
    for f in folders_meta:
        fid = str(f.get("id") or "")
        try:
            res = doc_create_folder(
                db,
                kb.id,
                {"name": str(f.get("name") or "").strip(), "parent_id": folder_map.get(str(f.get("parent_id") or ""), "")},
                user_id=caller_user_id or None,
            )
            folder_map[fid] = res["id"]
            path_to_folder[old_paths.get(fid, "")] = res["id"]
        except ValueError as exc:
            warnings.append(f"目录「{f.get('name')}」重建失败: {exc}")

    # 文档入上传链（manifest.documents + files/ 对应字节）
    docs_created = 0
    docs_skipped = 0
    for item in manifest.get("documents") or []:
        fname = str(item.get("file_name") or "").strip()
        if not fname:
            docs_skipped += 1
            continue
        rel = str(item.get("folder_path") or "")
        arc = f"files/{rel}/{fname}" if rel else f"files/{fname}"
        try:
            data = zf.read(arc)
        except KeyError:
            docs_skipped += 1
            continue
        if not data:
            docs_skipped += 1
            continue
        ext = ext_of(fname)
        if not ext and "." in fname:
            ext = Path(fname).suffix or ""
        doc_id = uuid.uuid4().hex
        storage_path = storage_svc.resolve_new_path(f"kb_documents/{kb.id}/{doc_id}{ext}")
        try:
            storage_svc.put_bytes(storage_path, data)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"文档「{fname}」存储失败: {exc}")
            docs_skipped += 1
            continue
        doc = create_document(
            db,
            kb_id=kb.id,
            file_name=fname,
            file_ext=ext.lstrip(".").lower() or None,
            file_size=len(data),
            storage_path=storage_path,
            folder_id=path_to_folder.get(rel, ""),
            process_config=(item.get("process_config") or "") or None,
            build_tape_id=(item.get("build_tape_id") or "") or None,
            created_by=caller_user_id or None,
        )
        try:
            _enqueue_document_process(kb.id, doc.id, caller_identity="")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"文档「{fname}」解析任务投递失败（保留 PENDING 待重试）: {exc}")
        docs_created += 1

    return {
        "kb_id": kb.id,
        "kb_name": kb.name,
        "docs_created": docs_created,
        "docs_skipped": docs_skipped,
        "warnings": warnings,
    }
