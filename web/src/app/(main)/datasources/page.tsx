"use client";

/**
 * 数据源管理页 —— 对齐 data-synth system/datasources（2026-10-06 ds-align）：
 * - Tabs 布局（列表 + 新建/编辑动态 Tab）；团队数据源授权已迁至「系统管理 / 团队管理」页
 * - 列表：三条件筛选（英文名/中文名/类型下拉，数据驱动）
 * - 新建两步向导：选择类型（分类侧栏 + 类型卡片 img/字母头像）→ 信息配置
 * - 编辑直达「信息配置」步（类型只读，无需重选）；dsConf 解析回填动态字段，endpoints 数组转逗号串
 * - 信息配置：水平表单居中(max 720) + 底部固定操作栏（上一步/测试连通性/保存）
 * - 动态字段：required + regex + tooltip + boolean(checked) + radio/checkbox-group + defaultValue 回填
 * - vector_es 类型前端禁用删除按钮（与后端保护一致）
 *
 * 2026-10-14：新建/编辑 tab 内容抽至 DatasourceEditTab（每个 tab 独立 form 实例
 * 与状态），修复「打开新建 tab 后再开编辑 tab，编辑回显污染新建表单」的共享
 * 状态串扰。
 */
import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Empty, Form, Input, Select, Space, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { DatabaseOutlined } from "@ant-design/icons";
import {
  apiDeleteDatasource,
  apiListDatasources,
  apiListDsCategories,
  apiListDsTypes,
  DsCategoryItem,
  DsTypeItem,
  DatasourceItem,
} from "@/lib/api";
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import {
  DatasourceEditTab,
  type DatasourceEditTabInfo,
} from "./datasource-edit-tab";

type EditTab = DatasourceEditTabInfo;

export default function DatasourcesPage() {
  const { message, modal } = App.useApp();
  const [filterForm] = Form.useForm();

  // ---- 列表 ----
  const [rows, setRows] = useState<DatasourceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [filters, setFilters] = useState<{ name?: string; label?: string; dsType?: string }>({});

  // ---- 元数据（类型选择共用只读） ----
  const [categories, setCategories] = useState<DsCategoryItem[]>([]);
  const [allTypes, setAllTypes] = useState<DsTypeItem[]>([]);

  // ---- Tab ----
  const [tabs, setTabs] = useState<EditTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const loadList = useCallback(
    async (p = page, size = pageSize, flt = filters) => {
      setLoading(true);
      try {
        const res = await apiListDatasources(p, size, flt);
        if (res.success) {
          setRows(res.data?.items || []);
          setTotal(res.data?.total || 0);
        }
      } finally {
        setLoading(false);
      }
    },
    [page, pageSize, filters],
  );

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    void (async () => {
      const [c, t] = await Promise.all([apiListDsCategories(), apiListDsTypes()]);
      if (c.success && c.data) setCategories(c.data);
      if (t.success && t.data) setAllTypes(t.data);
    })();
  }, []);

  const openCreate = () => {
    const key = `new-${Date.now()}`;
    setTabs((prev) => [...prev, { key, title: "新建数据源" }]);
    setActiveTab(key);
  };

  const openEdit = (row: DatasourceItem) => {
    const key = `edit-${row.id}`;
    const label = row.dsLabel || row.dsName || row.id || "";
    setTabs((prev) => {
      const filtered = prev.filter((t) => t.key !== key);
      return [...filtered, { key, title: `编辑: ${label}`, dsId: row.id }];
    });
    setActiveTab(key);
  };

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

  const handleDelete = (row: DatasourceItem) => {
    modal.confirm({
      title: "确认删除",
      content: `确定要删除数据源「${row.dsLabel || row.dsName}」吗？`,
      okType: "danger",
      onOk: async () => {
        const res = await apiDeleteDatasource(String(row.id));
        if (res.success) {
          message.success("删除成功");
          void loadList();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  const columns: ColumnsType<DatasourceItem> = [
    { title: "英文名", dataIndex: "dsName", width: 180, ellipsis: true },
    { title: "中文名", dataIndex: "dsLabel", width: 160, ellipsis: true },
    {
      title: "类型",
      dataIndex: "dsType",
      width: 120,
      render: (v: string) => <Tag color="blue">{v || "-"}</Tag>,
    },
    { title: "版本", dataIndex: "dsVersion", width: 100, render: (v: string) => v || "-" },
    {
      title: "状态",
      dataIndex: "state",
      width: 90,
      align: "center",
      render: (v: string) => (
        <Tag color={v === "1" ? "success" : "default"}>{v === "1" ? "生效" : "停用"}</Tag>
      ),
    },
    {
      title: "操作",
      key: "action",
      width: 140,
      fixed: "right",
      render: (_, row) => (
        <ModoActionGroup
          actions={[
            {
              key: "edit",
              label: "编辑",
              onClick: () => openEdit(row),
            },
            {
              key: "delete",
              label: "删除",
              danger: true,
              disabled: row.dsType === "vector_es", // 对齐 ds：向量库数据源前端禁用删除
              onClick: () => handleDelete(row),
            },
          ]}
        />
      ),
    },
  ];

  const doSearch = (vals: { name?: string; label?: string; dsType?: string }) => {
    setFilters(vals);
    setPage(1);
    void loadList(1, pageSize, vals);
  };

  const doReset = () => {
    filterForm.resetFields();
    setFilters({});
    setPage(1);
    void loadList(1, pageSize, {});
  };

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
        {/* 筛选行 + 新增按钮（上方页签已显示「数据源管理」，此处不再重复标题） */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexShrink: 0 }}>
        <Form
          form={filterForm}
          layout="inline"
          style={{ marginBottom: 12, flex: 1 }}
          onFinish={doSearch}
          initialValues={{ name: "", label: "", dsType: undefined }}
        >
          <Form.Item name="name" label="英文名">
            <Input allowClear placeholder="请输入英文名" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="label" label="中文名">
            <Input allowClear placeholder="请输入中文名" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="dsType" label="类型">
            <Select
              allowClear
              showSearch
              placeholder="请选择类型"
              style={{ width: 200 }}
              optionFilterProp="label"
              options={allTypes.map((t) => ({ label: t.dsTypeLabel || t.dsType, value: t.dsType }))}
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
        <Button type="primary" onClick={openCreate} style={{ marginBottom: 12, flexShrink: 0 }}>
          新增数据源
        </Button>
        </div>
        <Table<DatasourceItem>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={rows}
          columns={columns}
          pagination={false}
          scroll={{ x: 900, y: "calc(100vh - 252px)" }}
          locale={{ emptyText: <Empty description="暂无数据源" /> }}
        />
        <div style={{ flexShrink: 0, marginTop: "auto" }}>
          <ModoPagination
            current={page}
            pageSize={pageSize}
            total={total}
            showSizeChanger
            showTotal={(t) => `共 ${t} 条`}
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
      className="datasources-page"
      style={{ padding: 8, height: "calc(100vh - var(--app-header-h, 45px))", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
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
          { key: "home", label: "数据源管理", icon: <DatabaseOutlined />, children: listPane },
          ...tabs.map((t) => ({
            key: t.key,
            label: t.title,
            closable: true,
            children: (
              <DatasourceEditTab
                key={t.key}
                tab={t}
                categories={categories}
                allTypes={allTypes}
                onClose={closeTab}
                onSaved={() => void loadList()}
              />
            ),
          })),
        ]}
      />
    </div>
  );
}