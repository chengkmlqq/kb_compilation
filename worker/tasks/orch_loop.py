"""编排 loop 节点的异步迭代展开（2026-10-09 回归修复）。

实测 bug：编排异步执行链路里 loop 组件只被当普通节点跑一次，
循环体既不迭代、item_var 也不注入（`KeyError: 'it'`），
而**同步引擎** `OrchestrationEngine.execute` 是有 loop 展开逻辑的
（重置 body 步骤 executed 标记后逐轮重跑后继子图）——
两条链路行为不一致，用户在 UI 上建的 loop 编排拿不到结果。

本模块补齐异步侧的展开：
- 迭代状态借 `kb_tape_run.bindings["__loop_state__"]` 承载（不新增列，
  避免 schema 漂移——这是本项目踩过 4 次的坑）；
- 每轮循环体节点的执行记录带 `round` 字段，同一 body 节点跑 N 轮
  不会被「已执行」判定吞掉；
- 空集合时与同步引擎一致：循环体退化为执行一轮。

术语：body = loop 节点下游可达步骤集合（连线表达的循环体）。
"""

from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger("kb.orch.loop")

# 迭代状态在 kb_tape_run.bindings 内的键（下划线开头 = 内部状态，不外泄前端）
LOOP_STATE_KEY = "__loop_state__"


def done_pairs(results: Iterable[dict]) -> set:
    """已完成的 (step_id, round) 集合——loop 迭代同一 body 要跑 N 轮，故带轮次。"""
    return {(r["step_id"], int(r.get("round") or 0)) for r in results}


def collect_body_ids(steps: dict, loop_sid: str) -> list:
    """loop 的循环体 = 其下游可达步骤集合（对齐同步引擎的 body 子图语义）。"""
    body: list = []
    stack = [n for n in steps[loop_sid]["next"] if n in steps]
    while stack:
        n = stack.pop(0)
        if n == loop_sid or n in body:
            continue
        body.append(n)
        stack.extend(
            x for x in steps[n]["next"]
            if x in steps and x not in body and x != loop_sid
        )
    return body


def init_state(raw: dict | None) -> dict:
    """从 run.bindings 里取出 loop 迭代状态（不存在则建空壳）。"""
    return dict((raw or {}).get(LOOP_STATE_KEY) or {})


def body_of(loop_state: dict) -> dict:
    """循环体步骤 → 所属 loop 步骤 id 的映射（供就绪/终态判定按轮次追踪）。"""
    mapping: dict = {}
    for loop_sid, st in (loop_state.get("loops") or {}).items():
        for b in st.get("body_ids") or []:
            mapping[b] = loop_sid
    return mapping


def round_of(step_id: str, loop_state: dict) -> int:
    """某步骤当前应处于的轮次：循环体步骤跟随所属 loop 的当前轮，其余恒 0。"""
    loop_sid = body_of(loop_state).get(step_id)
    if loop_sid is None:
        return 0
    return int((loop_state["loops"][loop_sid] or {}).get("current_round") or 0)


def expected_rounds(steps: dict, loop_state: dict) -> dict:
    """每个步骤应完成的所有轮次集合（终态判定用）。"""
    exp: dict = {}
    loops = loop_state.get("loops") or {}
    for loop_sid, st in loops.items():
        items = st.get("items")
        rounds = set(range(len(items))) if isinstance(items, list) else {0}
        for b in st.get("body_ids") or []:
            exp[b] = rounds
    for sid in steps:
        exp.setdefault(sid, {0})
    return exp


def dispatch_next_round(run_id: str, steps: dict, results: list, loop_state: dict,
                        bindings: dict, step_queue_fn, send_task) -> bool:
    """对已完成的 loop 节点按 collection 逐轮投循环体。

    返回 True = 本函数本轮已投递（调用方应停在这里等节点回调再 advance）。
    """
    done = done_pairs(results)
    loops = loop_state.setdefault("loops", {})

    for loop_sid, s in steps.items():
        if s["inst"] != "loop" or (loop_sid, 0) not in done:
            continue  # 不是 loop / loop 自身还没跑完
        st = loops.get(loop_sid)
        if st is None:
            # 首次展开：从 loop 执行结果取 collection
            lr = next((r for r in results
                       if r["step_id"] == loop_sid and r.get("status") == "success"), None)
            collection = (lr or {}).get("body")
            if not isinstance(collection, (list, tuple)):
                continue  # loop 自身失败/返回非列表 → 交给常规失败传播处理
            st = {
                "items": list(collection),
                "idx": 0,
                "body_ids": collect_body_ids(steps, loop_sid),
                "current_round": 0,
                "bindings": {},
            }
            loops[loop_sid] = st
            logger.info(
                "编排 loop 展开 run=%s 节点=%s 迭代 %d 轮 循环体 %d 步: %s",
                run_id, loop_sid, len(st["items"]), len(st["body_ids"]), st["body_ids"])
        items, body_ids = st["items"], st["body_ids"]
        next_round = int(st["idx"])
        if next_round >= len(items):
            continue  # 该 loop 已全部展开
        # 2026-10-09 实测 bug：轮次推进必须等「本轮 body 全部完成」。
        # 原实现每轮无条件开下一轮并只投起点，导致同轮后续节点（n5/n6）来不及
        # 跑就被下一轮吞掉——实测 3 轮循环体里 n5/n6 只执行了第 2 轮，
        # run 卡在 running（终态判定缺 (n5,0)/(n5,1) 等）。
        cur_round = next_round - 1
        if cur_round >= 0:
            unfinished = [b for b in body_ids if (b, cur_round) not in done]
            if unfinished:
                continue  # 本轮尚未跑完 → 不开新轮，交常规就绪逻辑推进本轮
        round_no = next_round
        item = items[round_no]
        round_bindings = dict(bindings)
        item_var = (s.get("config") or {}).get("item_var")
        if item_var:
            round_bindings[item_var] = item
        body_set = set(body_ids)
        # 只投「本轮 pre 已满足」的起点节点；同轮后续节点由 _advance_impl
        # 的就绪判定（按 round 追踪）继续推进。
        pending = [
            b for b in body_ids
            if (b, round_no) not in done
            and all(
                (p, round_no if p in body_set else 0) in done
                for p in steps[b]["pre"]
            )
        ]
        if not pending:
            # 本轮无可投起点（起点 pre 未满足/已失败）→ 交回常规就绪逻辑。
            # 注意：此分支不动 st（idx 不推进），否则会跳轮导致漏迭代。
            continue
        st["idx"] = round_no + 1
        st["current_round"] = round_no
        st["bindings"] = round_bindings
        for b in pending:
            send_task(run_id, b, round_bindings, round_no, step_queue_fn(steps, b))
        logger.info("编排 loop 迭代 run=%s 节点=%s 第 %d/%d 轮 投 %d 步 item=%r",
                    run_id, loop_sid, round_no + 1, len(items), len(pending), item)
        return True
    return False