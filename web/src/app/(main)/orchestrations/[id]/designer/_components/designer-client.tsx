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
import type { Edge, Connection, Viewport } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { App, Button, Input, Modal, Select, Spin, Tag, Tooltip } from 'antd';
import {
  SaveOutlined,
  RocketOutlined,
  PlayCircleOutlined,
  DownloadOutlined,
  UploadOutlined,
  ApartmentOutlined,
  HistoryOutlined,
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
  apiListTapeRuns,
  apiExportTapeDraft,
  apiImportTapeDraft,
  apiSaveTapeAsTemplate,
  apiListTapeTemplateCategories,
} from '@/lib/api';
import type { StepDefineItem, TapeDetail, TapeRunDetail, TapeRunSummary } from '@/lib/api';

import { LeftSidebar } from './left-sidebar';
import { RightPanel } from './right-panel';
import { CustomNode, nodeTypes } from './custom-node';
import type { StepFlowNode, StepNodeData } from './custom-node';
import { TapeImportModal } from './tape-import-modal';
import { ExecuteInputModal } from '@/app/(main)/orchestrations/_components/execute-input-modal';
import { TapeRunLogDrawer } from '@/app/(main)/orchestrations/_components/tape-run-log-drawer';
import { TapeRunHistoryDrawer } from '@/app/(main)/orchestrations/_components/tape-run-history-drawer';
import { normalizeEdge, normalizeNode, uid } from '../_utils/flow';
import type { RawFlowEdge, RawFlowNode } from '../_utils/flow';

/** 编排设计器 —— 从 data-synth designer-client.tsx(1931行) 迁移的裁剪版。
 *
 * 对齐裁剪后的功能集：
 *  - ReactFlowProvider 上下文 + 节点拖拽/连线（onConnect）
 *  - 删除节点/删除边（节点菜单删除 + 键盘 Delete 原生删除）
 *  - 左栏组件拖入（onDrop 依据 dataTransfer 中的 StepDefineItem 生成节点）
 *  - 点节点打开右侧配参 Drawer（DynamicForm 渲染 step_cfg，改动实时写回 node.data.config）
 *  - 顶栏：返回列表 / 保存 / 保存并发布 / 执行（执行结果 Drawer 逐步展示）
 *  - 脏标记：有未保存改动时保存按钮高亮 + 「未保存」Tag
 *
 * 不再迁移（data-synth 专有）：自动保存、undo/redo、SSE 运行日志轮询、运行历史/版本回滚、
 * 导入导出草稿、节点单独运行、数据集/样本源绑定（config-helper.ts、runtime-status.ts、
 * tape-import-modal.tsx、sample-source-transformer.ts 全部裁掉）。
 */

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

/**
 * 画布「可持久化内容」指纹 —— 仅节点（含位置/配置）与连线的实际变化才改变指纹。
 * 用于脏标记判断：ReactFlow 初始化/选中会派发 dimensions、select 等不影响持久化
 * 内容的变更，不能据此认为用户改动过设计。
 */
function designSignature(nodes: StepFlowNode[], edges: Edge[]): string {
  const persistNodes = nodes
    .map(toPersistableNode)
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const persistEdges = edges
    .map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? null,
      targetHandle: e.targetHandle ?? null,
    }))
    .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return JSON.stringify({ nodes: persistNodes, edges: persistEdges });
}

const TAPE_STATUS_META: Record<string, { color: string; label: string }> = {
  draft: { color: 'default', label: '草稿' },
  effective: { color: 'green', label: '已发布' },
  offline: { color: 'orange', label: '已下线' },
};

/** 运行状态展示（顶栏实时状态 Tag）：与运行历史抽屉保持一致 */
const RUN_STATUS_META: Record<string, { color: string; label: string }> = {
  queued: { color: 'blue', label: '排队中' },
  running: { color: 'blue', label: '执行中' },
  success: { color: 'green', label: '成功' },
  failed: { color: 'red', label: '失败' },
  cancelled: { color: 'orange', label: '已取消' },
};

/** 活跃运行状态（未结束）：用于防重复发起（禁用执行按钮） */
const ACTIVE_RUN_STATUS = ['queued', 'running'];

interface DesignerClientProps {
  tapeId: string;
  /**
   * 「返回」动作的宿主回调（动态选项卡内嵌模式）。
   * 传入则点「返回」/「保存并发布」成功后调用它（由宿主页关闭当前 tab），
   * 不传则保持独立路由行为：router.push('/orchestrations')。
   */
  onBack?: () => void;
  /** dirty 状态变化回调（宿主据此在关闭 tab 前提示「有未保存的修改」） */
  onDirtyChange?: (dirty: boolean) => void;
}

export function DesignerClient({ tapeId, onBack, onDirtyChange }: DesignerClientProps) {
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

  // 内嵌模式：dirty 同步给宿主（关闭 tab 前可提示未保存）
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  // 执行结果弹窗（异步执行 2026-10-09：execute 返回 run_id，轮询 runs/{run_id} 拿逐步日志）
  const [execLoading, setExecLoading] = useState(false);
  const [execOpen, setExecOpen] = useState(false);
  const [execRun, setExecRun] = useState<TapeRunDetail | null>(null);
  const [polling, setPolling] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  // 运行历史抽屉（2026-10-14：从编排管理列表迁入设计页）
  const [historyOpen, setHistoryOpen] = useState(false);
  // 该编排最近一次运行（顶栏实时展示运行状态 + 防重复发起）
  const [latestRun, setLatestRun] = useState<TapeRunSummary | null>(null);
  const hasActiveRun = !!latestRun && ACTIVE_RUN_STATUS.includes(latestRun.status);

  /** 停止逐步日志轮询 */
  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
    setPolling(false);
  }, []);

  /** 启动逐步日志轮询：queued/running → 持续拉取；终态停止 */
  const startPolling = useCallback(
    (runId: string) => {
      if (pollTimer.current) {
        clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
      setPolling(true);
      pollTimer.current = setInterval(async () => {
        try {
          const r = await apiGetTapeRun(runId);
          if (r.success && r.data) {
            setExecRun(r.data);
            if (['success', 'failed', 'cancelled'].includes(r.data.status)) stopPolling();
          }
        } catch {
          // 轮询失败不中断：下次 tick 重试，直至抽屉关闭/终态
        }
      }, 1500);
    },
    [stopPolling],
  );

  /** 打开运行日志抽屉（新执行 / 历史回看共用）：拉详情后按状态决定是否轮询 */
  const openRunLog = useCallback(
    async (runId: string) => {
      setExecOpen(true);
      try {
        const r = await apiGetTapeRun(runId);
        if (r.success && r.data) {
          setExecRun(r.data);
          if (ACTIVE_RUN_STATUS.includes(r.data.status)) startPolling(runId);
          else stopPolling();
        } else {
          stopPolling();
        }
      } catch {
        stopPolling();
      }
    },
    [startPolling, stopPolling],
  );

  /** 拉取该编排最近一次运行（顶栏状态 Tag 数据源） */
  const refreshLatestRun = useCallback(async () => {
    const res = await apiListTapeRuns(tapeId, 1, 1);
    if (res.success) setLatestRun(res.data?.items?.[0] ?? null);
  }, [tapeId]);

  // 进入设计页即拉一次最近运行状态，便于刷新后仍能看到「执行中」
  useEffect(() => {
    void refreshLatestRun();
  }, [refreshLatestRun]);

  // 运行中时轮询刷新状态（终态后自动停止）——同时驱动执行按钮的禁用
  useEffect(() => {
    if (!hasActiveRun) return;
    const t = setInterval(() => void refreshLatestRun(), 3000);
    return () => clearInterval(t);
  }, [hasActiveRun, refreshLatestRun]);

  // 卸载时清理轮询定时器
  useEffect(() => stopPolling, [stopPolling]);

  // 导入弹窗
  const [importOpen, setImportOpen] = useState(false);

  // 存为模板弹窗（2026-10-12）
  const [tplOpen, setTplOpen] = useState(false);
  const [tplSaving, setTplSaving] = useState(false);
  const [tplName, setTplName] = useState('');
  const [tplCategory, setTplCategory] = useState('通用');
  const [tplDescr, setTplDescr] = useState('');
  const [tplCategories, setTplCategories] = useState<string[]>([]);
  const [tplNewCat, setTplNewCat] = useState(false);

  // 执行入参弹窗（先填初始调用参数，再提交执行）
  const [execInputOpen, setExecInputOpen] = useState(false);

  // 画布重载信号（导入成功后自增触发重新拉取）
  const [reloadKey, setReloadKey] = useState(0);

  /**
   * 脏标记基线：最近一次「已加载 / 已保存」状态的画布指纹。
   * 初始加载期间 ReactFlow 会派发 dimensions（节点测量）、select 等变更，
   * 它们不影响持久化内容；因此不采用「收到变更即置脏」，而是比对指纹——
   * 只有节点/连线/位置/配置真的与基线不同时才算有未保存的修改。
   */
  const baselineRef = useRef<string>('');
  const isInitialLoadRef = useRef(true);

  /** 当前画布的可持久化内容指纹（忽略选中态、测量尺寸等运行时字段） */
  const currentSignature = useMemo(() => designSignature(nodes, edges), [nodes, edges]);

  useEffect(() => {
    if (isInitialLoadRef.current || loading) return;
    setDirty(currentSignature !== baselineRef.current);
  }, [currentSignature, loading]);

  // ---------------- 数据加载 ----------------
  useEffect(() => {
    let cancelled = false;
    isInitialLoadRef.current = true;
    setLoading(true);
    // 基线先归零：加载失败时也不会因空画布被判成「有未保存改动」
    baselineRef.current = designSignature([], []);

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
        const loadedNodes = rawNodes.map(normalizeNode);
        const loadedEdges = rawEdges.map(normalizeEdge);
        setNodes(loadedNodes);
        setEdges(loadedEdges);
        // 以服务端加载到的内容作为脏标记基线
        baselineRef.current = designSignature(loadedNodes, loadedEdges);
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
  // 脏标记由上面的指纹比对统一负责（内容变了才置脏），此处无需再手工置脏。
  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => addEdge({ ...connection, type: 'default' }, eds));
    },
    [setEdges],
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
      setNodes((nds) => nds.concat(newNode));
    },
    [screenToFlowPosition, setNodes],
  );

  /** 节点双击 -> 打开右侧配参 Drawer（单击仅选中高亮，不进抽屉） */
  const onNodeDoubleClick = useCallback((_: React.MouseEvent, node: StepFlowNode) => {
    setSelectedNode(node);
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
    },
    [setNodes, setEdges, setSelectedNode],
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
    },
    [setNodes, setSelectedNode],
  );

  /** 视口变化 -> 同步缩放百分比显示 */
  const onViewportChange = useCallback((viewport: Viewport) => {
    setZoomLevel(Math.round(viewport.zoom * 100));
  }, []);

  // 2026-10-12 画布节点联动：由执行结果（execRun.step_results）注入每个节点的
  // runStatus（running/success/failed/skipped）与耗时——step_id 格式 tape_id::n1，
  // 拆出节点 id n1 做映射；无 execRun 时节点保持静态样式。
  const runNodeMap = useMemo(() => {
    const map: Record<string, { status: string; durationMs?: number }> = {};
    (execRun?.step_results || []).forEach((s) => {
      const seg = String(s.step_id || '').split('::');
      const nodeId = seg.length > 1 ? seg[seg.length - 1] : seg[0];
      if (nodeId) {
        const dur = Number(s.duration_ms ?? 0);
        map[nodeId] = { status: s.status || 'pending', durationMs: dur > 0 ? dur : undefined };
      }
    });
    return map;
  }, [execRun]);

  const renderedNodes = useMemo(
    () =>
      nodes.map((n) => {
        const run = runNodeMap[n.id];
        return {
          ...n,
          data: {
            ...n.data,
            onDelete: handleDeleteNode,
            // 执行中（终态已定）才注入状态；正在执行时当前节点闪烁效果由
            // custom-node 的 animation 负责，这里不做时间戳驱动（避免 1.5s
            // 轮询抖动重置动画）
            ...(run && execRun?.status !== 'queued'
              ? { runStatus: run.status, runDurationMs: run.durationMs }
              : { runStatus: undefined, runDurationMs: undefined }),
          },
        };
      }),
    [nodes, handleDeleteNode, runNodeMap, execRun],
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
          // 保存成功：当前内容成为新的脏标记基线
          baselineRef.current = designSignature(nodes, edges);
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

  /** 存为模板：先静默保存当前草稿，再把骨架存为模板 */
  const openTplModal = async () => {
    setTplName(tape?.tape_label || tape?.tape_name || '');
    setTplDescr(tape?.tape_descr || '');
    setTplCategory('通用');
    setTplNewCat(false);
    try {
      const res = await apiListTapeTemplateCategories();
      if (res.success) setTplCategories((res.data || []).map((c) => c.category));
    } catch {
      /* 分类拉取失败不阻塞，允许手输 */
    }
    setTplOpen(true);
  };

  const handleSaveAsTemplate = async () => {
    if (!tplName.trim()) {
      message.warning('请填写模板名称');
      return;
    }
    if (tplNewCat && !tplCategory.trim()) {
      message.warning('请填写新分类名');
      return;
    }
    setTplSaving(true);
    try {
      const saved = await handleSave(true);
      if (!saved) {
        message.error({ content: '草稿保存失败，未存为模板', key: 'tpl' });
        return;
      }
      const res = await apiSaveTapeAsTemplate(tapeId, {
        name: tplName.trim(),
        label: tplName.trim(),
        descr: tplDescr.trim() || undefined,
        category: tplCategory.trim() || '通用',
      });
      if (res.success) {
        message.success({ content: '已存为模板', key: 'tpl' });
        setTplOpen(false);
      } else {
        message.error({ content: res.message || '存为模板失败', key: 'tpl' });
      }
    } finally {
      setTplSaving(false);
    }
  };

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
      if (onBack) {
        onBack();
        return;
      }
      router.push('/orchestrations');
    } else {
      message.error({ content: res.message || '发布失败', key: 'publish' });
    }
  };

  /** 执行：先静默保存，再异步提交执行（API 返回 run_id），轮询逐步日志弹窗展示 */
  const handleExecute = async (inputs: Record<string, unknown>) => {
    if (!tape) return;
    setExecLoading(true);
    try {
      const saved = await handleSave(true);
      if (!saved) {
        message.error('保存失败，无法执行');
        return;
      }
      const res = await apiExecuteTape(tapeId, inputs);
      if (res.success && res.data?.run_id) {
        const runId = res.data.run_id;
        // 先占位展示 queued，再拉详情并按状态轮询
        setExecRun({
          id: runId,
          tape_id: tapeId,
          tape_name: tape.tape_name,
          status: 'queued',
          step_results: [],
          bindings: {},
        });
        await openRunLog(runId);
        void refreshLatestRun();
      } else {
        message.error(res.message || '执行失败');
      }
    } finally {
      setExecLoading(false);
    }
  };

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
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0, flex: 1 }}>
          <p style={{ margin: 0, fontSize: 14, fontWeight: 600, color: '#242e43', fontFamily: 'PingFang SC, sans-serif', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {tape?.tape_label || tape?.tape_name || '未命名编排'}
          </p>
          {statusMeta && <Tag color={statusMeta.color} style={{ flexShrink: 0 }}>{statusMeta.label}</Tag>}
          {dirty && <Tag color="orange" style={{ flexShrink: 0 }}>未保存</Tag>}
          {/* 运行状态（最近一次运行）：点击回看该次日志；运行中每 3s 自动刷新 */}
          {latestRun && (
            <Tooltip title="点击查看最近一次运行日志">
              <Tag
                color={(RUN_STATUS_META[latestRun.status] || { color: 'default' }).color}
                style={{ flexShrink: 0, cursor: 'pointer' }}
                onClick={() => void openRunLog(latestRun.id)}
              >
                {(RUN_STATUS_META[latestRun.status] || { label: latestRun.status }).label}
              </Tag>
            </Tooltip>
          )}
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
                    <Tooltip title="把当前编排骨架存为模板，可在模板管理中复用">
                      <Button icon={<ApartmentOutlined />} onClick={() => void openTplModal()}>
                        存为模板
                      </Button>
                    </Tooltip>
          <Tooltip title="从 JSON 文件导入，覆盖当前草稿">
            <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>
              导入
            </Button>
          </Tooltip>
          <Tooltip title="查看该编排的历史运行记录">
            <Button icon={<HistoryOutlined />} onClick={() => setHistoryOpen(true)}>
              历史
            </Button>
          </Tooltip>
          <Button icon={<RocketOutlined />} loading={saving} onClick={() => void handleSaveAndPublish()}>
            保存并发布
          </Button>
          <Tooltip title={hasActiveRun ? '该编排已有运行中的任务，请等待其结束' : '填写参数并执行'}>
            <Button
              icon={<PlayCircleOutlined />}
              type="primary"
              ghost
              loading={execLoading}
              disabled={hasActiveRun}
              onClick={() => setExecInputOpen(true)}
            >
              {hasActiveRun ? '运行中' : '执行'}
            </Button>
          </Tooltip>
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
            onNodesChange={onNodesChangeRaw}
            onEdgesChange={onEdgesChangeRaw}
            onConnect={onConnect}
            onNodeDoubleClick={onNodeDoubleClick}
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

      {/* ============ 执行结果抽屉（异步执行：轮询 runs/{run_id} 展示逐步日志；2026-10-13 Modal → Drawer） ============ */}
      <TapeRunLogDrawer
        open={execOpen}
        run={execRun}
        polling={polling}
        onClose={() => {
          stopPolling();
          setExecOpen(false);
        }}
      />

      {/* ============ 运行历史抽屉（2026-10-14：从编排管理列表迁入设计页） ============ */}
      <TapeRunHistoryDrawer
        open={historyOpen}
        tape={tape}
        onClose={() => setHistoryOpen(false)}
        onViewRun={(runId) => {
          setHistoryOpen(false);
          void openRunLog(runId);
        }}
      />

      {/* ============ 执行入参弹窗：填写初始调用参数后提交执行 ============ */}
      <ExecuteInputModal
        open={execInputOpen}
        tapeName={tape?.tape_name || ''}
        loading={execLoading}
        onCancel={() => setExecInputOpen(false)}
        onOk={(inputs) => {
          setExecInputOpen(false);
          void handleExecute(inputs);
        }}
      />

      {/* ============ 导入弹窗 ============ */}
            <TapeImportModal
              open={importOpen}
              onClose={() => setImportOpen(false)}
              onImport={(content) => handleImport(content)}
            />

            {/* ============ 存为模板弹窗（2026-10-12） ============ */}
            <Modal
              open={tplOpen}
              title="存为模板"
              okText="保存模板"
              confirmLoading={tplSaving}
              onCancel={() => setTplOpen(false)}
              onOk={() => void handleSaveAsTemplate()}
              width={480}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingTop: 8 }}>
                <div>
                  <div style={{ marginBottom: 4, fontSize: 13, color: '#4e5969' }}>模板名称 *</div>
                  <Input
                    value={tplName}
                    onChange={(e) => setTplName(e.target.value)}
                    placeholder="填写模板名称（如：wiki 批量构建）"
                    maxLength={64}
                  />
                </div>
                <div>
                  <div style={{ marginBottom: 4, fontSize: 13, color: '#4e5969' }}>分类</div>
                  <Select
                    value={tplNewCat ? '__new__' : tplCategory}
                    onChange={(v) => {
                      if (v === '__new__') {
                        setTplNewCat(true);
                        setTplCategory('');
                      } else {
                        setTplNewCat(false);
                        setTplCategory(v);
                      }
                    }}
                    style={{ width: '100%' }}
                    options={[
                      ...tplCategories.map((c) => ({ value: c, label: c })),
                      { value: '__new__', label: '＋ 新建分类…' },
                    ]}
                    showSearch
                    placeholder="选择或输入分类"
                  />
                  {tplNewCat && (
                    <Input
                      style={{ marginTop: 6 }}
                      value={tplCategory}
                      onChange={(e) => setTplCategory(e.target.value)}
                      placeholder="输入新分类名"
                      maxLength={64}
                      autoFocus
                    />
                  )}
                </div>
                <div>
                  <div style={{ marginBottom: 4, fontSize: 13, color: '#4e5969' }}>描述</div>
                  <Input.TextArea
                    value={tplDescr}
                    onChange={(e) => setTplDescr(e.target.value)}
                    placeholder="模板用途说明（可选）"
                    rows={3}
                    maxLength={2000}
                  />
                </div>
              </div>
            </Modal>
          </div>
        );
      }

/** 画布核心组件（custom-node 独立导出便于未来按需扩展） */
export { CustomNode };