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
    """'script' 任意 Python 执行（注入 context/bindings/config）。"""

    def handle(self, context: TapeRuntimeContext) -> StepResponse:
        code = self.config.get("code")
        if not code:
            return StepResponse.fail("Missing code in configuration")
        try:
            local_vars = {"context": context, "bindings": context.bindings, "config": self.config}
            exec(str(code), {}, local_vars)  # noqa: S102  (编排脚本 = 平台管理员授权能力)
            return StepResponse.success("Script executed successfully")
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