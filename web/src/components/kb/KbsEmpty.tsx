/**
 * 知识库空态（对齐 arco Empty 原生规格：48px 插画 + 「暂无数据」14px gray-5）
 *
 * 知识库管理页与知识库详情（文档列表 / 表格空行）共用，避免一处用 DSB 空态、
 * 另一处回落到 antd Empty 的观感差异。
 *
 * variant：
 *  - fill   撑满容器并在容器中部居中（页面级空态，对齐知识库管理页）
 *  - inline 随内容高度自然排布（表格占位行等不具备固定高度的容器）
 */
import { EmptyIllustration } from "./KbsIcons";

export interface KbsEmptyProps {
  description?: string;
  variant?: "fill" | "inline";
}

export default function KbsEmpty({
  description = "暂无数据",
  variant = "fill",
}: KbsEmptyProps) {
  return (
    <div className={`kb-kbs-empty${variant === "inline" ? " kb-kbs-empty--inline" : ""}`}>
      <div className="kb-kbs-empty-wrapper">
        <div className="kb-kbs-empty-image">
          <EmptyIllustration />
        </div>
        <div className="kb-kbs-empty-description">{description}</div>
      </div>
    </div>
  );
}
