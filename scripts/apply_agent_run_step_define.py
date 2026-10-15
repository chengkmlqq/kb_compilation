"""把编排组件「Agent执行」（agent_run）的定义 upsert 进 kb_step_define（幂等可重放）。

背景（2026-10-15，commit b1b9cc3）：编排引擎新增 KbAgentRunStep 原子（agent_run，
投递 agent 队列由 kb-celery-agent-worker 执行 openai-agents 循环），seed_framework_db.py
的 ORCH_STEP_DEFINES 已同步（组 Agent，seq 17）。本脚本用于把该组件定义装进线上库
（设计器左侧「组件」面板可选），不动其它任何种子数据（菜单/角色/编排模板等）。

用法：docker cp scripts/apply_agent_run_step_define.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/apply_agent_run_step_define.py
"""
import uuid

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import StepDefine

# 与 scripts/seed_framework_db.py ORCH_STEP_DEFINES 中 agent_run 定义同步
AGENT_RUN = (
    "agent_run", "Agent执行", "Agent", "RobotOutlined",
    "把一次 agent 循环投递到 agent-worker 执行（支持配置提示词/系统指令/技能/MCP/模型）："
    "agent 自主规划并调用技能与 MCP 工具完成任务，输出写入运行变量供下游节点使用",
    [{"name": "prompt", "label": "任务提示词(支持{{}}模板)", "type": "textarea", "required": True,
      "placeholder": "请查阅知识库并总结以下内容：{{doc_summary}}", "props": {"rows": 4}},
     {"name": "instructions", "label": "系统指令(可选)", "type": "textarea", "required": False,
      "placeholder": "你是知识库问答助手，只输出结论，不要复述问题", "props": {"rows": 3}},
     {"name": "agent_name", "label": "Agent名称", "type": "input", "required": False,
      "placeholder": "kb-agent-worker（默认）"},
     {"name": "skill", "label": "技能名(可选)", "type": "input", "required": False,
      "placeholder": "留空=不挂技能；技能按知识库创建者权限解析"},
     {"name": "kb_id", "label": "知识库ID(技能/MCP权限基准)", "type": "input", "required": False,
      "placeholder": "{{kb_id}}"},
     {"name": "model", "label": "模型(留空=agent默认)", "type": "input", "required": False,
      "placeholder": "deepseek-v4-pro"},
     {"name": "output_var", "label": "输出变量名", "type": "input", "required": False,
      "placeholder": "agent_out（默认）"},
     {"name": "timeout_sec", "label": "等待超时(秒)", "type": "input", "required": False,
      "placeholder": "1800"},
     {"name": "skip_empty", "label": "空Prompt时跳过(不失败)", "type": "boolean", "required": False}],
    17,
)


def main() -> None:
    inst, label, group, icon, desc, cfg, seq = AGENT_RUN
    db = get_sessionmaker()()
    try:
        existing = db.execute(
            select(StepDefine).where(StepDefine.step_inst == inst)
        ).scalars().first()
        if existing:
            existing.step_label = label
            existing.group_type = group
            existing.step_icon = icon
            existing.step_desc = desc
            existing.step_cfg = cfg
            existing.step_seq = seq
            existing.status = "effective"
            print(f"[update] step_define {inst} 已存在，更新为最新定义 (id={existing.id})")
        else:
            db.add(
                StepDefine(
                    id=uuid.uuid4().hex,
                    group_type=group,
                    step_inst=inst,
                    step_label=label,
                    step_icon=icon,
                    step_desc=desc,
                    step_cfg=cfg,
                    step_seq=seq,
                    status="effective",
                )
            )
            print(f"[insert] step_define {inst} 插入成功")
        db.commit()
        total = db.execute(select(StepDefine)).scalars().all()
        print(f"kb_step_define 现有 {len(total)} 个组件定义")
    finally:
        db.close()


if __name__ == "__main__":
    main()