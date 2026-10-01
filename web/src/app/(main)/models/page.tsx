"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, App, Button, Card, Form, Input, Space, Spin, Tag, Typography } from "antd";
import { ReloadOutlined, SaveOutlined } from "@ant-design/icons";
import {
  apiGetModelConfig,
  apiSaveModelConfig,
  apiTestModelEndpoint,
  ModelConfigItem,
  ModelTestResult,
} from "@/lib/api";

const { Text } = Typography;

function SourceTag({ source }: { source: "db" | "env" }) {
  return source === "db" ? <Tag color="blue">已保存</Tag> : <Tag>环境变量</Tag>;
}

export default function ModelConfigPage() {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [items, setItems] = useState<ModelConfigItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ModelTestResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiGetModelConfig();
      if (res.success && res.data) {
        setItems(res.data.items);
        const values: Record<string, string> = {};
        for (const it of res.data.items) {
          // 敏感字段不回填（留空 = 不变）；非敏感字段回填现值
          if (!it.sensitive) values[it.code] = it.value || "";
        }
        form.setFieldsValue(values);
      } else {
        message.error(res.message || "加载失败");
      }
    } finally {
      setLoading(false);
    }
  }, [form, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const doSave = async () => {
    setSaving(true);
    try {
      const values = form.getFieldsValue();
      const payload = items.map((it) => ({
        code: it.code,
        value: typeof values[it.code] === "string" && values[it.code] !== "" ? values[it.code] : null,
      }));
      const res = await apiSaveModelConfig(payload);
      if (res.success) {
        message.success(`已保存 ${res.data?.count ?? res.data?.saved?.length ?? "?"} 项模型配置`);
        setTestResult(null);
        await load();
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  const doTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const v = form.getFieldsValue();
      const res = await apiTestModelEndpoint({
        base_url: v.AI_CHAT_API_ENDPOINT || v.EMBEDDING_BASE_URL || "",
        api_key: v.AI_CHAT_API_KEY || v.EMBEDDING_API_KEY || "",
        model: v.AI_CHAT_MODEL || v.EMBEDDING_MODEL || "",
      });
      setTestResult(res.data ?? { error: "无返回" });
      if (res.data?.ok) message.success("连通正常");
      else message.warning(res.data?.error || "连通失败");
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 80 }}>
        <Spin />
      </div>
    );
  }

  return (
    <Card
      title="模型配置"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void doSave()}>
            保存
          </Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="配置写入系统存储（modo_dim），保存后对问答与文档向量化即时生效，无需重启服务。密钥字段留空 = 保持不变。"
      />
      <Form form={form} layout="vertical">
        {items.map((it) => (
          <Form.Item
            key={it.code}
            label={
              <Space>
                <Text strong>{it.label}</Text>
                <Text type="secondary" style={{ fontWeight: 400 }}>
                  {it.code}
                </Text>
                <SourceTag source={it.source} />
              </Space>
            }
            extra={it.description}
            name={it.code}
            style={{ maxWidth: 640 }}
          >
            {it.sensitive ? (
              <Input.Password placeholder="留空保持不变" autoComplete="new-password" />
            ) : (
              <Input placeholder={it.value || `请输入 ${it.code}`} />
            )}
          </Form.Item>
        ))}
      </Form>

      <Space style={{ marginTop: 8 }}>
        <Button loading={testing} onClick={() => void doTest()}>
          测试连通性
        </Button>
        {testResult && (
          <Text type={testResult.ok ? "success" : "danger"}>
            {testResult.ok
              ? `连通正常（${testResult.kind === "chat" ? "问答端点" : "向量端点"}${testResult.model_matches === false ? "，模型不在列表" : ""}）`
              : `失败: ${testResult.error}`}
          </Text>
        )}
      </Space>
    </Card>
  );
}
