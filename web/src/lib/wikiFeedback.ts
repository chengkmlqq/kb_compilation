/**
 * Wiki 反馈共享常量（对齐后端 api/models/knowledge.py 的 WIKI_FEEDBACK_* 白名单）。
 * 供维护中心 /wiki-maintenance、wiki 浏览/阅读页反馈弹窗、高频词标注共用，避免文案漂移。
 */

/** 快捷标注预设白名单：键 -> 中文文案（对齐 WIKI_FEEDBACK_PRESETS） */
export const PRESET_LABELS: Record<string, string> = {
  content_error: "内容有误",
  outdated: "信息过时",
  broken_link: "链接失效",
  format_issue: "格式问题",
  incomplete: "信息不完整",
  kw_meaningful: "有业务含义",
  kw_noise: "无业务含义（噪音）",
  kw_mis_extracted: "提取不准确",
};

/** 预设下拉选项（供 antd Select 使用） */
export const PRESET_OPTIONS = Object.entries(PRESET_LABELS).map(([value, label]) => ({ value, label }));

/** 反馈类型选项（对齐 WIKI_FEEDBACK_TYPES=comment/question，另兼容 helpful/issue） */
export const TYPE_OPTIONS = [
  { label: "评论", value: "comment" },
  { label: "问题", value: "question" },
  { label: "有帮助", value: "helpful" },
  { label: "问题上报", value: "issue" },
];

/** 反馈类型 -> 中文标签（未知类型原样返回） */
export function feedbackTypeLabel(t: string): string {
  return TYPE_OPTIONS.find((o) => o.value === t)?.label ?? t;
}

/** 预设键 -> 中文标签（未知键原样返回） */
export function feedbackPresetLabel(p: string): string {
  return PRESET_LABELS[p] ?? p;
}
