"use client";

/**
 * 通知管理（独立页，对齐 ds (main)/notification 两个 Tab）：
 *   系统消息发送（选用户/全量 + 标题/内容/类型/优先级）
 *   短信告警接口测试（对端地址 + 请求方式 + 告警字段，真实 HTTP 请求）
 */
import { useCallback, useEffect, useState } from "react";
import {
  App,
  Alert,
  Button,
  Card,
  Form,
  Input,
  Radio,
  Select,
  Space,
  Tabs,
  Transfer,
} from "antd";
import {
  apiListNotifyUsers,
  apiSendSystemMessage,
  apiTestExternalAlertApi,
  NotifyUserItem,
} from "@/lib/api";

const TYPE_OPTIONS = [
  { label: "INFO", value: "INFO" },
  { label: "WARNING", value: "WARNING" },
  { label: "ERROR", value: "ERROR" },
  { label: "SUCCESS", value: "SUCCESS" },
];

const PRIORITY_OPTIONS = [
  { label: "LOW", value: "LOW" },
  { label: "NORMAL", value: "NORMAL" },
  { label: "HIGH", value: "HIGH" },
];

function SendMessageTab() {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [users, setUsers] = useState<NotifyUserItem[]>([]);
  const [targetKeys, setTargetKeys] = useState<string[]>([]);
  const [sending, setSending] = useState(false);

  const loadUsers = useCallback(async (kw = "") => {
    const res = await apiListNotifyUsers(kw);
    if (res.success) setUsers(res.data?.items || []);
  }, []);

  useEffect(() => {
    void loadUsers("");
  }, [loadUsers]);

  const doSend = async () => {
    const values = await form.validateFields();
    setSending(true);
    try {
      const payload = {
        userIds: targetKeys.length > 0 ? targetKeys : undefined, // 空 = 全量
        title: values.title,
        content: values.content,
        type: values.type || "INFO",
        priority: values.priority || "NORMAL",
        linkUrl: values.linkUrl,
      };
      const res = await apiSendSystemMessage(payload);
      if (res.success) {
        message.success(res.message || `已发送给 ${res.data?.sent ?? "?"} 个用户`);
        form.resetFields();
        setTargetKeys([]);
      } else {
        message.error(res.message || "发送失败");
      }
    } finally {
      setSending(false);
    }
  };

  return (
    <Card title="系统消息发送">
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="左侧选择目标用户（不选则发送给全部启用用户，安全上限 1000）。"
      />
      <Form form={form} layout="vertical">
        <Form.Item
          name="title"
          label="消息标题"
          rules={[{ required: true, message: "请输入消息标题" }]}
        >
          <Input placeholder="消息标题" maxLength={200} />
        </Form.Item>
        <Form.Item
          name="content"
          label="消息内容"
          rules={[{ required: true, message: "请输入消息内容" }]}
        >
          <Input.TextArea rows={4} placeholder="消息内容" />
        </Form.Item>
        <Space size={16} wrap>
          <Form.Item name="type" label="消息类型" initialValue="INFO">
            <Select options={TYPE_OPTIONS} style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="priority" label="优先级" initialValue="NORMAL">
            <Select options={PRIORITY_OPTIONS} style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="linkUrl" label="链接地址">
            <Input placeholder="https://…（可选）" style={{ width: 300 }} />
          </Form.Item>
        </Space>
        <Form.Item label="目标用户">
          <Transfer
            dataSource={users.map((u) => ({
              key: u.user_id || "",
              title: `${u.user_id}${u.user_name ? `（${u.user_name}）` : ""}`,
            }))}
            titles={["全部用户", "已选用户"]}
            targetKeys={targetKeys}
            onChange={(keys) => setTargetKeys(keys as string[])}
            render={(item) => item.title}
            showSearch
            listStyle={{ width: 280, height: 320 }}
            pagination={{ pageSize: 10 }}
          />
        </Form.Item>
        <Space>
          <Button onClick={() => form.resetFields()}>重置</Button>
          <Button type="primary" htmlType="submit" loading={sending} onClick={() => void doSend()}>
            发送消息
          </Button>
        </Space>
      </Form>
    </Card>
  );
}

function SmsAlertTesterTab() {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<{ status_code: number; body: string } | null>(null);

  const doTest = async () => {
    const values = await form.validateFields();
    setTesting(true);
    setResult(null);
    try {
      const res = await apiTestExternalAlertApi({
        endpoint: values.endpoint,
        method: values.method || "POST",
        id: values.id,
        alertLevel: values.alertLevel,
        alertTitle: values.alertTitle || "",
        alertContent: values.alertContent,
        alertTime: values.alertTime,
      });
      if (res.success) {
        message.success(res.message || "测试成功");
        setResult(res.data || null);
      } else {
        message.error(res.message || "测试失败");
      }
    } finally {
      setTesting(false);
    }
  };

  return (
    <Card title="短信告警接口测试">
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="向对端告警接口发送一条测试请求（仅支持 http/https，POST/PUT/PATCH）。"
      />
      <Form form={form} layout="vertical" initialValues={{ method: "POST" }}>
        <Form.Item
          name="endpoint"
          label="对端接口地址"
          rules={[{ required: true, message: "请输入对端接口地址" }]}
        >
          <Input placeholder="https://example.com/api/sms/alert" />
        </Form.Item>
        <Form.Item name="method" label="请求方式" rules={[{ required: true }]}>
          <Radio.Group>
            <Radio value="POST">POST</Radio>
            <Radio value="PUT">PUT</Radio>
            <Radio value="PATCH">PATCH</Radio>
          </Radio.Group>
        </Form.Item>
        <Space size={16} wrap>
          <Form.Item name="id" label="告警ID">
            <Input placeholder="1" style={{ width: 160 }} />
          </Form.Item>
          <Form.Item name="alertLevel" label="告警级别">
            <Input placeholder="WARNING / ERROR / CRITICAL" style={{ width: 220 }} />
          </Form.Item>
          <Form.Item name="alertTime" label="告警时间">
            <Input placeholder="2026-10-04 12:00:00" style={{ width: 200 }} />
          </Form.Item>
        </Space>
        <Form.Item
          name="alertTitle"
          label="告警标题"
          rules={[{ required: true, message: "请输入告警标题" }]}
        >
          <Input placeholder="告警标题" />
        </Form.Item>
        <Form.Item name="alertContent" label="告警内容">
          <Input.TextArea rows={3} placeholder="告警内容（可选）" />
        </Form.Item>
        <Space>
          <Button onClick={() => form.resetFields()}>重置</Button>
          <Button type="primary" loading={testing} onClick={() => void doTest()}>
            发送测试
          </Button>
        </Space>
      </Form>
      {result && (
        <Alert
          type={result.status_code >= 200 && result.status_code < 300 ? "success" : "warning"}
          style={{ marginTop: 12 }}
          message={`HTTP ${result.status_code}`}
          description={result.body}
          showIcon
        />
      )}
    </Card>
  );
}

export default function NotificationPage() {
  return (
    <Tabs
      defaultActiveKey="send"
      items={[
        { key: "send", label: "系统消息发送", children: <SendMessageTab /> },
        { key: "sms", label: "短信告警接口测试", children: <SmsAlertTesterTab /> },
      ]}
    />
  );
}