"use client";

/**
 * 参数管理（2026-10-08 参考用户管理页改造）：
 * 外层 8px padding + Card(标题「参数」/ extra 新建按钮) + inline 筛选表单(参数编码/参数分组/查询/重置)
 * + antd Table 固定视口高度 + ModoPagination 常驻底栏；ModoDrawer 新建/编辑（dimCode 编辑禁用）。
 * 数据走 /api/v1/system/dims（modo_dim 通用 CRUD）。
 */
import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Form, Input, InputNumber, Select, Space, Table, Tooltip } from "antd";
import type { ColumnsType } from "antd/es/table";
import { CheckCircleFilled, CloseCircleFilled } from "@ant-design/icons";
import { ModoInput, ModoTextArea } from "@/components/biz/modo-input";
import { ModoSelect } from "@/components/biz/modo-select";
import { ModoRadio } from "@/components/biz/modo-radio";
import { ModoDrawer } from "@/components/biz/modo-drawer";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoPagination } from "@/components/biz/modo-pagination";
import {
  apiCreateDim,
  apiDeleteDim,
  apiListDimGroups,
  apiListDims,
  apiUpdateDim,
  DimItem,
} from "@/lib/api";

export default function SystemDimsPage() {
  const { message, modal } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<DimItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [filter, setFilter] = useState<{ dimCode?: string; dimGroup?: string }>({});
  const [groupOptions, setGroupOptions] = useState<{ label: string; value: string }[]>([]);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerMode, setDrawerMode] = useState<"create" | "edit">("create");
  const [current, setCurrent] = useState<DimItem | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [saving, setSaving] = useState(false);

  const [searchForm] = Form.useForm();
  const [dimForm] = Form.useForm();

  const load = useCallback(
    async (p = page, size = pageSize, flt = filter) => {
      setLoading(true);
      try {
        const res = await apiListDims(p, size, flt);
        if (res.success) {
          setData(res.data?.items || []);
          setTotal(res.data?.total || 0);
        } else {
          message.error(res.message || "加载参数数据失败");
        }
      } finally {
        setLoading(false);
      }
    },
    [page, pageSize, filter, message],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void apiListDimGroups().then((res) => {
      if (res.success) {
        setGroupOptions((res.data?.items || []).map((g) => ({ label: g, value: g })));
      }
    });
  }, []);

  // ---- 筛选（对齐用户管理页：查询/重置仅驱动状态，load 由 effect 触发） ----
  const handleSearch = (values: { dimCode?: string; dimGroup?: string }) => {
    setFilter(values);
    setPage(1);
  };

  const handleReset = () => {
    setFilter({});
    setPage(1);
  };

  const openCreate = () => {
    setDrawerMode("create");
    setCurrent(null);
    setFormKey((prev) => prev + 1);
    dimForm.resetFields();
    setDrawerOpen(true);
  };

  const openEdit = (record: DimItem) => {
    setDrawerMode("edit");
    setCurrent(record);
    setFormKey((prev) => prev + 1);
    dimForm.setFieldsValue({
      dim_code: record.dim_code,
      dim_group: record.dim_group || undefined,
      dim_value: record.dim_value || "",
      dim_desc: record.dim_desc || "",
      seq: record.seq ?? 0,
      state: record.state || "1",
    });
    setDrawerOpen(true);
  };

  const doDelete = async (id: string) => {
    const res = await apiDeleteDim(id);
    if (res.success) {
      message.success("删除成功");
      void load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const doSave = async () => {
    try {
      const values = await dimForm.validateFields();
      setSaving(true);
      const payload: Partial<DimItem> = {
        dim_code: values.dim_code,
        dim_group: values.dim_group || null,
        dim_value: values.dim_value || null,
        dim_desc: values.dim_desc || null,
        seq: values.seq ?? 0,
        state: values.state || "1",
      };
      const res = current?.id
        ? await apiUpdateDim(current.id, payload)
        : await apiCreateDim(payload);
      if (res.success) {
        message.success(drawerMode === "create" ? "创建成功" : "更新成功");
        setDrawerOpen(false);
        void load();
      } else {
        message.error(res.message || "操作失败");
      }
    } catch {
      /* 校验失败 */
    } finally {
      setSaving(false);
    }
  };

  const columns: ColumnsType<DimItem> = [
    {
      title: "参数编码",
      dataIndex: "dim_code",
      width: 150,
      ellipsis: true,
    },
    {
      title: "参数分组",
      dataIndex: "dim_group",
      width: 150,
      ellipsis: true,
      render: (v: string | null | undefined) => v || "-",
    },
    {
      title: "参数值",
      dataIndex: "dim_value",
      width: 240,
      render: (_, record) => {
        const value = record.dim_value || "-";
        return (
          <Tooltip title={value} mouseEnterDelay={0.3}>
            <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {value}
            </span>
          </Tooltip>
        );
      },
    },
    {
      title: "参数描述",
      dataIndex: "dim_desc",
      width: 200,
      ellipsis: true,
      render: (v: string | null | undefined) => v || "-",
    },
    {
      title: "排序",
      dataIndex: "seq",
      width: 80,
      render: (v: number | null | undefined) => v ?? 0,
    },
    {
      title: "状态",
      dataIndex: "state",
      width: 100,
      render: (state: string | null | undefined) => {
        const isActive = state === "1";
        return (
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              padding: "0 8px",
              height: 22,
              borderRadius: 4,
              fontSize: 12,
              background: isActive ? "#f6ffed" : "#f5f5f5",
              color: isActive ? "#389e0d" : "#8c8c8c",
            }}
          >
            {isActive ? <CheckCircleFilled /> : <CloseCircleFilled />}
            {isActive ? "已生效" : "未生效"}
          </span>
        );
      },
    },
    {
      title: "操作",
      key: "action",
      fixed: "right",
      width: 140,
      render: (_, record) => (
        <ModoActionGroup
          actions={[
            { key: "edit", label: "编辑", onClick: () => openEdit(record) },
            {
              key: "delete",
              label: "删除",
              danger: true,
              onClick: () => {
                modal.confirm({
                  title: "确定删除该项？",
                  onOk: () => void doDelete(String(record.id)),
                });
              },
            },
          ]}
        />
      ),
    },
  ];

  return (
    // 2026-10-08: 参考用户管理页改造 —— 外层 8px padding、Card 标题「参数」、extra 新建按钮、
    // inline 筛选表单、分页常驻底栏
    // 2026-10-08: 表格高度对齐用户管理页「固定视口高度」——高度锁定 视口 − 顶部/筛选/分页偏移
    // （100vh-264px），进页即撑满无底部空白；数据多时表头固定、表体滚动（antd Table scroll.y）
    <div
      className="dims-page"
      style={{ padding: 8, height: "calc(100vh - 45px)", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
    >
      <Card
        title="参数"
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
        extra={
          <Button type="primary" onClick={openCreate}>
            新建参数
          </Button>
        }
      >
        {/* 筛选表单（对齐用户管理页 FilterForm：参数编码 + 参数分组 + 查询/重置） */}
        <Form
          form={searchForm}
          layout="inline"
          colon={false}
          style={{ marginBottom: 12, flexShrink: 0 }}
          onFinish={handleSearch}
        >
          <Form.Item name="dimCode" label="参数编码">
            <Input allowClear placeholder="请输入参数编码" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="dimGroup" label="参数分组">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="请选择参数分组"
              style={{ width: 180 }}
              options={groupOptions}
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button
                onClick={() => {
                  searchForm.resetFields();
                  handleReset();
                }}
              >
                重置
              </Button>
            </Space>
          </Form.Item>
        </Form>

        <Table<DimItem>
          columns={columns}
          dataSource={data}
          rowKey="id"
          loading={loading}
          pagination={false}
          scroll={{ x: "100%", y: "calc(100vh - 264px)" }}
          locale={{ emptyText: "暂无参数" }}
          size="small"
        />

        <div style={{ flexShrink: 0, marginTop: "auto" }}>
          <ModoPagination
            current={page}
            pageSize={pageSize}
            total={total}
            showSizeChanger
            showQuickJumper
            showTotal={(t) => `共 ${t} 条`}
            onChange={(p, ps) => {
              setPage(p);
              setPageSize(ps);
            }}
          />
        </div>
      </Card>

      <ModoDrawer
        title={drawerMode === "create" ? "新建参数" : "编辑参数"}
        open={drawerOpen}
        onCancel={() => setDrawerOpen(false)}
        onOk={() => void doSave()}
        confirmLoading={saving}
        styles={{ wrapper: { width: 500 } }}
      >
        <Form
          key={formKey}
          form={dimForm}
          layout="vertical"
          initialValues={{ state: "1", seq: 0 }}
          autoComplete="off"
          validateTrigger={false}
        >
          <Form.Item
            name="dim_code"
            label="参数编码"
            rules={[{ required: true, message: "请输入参数编码" }]}
          >
            <ModoInput disabled={drawerMode === "edit"} placeholder="请输入参数编码" />
          </Form.Item>

          <Form.Item name="dim_group" label="参数分组">
            <ModoSelect placeholder="请选择参数分组" allowClear showSearch optionFilterProp="label" options={groupOptions} />
          </Form.Item>

          <Form.Item name="dim_value" label="参数值">
            <ModoTextArea placeholder="请输入参数值" rows={4} />
          </Form.Item>

          <Form.Item name="dim_desc" label="参数描述">
            <ModoTextArea placeholder="请输入参数描述" rows={3} />
          </Form.Item>

          <Form.Item name="seq" label="排序">
            <InputNumber style={{ width: "100%" }} placeholder="请输入排序" variant="filled" />
          </Form.Item>

          <Form.Item name="state" label="状态">
            <ModoRadio.Group>
              <ModoRadio value="1">已生效</ModoRadio>
              <ModoRadio value="0">未生效</ModoRadio>
            </ModoRadio.Group>
          </Form.Item>
        </Form>
      </ModoDrawer>
    </div>
  );
}
