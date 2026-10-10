'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  useReactFlow,
  addEdge,
} from '@xyflow/react';
import type { Edge, NodeChange, EdgeChange, Connection, OnSelectionChangeParams, Viewport } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { App, Button, Modal, Spin, Tag, Tooltip, Typography } from 'antd';
import {
  ArrowLeftOutlined,
  SaveOutlined,
  RocketOutlined,
  PlayCircleOutlined,
  DownloadOutlined,
  UploadOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
  FullscreenOutlined,
} from '@ant-design/icons';
import { useRouter } from 'next/navigation';

import {
  apiGetTape,
  apiListStepDefines,
  apiSaveTapeDesign,
  apiPublishTape,
  apiExecuteTape,
  apiGetTapeRun,
  apiExportTapeDraft,
  apiImportTapeDraft,
} from '@/lib/api';
import type { StepDefineItem, TapeDetail, TapeRunDetail } from '@/lib/api';

import { LeftSidebar } from './left-sidebar';
import { RightPanel } from './right-panel';
import { CustomNode, nodeTypes } from './custom-node';
import type { StepFlowNode, StepNodeData } from './custom-node';
import { TapeImportModal } from './tape-import-modal';
import { parseJsonRecord } from '../_utils/json';

const { Text } = Typography;

/** 编排设计器 —— 从 data-synth designer-client.tsx(1931行) 迁移的裁剪版。
 *
 * 对齐裁剪后的功能集：
 *  - ReactFlowProvider 上下文 + 节点拖拽/连线（onConnect）
 *  - 删除节点/删除边（节点菜单删除 + 键盘 Delete 原生删除）
 *  - 左栏组件拖入（onDrop 依据 dataTransfer 中的 StepDefineItem 生成节点）
 *  - 点节点打开右侧配参 Drawer（DynamicForm 渲染 step_cfg，改动实时写回 node.data.config）
 *  - 顶栏：返回列表 / 保存 / 保存并发布 / 执行（执行结果 Modal 逐步展示）
 *  - 脏标记：有未保存改动时保存按钮高亮 + 「未保存」Tag
 *
 * 不再迁移（data-synth 专有）：自动保存、undo/redo、SSE 运行日志轮询、运行历史/版本回滚、
 * 导入导出草稿、节点单独运行、数据集/样本源绑定（config-helper.ts、runtime-status.ts、
 * tape-import-modal.tsx、sample-source-transformer.ts 全部裁掉）。
 */

/** 画布节点 ID 生成（无 nanoid 依赖） */
function uid(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/** 后端 nodes 原始结构（TapeDetail.nodes 为宽松结构，此处限定读取字段） */
interface RawFlowNode {
  id?: unknown;
  type?: unknown;
  position?: { x?: unknown; y?: unknown };
  data?: { stepInst?: unknown; label?: unknown; icon?: unknown; group?: unknown; config?: unknown };
}

/** 后端 edges 原始结构 */
interface RawFlowEdge {
  id?: unknown;
  source?: unknown;
  target?: unknown;
  sourceHandle?: unknown;
  targetHandle?: unknown;
  type?: unknown;
}

function normalizeNode(raw: RawFlowNode): StepFlowNode {
  return {
    id: String(raw.id ?? uid('node')),
    type: 'stepNode',
    position: {
      x: Number(raw.position?.x ?? 0),
      y: Number(raw.position?.y ?? 0),
    },
    data: {
      stepInst: String(raw.data?.stepInst ?? ''),
      label: String(raw.data?.label ?? ''),
      icon: typeof raw.data?.icon === 'string' ? raw.data.icon : null,
      group: typeof raw.data?.group === 'string' ? raw.data.group : null,
      config: parseJsonRecord(raw.data?.config ?? {}),
    },
  };
}

function normalizeEdge(raw: RawFlowEdge): Edge {
  return {
    id: String(raw.id ?? `${raw.source}-${raw.target}`),
    source: String(raw.source ?? ''),
    target: String(raw.target ?? ''),
    sourceHandle: typeof raw.sourceHandle === 'string' ? raw.sourceHandle : undefined,
    targetHandle: typeof raw.targetHandle === 'string' ? raw.targetHandle : undefined,
    type: typeof raw.type === 'string' ? raw.type : 'default',
  };
}

/** 持久化节点：只保留数据契约字段，剔除运行时注入的回调 */
function toPersistableNode(n: StepFlowNode): {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: StepNodeData;
} {
  return {
    id: n.id,
    type: n.type ?? 'stepNode',
    position: { x: n.position.x, y: n.position.y },
    data: {
      stepInst: n.data.stepInst,
      label: n.data.label,
      icon: n.data.icon ?? null,
      group: n.data.group ?? null,
      config: n.data.config ?? {},
    },
  };
}

const TAPE_STATUS_META: Record<string, { color: string; label: string }> = {
  draft: { color: 'default', label: '草稿' },
  effective: { color: 'green', label: '已发布' },
  offline: { color: 'orange', label: '已下线' },
};

interface DesignerClientProps {
  tapeId: string;
}

export function DesignerClient({ tapeId }: DesignerClientProps) {
  const { message } = App.useApp();
  const router = useRouter();
  const { screenToFlowPosition, fitView, zoomIn, zoomOut } = useReactFlow();

  const [nodes, setNodes, onNodesChangeRaw] = useNodesState<StepFlowNode>([]);
  const [edges, setEdges, onEdgesChangeRaw] = useEdgesState<Edge>([]);

  const [tape, setTape] = useState<TapeDetail | null>(null);
  const [defines, setDefines] = useState<StepDefineItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedNode, setSelectedNode] = useState<StepFlowNode | null>(null);
  const [zoomLevel, setZoomLevel] = useState(100);

  // 脏标记：有未保存改动时顶栏保存按钮高亮
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  // 执行结果弹窗（异步执行 2026-10-09：execute 返回 run_id，轮询 runs/{run_id} 拿逐步日志）
  const [execLoading, setExecLoading] = useState(false);
  const [execOpen, setExecOpen] = useState(false);
  const [execRun, setExecRun] = useState<TapeRunDetail | null>(null);
  const [polling, setPolling] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  // 导入弹窗
  const [importOpen, setImportOpen] = useState(false);

  // 画布重载信号（导入成功后自增触发重新拉取）
  const [reloadKey, setReloadKey] = useState(0);

  /** 初始加载期间不标记 dirty（ReactFlow 初始化可能派发维度/选择类变更） */
  const isInitialLoadRef = useRef(true);

  const markDirty = useCallback(() => {
    if (isInitialLoadRef.current || loading) return;
    setDirty(true);
  }, [loading]);

  // ---------------- 数据加载 ----------------
  useEffect(() => {
    let cancelled = false;
    isInitialLoadRef.current = true;
    setLoading(true);

    (async () => {
      const [tapeRes, defRes] = await Promise.all([
        apiGetTape(tapeId),
        apiListStepDefines(1, 200),
      ]);
      if (cancelled) return;

      if (tapeRes.success && tapeRes.data) {
        setTape(tapeRes.data);
        const rawNodes = (tapeRes.data.nodes ?? []) as unknown as RawFlowNode[];
        const rawEdges = (tapeRes.data.edges ?? []) as unknown as RawFlowEdge[];
        setNodes(rawNodes.map(normalizeNode));
        setEdges(rawEdges.map(normalizeEdge));
      } else {
        message.error(tapeRes.message || '加载编排失败');
      }

      if (defRes.success) {
        setDefines(defRes.data?.items ?? []);
      }
      setLoading(false);
      isInitialLoadRef.current = false;
      setDirty(false);

      // 画布就绪后自适应视野
      requestAnimationFrame(() => {
        void fitView({ padding: 0.15, maxZoom: 1 });
      });
    })();

    return () => {
      cancelled = true;
    };
  }, [tapeId, reloadKey, setNodes, setEdges, fitView, message]);

  // ---------------- 画布事件 ----------------
  const onNodesChange = useCallback(
    (changes: NodeChange<StepFlowNode>[]) => {
      if (changes.some((c) => c.type !== 'select')) markDirty();
      onNodesChangeRaw(changes);
    },
    [markDirty, onNodesChangeRaw],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      if (changes.some((c) => c.type !== 'select')) markDirty();
      onEdgesChangeRaw(changes);
    },
    [markDirty, onEdgesChangeRaw],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      markDirty();
      setEdges((eds) => addEdge({ ...connection, type: 'default' }, eds));
    },
    [markDirty, setEdges],
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  /** 左栏拖入 -> 生成新节点 */
  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const nodeType = event.dataTransfer.getData('application/reactflow');
      const defRaw = event.dataTransfer.getData('application/reactflow-def');
      if (!nodeType || !defRaw) return;

      let def: StepDefineItem;
      try {
        def = JSON.parse(defRaw) as StepDefineItem;
      } catch {
        return;
      }

      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      const newNode: StepFlowNode = {
        id: uid('node'),
        type: 'stepNode',
        position,
        data: {
          stepInst: def.step_inst,
          label: def.step_label || def.step_inst,
          icon: def.step_icon ?? null,
          group: def.group_type,
          config: {},
        },
      };
      markDirty();
      setNodes((nds) => nds.concat(newNode));
    },
    [screenToFlowPosition, setNodes, markDirty],
  );

  /** 节点选中 -> 打开右侧配参 Drawer */
  const onSelectionChange = useCallback(({ nodes: selected }: OnSelectionChangeParams<StepFlowNode>) => {
    setSelectedNode(selected[0] ?? null);
  }, []);

  const handleClosePanel = useCallback(() => {
    setSelectedNode(null);
    setNodes((nds) => nds.map((n) => ({ ...n, selected: false })));
  }, [setNodes]);

  /** 节点菜单删除：删节点 + 级联删关联边 */
  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      setNodes((nds) => nds.filter((n) => n.id !== nodeId));
      setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
      setSelectedNode((prev) => (prev && prev.id === nodeId ? null : prev));
      markDirty();
    },
    [setNodes, setEdges, setSelectedNode, markDirty],
  );

  /** 右侧面板参数改动 -> 实时写回 node.data.config（受控） */
  const handleConfigChange = useCallback(
    (nodeId: string, values: Record<string, unknown>) => {
      const pickLabel = (data: StepNodeData): string => {
        const label = values.label;
        return typeof label === 'string' && label.trim() ? label : data.label;
      };
      setNodes((nds) =>
        nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, label: pickLabel(n.data), config: values } } : n)),
      );
      setSelectedNode((prev) =>
        prev && prev.id === nodeId ? { ...prev, data: { ...prev.data, label: pickLabel(prev.data), config: values } } : prev,
      );
      markDirty();
    },
    [setNodes, setSelectedNode, markDirty],
  );

  /** 视口变化 -> 同步缩放百分比显示 */
  const onViewportChange = useCallback((viewport: Viewport) => {
    setZoomLevel(Math.round(viewport.zoom * 100));
  }, []);

  const renderedNodes = useMemo(
    () => nodes.map((n) => ({ ...n, data: { ...n.data, onDelete: handleDeleteNode } })),
    [nodes, handleDeleteNode],
  );

  // ---------------- 保存 / 发布 / 执行 ----------------
  const handleSave = useCallback(
    async (silent = false): Promise<boolean> => {
      if (!tape) return false;
      setSaving(true);
      try {
        if (!silent) message.loading({ content: '保存中...', key: 'saveDesign' });
        const res = await apiSaveTapeDesign(tapeId, nodes.map(toPersistableNode), edges, tape.exec_params);
        if (res.success) {
          setDirty(false);
          if (!silent) message.success({ content: '保存成功', key: 'saveDesign' });
          return true;
        }
        if (!silent) message.error({ content: res.message || '保存失败', key: 'saveDesign' });
        return false;
      } finally {
        setSaving(false);
      }
    },
    [tape, tapeId, nodes, edges, message],
  );

  const handleBack = useCallback(() => {
    router.push('/orchestrations');
  }, [router]);

  /** 保存并发布：先静默保存草稿，再调发布接口，成功返回列表 */
  const handleSaveAndPublish = async () => {
    if (!tape) return;
    message.loading({ content: '正在保存并发布...', key: 'publish' });
    const saved = await handleSave(true);
    if (!saved) {
      message.error({ content: '保存失败，无法发布', key: 'publish' });
      return;
    }
    const res = await apiPublishTape(tapeId);
    if (res.success) {
      message.success({ content: '发布成功', key: 'publish' });
      router.push('/orchestrations');
    } else {
      message.error({ content: res.message || '发布失败', key: 'publish' });
    }
  };

  /** 执行：先静默保存，再异步提交执行（API 返回 run_id），轮询逐步日志弹窗展示 */
  const handleExecute = async () => {
    if (!tape) return;
    setExecLoading(true);
    try {
      const saved = await handleSave(true);
      if (!saved) {
        message.error('保存失败，无法执行');
        return;
      }
      const res = await apiExecuteTape(tapeId, {});
      if (res.success && res.data?.run_id) {
        const runId = res.data.run_id;
        setExecOpen(true);
        setExecRun({
          id: runId,
          tape_id: tapeId,
          tape_name: tape.tape_name,
          status: 'queued',
          step_results: [],
          bindings: {},
        });
        setPolling(true);
        // 轮询执行进度：queued/running → 逐步日志；终态（success/failed/cancelled）停止
        pollTimer.current = setInterval(async () => {
          try {
            const r = await apiGetTapeRun(runId);
            if (r.success && r.data) {
              setExecRun(r.data);
              if (['success', 'failed', 'cancelled'].includes(r.data.status)) {
                if (pollTimer.current) {
                  clearInterval(pollTimer.current);
                  pollTimer.current = null;
                }
                setPolling(false);
              }
            }
          } catch {
            // 轮询失败不中断：下次 tick 重试，直至弹窗关闭
          }
        }, 1500);
      } else {
        message.error(res.message || '执行失败');
      }
    } finally {
      setExecLoading(false);
    }
  };

  // 卸载时清理轮询定时器
  useEffect(
    () => () => {
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
    },
    [],
  );

  /** 导出：先静默保存当前草稿，再拉取导出 JSON 并触发浏览器下载 */
  const handleExport = async () => {
    if (!tape) return;
    message.loading({ content: '正在导出...', key: 'exportDraft' });
    try {
      const saved = await handleSave(true);
      if (!saved) {
        message.error({ content: '保存失败，无法导出', key: 'exportDraft' });
        return;
      }
      const res = await apiExportTapeDraft(tapeId);
      if (!res.success || !res.data) {
        message.error({ content: res.message || '导出失败', key: 'exportDraft' });
        return;
      }
      const blob = new Blob([res.data.content], { type: 'application/json;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = res.data.file_name || `${tape.tape_name || tapeId}_draft_export.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      message.success({ content: '导出成功', key: 'exportDraft' });
    } catch (e) {
      message.error({ content: e instanceof Error ? e.message : '导出失败', key: 'exportDraft' });
    }
  };

  /** 导入：调后端导入接口，成功后重载画布 */
  const handleImport = async (content: string) => {
    const res = await apiImportTapeDraft(tapeId, content);
    if (!res.success || !res.data) {
      throw new Error(res.message || '导入失败');
    }
    message.success(`导入成功，共 ${res.data.imported_step_count} 个节点`);
    setImportOpen(false);
    setReloadKey((k) => k + 1);
  };

  const statusMeta = tape ? TAPE_STATUS_META[tape.status] : undefined;

  if (loading) {
    return (
      <div style={{ height: '100%', width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f9fbfd' }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div style={{ height: '100%', width: '100%', display: 'flex', flexDirection: 'column', background: '#fff', overflow: 'hidden' }}>
      {/* ============ 顶栏 ============ */}
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '0 16px',
          height: 48,
          borderBottom: '1px solid #eff4f9',
          flexShrink: 0,
        }}
      >
        <Tooltip title="返回列表">
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={handleBack} style={{ color: '#4e5969' }} />
        </Tooltip>

        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0, flex: 1 }}>
          <p style={{ margin: 0, fontSize: 14, fontWeight: 600, color: '#242e43', fontFamily: 'PingFang SC, sans-serif', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {tape?.tape_label || tape?.tape_name || '未命名编排'}
          </p>
          {statusMeta && <Tag color={statusMeta.color} style={{ flexShrink: 0 }}>{statusMeta.label}</Tag>}
          {dirty && <Tag color="orange" style={{ flexShrink: 0 }}>未保存</Tag>}
        </div>

        <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
          <Tooltip title={dirty ? '有未保存的修改' : '当前已是最新'}>
            <Button
              icon={<SaveOutlined />}
              type={dirty ? 'primary' : 'default'}
              loading={saving}
              onClick={() => void handleSave(false)}
            >
              保存
            </Button>
          </Tooltip>
          <Tooltip title="导出当前草稿为 JSON 文件">
            <Button icon={<DownloadOutlined />} onClick={() => void handleExport()}>
              导出
            </Button>
          </Tooltip>
          <Tooltip title="从 JSON 文件导入，覆盖当前草稿">
            <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>
              导入
            </Button>
          </Tooltip>
          <Button icon={<RocketOutlined />} loading={saving} onClick={() => void handleSaveAndPublish()}>
            保存并发布
          </Button>
          <Button icon={<PlayCircleOutlined />} type="primary" ghost loading={execLoading} onClick={() => void handleExecute()}>
            执行
          </Button>
        </div>
      </header>

      {/* ============ 主体：左栏 + 画布 ============ */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0, overflow: 'hidden', position: 'relative' }}>
        <LeftSidebar defines={defines} />

        <main
          style={{ flex: 1, minWidth: 0, position: 'relative', background: '#f9fbfd' }}
          onDrop={onDrop}
          onDragOver={onDragOver}
        >
          <ReactFlow
            nodes={renderedNodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onSelectionChange={onSelectionChange}
            onPaneClick={handleClosePanel}
            onViewportChange={onViewportChange}
            defaultEdgeOptions={{ type: 'default' }}
            proOptions={{ hideAttribution: true }}
            fitViewOptions={{ maxZoom: 1, padding: 0.2 }}
            minZoom={0.3}
            maxZoom={2}
          >
            <Background gap={18} size={1} color="#BED6F5" variant={BackgroundVariant.Dots} />
          </ReactFlow>

          {/* 左下角缩放控制条 */}
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
            <span style={{ fontSize: 12, fontWeight: 700, color: '#242e43', minWidth: 42, textAlign: 'center' }}>{zoomLevel}%</span>
            <Tooltip title="放大">
              <Button type="text" size="small" icon={<ZoomInOutlined />} onClick={() => void zoomIn({ duration: 200 })} />
            </Tooltip>
            <Tooltip title="适应视图">
              <Button type="text" size="small" icon={<FullscreenOutlined />} onClick={() => void fitView({ padding: 0.15, maxZoom: 1 })} />
            </Tooltip>
          </div>
        </main>
      </div>

      {/* ============ 右侧配参 Drawer ============ */}
      <RightPanel
        selectedNode={selectedNode}
        defines={defines}
        onClose={handleClosePanel}
        onConfigChange={handleConfigChange}
      />

      {/* ============ 执行结果弹窗（异步执行：轮询 runs/{run_id} 展示逐步日志） ============ */}
      <Modal
        title={`执行结果 · ${execRun?.tape_name || ''}`}
        open={execOpen}
        onCancel={() => {
          if (pollTimer.current) {
            clearInterval(pollTimer.current);
            pollTimer.current = null;
          }
          setPolling(false);
          setExecOpen(false);
        }}
        footer={<Button onClick={() => {
          if (pollTimer.current) {
            clearInterval(pollTimer.current);
            pollTimer.current = null;
          }
          setPolling(false);
          setExecOpen(false);
        }}>关闭</Button>}
        width={680}
      >
        {execRun && (
          <div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
              <Tag color={execRun.status === 'success' ? 'green' : execRun.status === 'failed' ? 'red' : 'blue'}>
                {execRun.status === 'success' ? '成功' : execRun.status === 'failed' ? '失败' : execRun.status === 'cancelled' ? '已取消' : '执行中'}
              </Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>run_id: {execRun.id}</Text>
              {polling && <Text type="secondary" style={{ fontSize: 12 }}>（轮询中…）</Text>}
            </div>
            {execRun.error && (
              <pre style={{ margin: '0 0 8px', fontSize: 12, color: '#cf1322', whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 120, overflow: 'auto' }}>
                {execRun.error}
              </pre>
            )}
            <div style={{ maxHeight: 360, overflow: 'auto', border: '1px solid #f0f0f0', borderRadius: 8, padding: 8 }}>
              {(execRun.step_results || []).map((s, i) => (
                <div key={s.step_id || i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '4px 0', borderBottom: '1px dashed #f5f5f5' }}>
                  <Tag color={s.status === 'success' ? 'green' : s.status === 'skipped' ? 'orange' : 'red'} style={{ width: 62, textAlign: 'center', margin: 0, flexShrink: 0 }}>
                    {s.status === 'success' ? '成功' : s.status === 'skipped' ? '跳过' : '失败'}
                  </Tag>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Text strong style={{ fontSize: 13 }}>
                      [{s.step_inst}] {s.step_label}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>{s.duration_ms ?? 0}ms</Text>
                    {s.error ? (
                      <pre style={{ margin: '4px 0 0', fontSize: 12, color: '#cf1322', whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 120, overflow: 'auto' }}>
                        {s.error}
                      </pre>
                    ) : (
                      s.body !== null && s.body !== undefined && (
                        <pre style={{ margin: '4px 0 0', fontSize: 12, color: '#555', whiteSpace: 'pre-wrap', wordBreak: 'break-all', maxHeight: 160, overflow: 'auto' }}>
                          {typeof s.body === 'string' ? s.body : JSON.stringify(s.body, null, 2)}
                        </pre>
                      )
                    )}
                  </div>
                </div>
              ))}
              {(execRun.step_results || []).length === 0 && (
                <Text type="secondary">尚无步骤日志{execRun.status === 'queued' ? '（排队中）' : '（执行中）'}…</Text>
              )}
            </div>
            {execRun.bindings && Object.keys(execRun.bindings).length > 0 && (
              <div style={{ marginTop: 10 }}>
                <Text strong style={{ fontSize: 12 }}>最终变量</Text>
                <pre style={{ margin: '4px 0 0', fontSize: 12, background: '#fafafa', padding: 8, borderRadius: 6, maxHeight: 140, overflow: 'auto' }}>
                  {JSON.stringify(execRun.bindings, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* ============ 导入弹窗 ============ */}
      <TapeImportModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImport={(content) => handleImport(content)}
      />
    </div>
  );
}

/** 画布核心组件（custom-node 独立导出便于未来按需扩展） */
export { CustomNode };