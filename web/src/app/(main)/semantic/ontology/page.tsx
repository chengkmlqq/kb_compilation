"use client";

/**
 * 语义层 · 知识本体（2026-10-21）
 *
 * 照抄知识库 Wiki 页面（KbWikiPane → WikiBrowseView）：左侧 wiki 目录树 +
 * 右侧页面浏览/全文检索，数据取「电信语义层」知识库的 wiki。
 *
 * 绑定知识库 id 由参数管理表（modo_dim，SEMANTIC_LAYER 组）读取；
 * 参数缺失时自动按「电信语义层」库名发现。
 *
 * 动态选项卡（2026-10-21）：套用知识库页面 ModoTabs 模式——首 tab
 * 「知识本体」= wiki 浏览；wiki 页内「在图谱中查看」打开 wiki 关系图
 * 选项卡、「来源文档」打开文档详情选项卡。
 */
import KbWikiPane from "@/components/kb/KbWikiPane";
import { useSemanticBinding } from "../_lib/use-semantic-binding";
import SemanticTabsFrame from "../_components/semantic-tabs-frame";

export default function SemanticOntologyPage() {
  const { kbId, loading, error } = useSemanticBinding();

  return (
    <SemanticTabsFrame title="知识本体" kbId={kbId} loading={loading} error={error}>
      {({ openDocById, openGraph }) => (
        <KbWikiPane kbId={kbId} onOpenGraph={openGraph} onOpenDoc={openDocById} />
      )}
    </SemanticTabsFrame>
  );
}