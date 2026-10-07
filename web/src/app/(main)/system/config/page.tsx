"use client";

/** 平台运行参数（系统 → 平台参数）：wiki 构建执行方式、agent 编排回合上限。
 * 参数存 modo_dim（PLATFORM_CONFIG 组），worker 侧 15s 内生效，无需重启。
 */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRightOutlined } from "@ant-design/icons";
import { App, Button, Form, InputNumber, Select, Space, Spin, Tag, Typography } from "antd";
import { apiPlatformConfig, apiSavePlatformConfig, PlatformConfigItem } from "@/lib/api";

const MODE_LABELS: Record<string, string> = {
  direct: "直跑（技能脚本，推荐）",
  agent: "agent 编排（LLM 逐步决策）",
  gateway: "外部 agent-gateway 网关",
};

const SOURCE_TAGS: Record<string, { color: string; text: string }> = {
  db: { color: "green", text: "已配置" },
  env: { color: "blue", text: "环境变量" },
  default: { color: "default", text: "默认值" },
};

export default function PlatformConfigPage() {
  const router = useRouter();
  const { message } = App.useApp();
  const [items, setItems] = useState<PlatformConfigItem[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiPlatformConfig();
      const list = res.data?.items || [];
      setItems(list);
      const v: Record<string, string> = {};
      list.forEach((it) => {
        v[it.code] = it.value;
      });
      setValues(v);
    } catch {
      message.error("加载平台参数失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      const res = await apiSavePlatformConfig(
        items.map((it) => ({ code: it.code, value: String(values[it.code] ?? it.value) })),
      );
      if (res.success) {
        message.success(`已保存 ${res.data?.count ?? 0} 项，15 秒内生效`);
        void load();
      } else {
        message.error(res.message || "保存失败");
      }
    } catch {
      message.error("保存失败");
    } finally {
      setSaving(false);
    }
  };

  const dirty = items.some((it) => String(values[it.code] ?? it.value) !== it.value);

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Space style={{ width: "100%", justifyContent: "space-between", alignItems: "center" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          平台运行参数
        </Typography.Title>
        <Button type="text" icon={<ArrowRightOutlined />} onClick={() => router.push("/system/retrieval")}>
          检索参数
        </Button>
      </Space>
      <Form layout="vertical" style={{ maxWidth: 720 }}>
        <Spin spinning={loading}>
          {items.map((it) => {
            const tag = SOURCE_TAGS[it.source] || SOURCE_TAGS.default;
            return (
              <Form.Item
                key={it.code}
                label={
                  <Space size={8}>
                    <span>{it.label}</span>
                    <Tag color={tag.color} style={{ fontSize: 12 }}>
                      {tag.text}
                    </Tag>
                  </Space>
                }
                extra={it.description}
              >
                {it.value_type === "enum" ? (
                  <Select
                    style={{ width: 360 }}
                    value={values[it.code]}
                    onChange={(v) => setValues((prev) => ({ ...prev, [it.code]: v }))}
                    options={(it.options || []).map((o) => ({
                      value: o,
                      label: MODE_LABELS[o] || o,
                    }))}
                  />
                ) : (
                  <InputNumber
                    min={1}
                    max={86400}
                    step={100}
                    style={{ width: 240 }}
                    value={Number(values[it.code])}
                    onChange={(v) => setValues((prev) => ({ ...prev, [it.code]: String(v ?? it.default) }))}
                  />
                )}
              </Form.Item>
            );
          })}
          <Button type="primary" loading={saving} disabled={!dirty} onClick={save}>
            保存
          </Button>
          {!dirty && (
            <Typography.Text type="secondary" style={{ marginLeft: 12, fontSize: 12 }}>
              无改动
            </Typography.Text>
          )}
        </Spin>
      </Form>
    </Space>
  );
}