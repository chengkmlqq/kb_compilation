/**
 * 编排功能标签（tape_type）：summary 摘要 / kb_build 知识构建 / general 其他。
 *
 * 2026-10-15：编排管理页按功能内容展示标签；「知识库构建配置」的构建引擎
 * 下拉按标签分组，选择编排时能明确区分摘要编排与知识构建编排。
 */
import type { BuildEngineItem } from "./api";

export type TapeFunc = "summary" | "kb_build" | "quality" | "domain_resolve" | "general";

export const TAPE_FUNC_META: Record<TapeFunc, { label: string; color: string }> = {
  summary: { label: "摘要", color: "blue" },
  kb_build: { label: "知识构建", color: "green" },
  quality: { label: "质量评估", color: "purple" },
  domain_resolve: { label: "领域识别", color: "geekblue" },
  general: { label: "其他", color: "default" },
};

export function tapeFuncMeta(tapeType?: string | null): { label: string; color: string } {
  const t = (tapeType || "general") as TapeFunc;
  return TAPE_FUNC_META[t] || { label: tapeType || "其他", color: "default" };
}

/** 构建引擎下拉分组：知识构建 / 摘要 / 其他（antd Select options 分组形态） */
export function groupBuildEngineOptions(items: BuildEngineItem[]) {
  const order: TapeFunc[] = ["kb_build", "summary", "general"];
  const groups = order.map((f) => ({
    label: TAPE_FUNC_META[f].label,
    options: items
      .filter((t) => (t.tape_type || "general") === f)
      .map((t) => ({
        value: t.id,
        label: `${t.tape_label || t.tape_name}${t.tape_descr ? `（${t.tape_descr}）` : ""}`,
      })),
  }));
  return groups.filter((g) => g.options.length > 0);
}
