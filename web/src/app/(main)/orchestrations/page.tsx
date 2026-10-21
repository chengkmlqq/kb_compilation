"use client";

/**
 * 编排管理（迁移自 data-synth algorithm/tapes）：
 * Card + inline 筛选（关键词/状态）+ antd Table + ModoPagination；
 * 操作：设计(动态选项卡打开 React Flow 画布) / 编辑 / 发布 / 下线 / 执行(逐步日志弹窗) / 删除。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { App, Button, Card, Empty, Form, Input, Modal, Select, Space, Table, Tag, Tooltip, TreeSelect, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { CheckCircleOutlined, ExperimentOutlined, ExportOutlined, PauseCircleOutlined, FileAddOutlined, AppstoreOutlined, FileTextOutlined, FolderOutlined } from "@ant-design/icons";
import { ModoInput, ModoTextArea } from "@/components/biz/modo-input";
import { ModoSelect } from "@/components/biz/modo-select";
import { ModoDrawer } from "@/components/biz/modo-drawer";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoPagination } from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";
import { DesignerClient } from "./[id]/designer/_components/designer-client";
import { ExecuteInputModal } from "./_components/execute-input-modal";
import { TapeRunLogDrawer } from "./_components/tape-run-log-drawer";
import { useUserNames } from "@/lib/use-user-names";
import {
  apiApplyTapeTemplate,
  apiCreateTape,
  apiDeleteTape,
  apiExecuteTape,
  apiGetTapeRun,
  apiListTapeCategories,
  apiListTapeRuns,
  apiListTapes,
  apiListTapeTemplates,
  apiOfflineTape,
  apiPublishTape,
  apiUpdateTape,
  TapeCategory,
  TapeItem,
  TapeRunDetail,
  TapeTemplateItem,
} from "@/lib/api";
import { tapeFuncMeta } from "@/lib/tape-func";

const { Text } = Typography;

const STATUS_META: Record<string, { color: string; label: string }> = {
  draft: { color: "default", label: "草稿" },
  effective: { color: "green", label: "已发布" },
  offline: { color: "orange", label: "已下线" },
};

/** 动态选项卡条目（对齐知识库页面模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的设计器 tab。
 *  只存元数据（tapeId），children 由 renderDesignerPane 按 tab 渲染——避免把 ReactNode 塞进 state。 */
type OrchestrationTab = { key: string; title: string; tapeId: string };

export default function OrchestrationsPage() {
  const { message, modal } = App.useApp();
  // 创建人列：user_id → 中文用户名映射（拉全量用户一次）
  const userNames = useUserNames();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<TapeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  // 筛选：applied 为「已提交」条件（驱动查询），表单值由 searchForm 托管
  const [applied, setApplied] = useState<{ keyword: string; status?: string; category?: string }>({
    keyword: "",
    status: undefined,
    category: undefined,
  });
  const [searchForm] = Form.useForm<{ keyword?: string; status?: string }>();

  // 左侧分类树（2026-10-13：编排管理页左右结构，参照编排模板页）
  const [selectedCategory, setSelectedCategory] = useState("");
  const [categories, setCategories] = useState<TapeCategory[]>([]);
  // 多级树：展开集合（2026-10-15 多级树；默认展开根目录一级，点节点/箭头可展开/收起）
  const [expandedCats, setExpandedCats] = useState<Set<string>>(new Set());

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<TapeItem | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  // 执行结果抽屉（异步执行 2026-10-09：execute 返回 run_id，轮询 runs/{run_id} 拿逐步日志）
  const [submitting, setSubmitting] = useState(false);
  const [execOpen, setExecOpen] = useState(false);
  const [execRun, setExecRun] = useState<TapeRunDetail | null>(null);
  const [polling, setPolling] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  // 执行入参弹窗（先填初始调用参数，再提交执行）
  const [execInputOpen, setExecInputOpen] = useState(false);
  const [execInputTape, setExecInputTape] = useState<TapeItem | null>(null);

  // 从模板新建（2026-10-12）
  const [tplPickerOpen, setTplPickerOpen] = useState(false);
  const [tplItems, setTplItems] = useState<TapeTemplateItem[]>([]);
  const [tplLoading, setTplLoading] = useState(false);
  const [tplApplying, setTplApplying] = useState<string | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
    setPolling(false);
  }, []);

  /** 启动轮询：queued/running → 持续拉逐步日志；终态（success/failed/cancelled）停止 */
  const startPolling = useCallback(
    (runId: string) => {
      stopPolling();
      setPolling(true);
      pollTimer.current = setInterval(async () => {
        try {
          const r = await apiGetTapeRun(runId);
          if (r.success && r.data) {
            setExecRun(r.data);
            if (["success", "failed", "cancelled"].includes(r.data.status)) {
              stopPolling();
            }
          }
        } catch {
          // 轮询失败不中断：下次 tick 重试，直至弹窗关闭/终态
        }
      }, 1500);
    },
    [stopPolling],
  );

  /** 打开日志抽屉（新建执行结果 / 运行中重开 / 历史回看共用）：拉详情后按状态决定是否轮询 */
  const openRunLog = useCallback(
    async (runId: string) => {
      setExecOpen(true);
      try {
        const r = await apiGetTapeRun(runId);
        if (r.success && r.data) {
          setExecRun(r.data);
          if (["queued", "running"].includes(r.data.status)) {
            startPolling(runId);
          } else {
            stopPolling();
          }
        } else {
          stopPolling();
        }
      } catch {
        stopPolling();
      }
    },
    [startPolling, stopPolling],
  );

  // 卸载时清理轮询定时器
  useEffect(() => stopPolling, [stopPolling]);

  // ---- 动态选项卡（对齐知识库页面模式：tab 只存元数据，children 由渲染函数按需构造）----
  // 首个 tab「编排管理」= 页面标题 + 列表，不可关闭；「设计」打开的设计器作为可关闭 tab。
  const [tabs, setTabs] = useState<OrchestrationTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");
  // 各设计器 tab 的脏标记（有未保存改动时关闭 tab 需确认）
  const [dirtyTabs, setDirtyTabs] = useState<Record<string, boolean>>({});

  const closeTab = (key: string) => {
    const dirty = dirtyTabs[key];
    const doClose = () => {
      setTabs((prev) => prev.filter((t) => t.key !== key));
      setDirtyTabs((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      if (activeTab === key) setActiveTab("home");
    };
    if (dirty) {
      modal.confirm({
        title: "放弃未保存的修改？",
        content: "设计器里有未保存的改动，关闭选项卡后将丢失。",
        okText: "放弃并关闭",
        okButtonProps: { danger: true },
        onOk: doClose,
      });
      return;
    }
    doClose();
  };

  /** 打开某编排的设计器选项卡：已存在则仅激活，否则新增后激活（对齐知识库 openKbSection） */
  const openDesignerTab = (row: { id: string; tape_name: string; tape_label?: string }) => {
    const key = `tape-${row.id}`;
    const title = `设计 · ${row.tape_label || row.tape_name}`;
    setTabs((prev) => {
      if (prev.some((t) => t.key === key)) return prev;
      return [...prev, { key, title, tapeId: row.id }];
    });
    setActiveTab(key);
  };

  /** 按 tab 渲染设计器 pane（渲染时构造 children；每个 tab 独立 ReactFlowProvider=独立 store，多 tab 互不干扰） */
  const renderDesignerPane = (t: OrchestrationTab) => (
    <div style={{ height: "100%", width: "100%", overflow: "hidden", background: "#f9fbfd" }}>
      <ReactFlowProvider>
        <DesignerClient
          tapeId={t.tapeId}
          onBack={() => closeTab(t.key)}
          onDirtyChange={(d) =>
            setDirtyTabs((prev) => (prev[t.key] === d ? prev : { ...prev, [t.key]: d }))
          }
        />
      </ReactFlowProvider>
    </div>
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListTapes(page, pageSize, applied.keyword, applied.status ?? "", applied.category ?? "");
      if (res.success) {
        setData(res.data?.items || []);
        setTotal(res.data?.total || 0);
      } else {
        message.error(res.message || "加载编排列表失败");
      }
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, applied, message]);

  const loadCategories = useCallback(async () => {
    const res = await apiListTapeCategories();
    if (res.success) {
      const list = res.data || [];
      setCategories(list);
      // 默认展开根目录这一级：初始只展开一级分类（无 parent_id），二级及以下默认收起
      setExpandedCats(new Set(list.filter((c) => !c.parent_id).map((c) => c.category)));
    }
  }, []);

  useEffect(() => {
    void loadCategories();
  }, [loadCategories]);

  useEffect(() => {
    void load();
  }, [load]);

  const doSearch = (v: { keyword?: string; status?: string }) => {
    setPage(1);
    setApplied({ keyword: v.keyword?.trim() || "", status: v.status, category: selectedCategory || undefined });
  };

  const doReset = () => {
    searchForm.resetFields();
    setSelectedCategory("");
    setPage(1);
    setApplied({ keyword: "", status: undefined, category: undefined });
  };

  /** 左树点击分类：仅重置分类维度，保留关键词/状态筛选；点一级分类同时展开其子级 */
  const selectCategory = (cat: string) => {
    setSelectedCategory(cat);
    setPage(1);
    setApplied((prev) => ({ ...prev, category: cat || undefined }));
    if (cat) {
      setExpandedCats((prev) => {
        const next = new Set(prev);
        next.add(cat);
        return next;
      });
    }
  };

  // 2026-10-15 多级树：按 parent_id 分组（一级 = 无父级，逐层挂子级）
  const topCategories = useMemo(
    () => categories.filter((c) => !c.parent_id),
    [categories],
  );
  const childByParent = useMemo(() => {
    const m = new Map<string, TapeCategory[]>();
    for (const c of categories) {
      if (!c.parent_id) continue;
      const arr = m.get(c.parent_id) || [];
      arr.push(c);
      m.set(c.parent_id, arr);
    }
    return m;
  }, [categories]);

  /** 递归渲染分类节点（领域 → 企业领域 → 文档类型 三级；有子级的行带 ▸/▾ 展开箭头；默认展开根目录一级） */
  const renderCatNode = useCallback(
    (c: TapeCategory, depth: number): ReactNode => {
      const kids = childByParent.get(c.category) || [];
      const expanded = expandedCats.has(c.category);
      return (
        <div key={c.category}>
          <div
            className={`kb-tpl-cat-item${depth > 0 ? " kb-tpl-cat-item--child" : ""}${selectedCategory === c.category ? " active" : ""}`}
            onClick={() => selectCategory(c.category)}
            style={{ paddingLeft: 8 + depth * 26, cursor: "pointer" }}
          >
            {kids.length > 0 ? (
              <span
                className="kb-tpl-cat-caret"
                style={{ width: 14, display: "inline-flex", justifyContent: "center", fontSize: 10, color: "#999", marginRight: 2 }}
                onClick={(e) => {
                  e.stopPropagation();
                  setExpandedCats((prev) => {
                    const next = new Set(prev);
                    if (expanded) next.delete(c.category);
                    else next.add(c.category);
                    return next;
                  });
                }}
              >
                {expanded ? "▾" : "▸"}
              </span>
            ) : null}
            {depth >= 2 ? (
              <FileTextOutlined style={{ fontSize: 12 }} />
            ) : (
              <FolderOutlined style={{ fontSize: 14 }} />
            )}
            <Tooltip title={c.descr || undefined}>
              <span className="kb-tpl-cat-name">{c.label || c.category}</span>
            </Tooltip>
            <Tooltip title={`启用 ${c.effective} 个`}>
              <span className="kb-tpl-cat-count">{c.total}</span>
            </Tooltip>
          </div>
          {expanded && kids.map((kid) => renderCatNode(kid, depth + 1))}
        </div>
      );
    },
    [childByParent, expandedCats, selectedCategory, selectCategory],
  );

  const openCreate = () => {
    setEditing(null);
    setFormKey((k) => k + 1);
    form.setFieldsValue({ tape_name: "", tape_label: "", tape_descr: "", tape_type: "general", category: "通用" });
    setDrawerOpen(true);
  };

  const openEdit = (row: TapeItem) => {
    setEditing(row);
    setFormKey((k) => k + 1);
    form.setFieldsValue({
      tape_name: row.tape_name,
      tape_label: row.tape_label,
      tape_descr: row.tape_descr || "",
      tape_type: row.tape_type || "general",
      category: row.category || "通用",
    });
    setDrawerOpen(true);
  };

  const doSave = async () => {
    const v = form.getFieldsValue();
    if (!v.tape_name?.trim()) {
      message.warning("请填写编排名称");
      return;
    }
    setSaving(true);
    try {
      const res = editing ? await apiUpdateTape(editing.id, v) : await apiCreateTape(v);
      if (res.success) {
        message.success(editing ? "已保存" : "已创建");
        setDrawerOpen(false);
        void load();
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  const doPublish = async (row: TapeItem) => {
    const res = await apiPublishTape(row.id);
    if (res.success) {
      message.success("已发布");
      void load();
    } else {
      message.error(res.message || "发布失败");
    }
  };

  const doOffline = async (row: TapeItem) => {
    const res = await apiOfflineTape(row.id);
    if (res.success) {
      message.success("已下线");
      void load();
    } else {
      message.error(res.message || "下线失败");
    }
  };

  /** 打开模板选择弹窗（从模板新建） */
  const openTplPicker = async () => {
    setTplPickerOpen(true);
    setTplLoading(true);
    try {
      const res = await apiListTapeTemplates(1, 100, "", "", "");
      if (res.success) setTplItems(res.data?.items || []);
      else message.error(res.message || "加载模板失败");
    } finally {
      setTplLoading(false);
    }
  };

  /** 应用模板 → 生成新编排草稿 → 在设计器选项卡打开 */
  const doApplyTemplate = async (tpl: TapeTemplateItem) => {
    setTplApplying(tpl.id);
    try {
      const res = await apiApplyTapeTemplate(tpl.id, {
        tape_name: `${tpl.name}_copy`,
        tape_label: tpl.label || tpl.name,
        tape_descr: `由模板「${tpl.label || tpl.name}」应用生成`,
      });
      if (res.success && res.data?.id) {
        message.success(`已生成新编排「${res.data.tape_name}」`);
        setTplPickerOpen(false);
        void load();
        openDesignerTab({
          id: res.data.id,
          tape_name: res.data.tape_name,
          tape_label: res.data.tape_label || res.data.tape_name,
        });
      } else {
        message.error(res.message || "应用模板失败");
      }
    } finally {
      setTplApplying(null);
    }
  };

  const doExecute = async (row: TapeItem, inputs: Record<string, unknown>) => {
    setSubmitting(true);
    try {
      const res = await apiExecuteTape(row.id, inputs);
      if (res.success && res.data?.run_id) {
        await openRunLog(res.data.run_id);
      } else {
        message.error(res.message || "执行失败");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const doDelete = (row: TapeItem) => {
    modal.confirm({
      title: `确认删除编排「${row.tape_name}」？`,
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await apiDeleteTape(row.id);
        if (res.success) {
          message.success("已删除");
          void load();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  // 2026-10-19 分类树形展示：表单分类从平铺下拉改 TreeSelect（一级 + 子级，保留 effective 计数）
  const categoryTreeData = useMemo(() => {
    type CatTreeNode = { value: string; title: string; children?: CatTreeNode[] };
    const tops = categories.filter((c) => !c.parent_id);
    const byParent = new Map<string, TapeCategory[]>();
    categories
      .filter((c) => c.parent_id)
      .forEach((c) => {
        const arr = byParent.get(c.parent_id!) || [];
        arr.push(c);
        byParent.set(c.parent_id!, arr);
      });
    const toNode = (c: TapeCategory): CatTreeNode => ({
      value: c.category,
      title: `${c.label || c.category}${c.effective ? `（${c.effective}）` : ""}`,
      children: (byParent.get(c.category) || []).map(toNode),
    });
    return tops.map(toNode);
  }, [categories]);
  // 左树「全部」计数
  const allTotal = useMemo(() => categories.reduce((acc, c) => acc + (c.total || 0), 0), [categories]);

  const columns: ColumnsType<TapeItem> = [
    {
      title: "编排名称",
      dataIndex: "tape_name",
      width: 200,
      ellipsis: true,
      render: (v: string, row) => (
        <Tooltip title="打开设计器">
          <a onClick={() => openDesignerTab(row)}>{v}</a>
        </Tooltip>
      ),
    },
    { title: "标签", dataIndex: "tape_label", width: 160, ellipsis: true, render: (v?: string) => v || "-" },
    {
      title: "领域分类",
      dataIndex: "category",
      width: 110,
      render: (v?: string) => <Tag>{v || "通用"}</Tag>,
    },
    {
      // 2026-10-15 编排类型标签：摘要（summary）/ 知识构建（kb_build）/ 其他（general）
      title: "编排类型",
      dataIndex: "tape_type",
      width: 100,
      render: (v?: string) => {
        const m = tapeFuncMeta(v);
        return <Tag color={m.color}>{m.label}</Tag>;
      },
    },
    {
      title: "描述",
      dataIndex: "tape_descr",
      ellipsis: true,
      render: (v?: string | null) =>
        v ? (
          <Tooltip title={v}>
            <span>{v}</span>
          </Tooltip>
        ) : (
          "-"
        ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (v: string) => {
        const m = STATUS_META[v] || { color: "default", label: v };
        return <Tag color={m.color}>{m.label}</Tag>;
      },
    },
    { title: "创建人", dataIndex: "create_user", width: 100, render: (v?: string) => (v ? userNames[v] || v : "-") },
    {
      title: "操作",
      key: "act",
      width: 140,
      render: (_, row) => (
        <ModoActionGroup
          maxCount={3}
          actions={[
            { key: "design", label: "设计", icon: <ExperimentOutlined />, onClick: () => openDesignerTab(row) },
            {
              key: "run",
              label: "执行",
              icon: <CheckCircleOutlined />,
              loading: submitting,
              onClick: () => {
                setExecInputTape(row);
                setExecInputOpen(true);
              },
            },
            { key: "edit", label: "编辑", onClick: () => openEdit(row) },
            ...(row.status === "effective"
              ? [{ key: "offline", label: "下线", icon: <PauseCircleOutlined />, onClick: () => doOffline(row) }]
              : [{ key: "publish", label: "发布", icon: <ExportOutlined />, onClick: () => doPublish(row) }]),
            { key: "del", label: "删除", danger: true, onClick: () => doDelete(row) },
          ]}
        />
      ),
    },
  ];

  // 列表区（首个选项卡内容）：左分类树 + 右筛选/表格/分页（2026-10-13 左右结构）
  const listPane = (
    <div className="kb-tpl-root">
      {/* ── 左栏：编排分类树（参照编排模板页 kb-tpl-sidebar） ── */}
      <aside className="kb-tpl-sidebar">
        <div className="kb-tpl-sidebar-header">
          {/* 2026-10-19 「编排分类」→「领域分类」（与列表「领域分类」列、领域概念对齐） */}
          <span className="kb-tpl-sidebar-title">领域分类</span>
        </div>
        <div className="kb-tpl-cat-list">
          <div
            className={`kb-tpl-cat-item${!selectedCategory ? " active" : ""}`}
            onClick={() => selectCategory("")}
          >
            <AppstoreOutlined style={{ fontSize: 14 }} />
            <span className="kb-tpl-cat-name">全部编排</span>
            <span className="kb-tpl-cat-count">{allTotal}</span>
          </div>
          {topCategories.map((c) => renderCatNode(c, 0))}
        </div>
      </aside>

      {/* ── 右栏：筛选 + 表格 + 钉底分页 ── */}
      <div className="kb-tpl-content">
        <Card
          style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
          styles={{
            body: {
              flex: 1,
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
              padding: "12px 16px 0",
            },
          }}
        >
        {/* 筛选表单（对齐用户管理页：查询/重置） */}
        <Form form={searchForm} layout="inline" style={{ marginBottom: 12, flexShrink: 0 }} onFinish={doSearch}>
          <Form.Item name="keyword" label="编排名称">
            <Input allowClear placeholder="名称/标签" style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select
              allowClear
              placeholder="全部"
              style={{ width: 130 }}
              options={[
                { label: "草稿", value: "draft" },
                { label: "已发布", value: "effective" },
                { label: "已下线", value: "offline" },
              ]}
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button onClick={doReset}>重置</Button>
            </Space>
          </Form.Item>
        </Form>

        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={data}
          pagination={false}
          scroll={{ x: "100%", y: "calc(100vh - 252px)" }}
          locale={{ emptyText: <Empty description="暂无编排" /> }}
        />
        <div style={{ flexShrink: 0, marginTop: "auto" }}>
          <ModoPagination
            current={page}
            pageSize={pageSize}
            total={total}
            showTotal={(t) => `共 ${t} 个编排`}
            onChange={(p, ps) => {
              setPage(p);
              setPageSize(ps);
            }}
          />
        </div>
        </Card>
      </div>
    </div>
  );

  return (
    // 2026-10-09: 顶部改为「动态选项卡」（复用用户管理页 ModoTabs editable-card 模式）——首 tab「编排管理」
    // = 页面标题 + 列表（不可关闭），后续内容较多的详情/编辑表单可作为可关闭 tab 打开；本轮先预留能力。
    // 表格高度偏移 264 → 252（tab 导航 44px 取代卡片头 56px）。
    <div
      className="orchestrations-page"
      style={{
        padding: 10,
        height: "calc(100vh - var(--app-header-h, 48px))",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey={activeTab}
        onChange={setActiveTab}
        onEdit={(key, action) => {
          if (action === "remove") closeTab(String(key));
        }}
        tabBarExtraContent={
                  <Space>
                    <Button icon={<FileAddOutlined />} onClick={() => void openTplPicker()}>
                      从模板新建
                    </Button>
                    <Button type="primary" onClick={openCreate}>
                      新建编排
                    </Button>
                  </Space>
                }
        items={[
          { key: "home", label: "编排管理", children: listPane },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: renderDesignerPane(t) })),
        ]}
      />

      <ModoDrawer
        open={drawerOpen}
        title={editing ? `编辑编排 · ${editing.tape_name}` : "新建编排"}
        width={520}
        onCancel={() => setDrawerOpen(false)}
        onOk={() => void doSave()}
        confirmLoading={saving}
      >
        <Form form={form} layout="vertical" key={formKey}>
          <Form.Item name="tape_name" label="编排名称" rules={[{ required: true, message: "请输入编排名称" }]}>
            <ModoInput placeholder="如：数据清洗流水线" />
          </Form.Item>
          <Form.Item name="tape_label" label="标签">
            <ModoInput placeholder="简短描述（可选）" />
          </Form.Item>
          <Form.Item name="tape_type" label="编排类型">
            <ModoSelect
              options={[
                { label: "通用编排", value: "general" },
                { label: "摘要", value: "summary" },
                { label: "知识构建", value: "kb_build" },
                { label: "知识聚合", value: "kb_aggregate" },
                { label: "质量评估", value: "quality" },
                { label: "领域识别", value: "domain_resolve" },
              ]}
            />
          </Form.Item>
          <Form.Item name="category" label="分类">
            <TreeSelect
              allowClear
              showSearch
              treeNodeFilterProp="title"
              placeholder="请选择分类"
              treeData={categoryTreeData}
            />
          </Form.Item>
          <Form.Item name="tape_descr" label="描述">
            <ModoTextArea rows={3} placeholder="编排用途说明" />
          </Form.Item>
        </Form>
      </ModoDrawer>

      {/* 从模板新建：模板选择弹窗（2026-10-12） */}
      <Modal
        title="从模板新建编排"
        open={tplPickerOpen}
        onCancel={() => setTplPickerOpen(false)}
        footer={null}
        width={640}
      >
        <Table
          rowKey="id"
          size="small"
          loading={tplLoading}
          dataSource={tplItems}
          pagination={false}
          scroll={{ y: 360 }}
          locale={{ emptyText: <Empty description="暂无模板，请先在设计器里「存为模板」" /> }}
          columns={[
            {
              title: "模板",
              dataIndex: "label",
              render: (v: string, row: TapeTemplateItem) => (
                <div>
                  <div>{v || row.name}</div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {row.node_count} 节点 / {row.edge_count} 连线 · 应用 {row.apply_count} 次
                  </Text>
                </div>
              ),
            },
            {
              title: "分类",
              dataIndex: "category",
              width: 110,
              render: (v: string) => <Tag>{v || "未分类"}</Tag>,
            },
            {
              title: "描述",
              dataIndex: "descr",
              ellipsis: true,
              render: (v?: string | null) =>
                v ? (
                  <Tooltip title={v}>
                    <span>{v}</span>
                  </Tooltip>
                ) : (
                  "-"
                ),
            },
            {
              title: "操作",
              key: "act",
              width: 90,
              render: (_: unknown, row: TapeTemplateItem) => (
                <Button
                  type="link"
                  size="small"
                  loading={tplApplying === row.id}
                  onClick={() => void doApplyTemplate(row)}
                  style={{ padding: "0 4px" }}
                >
                  应用
                </Button>
              ),
            },
          ]}
        />
      </Modal>

      {/* 执行结果抽屉（异步执行：轮询 runs/{run_id} 展示逐步日志；2026-10-13 Modal → Drawer） */}
      <TapeRunLogDrawer
        open={execOpen}
        run={execRun}
        polling={polling}
        onClose={() => {
          stopPolling();
          setExecOpen(false);
        }}
      />

      {/* 执行入参弹窗：填写初始调用参数后提交执行 */}
      <ExecuteInputModal
        open={execInputOpen}
        tapeName={execInputTape?.tape_name || ""}
        loading={submitting}
        onCancel={() => setExecInputOpen(false)}
        onOk={(inputs) => {
          const row = execInputTape;
          setExecInputOpen(false);
          if (row) void doExecute(row, inputs);
        }}
      />
    </div>
  );
}