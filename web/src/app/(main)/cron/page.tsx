"use client";

/**
 * 任务管理页 —— 定时任务（modo_cron_task）配置管理，对齐 data-synth cron：
 * 列表（搜索/分页）+ 新建/编辑（英文名/中文名/Cron表达式/任务类/队列/状态/扩展参数）
 * + 启停 + 删除。数据源 /api/v1/cron（list/save/delete/toggle/queues/registered-tasks）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  App,
  Button,
  Drawer,
  Form,
  Input,
  Radio,
  Select,
  Space,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { PlusOutlined, ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import ModoTable from "@/components/biz/modo-table";
import ModoPagination from "@/components/biz/modo-pagination";
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

export default function CronPage() {
  const { message, modal } = App.useApp();
  const [form] = Form.useForm();
  const [drawerForm] = Form.useForm();

  const [items, setItems] = useState<CronTaskItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [keyword, setKeyword] = useState("");

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const [queues, setQueues] = useState<Array<{ queueName: string; queueLabel: string | null }>>([]);
  const [registeredTasks, setRegisteredTasks] = useState<Array<{ taskClass: string; name: string }>>([]);

  const selectedTaskClass = Form.useWatch("taskClass", drawerForm) as string | undefined;

  const load = useCallback(
    async (p = page, size = pageSize, kw = keyword) => {
      setLoading(true);
      try {
        const res = await apiListCronTasks({
          pageNum: p,
          pageSize: size,
          keyWord: kw.trim() || undefined,
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
    [page, pageSize, keyword, message],
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

  const handleSearch = () => {
    setPage(1);
    void load(1, pageSize, keyword);
  };

  const handleReset = () => {
    setKeyword("");
    setPage(1);
    void load(1, pageSize, "");
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
      width: 180,
      align: "center" as const,
      fixed: "right" as const,
      render: (_, record) => (
        <Space size="small">
          <Button type="link" size="small" onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            onClick={() => handleToggle(record)}
          >
            {record.state === "1" ? "禁用" : "启用"}
          </Button>
          <Button type="link" size="small" danger onClick={() => handleDelete(record)}>
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div
      className="modo-page"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        background: "#F5F7FA",
        padding: 8,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
          background: "#F5F7FA",
        }}
      >
        {/* Filter + New Button Area */}
        <div
          style={{
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            gap: 12,
            background: "#fff",
            borderRadius: 8,
            border: "1px solid #E3E9EF",
            padding: "10px 16px",
            marginBottom: 8,
            flexWrap: "wrap",
          }}
        >
          <Input
            placeholder="搜索任务名称"
            allowClear
            style={{ width: 240 }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onPressEnter={handleSearch}
            prefix={<SearchOutlined />}
          />
          <Button icon={<SearchOutlined />} onClick={handleSearch}>
            查询
          </Button>
          <Button onClick={handleReset}>重置</Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => void load()}
          >
            刷新
          </Button>
          <div style={{ flex: 1 }} />
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建任务
          </Button>
        </div>

        {/* Table + Pagination Area */}
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
            overflow: "hidden",
            background: "#fff",
            borderRadius: 8,
            border: "1px solid #E3E9EF",
          }}
        >
          <ModoTable
            columns={columns}
            dataSource={items}
            rowKey="id"
            loading={loading}
            size="middle"
            scroll={{ x: 1100 }}
          />
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
      </div>

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