"use client";

/**
 * 语义层 · 知识图谱（2026-10-21）
 *
 * 照抄知识库 Wiki 关系图页面（WikiGraphView）：按 slug/关键词浏览 wiki 页的
 * 关系图（ego 聚焦/全库概览），数据用「电信语义层」知识库的 wiki 关系数据。
 *
 * 绑定知识库 id 由参数管理表（modo_dim，SEMANTIC_LAYER 组）读取；
 * 参数缺失时自动按「电信语义层」库名发现。
 *
 * 动态选项卡（2026-10-21）：套用知识库页面 ModoTabs 模式——首 tab
 * 「知识图谱」= 全库关系图概览；页面标题由选项卡承担（对齐知识库页面
 * 外壳，白底满屏 + 可关闭详情页签）。
 */
import WikiGraphView from "@/components/WikiGraphView";
import { useSemanticBinding } from "../_lib/use-semantic-binding";
import SemanticTabsFrame from "../_components/semantic-tabs-frame";

export default function SemanticGraphPage() {
  const { kbId, loading, error } = useSemanticBinding();

  return (
    <SemanticTabsFrame title="知识图谱" kbId={kbId} loading={loading} error={error}>
      {() => <WikiGraphView kbId={kbId} />}
    </SemanticTabsFrame>
  );
}