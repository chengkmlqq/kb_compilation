"""Document processing Celery tasks — parse + chunk + embed + store.

Task wiring follows the scheduler contract:
    TASK_CLASS_REGISTRY[task_class] = handler(job_id, task_params)
so modo_cron_task entries (or API enqueues) can trigger document ingestion.

Two tasks:
- `worker.tasks.doc_process.process_document_job` — full pipeline for one
  document (parse -> chunk -> embed -> write doc_chunk).
- `worker.tasks.doc_process.embed_document_job` — embed-only re-run (e.g. after
  the embedding model changed), reusing stored chunk text.
"""

from __future__ import annotations

import json
import logging
import os
import uuid

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.knowledge import DocChunk, KbDocument
from api.services.embedding import get_embedding_client, l2_normalize
from api.services.ingest import ingest_document
from worker.tasks.scheduler import TASK_CLASS_REGISTRY

logger = logging.getLogger(__name__)

# task_class values accepted in modo_cron_task / API enqueue.
TASK_CLASS_PROCESS_DOC = "KbDocumentProcessTask"
TASK_CLASS_EMBED_DOC = "KbDocumentEmbedTask"


def _load_document(document_id: str) -> KbDocument | None:
    db = get_sessionmaker()()
    try:
        return db.execute(select(KbDocument).where(KbDocument.id == document_id)).scalars().first()
    finally:
        db.close()


def _load_document_bytes(storage_path: str | None) -> bytes:
    """Load file bytes from a storage_path (local disk or minio:// bucket/key)."""
    if not storage_path:
        return b""
    try:
        from api.services.storage import get_bytes

        return get_bytes(storage_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to read document bytes from %s: %s", storage_path, e)
        return b""


def process_document(document_id: str, file_content: bytes, parser_engine: str | None = None) -> dict:
    """Run the full ingest pipeline for a document (called by the job task).

    - file_content: preloaded bytes (inline / test path). When empty, bytes
      are loaded from the document's `storage_path` (upload flow writes the
      file to local disk and records the absolute path on kb_document).
    """
    db = get_sessionmaker()()
    try:
        document = db.execute(select(KbDocument).where(KbDocument.id == document_id)).scalars().first()
        if not document:
            return {"success": False, "error": f"document not found: {document_id}"}

        if not file_content:
            file_content = _load_document_bytes(document.storage_path)
            if not file_content:
                return {
                    "success": False,
                    "error": f"document bytes unavailable (storage_path={document.storage_path!r})",
                }

        try:
            client = get_embedding_client(db, kb_id=document.kb_id)
        except Exception as exc:  # noqa: BLE001 - embedding 不可用必须显式 FAILED，防状态残留
            logger.exception("embedding client unavailable for document %s", document_id)
            document.parse_state = "FAILED"
            document.parse_error = str(exc)[:200]
            db.commit()
            return {"success": False, "error": f"embedding 模型不可用: {exc}"}

        # 文件级处理配置（对齐 WeKnora upload process_config）：上传/重解析时
        # 可携带，覆盖 KB 级默认切片/解析引擎配置。JSON 字符串存 kb_document.process_config。
        process_config = None
        if document.process_config:
            try:
                parsed = json.loads(document.process_config)
                if isinstance(parsed, dict):
                    process_config = parsed
            except (TypeError, json.JSONDecodeError):
                logger.warning("document %s has invalid process_config, ignored", document_id)
        return ingest_document(
            db,
            document,
            file_content,
            client,
            parser_engine=parser_engine,
            process_config=process_config,
        )
    finally:
        db.close()


def embed_document(document_id: str) -> dict:
    """Re-embed an existing document's chunks (embedding-model-change path).

    Chunks live in the business store; their new vectors go to the vector
    store (kb_embedding) via VectorStore.
    """
    from api.services.vector_store import get_vector_store

    db = get_sessionmaker()()
    try:
        chunks = (
            db.execute(
                select(DocChunk).where(DocChunk.document_id == document_id).order_by(DocChunk.seq)
            )
            .scalars()
            .all()
        )
        if not chunks:
            return {"success": False, "error": "no chunks to embed"}
        # KB 级 embedding_model_id 绑定：重嵌入用 KB 绑定的模型
        client = get_embedding_client(db, kb_id=chunks[0].kb_id)
        texts = [c.content for c in chunks]
        vectors = client.embed_texts(texts)
        store = get_vector_store()
        for chunk, vec in zip(chunks, vectors):
            store.upsert(chunk.kb_id, chunk.id, l2_normalize(vec))
        return {"success": True, "document_id": document_id, "embedded": len(chunks)}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduler registry handlers (called with job_id, task_params)
# ---------------------------------------------------------------------------


def _handle_process_document(job_id: str, task_params: str | None) -> dict:
    try:
        params = json.loads(task_params) if task_params else {}
    except (TypeError, json.JSONDecodeError):
        params = {}
    document_id = str(params.get("documentId") or params.get("document_id") or "").strip()
    if not document_id:
        return {"success": False, "error": "task_params must include documentId"}
    kb_id = str(params.get("kbId") or params.get("kb_id") or "").strip()
    parser_engine = params.get("parserEngine") or params.get("parser_engine")
    # 上传操作者身份 cookie（透传给编排脚本调平台 API；空 = 无身份）
    caller_cookie = str(params.get("cookie") or "")
    # Bytes are not storable in modo_job.task_params (text column); callers
    # fetch from object storage via the storage path on the document row.
    file_content = b""
    result = process_document(document_id, file_content, parser_engine)
    result["job_id"] = job_id

    # 自动触发下游构建（WeKnora 索引策略对齐）：文档解析/向量化成功后，
    #   - KB 绑定了构建引擎（build_tape_id，服务编排中已发布的编排）→ 按该编排
    #     构建 wiki（替代内置 skill 链，2026-10-12）
    #   - 未绑定时：wiki_enabled=true 且 custom_wiki_generation=false →
    #     enqueue KbAgentGatewayTask 用 KB 绑定的 wiki_config.skill（回退
    #     WIKI_SKILL_NAME）跑 build_wiki.py
    #   - 2026-10-14: 不再自动触发 KbGraphBuildTask（图谱构建改由文档 wiki
    #     处理编排自行承接，避免上传即全库切片 LLM 抽取 + 网关 429 限流）
    # 开关与技能绑定从 kb_datasource 读（_kb_pipeline_flags），读取失败回退历史默认。
    # 成功标志：ingest_document 返回 parse_state == "READY"（无 success 键）。
    if result.get("parse_state") == "READY" and kb_id:
        # 2026-10-13 上传链摘要（方案A 自动路由）：文档解析成功后按 KB 摘要策略
        # （off/auto/stuff/mapreduce/refine）触发标准摘要编排，生成摘要 wiki 页。
        # 独立于 wiki 构建（build_tape_id），两者可并存；失败静默不阻断解析。
        try:
            result["summary_triggered"] = _enqueue_summary_task(
                kb_id, document_id, parent_job_id=job_id, cookie=caller_cookie,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to trigger summary task: %s", exc)
            result["summary_triggered"] = False
        flags = _kb_pipeline_flags(kb_id, document_id)
        tape_id = flags.get("tape") or ""
        if tape_id:
            try:
                result["build_engine_triggered"] = _enqueue_build_engine_task(
                    kb_id, document_id, tape_id,
                    parent_job_id=job_id,  # 2026-10-12 父子：挂链头解析任务
                    cookie=caller_cookie,
                )
            except Exception as exc:  # noqa: BLE001 — 触发失败不使文档处理任务失败
                logger.warning("failed to trigger build engine task: %s", exc)
                result["build_engine_triggered"] = False
        elif flags["wiki"]:
            try:
                enqueued = _enqueue_wiki_skill_task(
                    kb_id, document_id, skill_name=flags["skill"] or None,
                    parent_job_id=job_id,  # 2026-10-12 父子：挂链头解析任务
                )
                result["wiki_skill_triggered"] = enqueued
            except Exception as exc:  # noqa: BLE001 — 触发失败不使文档处理任务失败
                logger.warning("failed to trigger wiki skill task: %s", exc)
                result["wiki_skill_triggered"] = False
        else:
            result["wiki_skill_triggered"] = False
            result["wiki_skipped"] = "wiki_enabled=false 或 custom_wiki_generation=true"
        # 2026-10-14: 移除上传链自动触发 KbGraphBuildTask（图谱构建改由文档 wiki
        # 处理编排自行承接，避免上传即全库切片 LLM 抽取 + 网关 429 限流）。
    return result


def _enqueue_build_engine_task(
    kb_id: str, document_id: str, tape_id: str,
    parent_job_id: str | None = None,  # 2026-10-12 父子：链头解析任务 job id
    cookie: str = "",  # 操作者身份 cookie，透传进编排 bindings.x_next_identity
) -> bool:
    """KB 绑定构建引擎（服务编排中已发布的编排）时：按该编排构建 wiki。

    链路：建 kb_tape_run（env=prod，与文档同库）→ 写 modo_job 行
    （KbOrchestrationRunTask，任务监控可见）→ 投 orch 队列由调度中枢
    execute_run 按 DAG 逐步执行（编排节点内 kb_read_doc 读文档切片、
    wiki_upsert_page/wiki_publish 写 wiki 页）。
    输入 bindings：kb_id / document_id（编排节点模板 {{kb_id}}/{{document_id}}）。
    """
    from api.db import get_sessionmaker
    from api.models.framework import Job
    from api.services.orchestration import _create_tape_run

    db = get_sessionmaker()()
    try:
        created = _create_tape_run(
            db, tape_id,
            inputs={
                "kb_id": kb_id,
                "document_id": document_id,
                # 2026-10-12 兼容存量编排：部分自建编排的 def/script 读 doc_id
                #（如「指标WIKI构建」），别名注入后开箱即用，无需逐个改编排
                "doc_id": document_id,
                # 2026-10-12 身份透传：自建编排脚本经 weknora_rpc 调平台 API
                # 需要登录身份（def 读 x_next_identity）；上传操作者的 cookie
                # 在此注入，过期时 weknora_rpc 自动用服务账号重登兜底
                "x_next_identity": cookie,
                # 2026-10-12 context 组装补全：编排 def/print 可能引用的变量
                # 显式给默认，避免模板渲染出字面量或脚本取到 None
                "limit": "",
                "only_codes": "",
                "build_summary": "",
                "language": "中文",
            },
            env="prod",  # 上传链数据在 prod，编排节点须同环境读写（2026-10-12）
        )
    except Exception as exc:  # noqa: BLE001 — 编排不可用（未保存步骤/未发布）不使解析任务失败
        logger.warning("failed to create build engine run (tape=%s): %s", tape_id, exc)
        return False
    finally:
        db.close()
    run_id, run_env = created["run_id"], created["run_env"]

    job_id = f"ORCH_RUN_{uuid.uuid4().hex[:12]}"
    task_params = json.dumps({"runId": run_id, "env": run_env}, ensure_ascii=False)
    # 顺序契约（对齐 scheduler.scan_cron_tasks Case 3 / _enqueue_job）：
    # 必须先 commit Job 行、再投 broker。若 send_task 先于 commit，worker 空闲时
    # 会在 Job 行可见前就消费 execute_modo_job，报 'job not found' 立即失败。
    db2 = get_sessionmaker()()
    try:
        db2.add(
            Job(
                id=job_id,
                task_id=job_id,
                task_class="KbOrchestrationRunTask",
                queue_name="orch",
                task_params=task_params,
                trigger_type="API",
                state="PENDING",
                parent_id=parent_job_id,  # 2026-10-12 父子：挂链头解析任务
            )
        )
        db2.commit()
    except Exception:  # noqa: BLE001
        db2.rollback()
        return False
    finally:
        db2.close()

    try:
        from worker.celery_app import celery_app

        celery_app.send_task(
            "worker.tasks.scheduler.execute_modo_job",
            args=[job_id],
            task_id=job_id,
            queue="orch",
        )
    except Exception as exc:  # noqa: BLE001 — broker 不可达时返回 False，不抛
        logger.warning("failed to enqueue build engine run %s: %s", run_id, exc)
        return False
    return True


# ───────────────────────── 上传链摘要（2026-10-13 方案A 自动路由） ─────────────────────────
# 三个标准摘要编排（Stuff 全文直塞 / Map-Reduce 分块合并 / Refine 增量修订）的
# tape_id 经 modo_dim(SYSTEM_CONFIG) 参数配置（seed_framework_db.py 种子写入），
# 运维可改（用户自建/复制编排后更新参数即可，无需改代码）。
SUMMARY_TAPE_DIM_KEYS: dict[str, str] = {
    "stuff": "SUMMARY_TAPE_STUFF",
    "mapreduce": "SUMMARY_TAPE_MAPREDUCE",
    "refine": "SUMMARY_TAPE_REFINE",
}
# auto 路由阈值：切片数 <= 该值视为小文档 → Stuff 全文直塞
SUMMARY_STUFF_MAX_CHUNKS = 10
# auto 路由：切片数超过该值视为超大文档 → Map-Reduce 时调大 batch_size 减少轮次
SUMMARY_MAPREDUCE_LARGE_CHUNKS = 60
SUMMARY_MAPREDUCE_LARGE_BATCH = 10


def _summary_tape_ids() -> dict[str, str]:
    """从 modo_dim(SYSTEM_CONFIG) 读三个摘要编排 tape_id，返回 {mode: tape_id}。

    缺失键跳过（auto 路由选不到对应编排时静默不触发）。
    """
    keys = list(SUMMARY_TAPE_DIM_KEYS.values())
    mode_of = {v: k for k, v in SUMMARY_TAPE_DIM_KEYS.items()}
    try:
        from api.models.framework import Dim

        db = get_sessionmaker()()
        try:
            rows = db.execute(
                select(Dim.dim_code, Dim.dim_value).where(
                    Dim.dim_group == "SYSTEM_CONFIG", Dim.dim_code.in_(keys)
                )
            ).all()
            return {mode_of.get(code, code): (val or "").strip() for code, val in rows if val and val.strip()}
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — 参数不可读（表/组缺失）不阻断
        logger.warning("failed to read summary tape ids from modo_dim", exc_info=True)
        return {}


def _resolve_summary_tape(
    kb_strategy: str, chunk_count: int, has_prev_summary: bool, tape_ids: dict[str, str]
) -> tuple[str, str] | None:
    """按 KB 摘要策略 + 文档规模 + 历史摘要选编排，返回 (mode, tape_id)。

    规则（auto）：
      - 已有历史摘要（kb_document.summary 非空，重解析/修订版）→ refine 增量修订
      - 无历史摘要且 chunk_count <= SUMMARY_STUFF_MAX_CHUNKS → stuff 全文直塞
      - 其余（中大/超大文档）→ mapreduce 分块合并（超大时上层传大 batch_size）
    指定模式（stuff/mapreduce/refine）直接映射；off 或参数缺失返回 None。
    """
    strategy = (kb_strategy or "auto").strip() or "auto"
    if strategy == "off":
        return None
    if strategy in ("stuff", "mapreduce", "refine"):
        tape = tape_ids.get(strategy)
        return (strategy, tape) if tape else None
    # auto
    if has_prev_summary:
        mode = "refine"
    elif chunk_count <= SUMMARY_STUFF_MAX_CHUNKS:
        mode = "stuff"
    else:
        mode = "mapreduce"
    tape = tape_ids.get(mode)
    return (mode, tape) if tape else None


def _enqueue_summary_task(
    kb_id: str,
    document_id: str,
    parent_job_id: str | None = None,
    cookie: str = "",
) -> bool:
    """文档解析 READY 后按 KB 摘要策略触发标准摘要编排（生成摘要 wiki 页）。

    与 _enqueue_build_engine_task 同链路：建 kb_tape_run(env=prod) → 写
    modo_job 行（KbOrchestrationRunTask，任务监控可见）→ 投 orch 队列。
    失败（策略 off / 参数缺失 / 编排不可用）静默返回 False，不使解析任务失败。
    """
    from api.models.framework import Dim  # noqa: F401
    from api.models.knowledge import KbDatasource

    db = get_sessionmaker()()
    try:
        kb = db.execute(select(KbDatasource).where(KbDatasource.id == kb_id)).scalars().first()
        doc = db.execute(select(KbDocument).where(KbDocument.id == document_id)).scalars().first()
        if not kb or not doc:
            logger.warning("summary trigger skipped: kb/doc not found kb=%s doc=%s", kb_id, document_id)
            return False
        tape_ids = _summary_tape_ids()
        # 文件级摘要策略覆盖（上传确认弹窗「构建配置 → 摘要生成服务」，
        # 存 kb_document.process_config.summary_strategy）；未设置时回退 KB 级。
        doc_strategy: str | None = None
        if doc.process_config:
            try:
                parsed_pc = json.loads(doc.process_config)
                if isinstance(parsed_pc, dict) and parsed_pc.get("summary_strategy"):
                    doc_strategy = str(parsed_pc["summary_strategy"]).strip()
            except (TypeError, json.JSONDecodeError):
                doc_strategy = None
        resolved = _resolve_summary_tape(
            doc_strategy or kb.summary_strategy or "auto",
            doc.chunk_count or 0,
            bool((doc.summary or "").strip()),
            tape_ids,
        )
        if not resolved:
            return False  # off / 参数缺失：不生成摘要
        mode, tape_id = resolved
        inputs = {
            "kb_id": kb_id,
            "document_id": document_id,
            "doc_id": document_id,  # 兼容存量编排 def/script 读 doc_id
            "x_next_identity": cookie,
            "limit": "",
            "only_codes": "",
            "build_summary": "",
            "language": "中文",
            # 2026-10-13 增量修订：Refine 编排把历史摘要（kb_document.summary）
            # 作为滚动起点（n2 读 prev_summary）；其它模式忽略该键。
            "prev_summary": (doc.summary or "").strip(),
        }
        # 超大文档走 Map-Reduce 时调大 batch_size（减少串行轮次，避免撞限流）
        if mode == "mapreduce" and (doc.chunk_count or 0) > SUMMARY_MAPREDUCE_LARGE_CHUNKS:
            inputs["batch_size"] = str(SUMMARY_MAPREDUCE_LARGE_BATCH)
        from api.services.orchestration import _create_tape_run

        created = _create_tape_run(
            db, tape_id, inputs=inputs, env="prod",
        )
    except Exception as exc:  # noqa: BLE001 — 摘要触发失败不使解析任务失败
        logger.warning("failed to create summary run (kb=%s doc=%s): %s", kb_id, document_id, exc)
        return False
    finally:
        db.close()
    run_id, run_env = created["run_id"], created["run_env"]

    job_id = f"SUMMARY_RUN_{uuid.uuid4().hex[:12]}"
    task_params = json.dumps({"runId": run_id, "env": run_env}, ensure_ascii=False)
    db2 = get_sessionmaker()()
    try:
        from api.models.framework import Job

        db2.add(
            Job(
                id=job_id,
                task_id=job_id,
                task_class="KbOrchestrationRunTask",
                queue_name="orch",
                task_params=task_params,
                trigger_type="API",
                state="PENDING",
                parent_id=parent_job_id,  # 挂链头解析任务
            )
        )
        db2.commit()
    except Exception:  # noqa: BLE001
        db2.rollback()
        return False
    finally:
        db2.close()
    try:
        from worker.celery_app import celery_app

        celery_app.send_task(
            "worker.tasks.scheduler.execute_modo_job",
            args=[job_id],
            task_id=job_id,
            queue="orch",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to enqueue summary run %s: %s", run_id, exc)
        return False
    return True


def _enqueue_wiki_build_task(kb_id: str, document_id: str, parent_job_id: str | None = None) -> bool:
    """enqueue KbSkillWikiBuildTask（worker 内执行 wiki 构建）。

    LLM 配置（WIKI_LLM_BASE_URL/MODEL/API_KEY）由 worker 容器 env 直接读取，
    无需经 gateway runner 注入。任务参数携带 kb_id/document_id。
    """
    from worker.celery_app import celery_app

    task_params = json.dumps(
        {"kbId": kb_id, "documentId": document_id, "language": "中文", "env": "prod"},  # 2026-10-12 上传链走生产库
        ensure_ascii=False,
    )
    job_id = f"WIKI_BUILD_{uuid.uuid4().hex[:12]}"
    try:
        celery_app.send_task(
            "worker.tasks.scheduler.execute_modo_job",
            args=[job_id],
            task_id=job_id,
            queue="default",
        )
    except Exception as exc:  # noqa: BLE001 — broker 不可达时返回 False，不抛
        logger.warning("failed to enqueue wiki build task: %s", exc)
        return False
    # 写 modo_job 行（任务监控可见）——与 scheduler._enqueue_job 契约一致
    from api.db import get_sessionmaker
    from api.models.framework import Job

    db = get_sessionmaker()()
    try:
        db.add(
            Job(
                id=job_id,
                task_id=job_id,
                task_class="KbSkillWikiBuildTask",
                queue_name="default",
                task_params=task_params,
                trigger_type="API",
                state="PENDING",
                parent_id=parent_job_id,  # 2026-10-12 父子：挂链头解析任务
            )
        )
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        db.rollback()
        return False
    finally:
        db.close()


def _agent_task_target() -> tuple[str, str]:
    """wiki 构建任务的投递目标：(task_class, queue)。

    默认 **inline**：worker 内联 agent 运行时（worker/agent/，openai-agents SDK
    完整能力）执行，投 agent 队列由 celery-agent-worker 消费——解析 worker 与
    agent worker 物理隔离、独立扩容、Flower 原生监控。

    `WIKI_AGENT_MODE=gateway` 回退到外部 agent-gateway（KbAgentGatewayTask，
    HTTP 提交独立部署的网关容器，default 队列）——新链路未稳定时的并行/回滚路径。
    """
    # 平台参数优先（页面「系统 → 平台参数」可改，15s TTL 缓存）；
    # 未配置时回落 env WIKI_AGENT_MODE，再回落代码默认 direct。
    from worker.platform_params import get_platform_param

    mode = get_platform_param("WIKI_BUILD_MODE", env_name="WIKI_AGENT_MODE", default="direct")
    if mode == "agent":
        return "KbAgentWikiBuildTask", "agent"
    if mode == "gateway":
        return "KbAgentGatewayTask", "default"
    return "KbSkillDirectBuildTask", "build"  # 2026-10-07 独立构建 worker（-Q build，内化引擎）


def _enqueue_wiki_skill_task(
    kb_id: str, document_id: str, skill_name: str | None = None,
    parent_job_id: str | None = None,  # 2026-10-12 父子：链头解析任务 job id
) -> bool:
    """enqueue wiki 构建的 agent 任务（默认内联 agent worker，见 _agent_task_target）。

    skill_name: 指定技能（KB 级 wiki_config.skill 绑定）；为空则用全局
    WIKI_SKILL_NAME（WeKnora WikiConfig.Skill 为空的回退语义）。
    config 携带:
      - skill: 技能名（kb_skill 表 scope=system 的 wiki 构建技能）
      - kb_id / document_id: 技能脚本定位文档（读 chunks 后生成 wiki 页）
      - kb_base_url: 技能脚本调 kb API 的入口（宿主 nginx）
    """
    from api.config import get_settings

    settings = get_settings()
    skill_name = (skill_name or settings.WIKI_SKILL_NAME or "kb-wiki-builder").strip()
    # 技能入口脚本：按技能名映射（监管版入口是 build_full.py，其余默认 build_wiki.py）
    entry_script = "build_full.py" if "监督管理制度文档转wiki" in skill_name else "build_wiki.py"
    kb_base_url = (settings.KB_PUBLIC_BASE_URL or "http://10.1.215.50").strip()
    llm_base = (settings.WIKI_LLM_BASE_URL or "").strip()
    llm_model = (settings.WIKI_LLM_MODEL or "").strip()
    llm_key = (settings.WIKI_LLM_API_KEY or "").strip()

    from worker.celery_app import celery_app

    # gateway runner 注入技能环境: kb_id→WEKNORA_KB_ID, doc_name→WEKNORA_DOC_NAME,
    # model/base_url/api_key→WEKNORA_LLM_*（技能脚本 llm_config 优先读取）
    llm_cfg = {}
    if llm_base:
        llm_cfg["base_url"] = llm_base
    if llm_model:
        llm_cfg["model"] = llm_model
    if llm_key:
        llm_cfg["api_key"] = llm_key
    task_params = json.dumps(
        {
            "input": (
                "请完成知识库 wiki 构建任务：使用技能工具 run_skill_script("
                f"skill_name='{skill_name}', script='{entry_script}') 执行技能脚本，"
                f"把知识库 {kb_id} 中的文档 {document_id} 编译进 wiki。"
                "不要使用 MCP 工具查询或写入知识库，不要调用 list_skills/load_skill 之外的工具；"
                "脚本会从环境变量 WEKNORA_KB_ID / WEKNORA_DOC_NAME 读取目标并自动完成全部工作，"
                "直接运行它即可。"
            ),
            "config": {
                "skill": skill_name,
                "kb_id": kb_id,
                "doc_name": document_id,
                "kb_base_url": kb_base_url,
                **llm_cfg,
            },
            "env": "prod",  # 2026-10-12 上传链走生产库
        },
        ensure_ascii=False,
    )
    job_id = f"WIKI_SKILL_{uuid.uuid4().hex[:12]}"
    task_class, queue = _agent_task_target()
    # 顺序契约（对齐 scheduler.scan_cron_tasks Case 3 / _enqueue_job）：
    # 必须先 commit Job 行、再投 broker。若 send_task 先于 commit，worker 空闲时
    # 会在 Job 行可见前就消费 execute_modo_job，报 'job not found' 立即失败。
    from api.db import get_sessionmaker
    from api.models.framework import Job

    db = get_sessionmaker()()
    try:
        db.add(
            Job(
                id=job_id,
                task_id=job_id,
                task_class=task_class,
                queue_name=queue,
                task_params=task_params,
                trigger_type="API",
                state="PENDING",
                parent_id=parent_job_id,  # 2026-10-12 父子：挂链头解析任务
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        return False
    finally:
        db.close()

    try:
        celery_app.send_task(
            "worker.tasks.scheduler.execute_modo_job",
            args=[job_id],
            task_id=job_id,
            queue=queue,
        )
    except Exception as exc:  # noqa: BLE001 — broker 不可达时返回 False，不抛
        logger.warning("failed to enqueue wiki skill task: %s", exc)
        return False
    return True


def _kb_pipeline_flags(kb_id: str, document_id: str = "") -> dict:
    """读 KB 的 wiki_enabled/graph_enabled 开关、wiki 技能绑定与构建引擎绑定。

    构建引擎（tape）优先级：文档级 kb_document.build_tape_id（上传/选表时
    重选）> 知识库级 kb_datasource.build_tape_id（创建库时选定的默认）。
    读取失败时返回兼容默认（wiki=True 自动建、graph=False），保证存量行为不回退。
    """
    fallback = {"wiki": True, "graph": False, "skill": "", "tape": ""}
    try:
        from api.db import get_sessionmaker as _gsm
        from api.models.knowledge import KbDatasource, KbDocument

        db = _gsm()()
        try:
            kb = db.execute(select(KbDatasource).where(KbDatasource.id == kb_id)).scalars().first()
            doc_tape = ""
            if document_id:
                doc_row = db.execute(
                    select(KbDocument.build_tape_id).where(KbDocument.id == document_id)
                ).scalars().first()
                doc_tape = doc_row or ""
        finally:
            db.close()
        if not kb:
            return fallback
        from api.services.kb_config import custom_wiki_generation, pipeline_enabled, wiki_skill_name

        return {
            "wiki": pipeline_enabled(kb, "wiki_enabled") and not custom_wiki_generation(kb),
            "graph": pipeline_enabled(kb, "graph_enabled"),
            "skill": wiki_skill_name(kb),
            # 构建引擎：文档级覆盖优先，其次服务编排中已发布的编排程序
            "tape": doc_tape or getattr(kb, "build_tape_id", None) or "",
        }
    except Exception:  # noqa: BLE001
        logger.warning("failed to read kb pipeline flags for kb=%s", kb_id, exc_info=True)
        return fallback


def _handle_embed_document(job_id: str, task_params: str | None) -> dict:
    try:
        params = json.loads(task_params) if task_params else {}
    except (TypeError, json.JSONDecodeError):
        params = {}
    document_id = str(params.get("documentId") or params.get("document_id") or "").strip()
    if not document_id:
        return {"success": False, "error": "task_params must include documentId"}
    result = embed_document(document_id)
    result["job_id"] = job_id
    return result


def register_task_handlers() -> None:
    """Register doc-processing handlers into the scheduler registry.

    Called at worker startup (worker/tasks/__init__.py imports this module).
    """
    TASK_CLASS_REGISTRY[TASK_CLASS_PROCESS_DOC] = _handle_process_document
    TASK_CLASS_REGISTRY[TASK_CLASS_EMBED_DOC] = _handle_embed_document


register_task_handlers()
