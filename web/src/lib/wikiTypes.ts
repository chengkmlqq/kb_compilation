/**
 * Wiki 页面类型（page_type）统一映射 —— 唯一真源（2026-10-18）
 *
 * 文案对齐 WeKnora frontend/src/i18n/locales/zh-CN.ts：
 *   filterOriginalSentence / filterRuleOntology / filterBusinessOntology /
 *   filterFrequentKeyword / filterTopicCluster / filterKnowledgeGraphSummary /
 *   filterCrossDocumentInsight / filterEntity / filterConcept /
 *   filterSynthesis / filterComparison / filterSummary
 *
 * 之前 WikiBrowseView / WikiGraphView / WikiManagePanel / 独立阅读页各有一份
 * 映射且多数缺 4 个类型，界面上直接显示 business_ontology 这类英文编码。
 */

export const WIKI_PAGE_TYPE_LABELS: Record<string, string> = {
  summary: "摘要",
  entity: "实体",
  concept: "概念",
  synthesis: "综合",
  comparison: "对比",
  business_ontology: "业务本体",
  rule_ontology: "规则本体",
  original_sentence: "原文长句",
  frequent_keyword: "高频关键词",
  topic_cluster: "主题聚类簇",
  knowledge_graph_summary: "知识图谱摘要",
  cross_document_insight: "跨文档洞察",
  index: "索引",
  keyword: "关键词",
};

/** antd Tag 颜色名（列表/表格用） */
export const WIKI_PAGE_TYPE_TAG_COLORS: Record<string, string> = {
  entity: "purple",
  concept: "blue",
  synthesis: "cyan",
  comparison: "magenta",
  summary: "gold",
  business_ontology: "purple",
  rule_ontology: "purple",
  original_sentence: "default",
  frequent_keyword: "cyan",
  topic_cluster: "geekblue",
  knowledge_graph_summary: "green",
  cross_document_insight: "orange",
  index: "default",
  keyword: "default",
};

/** Canvas 图谱用 hex（对齐 WeKnora PAGE_ICON_COLOR） */
export const WIKI_PAGE_TYPE_HEX: Record<string, string> = {
  entity: "#8b5cf6",
  concept: "#3b82f6",
  synthesis: "#06b6d4",
  comparison: "#d946ef",
  summary: "#f59e0b",
  business_ontology: "#7c3aed",
  rule_ontology: "#a855f7",
  original_sentence: "#64748b",
  frequent_keyword: "#14b8a6",
  topic_cluster: "#0ea5e9",
  knowledge_graph_summary: "#10b981",
  cross_document_insight: "#f97316",
  index: "#94a3b8",
  keyword: "#22c55e",
};

/** 图例/筛选顺序（与 WeKnora 侧栏顺序一致） */
export const WIKI_PAGE_TYPE_ORDER: string[] = [
  "business_ontology",
  "rule_ontology",
  "original_sentence",
  "frequent_keyword",
  "summary",
  "entity",
  "concept",
  "topic_cluster",
  "synthesis",
  "comparison",
  "knowledge_graph_summary",
  "cross_document_insight",
];

/** 「原始文档 Wiki」/「聚类知识 Wiki」两个 Tab 的类型分组（对齐 WeKnora） */
export const ORIGINAL_DOC_TYPES = [
  "summary",
  "entity",
  "concept",
  "original_sentence",
  "frequent_keyword",
  "business_ontology",
  "rule_ontology",
];
export const CLUSTERED_KNOWLEDGE_TYPES = [
  "synthesis",
  "comparison",
  "topic_cluster",
  "knowledge_graph_summary",
  "cross_document_insight",
];

/** page_type → 中文标签（未知类型原样返回，避免出现空白） */
export function wikiTypeLabel(t?: string | null): string {
  if (!t) return "—";
  return WIKI_PAGE_TYPE_LABELS[t] || t;
}
