'use client';

/**
 * 模板设计预览（只读）—— 与编排管理页的「设计」对齐（2026-10-14）。
 *
 * 在动态选项卡里用 ReactFlow 渲染模板骨架（节点/连线），复用设计器的 CustomNode
 * 与骨架归一化逻辑（_utils/flow），保证观感与设计器一致。
 * 只读约束：不可拖拽/连线/删除，节点操作菜单不渲染（CustomNode 无 onDelete 时隐藏）；
 * 点击节点在右侧抽屉查看该节点配置（JSON 只读）。
 */
import { useEffect, useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from '@xyflow/react';
import type { Edge } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { App, Button, Descriptions, Drawer, Empty, Spin, Tag, Tooltip, Typography } from 'antd';
import { ArrowLeftOutlined, FullscreenOutlined, ZoomInOutlined, ZoomOutOutlined } from '@ant-design/icons';

import { nodeTypes } from '@/app/(main)/orchestrations/[id]/designer/_components/custom-node';
import type { StepFlowNode } from '@/app/(main)/orchestrations/[id]/designer/_components/custom-node';
import { normalizeEdge, normalizeNode } from '@/app/(main)/orchestrations/[id]/designer/_utils/flow';
import type { RawFlowEdge, RawFlowNode } from '@/app/(main)/orchestrations/[id]/designer/_utils/flow';
import { apiGetTapeTemplate, TapeTemplateDetail } from '@/lib/api';

const { Text } = Typography;

interface TemplateDesignViewProps {
  templateId: string;
  /** 「返回」→ 由宿主页关闭当前选项卡 */
  onBack: () => void;
}

export function TemplateDesignView({ templateId, onBack }: TemplateDesignViewProps) {
  const { message } = App.useApp();
  const { zoomIn, zoomOut, fitView } = useReactFlow();

  const [nodes, setNodes, onNodesChange] = useNodesState<StepFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [tpl, setTpl] = useState<TapeTemplateDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedNode, setSelectedNode] = useState<StepFlowNode | null>(null);
  const [zoomLevel, setZoomLevel] = useState(100);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const res = await apiGetTapeTemplate(templateId);
        if (cancelled) return;
        if (res.success && res.data) {
          setTpl(res.data);
          setNodes(((res.data.nodes ?? []) as unknown as RawFlowNode[]).map(normalizeNode));
          setEdges(((res.data.edges ?? []) as unknown as RawFlowEdge[]).map(normalizeEdge));
        } else {
          message.error(res.message || '加载模板设计失败');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
          // 画布就绪后自适应视野（与设计器一致）
          requestAnimationFrame(() => {
            void fitView({ padding: 0.15, maxZoom: 1 });
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [templateId, message, setNodes, setEdges, fitView]);

  const configJson = useMemo(
    () => JSON.stringify(selectedNode?.data.config ?? {}, null, 2),
    [selectedNode],
  );

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: '#f9fbfd', overflow: 'hidden' }}>
      {/* ============ 顶栏：返回 + 模板信息 + 只读标记 ============ */}
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '0 16px',
          height: 48,
          borderBottom: '1px solid #eff4f9',
          background: '#fff',
          flexShrink: 0,
        }}
      >
        <Tooltip title="返回模板列表">
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack} style={{ color: '#4e5969' }} />
        </Tooltip>

        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0, flex: 1 }}>
          <p
            style={{
              margin: 0,
              fontSize: 14,
              fontWeight: 600,
              color: '#242e43',
              fontFamily: 'PingFang SC, sans-serif',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {tpl?.label || tpl?.name || '模板设计'}
          </p>
          {tpl && (
            <Tag color={tpl.status === 'effective' ? 'green' : 'default'} style={{ flexShrink: 0 }}>
              {tpl.status === 'effective' ? '启用' : '停用'}
            </Tag>
          )}
          {tpl?.category && <Tag style={{ flexShrink: 0 }}>{tpl.category}</Tag>}
          <Tag color="blue" style={{ flexShrink: 0 }}>
            只读预览
          </Tag>
        </div>

        <span style={{ fontSize: 12, color: '#79879c', flexShrink: 0 }}>
          {nodes.length} 节点 / {edges.length} 连线
        </span>
      </header>

      {/* ============ 画布 ============ */}
      <main style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {loading ? (
          <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Spin />
          </div>
        ) : nodes.length === 0 ? (
          <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Empty description="该模板暂无骨架（节点/连线）" />
          </div>
        ) : (
          <>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              nodesDraggable={false}
              nodesConnectable={false}
              deleteKeyCode={null}
              onNodeClick={(_, n) => setSelectedNode(n as StepFlowNode)}
              onPaneClick={() => setSelectedNode(null)}
              onViewportChange={(vp) => setZoomLevel(Math.round((vp.zoom || 1) * 100))}
              defaultEdgeOptions={{ type: 'default' }}
              proOptions={{ hideAttribution: true }}
              fitViewOptions={{ maxZoom: 1, padding: 0.2 }}
              minZoom={0.3}
              maxZoom={2}
            >
              <Background gap={18} size={1} color="#BED6F5" variant={BackgroundVariant.Dots} />
            </ReactFlow>

            {/* 左下角缩放控制条（与设计器一致） */}
            <div
              style={{
                position: 'absolute',
                bottom: 20,
                left: 20,
                background: '#fff',
                borderRadius: 8,
                boxShadow: '0 4px 10px rgba(36,46,67,0.1)',
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                padding: '4px 6px',
                zIndex: 10,
              }}
            >
              <Tooltip title="缩小">
                <Button type="text" size="small" icon={<ZoomOutOutlined />} onClick={() => void zoomOut({ duration: 200 })} />
              </Tooltip>
              <span style={{ fontSize: 12, fontWeight: 700, color: '#242e43', minWidth: 42, textAlign: 'center' }}>
                {zoomLevel}%
              </span>
              <Tooltip title="放大">
                <Button type="text" size="small" icon={<ZoomInOutlined />} onClick={() => void zoomIn({ duration: 200 })} />
              </Tooltip>
              <Tooltip title="适应视图">
                <Button
                  type="text"
                  size="small"
                  icon={<FullscreenOutlined />}
                  onClick={() => void fitView({ padding: 0.15, maxZoom: 1 })}
                />
              </Tooltip>
            </div>
          </>
        )}
      </main>

      {/* ============ 节点配置（只读） ============ */}
      <Drawer
        open={!!selectedNode}
        placement="right"
        width={380}
        onClose={() => setSelectedNode(null)}
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span
              style={{
                fontSize: 14,
                fontWeight: 700,
                color: '#242e43',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {selectedNode?.data.label || '节点配置'}
            </span>
            {selectedNode?.data.stepInst && (
              <Tag color="blue" style={{ flexShrink: 0 }}>
                {selectedNode.data.stepInst}
              </Tag>
            )}
          </div>
        }
        footer={
          <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center' }}>
            <Text type="secondary" style={{ fontSize: 12, marginRight: 12 }}>
              模板设计为只读预览
            </Text>
            <Button onClick={() => setSelectedNode(null)}>关闭</Button>
          </div>
        }
        styles={{ body: { paddingTop: 12 } }}
      >
        {selectedNode && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <Descriptions
              size="small"
              bordered
              column={1}
              items={[
                { key: 'label', label: '步骤名称', children: selectedNode.data.label || '-' },
                {
                  key: 'stepInst',
                  label: '步骤类型',
                  children: <Text type="secondary">{selectedNode.data.stepInst || '-'}</Text>,
                },
                { key: 'group', label: '分组', children: selectedNode.data.group || '-' },
                { key: 'id', label: '节点 ID', children: <Text type="secondary">{selectedNode.id}</Text> },
              ]}
            />
            <div>
              <Typography.Title level={5} style={{ fontSize: 14, marginBottom: 8 }}>
                步骤配置
              </Typography.Title>
              <pre className="kb-tpl-exec-params">{configJson}</pre>
            </div>
          </div>
        )}
      </Drawer>
    </div>
  );
}

export default TemplateDesignView;
