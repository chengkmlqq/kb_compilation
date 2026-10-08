'use client';

import React from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import { DesignerClient } from './_components/designer-client';

/**
 * 编排设计器页面壳 —— 从 data-synth 迁移。
 * ReactFlowProvider 包裹 DesignerClient，保证画布 Hook（useReactFlow 等）可用。
 * 数据全部走 @/lib/api 的 apiGetTape / apiListStepDefines / apiSaveTapeDesign 等，
 * 不使用 Server Actions。
 */
export default function TapeDesignerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = React.use(params);

  return (
    <div style={{ height: '100%', width: '100%', overflow: 'hidden', background: '#f9fbfd' }}>
      <ReactFlowProvider>
        <DesignerClient tapeId={id} />
      </ReactFlowProvider>
    </div>
  );
}
