"use client";

/**
 * 编排模板管理（2026-10-12）：
 * 模板 = 可复用的编排骨架（nodes/edges/exec_params），支持分类；
 * 「应用」= 复制骨架生成一条新编排（draft），可去设计器继续编辑/执行。
 *
 * 布局对齐 orchestrations/page.tsx：筛选（关键词/分类/状态）+ Table + ModoPagination，
 * 新建/编辑走抽屉表单，分类用 Select 下拉（options 来自 /templates/categories）。
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { ApartmentOutlined, ExperimentOutlined } from "@ant-design/icons";
import { ModoInput, ModoTextArea } from "@/components/biz/modo-input";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoDrawer } from "@/components/biz/modo-drawer";
import { ModoPagination } from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";
import {
  apiApplyTapeTemplate,
  apiCreateTapeTemplate,
  apiDeleteTapeTemplate,
  apiListTapeTemplateCategories,
  apiListTapeTemplates,
  apiUpdateTapeTemplate,
  TapeTemplateCategory,
  TapeTemplateItem,
} from "@/lib/api";

const { Text } = Typography;

const STATUS_META: Record<string, { color: string; label: string }> = {
  effective: { color: "green", label: "启用" },
  disabled: { color: "default", label: "停用" },
};

type TemplateTab = { key: string; title: string; children: ReactNode };

export default function TapeTemplatesPage() {
  const { message, modal } = App.useApp();
  const router = useRouter();
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
  const [searchForm] = Form.useForm<{ keyword?: string; category?: string; status?: string }>();

  const [categories, setCategories] = useState<TapeTemplateCategory[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<TapeTemplateItem | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [saving, setSaving] = useState(false);
  const [applying, setApplying] = useState<string | null>(null);
  const [form] = Form.useForm();

  // 动态选项卡（预留：模板详情/设计预览后续可 push 进来）
  const [tabs, setTabs] = useState<TemplateTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
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
    if (res.success) setCategories(res.data || []);
  }, []);

  useEffect(() => {
    void load();
    void loadCategories();
  }, [load, loadCategories]);

  const doSearch = (v: { keyword?: string; category?: string; status?: string }) => {
    setPage(1);
    setApplied({ keyword: v.keyword?.trim() || "", category: v.category, status: v.status });
  };

  const doReset = () => {
    searchForm.resetFields();
    setPage(1);
    setApplied({ keyword: "", category: undefined, status: undefined });
  };

  const openCreate = () => {
    setEditing(null);
    setFormKey((k) => k + 1);
    form.setFieldsValue({ name: "", label: "", descr: "", category: "通用" });
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
    if (!v.name?.trim()) {
      message.warning("请填写模板名称");
      return;
    }
    setSaving(true);
    try {
      // 新建时前端不碰 nodes/edges（骨架只能由「存为模板」产生）；
      // 编辑走后端 PUT 只更新基础字段，nodes/edges 保持原值。
      const res = editing
        ? await apiUpdateTapeTemplate(editing.id, {
            name: v.name,
            label: v.label || "",
            descr: v.descr || "",
            category: v.category || "通用",
            status: v.status || "effective",
          })
        : await apiCreateTapeTemplate({
            name: v.name,
            label: v.label || "",
            descr: v.descr || "",
            category: v.category || "通用",
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
        <Tooltip title={`${row.name}（${row.node_count} 节点 / ${row.edge_count} 连线）`}>
          <span>{v || row.name}</span>
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
    { title: "创建人", dataIndex: "create_user", width: 90, render: (v?: string) => v || "-" },
    {
      title: "操作",
      key: "act",
      width: 150,
      render: (_, row) => (
        <ModoActionGroup
          actions={[
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

  const listPane = (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
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
        <Form form={searchForm} layout="inline" style={{ marginBottom: 12, flexShrink: 0 }} onFinish={doSearch}>
          <Form.Item name="keyword" label="模板名称">
            <Input allowClear placeholder="名称/标签/描述" style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="category" label="分类">
            <Select allowClear placeholder="全部分类" style={{ width: 170 }} options={categoryOptions} />
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

        <Table
          rowKey="id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={data}
          pagination={false}
          scroll={{ x: "100%", y: "calc(100vh - 252px)" }}
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
  );

  return (
    <div
      className="tape-templates-page"
      style={{
        padding: 8,
        height: "calc(100vh - var(--app-header-h, 45px))",
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
          <Button type="primary" icon={<ApartmentOutlined />} onClick={openCreate}>
            新建模板
          </Button>
        }
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
          <Form.Item label="模板名称" required>
            <ModoInput placeholder="模板显示名" />
          </Form.Item>
          <Form.Item label="内部名（唯一）" required>
            <ModoInput placeholder="如 wiki_build_04" disabled={!!editing} />
          </Form.Item>
          <Form.Item label="分类">
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
          <Form.Item label="状态">
            <Select
              options={[
                { label: "启用", value: "effective" },
                { label: "停用", value: "disabled" },
              ]}
            />
          </Form.Item>
          <Form.Item label="描述">
            <ModoTextArea rows={3} placeholder="模板用途/适用场景说明" />
          </Form.Item>
        </Form>
        <Text type="secondary" style={{ fontSize: 12 }}>
          骨架（节点/连线）由设计器「存为模板」生成；此处仅维护模板基础信息。
        </Text>
      </ModoDrawer>
    </div>
  );
}