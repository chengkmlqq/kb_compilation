"use client";

/**
 * 编排管理（迁移自 data-synth algorithm/tapes）：
 * Card + inline 筛选（关键词/状态）+ antd Table + ModoPagination；
 * 操作：设计(React Flow 画布跳转) / 编辑 / 发布 / 下线 / 执行(逐步日志弹窗) / 删除。
 */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { App, Button, Card, Empty, Form, Input, Modal, Select, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { CheckCircleOutlined, ExperimentOutlined, ExportOutlined, PauseCircleOutlined } from "@ant-design/icons";
import { ModoInput, ModoTextArea } from "@/components/biz/modo-input";
import { ModoSelect } from "@/components/biz/modo-select";
import { ModoDrawer } from "@/components/biz/modo-drawer";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoPagination } from "@/components/biz/modo-pagination";
import {
  apiCreateTape,
  apiDeleteTape,
  apiExecuteTape,
  apiListTapes,
  apiOfflineTape,
  apiPublishTape,
  apiUpdateTape,
  TapeExecuteResult,
  TapeItem,
} from "@/lib/api";

const { Text } = Typography;

const STATUS_META: Record<string, { color: string; label: string }> = {
  draft: { color: "default", label: "草稿" },
  effective: { color: "green", label: "已发布" },
  offline: { color: "orange", label: "已下线" },
};

export default function OrchestrationsPage() {
  const { message, modal } = App.useApp();
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<TapeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  // 筛选：applied 为「已提交」条件（驱动查询），表单值由 searchForm 托管
  const [applied, setApplied] = useState<{ keyword: string; status?: string }>({
    keyword: "",
    status: undefined,
  });
  const [searchForm] = Form.useForm<{ keyword?: string; status?: string }>();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<TapeItem | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  // 执行结果弹窗
  const [running, setRunning] = useState(false);
  const [execResult, setExecResult] = useState<TapeExecuteResult | null>(null);
  const [execOpen, setExecOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListTapes(page, pageSize, applied.keyword, applied.status ?? "");
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

  useEffect(() => {
    void load();
  }, [load]);

  const doSearch = (v: { keyword?: string; status?: string }) => {
    setPage(1);
    setApplied({ keyword: v.keyword?.trim() || "", status: v.status });
  };

  const doReset = () => {
    searchForm.resetFields();
    setPage(1);
    setApplied({ keyword: "", status: undefined });
  };

  const openCreate = () => {
    setEditing(null);
    setFormKey((k) => k + 1);
    form.setFieldsValue({ tape_name: "", tape_label: "", tape_descr: "", tape_type: "general" });
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

  const doExecute = async (row: TapeItem) => {
    setRunning(true);
    try {
      const res = await apiExecuteTape(row.id, {});
      if (res.success) {
        setExecResult(res.data || null);
        setExecOpen(true);
      } else {
        message.error(res.message || "执行失败");
      }
    } finally {
      setRunning(false);
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

  const columns: ColumnsType<TapeItem> = [
    {
      title: "编排名称",
      dataIndex: "tape_name",
      width: 200,
      ellipsis: true,
      render: (v: string, row) => (
        <Tooltip title="打开设计器">
          <a onClick={() => router.push(`/orchestrations/${row.id}/designer`)}>{v}</a>
        </Tooltip>
      ),
    },
    { title: "标签", dataIndex: "tape_label", width: 160, ellipsis: true, render: (v?: string) => v || "-" },
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
    { title: "创建人", dataIndex: "create_user", width: 100, render: (v?: string) => v || "-" },
    {
      title: "操作",
      key: "act",
      width: 140,
      render: (_, row) => (
        <ModoActionGroup
          actions={[
            { key: "design", label: "设计", icon: <ExperimentOutlined />, onClick: () => router.push(`/orchestrations/${row.id}/designer`) },
            { key: "edit", label: "编辑", onClick: () => openEdit(row) },
            ...(row.status === "effective"
              ? [{ key: "offline", label: "下线", icon: <PauseCircleOutlined />, onClick: () => doOffline(row) }]
              : [{ key: "publish", label: "发布", icon: <ExportOutlined />, onClick: () => doPublish(row) }]),
            { key: "run", label: running ? "执行中…" : "执行", icon: <CheckCircleOutlined />, onClick: () => doExecute(row) },
            { key: "del", label: "删除", danger: true, onClick: () => doDelete(row) },
          ]}
        />
      ),
    },
  ];

  return (
    // 2026-10-09: 参考用户管理页——固定视口高度（外层不滚动），表格 scroll.y 锁定高度让表头固定、表体内部滚动，
    // 分页常驻底栏；筛选改为 inline Form（查询/重置）
    <div
      className="orchestrations-page"
      style={{
        padding: 8,
        height: "calc(100vh - 45px)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <Card
        title="编排管理"
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
            新建编排
          </Button>
        }
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
          scroll={{ x: "100%", y: "calc(100vh - 264px)" }}
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
                { label: "数据合成", value: "data_synthesis" },
              ]}
            />
          </Form.Item>
          <Form.Item name="tape_descr" label="描述">
            <ModoTextArea rows={3} placeholder="编排用途说明" />
          </Form.Item>
        </Form>
      </ModoDrawer>

      {/* 执行结果弹窗 */}
      <Modal
        title={`执行结果 · ${execResult?.tape_name || ""}`}
        open={execOpen}
        onCancel={() => setExecOpen(false)}
        footer={<Button onClick={() => setExecOpen(false)}>关闭</Button>}
        width={680}
      >
        {execResult && (
          <div>
            <Space style={{ marginBottom: 10 }}>
              <Tag color={execResult.success ? "green" : "red"}>{execResult.success ? "全部成功" : "存在失败"}</Tag>
              <Text type="secondary">task_id: {execResult.task_id}</Text>
            </Space>
            <div style={{ maxHeight: 360, overflow: "auto", border: "1px solid #f0f0f0", borderRadius: 8, padding: 8 }}>
              {execResult.steps.map((s, i) => (
                <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "4px 0", borderBottom: "1px dashed #f5f5f5" }}>
                  <Tag color={s.status === "success" ? "green" : "red"} style={{ width: 62, textAlign: "center" }}>
                    {s.status === "success" ? "成功" : "失败"}
                  </Tag>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Text strong style={{ fontSize: 13 }}>
                      [{s.step_inst}] {s.step_label}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
                      {s.duration_ms}ms
                    </Text>
                    {s.error && (
                      <pre style={{ margin: "4px 0 0", fontSize: 12, color: "#cf1322", whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 120, overflow: "auto" }}>
                        {s.error}
                      </pre>
                    )}
                    {s.body !== null && s.body !== undefined && !s.error && (
                      <pre style={{ margin: "4px 0 0", fontSize: 12, color: "#555", whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 160, overflow: "auto" }}>
                        {typeof s.body === "string" ? s.body : JSON.stringify(s.body, null, 2)}
                      </pre>
                    )}
                  </div>
                </div>
              ))}
            </div>
            {Object.keys(execResult.bindings).length > 0 && (
              <div style={{ marginTop: 10 }}>
                <Text strong style={{ fontSize: 12 }}>
                  最终变量
                </Text>
                <pre style={{ margin: "4px 0 0", fontSize: 12, background: "#fafafa", padding: 8, borderRadius: 6, maxHeight: 140, overflow: "auto" }}>
                  {JSON.stringify(execResult.bindings, null, 2)}
                </pre>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}