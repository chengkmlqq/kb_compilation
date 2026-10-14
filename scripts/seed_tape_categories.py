"""初始化编排管理分类树（两级：企业领域 → 文档类型）。

数据源：cates.md 企业领域与文档类型清单（2026-10-14）。
写入 kb_tape_category：19+1 个领域目录（一级，cat_type=tape）+ 各领域下
文档类型（二级，parent_id=领域 category 名）。幂等：已存在的分类跳过
（领域不动、文档类型补挂 parent_id）。

保留既有「通用」「摘要」分类（8 个编排挂在通用下、摘要模板在用），
不删不重置。医疗健康（清单第 20 项，标注可选）一并写入。

用法：docker exec kb-api-server /srv/kb/.venv/bin/python /srv/kb/scripts/seed_tape_categories.py
（或本机 .venv/bin/python scripts/seed_tape_categories.py 连 dev 库）
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from api.db import get_sessionmaker
from api.models.orchestration import TapeCategory

# (领域, [文档类型...])
CATEGORY_TREE: list[tuple[str, list[str]]] = [
    ("通用办公与综合管理", ["工作报告", "工作计划", "会议纪要", "汇报材料", "通知公告", "方案计划", "总结复盘", "调研分析", "请示批复", "公文函件", "经验案例", "汇报演示"]),
    ("制度与企业治理", ["管理制度", "管理规定", "操作规程", "管理流程", "管理办法", "职责规范", "制度解释", "制度修订", "内控规范", "风险合规", "授权管理", "规章汇编"]),
    ("采购与供应链", ["采购制度", "采购流程", "采购需求", "采购方案", "采购文件", "供应商管理", "合同文件", "采购评审", "定价与报价", "采购订单", "履约管理", "采购结算", "采购审计", "采购统计", "供应链管理"]),
    ("财务与会计", ["财务制度", "会计政策", "财务报表", "财务分析", "预算管理", "费用管理", "资金管理", "税务管理", "成本管理", "资产管理", "应收应付", "审计材料", "经营指标"]),
    ("人力资源与组织管理", ["人事制度", "组织架构", "岗位管理", "招聘管理", "员工入转调离", "培训管理", "绩效管理", "薪酬福利", "人才发展", "劳动关系", "考勤休假", "人力分析"]),
    ("法务与合同管理", ["合同协议", "合同模板", "法律法规", "法律意见", "合同审查", "争议纠纷", "知识产权", "合规审查", "授权委托", "法律案例"]),
    ("销售与市场营销", ["销售制度", "销售计划", "客户资料", "商机管理", "销售合同", "报价方案", "市场调研", "营销方案", "产品宣传", "投标材料", "销售复盘", "客户服务"]),
    ("客户服务与运营", ["服务制度", "操作手册", "常见问题", "服务工单", "投诉建议", "服务流程", "运营报告", "运营方案", "服务指标", "用户反馈", "应急处理"]),
    ("产品管理", ["产品需求", "产品规划", "产品设计", "产品规格", "需求评审", "版本管理", "用户研究", "竞品分析", "产品运营", "产品验收"]),
    ("技术研发与软件工程", ["技术规范", "系统架构", "详细设计", "API 文档", "数据模型", "研发需求", "测试文档", "部署运维", "故障排查", "研发复盘", "代码规范", "技术方案", "系统操作", "研发知识"]),
    ("IT 运维与信息安全", ["运维制度", "系统资产", "运维操作", "变更管理", "事件管理", "监控告警", "安全制度", "安全评估", "权限管理", "应急预案", "数据安全", "业务连续性"]),
    ("项目管理", ["项目立项", "项目计划", "项目方案", "需求管理", "项目会议", "进度报告", "风险管理", "质量管理", "验收交付", "项目复盘", "项目成本"]),
    ("生产制造与质量管理", ["生产制度", "工艺文件", "生产计划", "设备管理", "质量标准", "检验记录", "质量问题", "质量体系", "安全生产", "供应商质量", "生产分析"]),
    ("工程建设与设计", ["工程规范", "设计文件", "工程图纸", "工程合同", "施工组织", "施工记录", "质量验收", "安全管理", "设备材料", "工程变更", "竣工交付"]),
    ("物流与仓储", ["物流制度", "仓储制度", "入库出库", "库存管理", "运输管理", "物流合同", "物流成本", "供应保障", "物流异常"]),
    ("战略与经营管理", ["战略规划", "经营计划", "经营分析", "绩效考核", "组织变革", "投资管理", "决策材料", "经营指标", "风险管理"]),
    ("研究与知识创新", ["研究报告", "论文文献", "专利成果", "实验记录", "创新项目", "技术标准", "知识产权", "案例知识", "研究数据"]),
    ("培训与教育", ["培训教材", "课程设计", "操作教程", "考核资料", "培训记录", "知识手册", "认证标准"]),
    ("行业监管与公共政策", ["法规政策", "行业标准", "监管要求", "申报材料", "检查报告", "政策解读", "资质认证"]),
    ("医疗健康", ["医疗制度", "临床指南", "操作规范", "医学研究", "药品资料", "质量安全"]),
]


def _next_sort(db) -> int:
    max_row = db.execute(
        select(TapeCategory).order_by(TapeCategory.sort_num.desc()).limit(1)
    ).scalars().first()
    return (max_row.sort_num + 1) if max_row else 0


def main() -> None:
    db = get_sessionmaker()()
    created = updated = 0
    try:
        # 一级领域
        for i, (domain, _docs) in enumerate(CATEGORY_TREE):
            row = db.execute(
                select(TapeCategory).where(TapeCategory.category == domain)
            ).scalars().first()
            if row is None:
                db.add(
                    TapeCategory(
                        id=uuid.uuid4().hex,
                        category=domain,
                        label=domain,
                        cat_type="tape",
                        sort_num=1000 + i,  # 领域排通用/摘要之后（通用 1、摘要 2）
                        state="1",
                    )
                )
                created += 1
            else:
                if row.cat_type not in ("tape", "both"):
                    row.cat_type = "both"
                    updated += 1
        db.commit()

        # 二级文档类型（挂在领域下）；清单内跨领域同名文档类型只挂首个领域
        planned: set[str] = set()
        skipped_dup: list[str] = []
        for i, (domain, docs) in enumerate(CATEGORY_TREE):
            for j, doc in enumerate(docs):
                if doc in planned:
                    skipped_dup.append(f"{doc}(@{domain})")
                    continue  # 已在清单前段计划插入（跨领域重名，如 经营指标/风险管理/知识产权）
                row = db.execute(
                    select(TapeCategory).where(TapeCategory.category == doc)
                ).scalars().first()
                if row is None:
                    planned.add(doc)
                    db.add(
                        TapeCategory(
                            id=uuid.uuid4().hex,
                            category=doc,
                            label=doc,
                            parent_id=domain,
                            cat_type="tape",
                            sort_num=2000 + i * 100 + j,
                            state="1",
                        )
                    )
                    created += 1
                elif not row.parent_id:
                    # 已存在但未挂父级（如用户手工建的）→ 补挂到领域
                    row.parent_id = domain
                    updated += 1
                else:
                    planned.add(doc)
        db.commit()
        dup_note = f"；跳过跨领域重名 {len(skipped_dup)} 项：{', '.join(skipped_dup)}" if skipped_dup else ""
        print(f"[seed_tape_categories] OK: 新建 {created} / 修正 {updated} 条{dup_note}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
