"use client";

/**
 * 语义层 · 文档库（2026-10-21）
 *
 * 复用知识库详情页文档面板（KbDocsPane），锁定「电信语义层」知识库的
 * 「文档库」根目录：左侧目录树只显示文档库分支，右侧文档列表只展示
 * 该目录下的数据。
 *
 * 绑定知识库 id 由参数管理表（modo_dim，SEMANTIC_LAYER 组）读取；
 * 参数缺失时自动按「电信语义层」库名 + 「文档库」根目录名发现。
 *
 * 动态选项卡（2026-10-21）：套用知识库页面 ModoTabs 模式——首 tab
 * 「文档库」= 文档面板；点击文档文件在动态选项卡中打开详情，不再无响应。
 */
import KbDocsPane from "@/components/kb/KbDocsPane";
import { useSemanticBinding } from "../_lib/use-semantic-binding";
import SemanticTabsFrame from "../_components/semantic-tabs-frame";

export default function SemanticDocsPage() {
  const { kbId, docsFolderId, loading, error } = useSemanticBinding();

  return (
    <SemanticTabsFrame
      title="文档库"
      kbId={kbId}
      loading={loading}
      error={error}
      ready={!!docsFolderId}
    >
      {({ openDoc, refreshTick, onDocsChange, onDocRemoved }) => (
        <KbDocsPane
          kbId={kbId}
          lockedFolderId={docsFolderId}
          refreshTick={refreshTick}
          onOpenDoc={openDoc}
          onDocsChange={onDocsChange}
          onDocRemoved={onDocRemoved}
        />
      )}
    </SemanticTabsFrame>
  );
}
