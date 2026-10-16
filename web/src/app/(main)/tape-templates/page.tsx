"use client";

/**
 * 编排模板管理（2026-10-12 初版 / 2026-10-13 改造）：
 * 模板 = 可复用的编排骨架（nodes/edges/exec_params），支持分类；
 * 「应用」= 复制骨架生成一条新编排（draft），可去设计器继续编辑/执行。
 *
 * 布局（2026-10-13）：左侧模板分类树 + 右侧模板列表，参考知识集详情
 * （WikiBrowseView）的两栏结构（左 280px 侧栏 border-right / 右内容区）；
 * 分类由左树驱动筛选，右侧保留关键词/状态筛选。
 * 新增「查看」：抽屉展示模板基本信息 + 骨架（节点/连线/执行参数）只读预览。
 * 新增「设计」（2026-10-14）：对齐编排管理，点模板名/设计按钮在动态选项卡里用
 * ReactFlow 只读渲染骨架（TemplateDesignView），点击节点可看该节点配置。
 *
 * 修复（2026-10-13）：编辑表单不回显 —— 此前 Form.Item 全部缺 name 属性，
 * setFieldsValue 无处可挂、getFieldsValue 取不到值（保存必失败）。
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { ReactFlowProvider } from "@xyflow/react";
import {
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  ApartmentOutlined,
  AppstoreOutlined,
  ExperimentOutlined,
  EyeOutlined,
  FileTextOutlined,
  FolderOutlined,
} from "@ant-design/icons";
import { ModoInput, ModoTextArea } from "@/components/biz/modo-input";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoDrawer } from "@/components/biz/modo-drawer";
import { ModoPagination } from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";
import { useUserNames } from "@/lib/use-user-names";
import { TemplateDesignView } from "./_components/template-design-view";
import {
  apiApplyTapeTemplate,
  apiCreateTapeTemplate,
  apiDeleteTapeTemplate,
  apiGetTapeTemplate,
  apiListTapeTemplateCategories,
  apiListTapeTemplates,
  apiUpdateTapeTemplate,
  TapeTemplateCategory,
  TapeTemplateDetail,
  TapeTemplateItem,
} from "@/lib/api";

const { Text } = Typography;

const STATUS_META: Record<string, { color: string; label: string }> = {
  effective: { color: "green", label: "启用" },
  disabled: { color: "default", label: "停用" },
};

type TemplateTab = { key: string; title: string; children: ReactNode };

/** 节点配置摘要（data.config 对象 → 单行文本，超长截断交给列 ellipsis） */
function configSummary(config?: unknown): string {
  if (config == null) return "-";
  if (typeof config === "string") return config || "-";
  const s = JSON.stringify(config);
  return s && s.length > 2 ? s : "-";
}

export default function TapeTemplatesPage() {
  const { message, modal } = App.useApp();
  const router = useRouter();
  // 创建人展示：user_id → 中文用户名映射（拉全量用户一次）
  const userNames = useUserNames();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<TapeTemplateItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [applied, setApplied] = useState<{ keyword: string; category?: string; status?: string }>({
    keyword: "",
    category: undefined,
    status: undefined,
  });
  const [searchForm] = Form.useForm<{ keyword?: string; status?: string }>();

  // 左树：当前选中分类（"" = 全部）
  const [selectedCategory, setSelectedCategory] = useState("");
  const [categories, setCategories] = useState<TapeTemplateCategory[]>([]);
  // 多级树：展开集合（2026-10-15 多级树；默认展开根目录一级，点节点/箭头可展开/收起）
  const [expandedCats, setExpandedCats] = useState<Set<string>>(new Set());

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<TapeTemplateItem | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState<string | null>(null);
  const [form] = Form.useForm();

  // 查看：只读详情抽屉
  const [viewOpen, setViewOpen] = useState(false);
  const [viewing, setViewing] = useState<TapeTemplateDetail | null>(null);
  const [viewLoading, setViewLoading] = useState(false);

  // 动态选项卡：承载「模板设计」只读预览（对齐编排管理）
  const [tabs, setTabs] = useState<TemplateTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

  /**
   * 查看设计（对齐编排管理：动态选项卡 + ReactFlow 只读骨架预览）。
   * 每个 tab 独立 ReactFlowProvider = 独立画布 store，多 tab 互不干扰。
   */
  const openDesignTab = (row: TapeTemplateItem) => {
    const key = `tpl-design-${row.id}`;
    const title = `设计 · ${row.label || row.name}`;
    setTabs((prev) => {
      if (prev.some((t) => t.key === key)) return prev;
      return [
        ...prev,
        {
          key,
          title,
          children: (
            <div style={{ height: "100%", width: "100%", overflow: "hidden" }}>
              <ReactFlowProvider>
                <TemplateDesignView templateId={row.id} onBack={() => closeTab(key)} />
              </ReactFlowProvider>
            </div>
          ),
        },
      ];
    });
    setActiveTab(key);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListTapeTemplates(page, pageSize, applied.keyword, applied.category ?? "", applied.status ?? "");
      if (res.success) {
        setData(res.data?.items || []);
        setTotal(res.data?.total || 0);
      } else {
        message.error(res.message || "加载模板列表失败");
      }
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, applied, message]);

  const loadCategories = useCallback(async () => {
    const res = await apiListTapeTemplateCategories();
    if (res.success) {
      const list = res.data || [];
      setCategories(list);
      // 默认展开根目录这一级：初始只展开一级分类（无 parent_id），二级及以下默认收起
      setExpandedCats(new Set(list.filter((c) => !c.parent_id).map((c) => c.category)));
    }
  }, []);

  useEffect(() => {
    void load();
    void loadCategories();
  }, [load, loadCategories]);

  const doSearch = (v: { keyword?: string; status?: string }) => {
    setPage(1);
    setApplied({ keyword: v.keyword?.trim() || "", category: selectedCategory || undefined, status: v.status });
  };

  const doReset = () => {
    searchForm.resetFields();
    setSelectedCategory("");
    setPage(1);
    setApplied({ keyword: "", category: undefined, status: undefined });
  };

  /** 左树点击分类：仅重置分类维度，保留关键词/状态筛选 */
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

  // 多级树：按 parent_id 分组（一级 = 无父级，逐层挂子级；2026-10-15 对齐编排管理页）
  const topCategories = useMemo(
    () => categories.filter((c) => !c.parent_id),
    [categories],
  );
  const childByParent = useMemo(() => {
    const m = new Map<string, TapeTemplateCategory[]>();
    for (const c of categories) {
      if (!c.parent_id) continue;
      const arr = m.get(c.parent_id) || [];
      arr.push(c);
      m.set(c.parent_id, arr);
    }
    return m;
  }, [categories]);

  /** 递归渲染分类节点（有子级的行带 ▸/▾ 展开箭头；默认展开根目录一级） */
  const renderCatNode = useCallback(
    (c: TapeTemplateCategory, depth: number): ReactNode => {
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
            <span className="kb-tpl-cat-name">{c.label || c.category}</span>
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
    form.setFieldsValue({ name: "", label: "", descr: "", category: "通用", status: "effective" });
    setDrawerOpen(true);
  };

  const openEdit = (row: TapeTemplateItem) => {
    setEditing(row);
    setFormKey((k) => k + 1);
    form.setFieldsValue({
      name: row.name,
      label: row.label,
      descr: row.descr || "",
      category: row.category,
      status: row.status,
    });
    setDrawerOpen(true);
  };

  const doSave = async () => {
    const v = form.getFieldsValue();
    if (!v.label?.trim()) {
      message.warning("请填写模板名称");
      return;
    }
    if (!v.name?.trim()) {
      message.warning("请填写内部名");
      return;
    }
    setSaving(true);
    try {
      // 新建时前端不碰 nodes/edges（骨架只能由「存为模板」产生）；
      // 编辑走后端 PUT 只更新基础字段，nodes/edges 保持原值。
      const res = editing
        ? await apiUpdateTapeTemplate(editing.id, {
            name: v.name.trim(),
            label: v.label.trim(),
            descr: v.descr || "",
            category: v.category || "通用",
            status: v.status || "effective",
          })
        : await apiCreateTapeTemplate({
            name: v.name.trim(),
            label: v.label.trim(),
            descr: v.descr || "",
            category: v.category || "通用",
            status: v.status || "effective",
            nodes: [],
            edges: [],
            exec_params: {},
          });
      if (res.success) {
        message.success(editing ? "已保存" : "已创建");
        setDrawerOpen(false);
        void load();
        void loadCategories();
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  const openView = async (row: TapeTemplateItem) => {
    setViewing(row as TapeTemplateDetail);
    setViewOpen(true);
    setViewLoading(true);
    try {
      const res = await apiGetTapeTemplate(row.id);
      if (res.success && res.data) {
        setViewing(res.data);
      } else {
        message.error(res.message || "加载模板详情失败");
        setViewOpen(false);
      }
    } finally {
      setViewLoading(false);
    }
  };

  const doApply = async (row: TapeTemplateItem) => {
    setApplying(row.id);
    try {
      const res = await apiApplyTapeTemplate(row.id, {
        tape_name: `${row.name}_copy`,
        tape_label: row.label || row.name,
        tape_descr: `由模板「${row.label || row.name}」应用生成`,
      });
      if (res.success && res.data?.id) {
        message.success(`已生成新编排「${res.data.tape_name}」，去设计器查看`);
        setViewOpen(false);
        void load();
        router.push(`/orchestrations/${res.data.id}/designer`);
      } else {
        message.error(res.message || "应用模板失败");
      }
    } finally {
      setApplying(null);
    }
  };

  const doDelete = (row: TapeTemplateItem) => {
    modal.confirm({
      title: `确认删除模板「${row.label || row.name}」？`,
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await apiDeleteTapeTemplate(row.id);
        if (res.success) {
          message.success("已删除");
          void load();
          void loadCategories();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  const columns: ColumnsType<TapeTemplateItem> = [
    {
      title: "模板名称",
      dataIndex: "label",
      width: 190,
      ellipsis: true,
      render: (v: string, row) => (
        <Tooltip title={`查看设计｜${row.name}（${row.node_count} 节点 / ${row.edge_count} 连线）`}>
          <span style={{ cursor: "pointer", color: "var(--color-primary, #3261ce)" }} onClick={() => openDesignTab(row)}>
            {v || row.name}
          </span>
        </Tooltip>
      ),
    },
    { title: "内部名", dataIndex: "name", width: 160, ellipsis: true, render: (v: string) => <Text type="secondary">{v}</Text> },
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
      title: "节点",
      dataIndex: "node_count",
      width: 64,
      align: "center" as const,
      render: (v: number) => v ?? 0,
    },
    {
      title: "应用次数",
      dataIndex: "apply_count",
      width: 84,
      align: "center" as const,
      render: (v: number) => v ?? 0,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 80,
      render: (v: string) => {
        const m = STATUS_META[v] || { color: "default", label: v };
        return <Tag color={m.color}>{m.label}</Tag>;
      },
    },
    { title: "创建人", dataIndex: "create_user", width: 90, render: (v?: string) => (v ? userNames[v] || v : "-") },
    {
      title: "操作",
      key: "act",
      width: 190,
      render: (_, row) => (
        <ModoActionGroup
          actions={[
            { key: "design", label: "设计", icon: <ApartmentOutlined />, onClick: () => openDesignTab(row) },
            { key: "view", label: "查看", icon: <EyeOutlined />, onClick: () => void openView(row) },
            { key: "apply", label: "应用", icon: <ExperimentOutlined />, loading: applying === row.id, onClick: () => doApply(row) },
            { key: "edit", label: "编辑", onClick: () => openEdit(row) },
            ...(row.is_builtin
              ? []
              : [{ key: "del", label: "删除", danger: true, onClick: () => doDelete(row) }]),
          ]}
        />
      ),
    },
  ];

  const categoryOptions = categories.map((c) => ({ value: c.category, label: `${c.category}（${c.effective}）` }));

  // ── 左树数据 ──
  const allTotal = useMemo(() => categories.reduce((acc, c) => acc + (c.total || 0), 0), [categories]);

  // ── 查看抽屉：骨架节点/连线 ──
  const nodeIdToLabel = useMemo(() => {
    const m: Record<string, string> = {};
    for (const n of viewing?.nodes || []) {
      m[n?.id] = n?.data?.label || n?.id;
    }
    return m;
  }, [viewing]);

  const viewNodeColumns: ColumnsType<Record<string, any>> = [
    { title: "#", key: "idx", width: 44, render: (_, __, i) => i + 1 },
    { title: "节点名称", dataIndex: ["data", "label"], ellipsis: true, render: (v?: string, n?: Record<string, any>) => v || n?.id || "-" },
    { title: "步骤类型", dataIndex: ["data", "stepInst"], width: 150, ellipsis: true, render: (v?: string) => v || "-" },
    { title: "分组", dataIndex: ["data", "group"], width: 110, ellipsis: true, render: (v?: string) => v || "-" },
    {
      title: "配置",
      key: "config",
      ellipsis: true,
      render: (_, n) => <Tooltip title={configSummary(n?.data?.config)}><span>{configSummary(n?.data?.config)}</span></Tooltip>,
    },
  ];

  const viewEdgeColumns: ColumnsType<Record<string, any>> = [
    { title: "#", key: "idx", width: 44, render: (_, __, i) => i + 1 },
    { title: "源节点", dataIndex: "source", width: 220, ellipsis: true, render: (v?: string) => nodeIdToLabel[v || ""] || v || "-" },
    { title: "目标节点", dataIndex: "target", width: 220, ellipsis: true, render: (v?: string) => nodeIdToLabel[v || ""] || v || "-" },
    { title: "连线 ID", dataIndex: "id", ellipsis: true, render: (v?: string) => <Text type="secondary">{v || "-"}</Text> },
  ];

  const listPane = (
    <div className="kb-tpl-root">
      {/* ── 左栏：模板分类树（参考知识集详情 wiki-sidebar 两栏结构） ── */}
      <aside className="kb-tpl-sidebar">
        <div className="kb-tpl-sidebar-header">
          <span className="kb-tpl-sidebar-title">模板分类</span>
        </div>
        <div className="kb-tpl-cat-list">
          <div
            className={`kb-tpl-cat-item${!selectedCategory ? " active" : ""}`}
            onClick={() => selectCategory("")}
          >
            <AppstoreOutlined style={{ fontSize: 14 }} />
            <span className="kb-tpl-cat-name">全部模板</span>
            <span className="kb-tpl-cat-count">{allTotal}</span>
          </div>
          {topCategories.map((c) => renderCatNode(c, 0))}
        </div>
      </aside>

      {/* ── 右栏：模板列表 ── */}
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
          <div className="kb-tpl-toolbar">
            <Form form={searchForm} layout="inline" onFinish={doSearch}>
              <Form.Item name="keyword" label="模板名称">
                <Input allowClear placeholder="名称/标签/描述" style={{ width: 200 }} />
              </Form.Item>
              <Form.Item name="status" label="状态">
                <Select
                  allowClear
                  placeholder="全部"
                  style={{ width: 110 }}
                  options={[
                    { label: "启用", value: "effective" },
                    { label: "停用", value: "disabled" },
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
            <span style={{ flex: 1 }} />
            <Button type="primary" icon={<ApartmentOutlined />} onClick={openCreate}>
              新建模板
            </Button>
          </div>

          <Table
            rowKey="id"
            size="small"
            loading={loading}
            columns={columns}
            dataSource={data}
            pagination={false}
            scroll={{ x: "100%", y: "calc(100vh - 300px)" }}
            locale={{ emptyText: <Empty description="暂无编排模板" /> }}
          />
          <div style={{ flexShrink: 0, marginTop: "auto" }}>
            <ModoPagination
              current={page}
              pageSize={pageSize}
              total={total}
              showTotal={(t) => `共 ${t} 个模板`}
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
    <div
      className="tape-templates-page"
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
        items={[
          { key: "home", label: "编排模板", children: listPane },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: t.children })),
        ]}
      />

      <ModoDrawer
        title={editing ? "编辑模板" : "新建模板"}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={460}
        footer={
          <Space>
            <Button onClick={() => setDrawerOpen(false)}>取消</Button>
            <Button type="primary" loading={saving} onClick={() => void doSave()}>
              保存
            </Button>
          </Space>
        }
      >
        <Form key={formKey} form={form} layout="vertical">
          <Form.Item
            name="label"
            label="模板名称"
            rules={[{ required: true, message: "请输入模板名称" }]}
          >
            <ModoInput placeholder="模板显示名" />
          </Form.Item>
          <Form.Item
            name="name"
            label="内部名（唯一）"
            rules={[{ required: true, message: "请输入内部名" }]}
          >
            <ModoInput placeholder="如 wiki_build_04" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="category" label="分类">
            <Select
              showSearch
              placeholder="选择或输入新分类"
              options={categoryOptions}
              optionFilterProp="label"
              onSearch={(v) => {
                if (v && !categoryOptions.some((o) => o.value === v)) {
                  form.setFieldsValue({ category: v });
                }
              }}
            />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select
              options={[
                { label: "启用", value: "effective" },
                { label: "停用", value: "disabled" },
              ]}
            />
          </Form.Item>
          <Form.Item name="descr" label="描述">
            <ModoTextArea rows={3} placeholder="模板用途/适用场景说明" />
          </Form.Item>
        </Form>
        <Text type="secondary" style={{ fontSize: 12 }}>
          骨架（节点/连线）由设计器「存为模板」生成；此处仅维护模板基础信息。
        </Text>
      </ModoDrawer>

      {/* 查看：只读模板详情 + 骨架预览 */}
      <ModoDrawer
        title={`查看模板「${viewing?.label || viewing?.name || ""}」`}
        open={viewOpen}
        onClose={() => setViewOpen(false)}
        width={720}
        footer={
          <Space>
            <Button onClick={() => setViewOpen(false)}>关闭</Button>
            {viewing && (
              <Button type="primary" icon={<ExperimentOutlined />} loading={applying === viewing.id} onClick={() => void doApply(viewing)}>
                应用模板
              </Button>
            )}
          </Space>
        }
      >
        {viewLoading ? (
          <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
            <Spin />
          </div>
        ) : viewing ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <Descriptions
              size="small"
              bordered
              column={2}
              items={[
                { key: "label", label: "模板名称", children: viewing.label || viewing.name },
                { key: "name", label: "内部名", children: <Text type="secondary">{viewing.name}</Text> },
                { key: "category", label: "分类", children: <Tag>{viewing.category || "未分类"}</Tag> },
                {
                  key: "status",
                  label: "状态",
                  children: (() => {
                    const m = STATUS_META[viewing.status] || { color: "default", label: viewing.status };
                    return <Tag color={m.color}>{m.label}</Tag>;
                  })(),
                },
                { key: "nodes", label: "节点数", children: viewing.node_count ?? 0 },
                { key: "edges", label: "连线数", children: viewing.edge_count ?? 0 },
                { key: "apply", label: "应用次数", children: viewing.apply_count ?? 0 },
                { key: "creator", label: "创建人", children: (viewing.create_user ? userNames[viewing.create_user] || viewing.create_user : "-") },
                { key: "created", label: "创建时间", children: viewing.created_at || "-" },
                { key: "updated", label: "更新时间", children: viewing.updated_at || "-" },
                { key: "descr", label: "描述", span: 2, children: viewing.descr || "-" },
              ]}
            />

            <div>
              <Typography.Title level={5} style={{ marginBottom: 8, fontSize: 14 }}>
                骨架节点（{viewing.nodes?.length ?? 0}）
              </Typography.Title>
              <Table
                rowKey={(n) => n?.id || Math.random().toString(36)}
                size="small"
                columns={viewNodeColumns}
                dataSource={viewing.nodes || []}
                pagination={false}
                scroll={{ x: "100%", y: 220 }}
                locale={{ emptyText: <Empty description="该模板暂无节点" /> }}
              />
            </div>

            <div>
              <Typography.Title level={5} style={{ marginBottom: 8, fontSize: 14 }}>
                骨架连线（{viewing.edges?.length ?? 0}）
              </Typography.Title>
              <Table
                rowKey={(e) => e?.id || Math.random().toString(36)}
                size="small"
                columns={viewEdgeColumns}
                dataSource={viewing.edges || []}
                pagination={false}
                scroll={{ x: "100%", y: 180 }}
                locale={{ emptyText: <Empty description="该模板暂无连线" /> }}
              />
            </div>

            {viewing.exec_params && Object.keys(viewing.exec_params).length > 0 && (
              <div>
                <Typography.Title level={5} style={{ marginBottom: 8, fontSize: 14 }}>
                  执行参数
                </Typography.Title>
                <pre className="kb-tpl-exec-params">
                  {JSON.stringify(viewing.exec_params, null, 2)}
                </pre>
              </div>
            )}
          </div>
        ) : null}
      </ModoDrawer>
    </div>
  );
}
