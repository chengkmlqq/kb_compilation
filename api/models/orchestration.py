"""编排（Orchestration）数据模型——迁移自 data-synth algorithm/tapes。

三张表：
- kb_step_define：组件（步骤）定义，step_cfg 为配置表单 JSON schema，
  前端动态表单按其渲染参数（对齐 data-synth synth_tape_step_define）。
- kb_tape：编排主表，nodes/edges 为 React Flow 序列化 JSON
  （对齐 data-synth synth_tape + 设计器持久化），publish 时同步派生 kb_tape_step。
- kb_tape_step：编排步骤子表（执行引擎读取，由设计器保存时覆盖式同步）。

保留组件指令（ATOM_MAP）：def（变量定义）/ script（脚本执行）/ print（日志输出）/
if（条件分支）/ loop（循环，kb 新增，data-synth 无）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base


class TapeCategory(Base):
    """编排分类（2026-10-14 独立表存储，取代从 kb_tape/kb_tape_template
    的 category 字段实时聚合左侧目录树）。

    cat_type 区分归属：tape（编排管理）/ template（编排模板）/ both（通用）。
    排序 sort_num 决定左侧目录树顺序；计数（total/effective）仍由
    list_tape_categories / list_template_categories 动态聚合计算。
    """

    __tablename__ = "kb_tape_category"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(128))
    cat_type: Mapped[str] = mapped_column(String(16), default="both")  # tape | template | both
    sort_num: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default="1")  # 1=启用 0=停用
    create_user: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StepDefine(Base):
    """编排组件（步骤）定义。"""

    __tablename__ = "kb_step_define"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_type: Mapped[str] = mapped_column(String(64), default="基础")  # 分组：基础 / 流程控制
    step_inst: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # def/script/print/if/loop
    step_label: Mapped[str] = mapped_column(String(128), nullable=False)  # 组件名（如 脚本执行）
    step_icon: Mapped[str | None] = mapped_column(String(32))  # antd icon 名或 emoji
    step_desc: Mapped[str | None] = mapped_column(Text)
    # 配置表单 JSON schema：{"properties":{...},"required":[...]}，前端 dynamic-form 渲染
    step_cfg: Mapped[dict | None] = mapped_column(JSON)  # 配置表单 JSON schema
    step_seq: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="effective")  # effective | disabled
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Tape(Base):
    """编排主表（React Flow nodes/edges 序列化）。"""

    __tablename__ = "kb_tape"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tape_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tape_label: Mapped[str] = mapped_column(String(128), default="")
    tape_descr: Mapped[str | None] = mapped_column(String(2000))
    tape_type: Mapped[str] = mapped_column(String(32), default="general")
    category: Mapped[str] = mapped_column(String(64), default="通用", index=True)  # 编排分类（左树分组，2026-10-13）
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft | effective | offline
    nodes: Mapped[list | None] = mapped_column(JSON)  # React Flow nodes
    edges: Mapped[list | None] = mapped_column(JSON)  # React Flow edges
    exec_params: Mapped[dict | None] = mapped_column(JSON)  # 运行入参模板
    create_user: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TapeStep(Base):
    """编排步骤子表（执行引擎读取；设计器保存时覆盖式同步）。"""

    __tablename__ = "kb_tape_step"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # = React Flow node id
    tape_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    step_inst: Mapped[str] = mapped_column(String(64), nullable=False)
    step_label: Mapped[str] = mapped_column(String(128), default="")
    step_config: Mapped[dict | None] = mapped_column(JSON)
    step_seq: Mapped[int] = mapped_column(Integer, default=0)
    pre_step_ids: Mapped[list | None] = mapped_column(JSON)  # 前置步骤 id 数组
    next_step_ids: Mapped[list | None] = mapped_column(JSON)  # 后继步骤 id 数组
    # 节点目标队列（2026-10-09 编排异步化）：指定该节点投递到哪个 celery worker
    # 队列（default/agent/build/orch）。空 = 继承编排默认队列。不同节点可跑不同 worker。
    queue_name: Mapped[str | None] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TapeRun(Base):
    """编排执行记录（异步执行：API 立即返回 run_id，worker 后台逐步执行）。

    step_results 每步执行完追加一条：
      {step_id, step_inst, step_label, queue, status(success/failed/skipped),
       start_time, end_time, duration_ms, body, error}
    bindings 存最终运行上下文（可回溯中间结果，截断长值）。
    """

    __tablename__ = "kb_tape_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # run_id
    tape_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tape_name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="queued")  # queued|running|success|failed|cancelled
    inputs: Mapped[dict | None] = mapped_column(JSON)  # 运行入参
    step_results: Mapped[list | None] = mapped_column(JSON)  # 逐步日志（执行时追加）
    bindings: Mapped[dict | None] = mapped_column(JSON)  # 最终 bindings（截断长值）
    error: Mapped[str | None] = mapped_column(Text)
    create_user: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TapeTemplate(Base):
    """编排模板：可复用的编排骨架（nodes/edges 与 Tape 同结构）。

    存模板 = 从编排「存为模板」；应用模板 = 复制骨架生成一条新编排（draft），
    应用时重写节点 id，避免与目标编排的前置/后继引用串味。
    """

    __tablename__ = "kb_tape_template"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # 内部名（唯一）
    label: Mapped[str] = mapped_column(String(128), default="")  # 显示名
    descr: Mapped[str | None] = mapped_column(String(2000))  # 用途说明
    category: Mapped[str] = mapped_column(String(64), default="未分类", index=True)  # 分类
    nodes: Mapped[list | None] = mapped_column(JSON)  # React Flow nodes（与 Tape 一致）
    edges: Mapped[list | None] = mapped_column(JSON)  # React Flow edges
    exec_params: Mapped[dict | None] = mapped_column(JSON)  # 运行入参模板
    status: Mapped[str] = mapped_column(String(32), default="effective")  # effective | disabled
    is_builtin: Mapped[int] = mapped_column(Integer, default=0)  # 1=内置种子模板（禁止删除）
    apply_count: Mapped[int] = mapped_column(Integer, default=0)  # 被应用次数
    create_user: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
