"use client";

/**
 * 任务管理页 —— 定时任务（modo_cron_task）配置管理，对齐 data-synth cron：
 * 列表（搜索/分页）+ 新建/编辑（英文名/中文名/Cron表达式/任务类/队列/状态/扩展参数）
 * + 启停 + 删除。数据源 /api/v1/cron（list/save/delete/toggle/queues/registered-tasks）。
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  App,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Radio,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { ReloadOutlined } from "@ant-design/icons";
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoTabs } from "@/components/biz/modo-tabs";
import {
  apiCreateCronTask,
  apiCronQueues,
  apiCronRegisteredTasks,
  apiDeleteCronTask,
  apiListCronTasks,
  apiToggleCronTask,
  apiUpdateCronTask,
  CronTaskItem,
  CronTaskSavePayload,
} from "@/lib/api";

/** 动态选项卡条目（复用用户管理页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务内容 */
type CronTab = { key: string; title: string; children: ReactNode };

export default function CronPage() {
  const { message, modal } = App.useApp();
  const [searchForm] = Form.useForm();
  const [drawerForm] = Form.useForm();

  const [items, setItems] = useState<CronTaskItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [applied, setApplied] = useState<{ keyword?: string }>({});

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const [queues, setQueues] = useState<Array<{ queueName: string; queueLabel: string | null }>>([]);
  const [registeredTasks, setRegisteredTasks] = useState<Array<{ taskClass: string; name: string }>>([]);

  const selectedTaskClass = Form.useWatch("taskClass", drawerForm) as string | undefined;

  // ---- 动态选项卡（复用用户管理页 ModoTabs 模式）----
  // 首个 tab「定时任务」= 页面标题 + 列表，不可关闭；后续内容较多的详情/编辑表单可 push 进 tabs 以选项卡打开。
  // 本轮先预留能力（容器 + 开关 tab 机制就位），具体接入哪些内容后续再定。
  const [tabs, setTabs] = useState<CronTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

  const load = useCallback(
    async (p = page, size = pageSize, kw = applied.keyword) => {
      setLoading(true);
      try {
        const res = await apiListCronTasks({
          pageNum: p,
          pageSize: size,
          keyWord: kw || undefined,
        });
        if (res.success && res.data) {
          setItems(res.data.content);
          setTotal(res.data.totalElements);
        } else {
          message.error(res.message || "加载定时任务失败");
        }
      } finally {
        setLoading(false);
      }
    },
    [page, pageSize, applied, message],
  );

  const loadMeta = useCallback(async () => {
    const [q, t] = await Promise.all([apiCronQueues(), apiCronRegisteredTasks()]);
    if (q.success && q.data) setQueues(q.data);
    if (t.success && t.data) setRegisteredTasks(t.data);
  }, []);

  useEffect(() => {
    void load();
    void loadMeta();
  }, [load, loadMeta]);

  const handleSearch = (values: { keyword?: string }) => {
    setApplied({ keyword: (values.keyword ?? "").trim() || undefined });
    setPage(1);
  };

  const handleReset = () => {
    searchForm.resetFields();
    setApplied({});
    setPage(1);
  };

  const openCreate = () => {
    setEditingId(null);
    drawerForm.resetFields();
    drawerForm.setFieldsValue({ state: "1", cronExpression: "* * * * *" });
    setDrawerOpen(true);
  };

  const openEdit = (record: CronTaskItem) => {
    setEditingId(record.id);
    drawerForm.setFieldsValue({
      name: record.name || "",
      label: record.label || "",
      cronExpression: record.cronExpression || "* * * * *",
      taskClass: record.taskClass || undefined,
      queueName: record.queueName || undefined,
      state: record.state || "1",
      fireParams: record.fireParams || undefined,
    });
    setDrawerOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await drawerForm.validateFields();
      setDrawerLoading(true);
      const payload: CronTaskSavePayload = {
        name: values.name,
        label: values.label,
        cronExpression: values.cronExpression,
        taskClass: values.taskClass,
        state: values.state || "1",
        fireParams: values.fireParams,
        queueName: values.queueName,
      };
      const res = editingId
        ? await apiUpdateCronTask(editingId, payload)
        : await apiCreateCronTask(payload);
      if (res.success) {
        message.success(editingId ? "更新成功" : "创建成功");
        setDrawerOpen(false);
        void load();
      } else {
        message.error(res.message || "保存失败");
      }
    } catch {
      // validation failed
    } finally {
      setDrawerLoading(false);
    }
  };

  const handleToggle = async (record: CronTaskItem) => {
    const next = record.state === "1" ? "0" : "1";
    const res = await apiToggleCronTask(record.id, next as "0" | "1");
    if (res.success) {
      message.success(next === "1" ? "任务已启用" : "任务已禁用");
      void load();
    } else {
      message.error(res.message || "操作失败");
    }
  };

  const handleDelete = (record: CronTaskItem) => {
    modal.confirm({
      title: "确认删除",
      content: `确定要删除定时任务「${record.label || record.name}」吗？`,
      okType: "danger",
      onOk: async () => {
        const res = await apiDeleteCronTask(record.id);
        if (res.success) {
          message.success("删除成功");
          void load();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  const columns: ColumnsType<CronTaskItem> = [
    {
      title: "英文名",
      dataIndex: "name",
      width: 180,
      ellipsis: true,
    },
    {
      title: "中文名",
      dataIndex: "label",
      width: 180,
      ellipsis: true,
    },
    {
      title: "Cron表达式",
      dataIndex: "cronExpression",
      width: 150,
      render: (text) => <Tag>{text}</Tag>,
    },
    {
      title: "任务类",
      dataIndex: "taskClass",
      width: 190,
      ellipsis: true,
    },
    {
      title: "执行队列",
      dataIndex: "queueName",
      width: 120,
      render: (text) => {
        const q = queues.find((x) => x.queueName === text);
        return q ? q.queueLabel || q.queueName : text || "-";
      },
    },
    {
      title: "状态",
      dataIndex: "state",
      width: 80,
      align: "center" as const,
      render: (state) => (
        <Tag color={state === "1" ? "success" : "error"}>
          {state === "1" ? "生效" : "失效"}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "action",
      width: 160,
      align: "center" as const,
      fixed: "right" as const,
      render: (_, record) => (
        <ModoActionGroup
          maxCount={2}
          actions={[
            { key: "edit", label: "编辑", onClick: () => openEdit(record) },
            {
              key: "toggle",
              label: record.state === "1" ? "禁用" : "启用",
              onClick: () => handleToggle(record),
            },
            { key: "delete", label: "删除", danger: true, onClick: () => handleDelete(record) },
          ]}
        />
      ),
    },
  ];

  // 列表区（首个选项卡内容）：筛选 + 表格 + 钉底分页
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
        <Form
          form={searchForm}
          layout="inline"
          style={{ marginBottom: 12, flexShrink: 0 }}
          onFinish={handleSearch}
        >
          <Form.Item name="keyword" label="任务名称">
            <Input allowClear placeholder="请输入任务名称" style={{ width: 220 }} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button onClick={handleReset}>重置</Button>
            </Space>
          </Form.Item>
        </Form>

        <Table
          columns={columns}
          dataSource={items}
          rowKey="id"
          loading={loading}
          size="small"
          pagination={false}
          scroll={{ x: 1100, y: "calc(100vh - 252px)" }}
        />

        <div style={{ flexShrink: 0, marginTop: "auto" }}>
          <ModoPagination
            current={page}
            pageSize={pageSize}
            total={total}
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
    // 2026-10-09: 顶部改为「动态选项卡」（复用用户管理页 ModoTabs editable-card 模式）——首 tab「定时任务」
    // = 页面标题 + 列表（不可关闭），后续内容较多的详情/编辑表单可作为可关闭 tab 打开；本轮先预留能力。
    // 表格高度偏移 264 → 252（tab 导航 44px 取代卡片头 56px）。
    <div
      className="cron-page"
      style={{ padding: 8, height: "calc(100vh - 45px)", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
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
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            <Button type="primary" onClick={openCreate}>
              新建任务
            </Button>
          </Space>
        }
        items={[
          { key: "home", label: "定时任务", children: listPane },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: t.children })),
        ]}
      />

      <Drawer
        title={editingId ? "编辑任务" : "新建任务"}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={480}
        extra={
          <Space>
            <Button onClick={() => setDrawerOpen(false)}>取消</Button>
            <Button type="primary" loading={drawerLoading} onClick={handleSave}>
              保存
            </Button>
          </Space>
        }
      >
        <Form form={drawerForm} layout="vertical">
          <Form.Item
            name="name"
            label="英文名"
            rules={[{ required: true, message: "请输入英文名" }]}
          >
            <Input placeholder="请输入任务英文标识" />
          </Form.Item>
          <Form.Item
            name="label"
            label="中文名"
            rules={[{ required: true, message: "请输入中文名" }]}
          >
            <Input placeholder="请输入任务中文名称" />
          </Form.Item>
          <Form.Item
            name="cronExpression"
            label="Cron表达式"
            rules={[{ required: true, message: "请输入Cron表达式" }]}
            initialValue="* * * * *"
            extra="五段式 cron，例如：0 */30 * * * ?（每30分钟）"
          >
            <Input placeholder="* * * * *" />
          </Form.Item>
          <Form.Item
            name="taskClass"
            label="任务类"
            rules={[{ required: true, message: "请选择任务类" }]}
          >
            <Select
              placeholder="选择任务类型"
              showSearch
              optionFilterProp="label"
              options={registeredTasks.map((t) => ({
                value: t.taskClass,
                label: t.taskClass,
              }))}
            />
          </Form.Item>
          <Form.Item name="queueName" label="执行队列">
            <Select
              placeholder="选择队列（可选）"
              allowClear
              options={queues.map((q) => ({
                value: q.queueName,
                label: q.queueLabel || q.queueName,
              }))}
            />
          </Form.Item>
          <Form.Item name="state" label="状态" initialValue="1">
            <Radio.Group>
              <Radio value="1">生效</Radio>
              <Radio value="0">失效</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item
            name="fireParams"
            label="扩展参数 (JSON)"
            extra="JSON 对象，例如：{'retentionDays': 30}"
          >
            <Input.TextArea rows={4} placeholder='{"key": "value"}' />
          </Form.Item>
          {selectedTaskClass && (
            <div
              style={{
                marginBottom: 16,
                borderRadius: 6,
                border: "1px solid #E2E8F0",
                background: "#F8FAFC",
                padding: 12,
                fontSize: 12,
                color: "#4D5E7D",
              }}
            >
              任务说明：{selectedTaskClass}
            </div>
          )}
        </Form>
      </Drawer>
    </div>
  );
}