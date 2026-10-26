"use client";

/**
 * 语义层 · 元数据（2026-10-21）
 *
 * 复用知识库详情页文档面板（KbDocsPane），锁定「电信语义层」知识库的
 * 「元数据」根目录：左侧目录树只显示元数据分支（模型/程序/调度/数据源/
 * 质量规则/血缘/指标/指标维度/模型授权），右侧文档列表只展示该目录下的数据。
 *
 * 绑定知识库 id 由参数管理表（modo_dim，SEMANTIC_LAYER 组）读取；
 * 参数缺失时自动按「电信语义层」库名 + 「元数据」根目录名发现。
 *
 * 动态选项卡（2026-10-21）：套用知识库页面 ModoTabs 模式——首 tab
 * 「元数据」= 文档面板；点击文档文件在动态选项卡中打开详情，不再无响应。
 */
import KbDocsPane from "@/components/kb/KbDocsPane";
import { useSemanticBinding } from "../_lib/use-semantic-binding";
import SemanticTabsFrame from "../_components/semantic-tabs-frame";

export default function SemanticMetadataPage() {
  const { kbId, metadataFolderId, loading, error } = useSemanticBinding();

  return (
    <SemanticTabsFrame
      title="元数据"
      kbId={kbId}
      loading={loading}
      error={error}
      ready={!!metadataFolderId}
    >
      {({ openDoc, refreshTick, onDocsChange, onDocRemoved }) => (
        <KbDocsPane
          kbId={kbId}
          lockedFolderId={metadataFolderId}
          refreshTick={refreshTick}
          onOpenDoc={openDoc}
          onDocsChange={onDocsChange}
          onDocRemoved={onDocRemoved}
        />
      )}
    </SemanticTabsFrame>
  );
}
