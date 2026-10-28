"""编排执行引擎——迁移自 data-synth synth_weaver/orchestrator。

保留的组件指令（ATOM_MAP）：
- def    变量定义：assignments [{variable, expression}]，evaluate 后写 bindings
- print  日志输出：text 支持 {{ }} 模板
- script 脚本执行：exec(Python)，注入 context/bindings/config
- if     条件分支：condition 真/假分别走 complete/fail 后继
- loop   循环（kb 新增）：{item_var, collection} 迭代「后继子图」
  （edges 下游可达步骤集合，即用户连线表达的循环体）

执行模型：DAG BFS + pre 依赖检查 + {{ }} 模板渲染（Jinja2），
与 data-synth engine.py 对齐；loop 步骤每轮迭代重置循环体步骤的
已执行标记后重跑其后继子图，迭代间通过 bindings 传递状态。
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Set

logger = logging.getLogger("kb.orchestration.engine")

from api.models.orchestration import StepDefine, Tape, TapeStep


# ───────────────────────── 运行时上下文 ─────────────────────────

class StepResponse:
    """步骤执行结果（success/body/error），对齐 data-synth StepResponse。"""

    def __init__(self, success: bool = True, body: Any = None, error: str = ""):
        self.success = success
        self.body = body
        self.error = error

    @classmethod
    def success(cls, body: Any = None) -> "StepResponse":
        return cls(True, body)

    @classmethod
    def fail(cls, error: str) -> "StepResponse":
        return cls(False, None, error)


_TEMPLATE_RE = None

def _template_replace(text: str, bindings: Dict[str, Any]) -> str:
    """{{ expr }} → Python 表达式求值替换（无 jinja2 依赖）。"""
    import re

    def repl(m):
        expr = m.group(1).strip()
        try:
            scope = dict(bindings)
            scope["bindings"] = bindings
            return str(eval(expr, {}, scope))
        except Exception as e:  # noqa: BLE001
            logger.warning("模板表达式求值失败 %r: %s", expr, e)
            return m.group(0)

    return re.sub(r"\{\{\s*(.*?)\s*\}\}", repl, text)


class TapeRuntimeContext:
    """编排运行上下文：bindings 变量字典 + 模板渲染/表达式求值。"""

    def __init__(self, bindings: Optional[Dict[str, Any]] = None):
        self.bindings: Dict[str, Any] = bindings or {}
        self.step_logs: Dict[str, Any] = {}

    def render(self, text: str) -> str:
        """{{ expr }} 模板替换；失败时原样返回文本。"""
        if not isinstance(text, str):
            return str(text)
        if "{{" not in text:
            return text
        return _template_replace(text, self.bindings)

    def evaluate(self, expression: str) -> Any:
        """先按 {{ }} 模板渲染，再尝试作为 Python 表达式求值。"""
        rendered = self.render(expression)
        scope = dict(self.bindings)
        scope["bindings"] = self.bindings
        try:
            return eval(rendered, {}, scope)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"表达式求值失败: {e}，表达式: {expression}") from e


# ───────────────────────── 原子基类 ─────────────────────────

class BaseStep:
    """原子步骤基类：handle(context) 返回 StepResponse。"""

    def __init__(self, step_id: str, step_label: str, step_inst: str, config: Optional[dict]):
        self.step_id = step_id
        self.step_label = step_label
        self.step_inst = step_inst
        self.config: dict = config or {}
        self.pre_step_ids: List[str] = []
        self.next_step_ids: List[str] = []

    def handle(self, context: TapeRuntimeContext) -> StepResponse:  # pragma: no cover
        raise NotImplementedError


# ───────────────────────── 内置原子 ─────────────────────────

class DefStep(BaseStep):
    """'def' 变量定义。"""

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        assignments = self.config.get("assignments", [])
        # 前端 textarea 存 JSON 字符串 → 兜底解析成 list
        if isinstance(assignments, str):
            try:
                assignments = json.loads(assignments)
            except Exception:  # noqa: BLE001
                return StepResponse.fail(f"变量定义 JSON 解析失败: {assignments[:80]}")
        if not assignments and self.config.get("variable"):
            assignments = [{"variable": self.config["variable"], "expression": self.config.get("expression")}]
        if not assignments:
            return StepResponse.fail("未找到有效的变量定义")
        last_value = None
        for item in assignments:
            var_name = item.get("variable")
            expression = item.get("expression")
            if not var_name:
                continue
            last_value = context.evaluate(expression)
            context.bindings[var_name] = last_value
        return StepResponse.success(last_value)


class PrintStep(BaseStep):
    """'print' 日志输出。"""

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        message = self.config.get("text") or self.config.get("message", "")
        output = context.render(message)
        logger.info("[STEP PRINT] %s", output)
        return StepResponse.success(output)


class ScriptStep(BaseStep):
    """'script' 任意 Python 执行（注入 context/bindings/config）。

    exec 用「同一命名空间」执行：globals 与 locals 指向同一个 dict —— 与
    .py 脚本文件的运行语义一致。传空 globals（2026-10 前）会让顶层赋值只进
    locals，而函数体/listcomp 按 CPython 规则查 globals → `NameError: name
    'sents' is not defined`（长句抽取节点实测失败）。
    """

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        code = self.config.get("code")
        if not code:
            return StepResponse.fail("Missing code in configuration")
        try:
            ns = {"context": context, "bindings": context.bindings, "config": self.config}
            exec(str(code), ns, ns)  # noqa: S102  (编排脚本 = 平台管理员授权能力)
            return StepResponse.success("Script executed successfully")
        except SystemExit as se:  # 脚本用 raise SystemExit(0) 表示"干净提前退出/跳过"
            code_ = se.code
            if code_ in (0, None):
                return StepResponse.success("Script exited cleanly (SystemExit)")
            return StepResponse.fail(f"Script exited with code {code_}")
        except Exception as e:  # noqa: BLE001
            import traceback

            return StepResponse.fail(f"Script execution error: {e}\n{traceback.format_exc()}")


class RunScriptStep(BaseStep):
    """'run_script' 子进程执行（2026-10-09 编排异步化新增）。

    在节点所在 worker 内以 subprocess 运行脚本（如技能 run_one.py / build_full.py），
    支持超时 + 环境变量注入。config:
      - command: 命令行 argv 列表（首项可含 {{ }} 模板）
      - script_path: 或指定脚本路径（worker 内置技能脚本），自动拼 python + args
      - args: 脚本参数列表（支持 {{ }} 模板）
      - env: 附加环境变量 dict（值支持 {{ }} 模板）
      - timeout_sec: 超时（默认 0 = 不限；建议按步骤实际耗时配置）
    """

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        import os
        import subprocess

        def _render(v):
            if isinstance(v, str):
                return context.render(v)
            if isinstance(v, list):
                return [context.render(x) for x in v]
            if isinstance(v, dict):
                return {k: context.render(x) for k, x in v.items()}
            return v

        cmd = _render(self.config.get("command") or [])
        script_path = _render(self.config.get("script_path") or "")
        args = _render(self.config.get("args") or [])
        env_extra = _render(self.config.get("env") or {})
        timeout_sec = float(self.config.get("timeout_sec") or 0)

        if script_path:
            cmd = [sys.executable, script_path] + list(args)
        if not cmd:
            return StepResponse.fail("run_script 需要 command 或 script_path 配置")

        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in env_extra.items()})
        # 2026-10-20 双表改造：子进程不继承 contextvar（新进程 current_env 默认 prod），
        # 显式把当前执行环境注入 KB_ENV，脚本入口 env_scope(KB_ENV) 后其内部
        # get_sessionmaker()/storage 写操作才路由到对应表（dev → _dev 表）。
        from api.db import current_env

        env.setdefault("KB_ENV", current_env())
        import signal as _sig
        proc = None
        try:
            # 2026-10-09 实测坑: subprocess.run(timeout=) 超时只杀直接子进程,
            # run_one.py 的孙进程(build_full/extract_entities)残留成孤儿。
            # 改 Popen + start_new_session(独立进程组) + 超时 killpg 整组杀。
            proc = subprocess.Popen(
                [str(c) for c in cmd],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=env, cwd=context.bindings.get("cwd") or None,
                start_new_session=True,
            )
            try:
                out, err = proc.communicate(timeout=timeout_sec or None)
            except subprocess.TimeoutExpired:
                # 杀整个进程组（含孙进程），再回收
                try:
                    os.killpg(os.getpgid(proc.pid), _sig.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                return StepResponse.fail(f"子进程超时（>{int(timeout_sec)}s）: {cmd}")
            tail_out = (out or "")[-3000:]
            tail_err = (err or "")[-1500:]
            if proc.returncode != 0:
                return StepResponse.fail(
                    f"退出码 {proc.returncode}\n[stdout]\n{tail_out}\n[stderr]\n{tail_err}"
                )
            return StepResponse.success({"returncode": 0, "stdout_tail": tail_out})
        except Exception as e:  # noqa: BLE001
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), _sig.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            return StepResponse.fail(f"脚本执行异常: {e}")


class _AtomScriptStep(RunScriptStep):
    """业务原子基类（2026-10-09）：把业务配置翻译成 orch_atoms.py CLI 调用。

    设计：wiki 构建的 LLM 抽取/知识入库等动作实现在 worker 内置脚本
    orch_atoms.py。业务 Step 继承 RunScriptStep 的进程组/超时/杀组语义，
    但 handle 前先把 config 翻译成 orch_atoms CLI：写临时 cfg.json + 拼接
    command。子类只需声明 ATOM_NAME 与需要透传的字段。
    """

    ATOM_NAME: str = ""
    SCRIPTS_DIR = "/srv/kb/worker/builtin_engine/scripts"

    def _build_command(self, context: TapeRuntimeContext) -> list:
        cfg = {}
        for k, v in (self.config or {}).items():
            if k in ("command", "script_path", "args", "env", "timeout_sec", "queue", "workdir"):
                continue  # 调度字段不传给业务配置
            if isinstance(v, str):
                v = context.render(v)
            cfg[k] = v
        # 写临时 cfg.json（worker /tmp 共享）
        import os as _os
        import tempfile
        fd, cfg_path = tempfile.mkstemp(suffix=".json", prefix=f"orch_{self.ATOM_NAME}_")
        with _os.fdopen(fd, "w") as f:
            json.dump(cfg, f, ensure_ascii=False)
        self._cfg_path = cfg_path
        return [sys.executable, f"{self.SCRIPTS_DIR}/orch_atoms.py",
                self.ATOM_NAME, "--config", cfg_path]

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        self._cfg_path = None
        try:
            cmd = self._build_command(context)
            # 复用 RunScriptStep 的 command 路径（临时把 command 塞进 config）
            saved = self.config
            self.config = dict(saved)
            self.config["command"] = cmd
            try:
                return super().handle(context)
            finally:
                self.config = saved
        finally:
            if getattr(self, "_cfg_path", None):
                try:
                    import os as _os2
                    _os2.remove(self._cfg_path)
                except (OSError, NameError):
                    pass


class LlmExtractStep(_AtomScriptStep):
    """'llm_extract' LLM 结构化抽取（2026-10-09 业务原子）。

    config 字段见 orch_atoms.py llm_extract：source/source_key/prompt/
    prompt_file/output/output_shape/batch_size/max_workers/max_tokens/
    retries/dedupe_field。
    """

    ATOM_NAME = "llm_extract"


class WikiPublishStep(_AtomScriptStep):
    """'wiki_publish' 幂等知识入库（2026-10-09 业务原子）。

    config 字段见 orch_atoms.py wiki_publish：source/folder/page_type/
    slug_prefix/slug_hash_field/title_field/content_template/source_ref/dry_run。
    """

    ATOM_NAME = "wiki_publish"


class LongSentenceExtractStep(_AtomScriptStep):
    """'long_sentence_extract' 长句原文拆分（2026-10-12 业务原子）。

    包装 WeKnora extract_long_sentences_v2.py（切句→LLM 整篇归组→拼接校验）。
    config 字段见 orch_atoms.py long_sentence_extract：chunks_file/output/
    summary_file/summary_slug/max_tokens/model/timeout_sec。
    """

    ATOM_NAME = "long_sentence_extract"


class KeywordExtractStep(_AtomScriptStep):
    """'keyword_extract' 高频关键词抽取（2026-10-12 业务原子）。

    两步管线：程序统计 discover_terms → LLM 过滤 filter_terms_llm。
    config 字段见 orch_atoms.py keyword_extract：ls_file/output/min_freq/
    top/max_len/batch_size/kb/model/dry_run/timeout_sec。
    """

    ATOM_NAME = "keyword_extract"


class KbReadDocStep(BaseStep):
    """'kb_read_doc' 读文档切片（2026-10-12 基础原子）。

    细粒度基础能力：从 doc_chunk 读指定文档的全部切片内容，写入运行变量
    （默认 chunks），供下游 script/llm 原子使用。config:
      - document_id: 文档 ID（支持 {{ }} 模板）
      - output_var: 输出变量名（默认 chunks）
      - max_chunks: 最多读 N 块（默认 0=全部）
    """

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        from api.db import get_sessionmaker
        from api.models.knowledge import DocChunk, KbDatasource, KbDocument

        document_id = str(context.render(self.config.get("document_id") or "")).strip()
        if not document_id:
            return StepResponse.fail("kb_read_doc 缺 document_id")
        output_var = str(self.config.get("output_var") or "chunks")
        max_chunks = int(self.config.get("max_chunks") or 0)

        # 2026-10-14：文档链路的编排使用知识库配置的摘要模型——读 KB 的
        # summary_model_id 注入 bindings（下游 kb_llm_chat 配 model={{summary_model_id}}
        # 即生效；KB 未配置/查询失败时为空串，LLM 节点回落系统默认，不阻断）。
        summary_model_id = ""
        db = get_sessionmaker()()
        try:
            doc = db.query(KbDocument).filter(KbDocument.id == document_id).first()
            if doc and doc.kb_id:
                kb = db.query(KbDatasource).filter(KbDatasource.id == doc.kb_id).first()
                if kb:
                    summary_model_id = getattr(kb, "summary_model_id", None) or ""
            q = (
                db.query(DocChunk)
                .filter(DocChunk.document_id == document_id)
                .order_by(DocChunk.seq)
            )
            if max_chunks > 0:
                q = q.limit(max_chunks)
            rows = q.all()
            chunks = [
                {"seq": c.seq, "content": c.content or ""}
                for c in rows
            ]
        except Exception:  # noqa: BLE001 — KB 模型读取失败不阻断切片读取
            summary_model_id = ""
            db.rollback()
            rows = []
            chunks = []
        finally:
            db.close()
        if not chunks:
            return StepResponse.fail(f"文档无切片: {document_id}")
        context.bindings[output_var] = chunks
        context.bindings["summary_model_id"] = summary_model_id
        return StepResponse.success(
            {f"set:{output_var}": chunks, "count": len(chunks), "summary_model_id": summary_model_id}
        )


class KbLlmChatStep(BaseStep):
    """'kb_llm_chat' 通用 LLM 对话（2026-10-12 基础原子）。

    细粒度基础能力：一次 LLM 调用（OpenAI 兼容，走平台 chat 配置），
    prompt 支持 {{ }} 模板引用前面节点的变量；输出写入变量。config:
      - prompt: 用户消息（支持 {{ }} 模板）
      - output_var: 输出变量名（默认 llm_out）
      - model: 覆盖模型（留空=平台默认）
      - temperature: 采样温度（默认 0.2）
      - max_tokens: 上限（默认 2048）
      - json_mode: 强制解析 JSON 输出为 dict/list（默认 False）
    """

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        from api.services.chat import ChatClient, load_chat_config

        prompt = str(context.render(self.config.get("prompt") or "")).strip()
        output_var = str(self.config.get("output_var") or "llm_out")
        # 2026-10-14：编排节点可配 model={{summary_model_id}} 引用知识库配置的
        # 摘要模型（文档链路 KbReadDocStep 注入 bindings）。模板渲染失败会残留
        # {{...}} 字面量（bindings 缺键时 _template_replace 原样返回）——此时
        # 视为未指定模型回落系统默认，避免把字面量当模型 id 去查询。
        model_raw = str(context.render(self.config.get("model") or "")).strip()
        model = None
        if model_raw and "{{" not in model_raw:
            model = model_raw
        if not model:
            # 2026-10-17 改造：未显式指定模型时，优先用知识库绑定的摘要/对话模型
            # 2026-10-28 收紧：**不再回落系统默认/env**——知识库创建时强制绑定
            # summary_model_id，该模型即唯一来源；bindings.summary_model_id 为空
            # （编排节点未配置 model={{summary_model_id}}）或知识库未绑定 → 显式失败。
            _kb_id = str(context.bindings.get("kb_id") or "").strip()
            if not _kb_id:
                return StepResponse.fail(
                    "kb_llm_chat 缺少模型：编排节点未配置 model 且运行时无 kb_id——"
                    "请在编排节点配置 model={{summary_model_id}} 或传入知识库上下文"
                )
            from api.db import get_sessionmaker as _gsm2
            from sqlalchemy import text as _sqlt2
            _dbm = _gsm2()()
            try:
                _mr = _dbm.execute(_sqlt2(
                    "select summary_model_id from kb_datasource where id = :k"),
                    {"k": _kb_id}).first()
            finally:
                _dbm.close()
            if _mr and _mr[0]:
                model = _mr[0]
            if not model:
                return StepResponse.fail(
                    f"kb_llm_chat 缺少模型：知识库 {_kb_id} 未配置大语言模型"
                    "（summary_model_id 为空）——请到知识库配置中绑定 LLM 模型"
                )
        temperature = float(self.config.get("temperature") or 0.2)
        max_tokens = int(self.config.get("max_tokens") or 2048)
        json_mode = bool(self.config.get("json_mode"))
        # 2026-10-12 摘要三模式模板：loop 内条件触发合并 LLM——非最后一轮
        # prompt 渲染为空时跳过（空合并）而不失败，与 wiki_upsert_page 的
        # skip_empty 语义对称（loop 下游节点每轮都跑，靠它只让最后一轮真合并）。
        skip_empty = bool(self.config.get("skip_empty"))
        if not prompt:
            if skip_empty:
                return StepResponse.success({"skipped": True, "reason": "empty prompt (skip_empty)"})
            return StepResponse.fail("kb_llm_chat 缺 prompt")

        try:
            # 2026-10-12 修复：db 必须传——load_chat_config(None) 走 legacy env，
            # model_id 解析不到模型注册表（KB 配置的摘要模型）→ AI_CHAT_API_ENDPOINT
            # is not configured。开会话传 db 后 model_id 走 resolve_model_config。
            from api.db import get_sessionmaker

            _db = get_sessionmaker()()
            try:
                cfg = load_chat_config(_db, model_id=model)
                client = ChatClient(cfg)
                from api.services.chat import ChatMessage

                # 2026-10-13 质量修复：支持 config.system（支持 {{ }} 模板）。
                # 此前只发 user 消息 → 模型无角色/输出纪律约束，思考草稿混入
                # content（实测 Refine 摘要输出英文推理+逐字数字数）。其他脚本
                # 均带 system「只输出JSON，不要思考过程」故正常。
                system_prompt = str(context.render(self.config.get("system") or "")).strip()
                msgs: list = []
                if system_prompt:
                    msgs.append(ChatMessage(role="system", content=system_prompt))
                msgs.append(ChatMessage(role="user", content=prompt))

                # 2026-10-13 可靠性修复：429/403/5xx 退避重试。此前无重试，
                # 多轮循环编排撞 inferaiapi 限流（~3 次/分）即失败（实测凌晨
                # Refine run 第 6 轮 429 挂掉）。429 退避 15s 起逐步到 45s，
                # 5xx 3s 起；max_attempts 默认 5。
                import time as _time

                max_attempts = max(1, int(self.config.get("max_attempts") or 5))
                last_exc: Exception | None = None
                raw = ""
                # 2026-10-13 质量修复：config.thinking 透传（"disabled" 常见）——
                # inferai 网关的 deepseek-v4-pro 默认 thinking 开启，会习惯性把
                # 思考过程写进 content（实测摘要输出英文推理+字数统计+草稿），
                # 禁掉 thinking 后模型被迫直接输出答案。None=不传保持原行为。
                thinking_cfg = str(self.config.get("thinking") or "").strip().lower()
                thinking: bool | None = None
                if thinking_cfg == "enabled":
                    thinking = True
                elif thinking_cfg == "disabled":
                    thinking = False
                for attempt in range(max_attempts):
                    try:
                        raw = client.chat(
                            msgs, temperature=temperature, max_tokens=max_tokens, thinking=thinking
                        )
                        last_exc = None
                        break
                    except Exception as e:  # noqa: BLE001
                        last_exc = e
                        resp = getattr(e, "response", None)
                        code = getattr(resp, "status_code", None) if resp is not None else None
                        retryable = code in (403, 429, 500, 502, 503, 504, 520, 521, 522, 524)
                        if not retryable:
                            break
                        base = 15 if code == 429 else 3
                        _time.sleep(min(base * (attempt + 1), 45))
            finally:
                _db.close()
        except Exception as e:  # noqa: BLE001
            return StepResponse.fail(f"LLM 调用失败: {e}")
        if last_exc is not None:
            return StepResponse.fail(f"LLM 调用失败: {last_exc}")
        if json_mode:
            import json as _json

            text = (raw or "").strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.startswith("json"):
                    text = text[4:].strip()
            try:
                parsed = _json.loads(text)
            except Exception as e:  # noqa: BLE001
                return StepResponse.fail(f"LLM 输出非 JSON: {e}，原文: {raw[:200]}")
            context.bindings[output_var] = parsed
            return StepResponse.success({f"set:{output_var}": parsed})
        context.bindings[output_var] = raw or ""
        return StepResponse.success({f"set:{output_var}": raw or ""})


class KbAgentRunStep(BaseStep):
    """'agent_run' Agent 执行（2026-10-15 基础原子）。

    把一次 agent 循环（openai-agents SDK：思考 + 技能工具 + MCP 工具）投递到
    **agent 队列**（kb-celery-agent-worker）执行，参与服务编排。节点所在 worker
    若本身即 agent worker（进程内可 import SDK）则直接内联执行（零跨进程等待），
    否则 send_task 到 agent 队列并同步等待结果（AsyncResult.get）。

    支持配置提示词/系统指令/技能/MCP/模型：
      - prompt: 任务输入（支持 {{ }} 模板；agent 据此自主调用工具完成任务）
      - instructions: agent 系统提示词（可选；角色/输出纪律约束）
      - agent_name: agent 名（默认 kb-agent-worker）
      - skill: 技能名（内置技能或 kb_skill 表技能；留空=不挂技能工具）
      - kb_id: 知识库 ID（技能/MCP 可见性按库创建者个人/团队权限解析）
      - model: 覆盖模型（留空=agent worker 环境默认，WIKI_LLM_* 链）
      - output_var: 输出变量名（默认 agent_out）
      - timeout_sec: 等待超时秒（默认 1800）
      - skip_empty: prompt 渲染为空时跳过（不失败）
    """

    def _run_agent(self, payload: dict, timeout_sec: float) -> dict:
        """执行一次 agent 循环：agent worker 进程内内联优先，否则投递 agent 队列等待。

        内联判断：worker.agent.runtime 依赖 openai-agents SDK（只装在 agent worker
        镜像，pyproject 刻意不引入）——import 成功说明当前 worker 就是 agent
        worker（节点配 queue=agent 时 handle 在其进程内执行）。
        """
        import uuid as _uuid

        try:
            from worker.agent.runtime import run_agent_sync
        except ImportError:  # SDK 未装 → 非 agent worker
            run_agent_sync = None
        if run_agent_sync is not None:
            return run_agent_sync(payload, task_id=f"orch_agent_{_uuid.uuid4().hex[:12]}")

        # 回退路径：投递 agent 队列，由 kb-celery-agent-worker 执行并回传结果。
        from worker.celery_app import celery_app

        task_id = f"agent_run_{_uuid.uuid4().hex[:12]}"
        res = celery_app.send_task(
            "worker.tasks.agent_worker.run_agent_inline",
            args=[payload, task_id],
            task_id=task_id,
            queue="agent",
        )
        return res.get(timeout=timeout_sec)

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        prompt = str(context.render(self.config.get("prompt") or "")).strip()
        output_var = str(self.config.get("output_var") or "agent_out")
        skip_empty = bool(self.config.get("skip_empty"))
        if not prompt:
            if skip_empty:
                return StepResponse.success({"skipped": True, "reason": "empty prompt (skip_empty)"})
            return StepResponse.fail("agent_run 缺 prompt")
        timeout_sec = float(self.config.get("timeout_sec") or 1800)

        # 模板渲染 + 字面量兜底（bindings 缺键时 _template_replace 原样返回
        # {{...}}——视为未配置，与 KbLlmChatStep 的 model 处理同语义）
        instructions = str(context.render(self.config.get("instructions") or "")).strip()
        agent_name = str(context.render(self.config.get("agent_name") or "kb-agent-worker")).strip()
        kb_id = str(context.render(self.config.get("kb_id") or "")).strip()
        model = str(context.render(self.config.get("model") or "")).strip()
        if "{{" in model or not model:
            model = ""
        skill = str(context.render(self.config.get("skill") or "")).strip()

        cfg: dict = {}
        if kb_id:
            cfg["kb_id"] = kb_id
        if model:
            cfg["model"] = model
        if skill:
            cfg["skill"] = skill
        payload: dict = {"input": prompt, "config": cfg}
        if instructions:
            payload["instructions"] = instructions
        if agent_name:
            payload["agent_name"] = agent_name

        try:
            result = self._run_agent(payload, timeout_sec)
        except Exception as e:  # noqa: BLE001 — 含 AsyncResult 超时/投递失败
            return StepResponse.fail(f"Agent 执行失败: {e}")
        if not result.get("success"):
            err = str(result.get("error") or "未知错误")
            return StepResponse.fail(f"Agent 执行失败: {err}")
        out = result.get("output") or ""
        context.bindings[output_var] = out
        return StepResponse.success(
            {f"set:{output_var}": out, "runs_ms": result.get("runs_ms")}
        )


class WikiUpsertPageStep(BaseStep):
    """'wiki_upsert_page' 单页幂等写入（2026-10-12 基础原子）。

    细粒度基础能力：写一张 wiki 页（slug 冲突则更新）。config:
      - kb_id / title / content / slug: 页面字段（支持 {{ }} 模板）
      - page_type: 页面类型（默认 summary）
      - skip_empty: title/slug 为空时跳过（成功记账）而不失败——loop 空集合
        退化成执行一轮时（引擎语义）循环体拿不到变量，用它兜底
      - output_var: 输出变量名（默认 page_slug）
    """

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        from api.db import get_sessionmaker
        from api.services.kb_admin import wiki_create_page, wiki_update_page

        def _v(key: str) -> str:
            return str(context.render(self.config.get(key) or "")).strip()

        kb_id = _v("kb_id")
        title = _v("title")
        content = _v("content")
        slug = _v("slug")
        page_type = str(self.config.get("page_type") or "summary")
        skip_empty = bool(self.config.get("skip_empty"))
        output_var = str(self.config.get("output_var") or "page_slug")
        if not kb_id:
            return StepResponse.fail(f"wiki_upsert_page 缺 kb_id（got {kb_id}）")
        if (not title or not slug) and skip_empty:
            return StepResponse.success({"skipped": True, "reason": "title/slug 为空（skip_empty）"})
        if not title or not slug:
            return StepResponse.fail(f"wiki_upsert_page 缺 title/slug（got title={title}, slug={slug}）")

        db = get_sessionmaker()()
        try:
            try:
                wiki_create_page(
                    db, kb_id,
                    {"title": title, "content": content, "slug": slug, "page_type": page_type},
                    user_id="system",
                )
                created = True
            except ValueError as e:
                if "已存在" in str(e) or "exists" in str(e).lower():
                    wiki_update_page(db, kb_id, slug, {"title": title, "content": content, "status": "active"})
                    created = False
                else:
                    raise
        finally:
            db.close()
        context.bindings[output_var] = slug
        return StepResponse.success({f"set:{output_var}": slug, "slug": slug, "created": created})


class KbWriteDocSummaryStep(BaseStep):
    """'kb_write_doc_summary' 把摘要写入 kb_document.summary（2026-10-14 基础原子）。

    直连 LLM 摘要链路移除（be65902）后，文档摘要统一由摘要编排生成；本原子
    承接「写字段」职责（替代原本写 wiki 摘要页的 wiki_upsert_page），把编排
    产出的最终摘要写入 kb_document.summary / summary_status=READY /
    summary_error=NULL（直连 LLM 时代摘要的存储位置）。config:
      - document_id: 文档 ID（支持 {{ }} 模板）
      - summary_var: 摘要文本来源变量名（默认 final_summary）
      - status: 写成的 summary_status（默认 READY）
      - skip_empty: summary 为空时跳过（成功记账）而不失败——loop 非末轮等
        场景下游每轮都会执行本节点，靠它只让有内容的轮次真正写库
    """

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        from api.db import get_sessionmaker
        from api.models.knowledge import KbDocument

        document_id = str(context.render(self.config.get("document_id") or "")).strip()
        summary_var = str(self.config.get("summary_var") or "final_summary")
        summary = str(context.bindings.get(summary_var) or "").strip()
        status = str(self.config.get("status") or "READY").strip() or "READY"
        skip_empty = bool(self.config.get("skip_empty"))
        if not document_id:
            return StepResponse.fail("kb_write_doc_summary 缺 document_id")
        if not summary and skip_empty:
            return StepResponse.success({"skipped": True, "reason": "summary 为空（skip_empty）"})
        if not summary:
            return StepResponse.fail("kb_write_doc_summary 缺 summary 文本")

        db = get_sessionmaker()()
        try:
            doc = db.query(KbDocument).filter(KbDocument.id == document_id).first()
            if not doc:
                return StepResponse.fail(f"文档不存在: {document_id}")
            doc.summary = summary
            doc.summary_status = status
            doc.summary_error = None
            db.commit()
        finally:
            db.close()
        return StepResponse.success(
            {"document_id": document_id, "summary_length": len(summary), "status": status}
        )


class WikiRebuildLinksStep(BaseStep):
    """'wiki_rebuild_links' 重建互链（2026-10-12 基础原子）。

    细粒度基础能力：重建一个知识库全部 wiki 页之间的互链。config:
      - kb_id: 知识库 ID（支持 {{ }} 模板）
    """

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        from api.db import get_sessionmaker
        from api.services.kb_admin import wiki_rebuild_links

        kb_id = str(context.render(self.config.get("kb_id") or "")).strip()
        if not kb_id:
            return StepResponse.fail("wiki_rebuild_links 缺 kb_id")
        db = get_sessionmaker()()
        try:
            wiki_rebuild_links(db, kb_id)
        finally:
            db.close()
        return StepResponse.success({"kb_id": kb_id, "links_rebuilt": True})


class KbReadTableStep(BaseStep):
    """'kb_read_table' 读表源数据（2026-10-12 基础原子）。

    细粒度基础能力：从数据源实时读一张绑定表（复用 datagrid 连接层），
    输出到运行变量供下游 script/llm 原子构造 markdown 与 wiki 页。
    等价于 table_ingest._load_table_rows + 元数据列注释补全。config:
      - document_id: 表源文档 ID（支持 {{ }} 模板）
      - output_var: 输出变量名（默认 table_data）
      - limit: 样例行数上限（默认 500）
    输出 bindings[output_var] = {
        "rows": [dict], "columns": [str], "col_comments": {c: str},
        "col_types": {c: str}, "table_comment": str|None,
        "table_name": str, "schema": str, "ds_name": str,
        "kb_id": str, "summary": str|None, "summary_model_id": str|None,
    }
    """

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        import sqlalchemy as sa

        from api.db import get_sessionmaker
        from api.models.knowledge import KbDocument

        document_id = str(context.render(self.config.get("document_id") or "")).strip()
        if not document_id:
            return StepResponse.fail("kb_read_table 缺 document_id")
        output_var = str(self.config.get("output_var") or "table_data")
        limit = int(self.config.get("limit") or 500)

        db = get_sessionmaker()()
        try:
            doc = db.execute(
                sa.select(KbDocument).where(KbDocument.id == document_id)
            ).scalars().first()
            if not doc:
                return StepResponse.fail(f"表源文档不存在: {document_id}")
            if doc.source_type not in ("table", None, ""):
                return StepResponse.fail(f"文档不是表源（source_type={doc.source_type}）")

            # 取数（复用 datagrid 连接层，空 user/team = 系统级）
            from api.services import datagrid

            ds_name = doc.ds_source_name or ""
            if not ds_name:
                return StepResponse.fail("表源缺少数据源（ds_source_name）")
            entry = datagrid._find_entry(db, ds_name, user_id="", team_name="")
            if entry is None:
                return StepResponse.fail(f"数据源不存在或不可用: {ds_name}")
            engine = datagrid._connect(entry)
            schema = (doc.ds_table_schema or "").strip()
            table = (doc.ds_table_name or "").strip()
            if not table:
                return StepResponse.fail("表源缺少表名（ds_table_name）")
            table_ref = f"{schema}.{table}" if schema else table
            sql = f"SELECT * FROM {table_ref} LIMIT {int(limit)}"
            with engine.connect() as conn:
                result = conn.exec_driver_sql(sql)
                cols = list(result.keys())
                rows = [dict(zip(cols, r)) for r in result.fetchall()]

            # 元数据列注释/类型/表注释补全（缺失不阻塞）
            col_comments: dict[str, str] = {}
            col_types: dict[str, str] = {}
            table_comment: str | None = None
            try:
                from api.models.framework import Datasource
                from api.models.metadata import MetadataColumn, MetadataTable

                mt_id = None
                if doc.ds_source_name and doc.ds_table_name:
                    ds_id = db.execute(
                        sa.select(Datasource.id).where(Datasource.name == doc.ds_source_name)
                    ).scalars().first()
                    if ds_id:
                        mt_id = db.execute(
                            sa.select(MetadataTable.id).where(
                                MetadataTable.datasource_id == ds_id,
                                MetadataTable.table_name == doc.ds_table_name,
                                sa.or_(
                                    MetadataTable.schema_name == (doc.ds_table_schema or ""),
                                    MetadataTable.schema_name.is_(None),
                                ),
                            )
                        ).scalars().first()
                if mt_id:
                    mt = db.get(MetadataTable, mt_id)
                    if mt and mt.table_comment:
                        table_comment = mt.table_comment
                    for cname, ccomment, ctype in db.execute(
                        sa.select(
                            MetadataColumn.column_name,
                            MetadataColumn.column_comment,
                            MetadataColumn.data_type,
                        ).where(MetadataColumn.metadata_table_id == mt_id)
                    ).all():
                        if cname and ccomment:
                            col_comments[cname] = ccomment
                        if cname and ctype:
                            col_types[cname] = str(ctype)
            except Exception:  # noqa: BLE001
                col_comments = {}
                col_types = {}

            # KB 配置（摘要模型/摘要文本供下游 LLM 与正文使用）
            summary = doc.summary or None
            summary_model_id = None
            try:
                from api.models.knowledge import KbDatasource

                kb = db.execute(
                    sa.select(KbDatasource).where(KbDatasource.id == doc.kb_id)
                ).scalars().first()
                if kb:
                    summary_model_id = getattr(kb, "summary_model_id", None) or None
            except Exception:  # noqa: BLE001
                pass
        finally:
            db.close()

        out = {
            "rows": rows,
            "columns": cols,
            "col_comments": col_comments,
            "col_types": col_types,
            "table_comment": table_comment,
            "table_name": table,
            "schema": schema,
            "ds_name": ds_name,
            "kb_id": doc.kb_id or "",
            "summary": summary,
            "summary_model_id": summary_model_id,
        }
        context.bindings[output_var] = out
        return StepResponse.success({f"set:{output_var}": out, "rows": len(rows), "columns": len(cols)})


class WikiEnsureFolderStep(BaseStep):
    """'wiki_ensure_folder' 初始化 wiki 目录（2026-10-12 基础原子）。

    细粒度基础能力：**幂等**确保目录存在（同 kb 同级同名已存在则复用，不重复建），
    支持多级路径（按 sep 逐级建）与批量（paths 数组）。目录 id 写入变量供下游
    wiki_upsert_page / wiki_publish 的 folder_id 使用。config:
      - kb_id: 知识库 ID（支持 {{ }} 模板，必填）
      - path: 单条目录路径（多级用 sep 分隔，如「长句原文/2024」）
      - paths: 批量目录路径（JSON 数组，可选，与 path 二选一或并存）
      - sep: 路径分隔符（默认 /）
      - parent_id: 顶层目录的父级（默认根目录）
      - output_var: 输出变量名（默认 folder_id；批量时输出 id 列表）
      - dry_run: 只统计不写库
    """

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        from api.db import get_sessionmaker
        from api.models.knowledge import WikiFolder
        from api.services.kb_admin import wiki_create_folder
        from sqlalchemy import select

        kb_id = str(context.render(self.config.get("kb_id") or "")).strip()
        if not kb_id:
            return StepResponse.fail("wiki_ensure_folder 缺 kb_id")
        sep = str(self.config.get("sep") or "/")
        parent_id = str(context.render(self.config.get("parent_id") or "")).strip()
        output_var = str(self.config.get("output_var") or "folder_id")
        dry_run = bool(self.config.get("dry_run"))

        # 归集要确保的路径列表（path 单条 + paths 批量）
        raw_paths: list[str] = []
        single = str(context.render(self.config.get("path") or "")).strip()
        if single:
            raw_paths.append(single)
        batch = self.config.get("paths")
        if isinstance(batch, str):
            # 2026-10-28 动态建目录：paths 支持 {{ }} 模板整体渲染
            # （渲染后为 JSON 数组字符串，如 {{kp_paths}}）——动态 wiki 构建
            # 编排的目录按知识点运行期决定，先渲染再解析。
            if "{{" in batch:
                batch = context.render(batch)
            try:
                batch = json.loads(batch)
            except ValueError:
                # {{ }} 整体渲染走 str() 会产生 Python 字面量（单引号
                # list，如 "['指标', '出帐']"）而非 JSON —— ast 兜底。
                import ast

                try:
                    batch = ast.literal_eval(batch)
                except (ValueError, SyntaxError) as e:  # noqa: BLE001
                    return StepResponse.fail(
                        f"paths JSON 解析失败: {batch[:80]}（{e}）"
                    )
        if isinstance(batch, list):
            for p in batch:
                ps = str(context.render(p) if isinstance(p, str) else (p or "")).strip()
                if ps:
                    raw_paths.append(ps)
        if not raw_paths:
            return StepResponse.fail("wiki_ensure_folder 缺 path 或 paths")

        db = get_sessionmaker()()
        created = reused = 0
        try:
            folder_ids: list[str] = []
            for full_path in raw_paths:
                cur_parent = parent_id
                segs = [s.strip() for s in full_path.split(sep) if s.strip()]
                for seg in segs:
                    row = db.execute(
                        select(WikiFolder).where(
                            WikiFolder.kb_id == kb_id,
                            WikiFolder.name == seg,
                            (WikiFolder.parent_id == cur_parent) | (WikiFolder.parent_id.is_(None)),
                        )
                    ).scalars().first()
                    if row:
                        reused += 1
                        cur_parent = row.id
                        if seg == segs[-1]:
                            folder_ids.append(cur_parent)  # 只记末级目录 id
                        continue
                    if dry_run:
                        created += 1
                        cur_parent = f"[dry]{seg}"
                        if seg == segs[-1]:
                            folder_ids.append(cur_parent)
                        continue
                    res = wiki_create_folder(
                        db, kb_id, {"name": seg, "parent_id": cur_parent}, user_id="system"
                    )
                    created += 1
                    cur_parent = str(res["id"])
                    if seg == segs[-1]:
                        folder_ids.append(cur_parent)
            result = {
                "kb_id": kb_id,
                "paths": raw_paths,
                "created": created,
                "reused": reused,
                "folder_ids": folder_ids,
            }
            if dry_run:
                return StepResponse.success(result)
            # 单条路径 → 输出末级目录 id；多条 → 输出 id 列表
            context.bindings[output_var] = folder_ids[0] if len(folder_ids) == 1 else folder_ids
            result[f"set:{output_var}"] = context.bindings[output_var]
            return StepResponse.success(result)
        except ValueError as e:
            return StepResponse.fail(f"目录创建失败: {e}")
        finally:
            db.close()


class IfStep(BaseStep):
    """'if' 条件分支：condition 为真走 next_step_ids，为假走 fail 后继。"""

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        condition = str(self.config.get("condition") or "True")
        try:
            result = bool(context.evaluate(condition))
        except Exception as e:  # noqa: BLE001
            return StepResponse.fail(f"条件表达式求值失败: {e}")
        return StepResponse.success(result)


class LoopStep(BaseStep):
    """'loop' 循环（kb 新增）：迭代后继子图。

    配置：{item_var: "x", collection: "[1,2,3]"}。执行器对 collection 的
    每一项设置 bindings[item_var] 并重跑后继子图（连线即循环体）。
    """

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        expr = str(self.config.get("collection") or "[]")
        try:
            items = context.evaluate(expr)
        except Exception as e:  # noqa: BLE001
            return StepResponse.fail(f"循环集合求值失败: {e}")
        if not isinstance(items, (list, tuple)):
            return StepResponse.fail(f"循环集合必须是列表，实际: {type(items).__name__}")
        return StepResponse.success(list(items))


# ───────────────────────── 指令注册表 ─────────────────────────

ATOM_MAP: Dict[str, Callable[[str, str, str, Optional[dict]], BaseStep]] = {
    "def": DefStep,
    "print": PrintStep,
    "script": ScriptStep,
    "run_script": RunScriptStep,
    "if": IfStep,
    "loop": LoopStep,
    # 业务原子（2026-10-09）：wiki 构建拆出的通用组件，实现在 orch_atoms.py
    "llm_extract": LlmExtractStep,
    "wiki_publish": WikiPublishStep,
    # 业务原子（2026-10-12）：长句拆分 / 关键词抽取——包装 WeKnora 既有脚本
    "long_sentence_extract": LongSentenceExtractStep,
    "keyword_extract": KeywordExtractStep,
    # 细粒度基础原子（2026-10-12）：编排自己实现 wiki 构建逻辑（进程内执行，
    # 结果经 bindings 流转）。原子 = 纯基础能力，流程由编排图表达。
    "kb_read_doc": KbReadDocStep,
    "kb_read_table": KbReadTableStep,
    "kb_llm_chat": KbLlmChatStep,
    # 2026-10-15：Agent 执行（agent_run）——投递 agent 队列由 kb-celery-agent-worker
    # 跑 openai-agents 循环（技能/MCP 工具），结果写 bindings。
    "agent_run": KbAgentRunStep,
    "wiki_upsert_page": WikiUpsertPageStep,
    "kb_write_doc_summary": KbWriteDocSummaryStep,
    "wiki_rebuild_links": WikiRebuildLinksStep,
    "wiki_ensure_folder": WikiEnsureFolderStep,
}


# ───────────────────────── 引擎加载器 ─────────────────────────

class OrchestrationEngine:
    """从 kb_tape_step 加载步骤并 BFS 执行。"""

    def __init__(self, db):
        self.db = db
        self.step_index: Dict[str, BaseStep] = {}

    def load_tape(self, tape: Tape) -> Tape:
        steps = (
            self.db.query(TapeStep)
            .filter(TapeStep.tape_id == tape.id)
            .order_by(TapeStep.step_seq)
            .all()
        )
        self.step_index = {}
        seq_to_id: Dict[str, str] = {}
        for s in steps:
            seq_to_id[str(s.step_seq)] = s.id
        for s in steps:
            step_class = ATOM_MAP.get(s.step_inst)
            if not step_class:
                logger.warning("未知组件指令: %s (step %s)", s.step_inst, s.id)
                continue
            obj = step_class(s.id, s.step_label or s.step_inst, s.step_inst, s.step_config or {})
            obj.pre_step_ids = [p for p in (s.pre_step_ids or []) if p]
            obj.next_step_ids = [p for p in (s.next_step_ids or []) if p]
            self.step_index[s.id] = obj
        return tape

    def __run_bfs(
        self,
        roots: List[str],
        context: TapeRuntimeContext,
        executed: Set[str],
        results: List[dict],
        task_id: str,
        depth: int = 0,
    ) -> None:
        """执行给定根步骤集合的 BFS（循环体子图执行复用）。"""
        queue: List[str] = list(roots)
        while queue:
            sid = queue.pop(0)
            step = self.step_index.get(sid)
            if not step or sid in executed:
                continue
            # 前置依赖是否全部已执行
            if not all(p in executed for p in step.pre_step_ids):
                continue
            start = time.time()
            status, body, error = "success", None, ""
            try:
                response = step.handle(context)
                status = "success" if response.success else "failed"
                if not response.success:
                    error = response.error
                else:
                    body = response.body
            except Exception as e:  # noqa: BLE001
                status = "failed"
                error = str(e)
            results.append(
                {
                    "step_id": sid,
                    "step_label": step.step_label,
                    "step_inst": step.step_inst,
                    "status": status,
                    "duration_ms": int((time.time() - start) * 1000),
                    "body": body if status == "success" else None,
                    "error": error or None,
                }
            )
            executed.add(sid)
            logger.info("编排步骤[%s %s] -> %s (%.0fms)", step.step_inst, step.step_label, status, (time.time() - start) * 1000)
            for nxt in step.next_step_ids:
                if nxt and nxt not in executed:
                    queue.append(nxt)

    def execute(self, tape: Tape, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # 幂等加载步骤（service 层可能已 load，直调时兜底）
        self.step_index = {}
        self.load_tape(tape)
        task_id = uuid.uuid4().hex[:12]
        context = TapeRuntimeContext(dict(inputs or {}))
        executed: Set[str] = set()
        results: List[dict] = []

        # 根节点：无前驱（或前驱不在本编排内）
        roots = [sid for sid, s in self.step_index.items() if not s.pre_step_ids]

        # 主流程 BFS；loop 步骤在队列中遇到时展开迭代
        queue: List[str] = list(roots)
        while queue:
            sid = queue.pop(0)
            step = self.step_index.get(sid)
            if not step or sid in executed:
                continue
            if not all(p in executed for p in step.pre_step_ids):
                continue

            if step.step_inst == "loop":
                # loop 自身视为已完成：迭代期间循环体步骤的 pre 依赖可满足
                executed.add(sid)
                # 记录循环体子图（跳过 loop 自身的 body 记录）
                body_ids: List[str] = []
                stack = [n for n in step.next_step_ids if n in self.step_index]
                while stack:
                    n = stack.pop(0)
                    if n in body_ids or n in executed:
                        continue
                    body_ids.append(n)
                    stack.extend(self.step_index[n].next_step_ids)
                # 循环体执行：逐项迭代
                collection = step.handle(context).body or []
                item_var = step.config.get("item_var")
                for item in collection:
                    if item_var:
                        context.bindings[item_var] = item
                    # 重置循环体步骤的已执行标记（每轮重跑）
                    for b in body_ids:
                        executed.discard(b)
                    self.__run_bfs(body_ids, context, executed, results, task_id)
                # loop 自身记一条成功
                results.append(
                    {
                        "step_id": sid,
                        "step_label": step.step_label,
                        "step_inst": "loop",
                        "status": "success",
                        "duration_ms": 0,
                        "body": f"循环完成，共 {len(collection)} 次迭代",
                        "error": None,
                    }
                )
                for nxt in step.next_step_ids:
                    if nxt and nxt not in executed:
                        queue.append(nxt)
                continue

            start = time.time()
            status, body, error = "success", None, ""
            try:
                response = step.handle(context)
                status = "success" if response.success else "failed"
                if not response.success:
                    error = response.error
                else:
                    body = response.body
            except Exception as e:  # noqa: BLE001
                status = "failed"
                error = str(e)
            results.append(
                {
                    "step_id": sid,
                    "step_label": step.step_label,
                    "step_inst": step.step_inst,
                    "status": status,
                    "duration_ms": int((time.time() - start) * 1000),
                    "body": body if status == "success" else None,
                    "error": error or None,
                }
            )
            executed.add(sid)
            logger.info("编排步骤[%s %s] -> %s (%.0fms)", step.step_inst, step.step_label, status, (time.time() - start) * 1000)
            for nxt in step.next_step_ids:
                if nxt and nxt not in executed:
                    queue.append(nxt)

        return {
            "task_id": task_id,
            "tape_id": tape.id,
            "tape_name": tape.tape_name,
            "success": all(r["status"] == "success" for r in results),
            "steps": results,
            "bindings": {k: str(v)[:200] for k, v in context.bindings.items()},
        }


def list_step_defines(db, page=1, page_size=20, keyword="", group_type=""):
    """组件定义列表（分页 + 关键词/分组筛选）。"""
    q = db.query(StepDefine)
    if keyword:
        q = q.filter(
            StepDefine.step_label.like(f"%{keyword}%") | StepDefine.step_inst.like(f"%{keyword}%")
        )
    if group_type:
        q = q.filter(StepDefine.group_type == group_type)
    total = q.count()
    items = q.order_by(StepDefine.step_seq.asc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": [serialize_step_define(s) for s in items]}


def serialize_step_define(s: StepDefine) -> dict:
    return {
        "id": s.id,
        "group_type": s.group_type,
        "step_inst": s.step_inst,
        "step_label": s.step_label,
        "step_icon": s.step_icon,
        "step_desc": s.step_desc,
        "step_cfg": s.step_cfg or {},
        "step_seq": s.step_seq,
        "status": s.status,
    }


def create_step_define(db, payload: dict) -> StepDefine:
    inst = str(payload.get("step_inst") or "").strip()
    if not inst:
        raise ValueError("组件指令不能为空")
    if inst not in ATOM_MAP:
        raise ValueError(f"不支持的组件指令: {inst}（支持 {', '.join(ATOM_MAP)}）")
    obj = StepDefine(
        id=uuid.uuid4().hex[:64],
        group_type=str(payload.get("group_type") or "基础").strip() or "基础",
        step_inst=inst,
        step_label=str(payload.get("step_label") or inst).strip() or inst,
        step_icon=payload.get("step_icon") or None,
        step_desc=payload.get("step_desc") or None,
        step_cfg=payload.get("step_cfg") or {},
        step_seq=int(payload.get("step_seq") or 0),
        status=str(payload.get("status") or "effective") or "effective",
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_step_define(db, define_id: str, payload: dict) -> StepDefine:
    obj = db.get(StepDefine, define_id)
    if not obj:
        raise KeyError("组件定义不存在")
    if "step_inst" in payload and payload["step_inst"]:
        inst = str(payload["step_inst"]).strip()
        if inst not in ATOM_MAP:
            raise ValueError(f"不支持的组件指令: {inst}")
        obj.step_inst = inst
    for field in ("group_type", "step_label", "step_icon", "step_desc", "step_cfg", "step_seq", "status"):
        if field in payload:
            setattr(obj, field, payload[field])
    db.commit()
    db.refresh(obj)
    return obj


def delete_step_define(db, define_id: str) -> None:
    obj = db.get(StepDefine, define_id)
    if not obj:
        raise KeyError("组件定义不存在")
    # 引用检查：已发布编排若引用了该指令则拒绝
    used = (
        db.query(TapeStep)
        .filter(TapeStep.step_inst == obj.step_inst)
        .first()
    )
    if used:
        raise ValueError("该组件已被编排引用，无法删除")
    db.delete(obj)
    db.commit()