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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
