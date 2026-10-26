"""双表环境路由（2026-10-20，替代双库方案）。

背景：原「dev/prod 双库」方案（DEV_DATABASE_URL 独立库）废弃——远程库只有
data_supply 一个，无法/不便再开第二个物理库。改为「同库双表」：
- prod 环境（正式链路：知识库上传/正式执行）→ 写正式表（kb_tape_run 等）
- dev 环境（编排测试执行：手工/设计器/定时）→ 写同名 _dev 后缀表（kb_tape_run_dev）

实现：engine 级 before_cursor_execute 事件——dev 环境下把编译后 SQL 中的
隔离表白名单（ISOLATED_TABLES）表名 + 列限定前缀替换为 _dev 版本。
这是 SQLAlchemy 多租户/分表的标准写法，覆盖全部执行路径
（ORM query、session.execute、flush 的 INSERT/UPDATE/DELETE、text() 原生 SQL）。

⚠️ 为什么不用 do_orm_execute / before_execute：
- before_execute 只在 DDL 路径（_execute_ddl）触发，普通 DML 不经它；
- do_orm_execute 只覆盖 Session.execute(select/insert...) 路径，
  ORM flush 的 INSERT/UPDATE/DELETE 走 connection.execute（persistence.py），
  事件拿不到 → 路由会漏掉大半写入。
before_cursor_execute 在编译完成后、cursor 执行前触发，SQL 字符串层全局拦截。

读取端（run 列表/详情/任务监控）按需合并正式表 + _dev 表查询。

隔离范围 = 编排测试执行会写入的全部业务表（执行记录 + wiki 产物 + 聊天
记录 + 文档/切片 + 质量 + LLM 用量）。MinIO 目录区分由 storage.build_remote
按 current_env() 前缀（dev/ vs prod/）完成，与此机制互补。
"""

from __future__ import annotations

import re
import threading

from sqlalchemy.engine import Engine

from api.db import ENV_DEV, current_env

# 编排测试执行隔离表白名单（_dev 物理表已建，见 deploy/create_dev_tables.sql）
ISOLATED_TABLES: frozenset[str] = frozenset(
    {
        # 执行记录
        "kb_tape_run",
        # wiki 产物
        "wiki_page",
        "wiki_folder",
        "wiki_link",
        "wiki_page_issue",
        "wiki_feedback",
        "wiki_operation_log",
        "wiki_page_folder",
        # 聊天产物
        "chat_message",
        "chat_session",
        "chat_attachment",
        # 文档/切片（源数据快照，保证测试可读正式快照）
        "kb_document",
        "doc_chunk",
        "doc_folder",
        # 质量评估
        "kb_quality_run",
        "kb_quality_report",
        # LLM 用量
        "kb_llm_usage",
    }
)


def dev_table_name(name: str) -> str:
    """正式表名 → _dev 表名（白名单外原样返回）。"""
    if name in ISOLATED_TABLES:
        return f"{name}_dev"
    return name


class _DevSqlRewriter:
    """编译后 SQL 表名改写器（词边界精确替换，防 `kb_tape_run` 误匹配
    `kb_tape_run_dev` / `kb_tape_run_log` 等更长标识符）。"""

    def __init__(self) -> None:
        # 词边界：表名只出现在标识符位置（前面非 [A-Za-z0-9_$`]，后面非
        # [A-Za-z0-9_$]）。反引号包裹（`kb_tape_run`）与裸表名都覆盖。
        names = sorted(ISOLATED_TABLES, key=len, reverse=True)
        self._pattern = re.compile(
            r"(?<![A-Za-z0-9_$`])(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9_$])"
        )

    def rewrite_sql(self, sql: str) -> str:
        if not sql or not ISOLATED_TABLES:
            return sql
        return self._pattern.sub(lambda m: m.group(1) + "_dev", sql)


_rewriter = _DevSqlRewriter()
_installed = False
_lock = threading.Lock()


def install_dev_table_router(engine: Engine) -> None:
    """在框架引擎上挂 before_cursor_execute：dev 环境自动把隔离表路由到 _dev 表。

    幂等：同一进程只挂一次（多引擎共享同一 Session 类级事件会被重复触发，
    但 SQL 改写按 env 判空、重复执行无害；这里用全局锁防重复注册）。
    prod 环境零开销（事件首行判 env 直接 return）。
    """
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True

    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def _route_dev(conn, cursor, statement, parameters, context, executemany):
        if current_env() != ENV_DEV:
            return statement, parameters
        try:
            new_sql = _rewriter.rewrite_sql(statement)
        except Exception:  # noqa: BLE001 — 改写失败回退正式表，不阻断执行
            return statement, parameters
        if new_sql is not statement:
            return new_sql, parameters
        return statement, parameters