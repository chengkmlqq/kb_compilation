"use client";

/**
 * 编排组件管理（迁移自 data-synth algorithm/steps）：
 * Card + inline 筛选（关键词/分组）+ antd Table + ModoPagination + ModoDrawer 新建/编辑。
 * step_cfg 为 dynamic-form FormField[] JSON schema（textarea 编辑）。
 */
import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Empty, Form, Input, InputNumber, Select, Space, Table, Tag, Tooltip } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ModoInput, ModoTextArea } from "@/components/biz/modo-input";
import { ModoSelect } from "@/components/biz/modo-select";
import { ModoRadio } from "@/components/biz/modo-radio";
import { ModoDrawer } from "@/components/biz/modo-drawer";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoPagination } from "@/components/biz/modo-pagination";
import {
  apiCreateStepDefine,
  apiDeleteStepDefine,
  apiListStepDefines,
  apiUpdateStepDefine,
  StepDefineItem,
} from "@/lib/api";

const INST_OPTIONS = [
  { label: "变量定义(def)", value: "def" },
  { label: "脚本执行(script)", value: "script" },
  { label: "日志输出(print)", value: "print" },
  { label: "条件分支(if)", value: "if" },
  { label: "循环(loop)", value: "loop" },
];

const GROUP_OPTIONS = [
  { label: "基础", value: "基础" },
  { label: "流程控制", value: "流程控制" },
];

export default function OrchestrationStepsPage() {
  const { message, modal } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<StepDefineItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  // 筛选：applied 为「已提交」条件（驱动查询），表单值由 searchForm 托管
  const [applied, setApplied] = useState<{ keyword: string; groupType?: string }>({
    keyword: "",
    groupType: undefined,
  });
  const [searchForm] = Form.useForm<{ keyword?: string; groupType?: string }>();

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<StepDefineItem | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListStepDefines(page, pageSize, applied.keyword, applied.groupType ?? "");
      if (res.success) {
        setData(res.data?.items || []);
        setTotal(res.data?.total || 0);
      } else {
        message.error(res.message || "加载组件定义失败");
      }
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, applied, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const doSearch = (v: { keyword?: string; groupType?: string }) => {
    setPage(1);
    setApplied({ keyword: v.keyword?.trim() || "", groupType: v.groupType });
  };

  const doReset = () => {
    searchForm.resetFields();
    setPage(1);
    setApplied({ keyword: "", groupType: undefined });
  };

  const openCreate = () => {
    setEditing(null);
    setFormKey((k) => k + 1);
    form.setFieldsValue({
      step_inst: "def",
      group_type: "基础",
      step_seq: 10,
      status: "effective",
      step_cfg: [{ name: "", label: "", type: "input" }],
    });
    setDrawerOpen(true);
  };

  const openEdit = (row: StepDefineItem) => {
    setEditing(row);
    setFormKey((k) => k + 1);
    form.setFieldsValue({
      step_inst: row.step_inst,
      group_type: row.group_type,
      step_label: row.step_label,
      step_icon: row.step_icon,
      step_desc: row.step_desc,
      step_seq: row.step_seq,
      status: row.status,
      step_cfg: JSON.stringify(row.step_cfg || [], null, 2),
    });
    setDrawerOpen(true);
  };

  const doSave = async () => {
    const v = form.getFieldsValue();
    let cfg: unknown[] = [];
    if (String(v.step_cfg || "").trim()) {
      try {
        cfg = JSON.parse(String(v.step_cfg));
        if (!Array.isArray(cfg)) throw new Error("不是数组");
      } catch {
        message.error("配置表单 schema 必须是合法 JSON 数组，如 [{\"name\":\"code\",\"label\":\"代码\",\"type\":\"textarea\"}]");
        return;
      }
    }
    setSaving(true);
    try {
      const payload = {
        step_inst: v.step_inst,
        group_type: v.group_type,
        step_label: v.step_label,
        step_icon: v.step_icon || null,
        step_desc: v.step_desc || null,
        step_seq: v.step_seq || 0,
        status: v.status,
        step_cfg: cfg,
      };
      const res = editing ? await apiUpdateStepDefine(editing.id, payload) : await apiCreateStepDefine(payload);
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

  const doDelete = (row: StepDefineItem) => {
    modal.confirm({
      title: `确认删除组件「${row.step_label}」？`,
      content: "已被编排引用的组件无法删除。",
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await apiDeleteStepDefine(row.id);
        if (res.success) {
          message.success("已删除");
          void load();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  const columns: ColumnsType<StepDefineItem> = [
    { title: "组件名称", dataIndex: "step_label", width: 160, ellipsis: true },
    { title: "指令", dataIndex: "step_inst", width: 100, render: (v: string) => <code>{v}</code> },
    { title: "分组", dataIndex: "group_type", width: 110 },
    { title: "排序", dataIndex: "step_seq", width: 70 },
    {
      title: "状态",
      dataIndex: "status",
      width: 90,
      render: (v: string) => <Tag color={v === "effective" ? "green" : "default"}>{v === "effective" ? "已生效" : "已停用"}</Tag>,
    },
    {
      title: "描述",
      dataIndex: "step_desc",
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
      width: 120,
      render: (_, row) => (
        <ModoActionGroup
          actions={[
            { key: "edit", label: "编辑", onClick: () => openEdit(row) },
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
      className="steps-page"
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
        title="编排组件"
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
            新建组件
          </Button>
        }
      >
        {/* 筛选表单（对齐用户管理页：查询/重置） */}
        <Form form={searchForm} layout="inline" style={{ marginBottom: 12, flexShrink: 0 }} onFinish={doSearch}>
          <Form.Item name="keyword" label="组件名称">
            <Input allowClear placeholder="名称/指令" style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="groupType" label="分组">
            <Select allowClear placeholder="全部" style={{ width: 140 }} options={GROUP_OPTIONS} />
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
          locale={{ emptyText: <Empty description="暂无组件" /> }}
        />
        <div style={{ flexShrink: 0, marginTop: "auto" }}>
          <ModoPagination
            current={page}
            pageSize={pageSize}
            total={total}
            showTotal={(t) => `共 ${t} 个组件`}
            onChange={(p, ps) => {
              setPage(p);
              setPageSize(ps);
            }}
          />
        </div>
      </Card>

      <ModoDrawer
        open={drawerOpen}
        title={editing ? `编辑组件 · ${editing.step_label}` : "新建组件"}
        width={560}
        onCancel={() => setDrawerOpen(false)}
        onOk={() => void doSave()}
        confirmLoading={saving}
      >
        <Form form={form} layout="vertical" key={formKey}>
          <Space size={16} style={{ width: "100%" }} align="start">
            <Form.Item name="step_inst" label="组件指令" style={{ width: 200 }} rules={[{ required: true }]}>
              <ModoSelect options={INST_OPTIONS} disabled={!!editing} placeholder="选择指令" />
            </Form.Item>
            <Form.Item name="group_type" label="分组" style={{ width: 160 }} rules={[{ required: true }]}>
              <ModoSelect options={GROUP_OPTIONS} />
            </Form.Item>
            <Form.Item name="step_seq" label="排序" style={{ width: 120 }}>
              <InputNumber style={{ width: "100%" }} min={0} />
            </Form.Item>
          </Space>
          <Form.Item name="step_label" label="组件名称" rules={[{ required: true, message: "请输入组件名称" }]}>
            <ModoInput placeholder="如：变量定义" />
          </Form.Item>
          <Form.Item name="step_icon" label="图标名称（antd icon 名）">
            <ModoInput placeholder="如 CodeOutlined" />
          </Form.Item>
          <Form.Item name="step_desc" label="描述">
            <ModoTextArea rows={2} placeholder="组件用途说明" />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <ModoRadio.Group>
              <ModoRadio value="effective">已生效</ModoRadio>
              <ModoRadio value="disabled">已停用</ModoRadio>
            </ModoRadio.Group>
          </Form.Item>
          <Form.Item
            name="step_cfg"
            label="配置表单 schema（dynamic-form FormField[] JSON）"
            extra='如：[{"name":"code","label":"Python 代码","type":"textarea","required":true}]；type 支持 input/select/number/radio/slider/switch/textarea'
          >
            <ModoTextArea rows={6} />
          </Form.Item>
        </Form>
      </ModoDrawer>
    </div>
  );
}