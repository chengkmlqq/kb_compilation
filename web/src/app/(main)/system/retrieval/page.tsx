"use client";

/** 检索参数（系统管理 → 检索参数）：对齐 WeKnora 租户级 RetrievalSettings。
 * 参数存 modo_dim（RETRIEVAL_CONFIG 组），保存后检索即时生效，无需重启。
 * int/float 用 Slider（min/max/step 取自 range）、bool 用 Switch；
 * 脏检查基于本次加载的原值，「恢复默认」把值改回 default 并标脏。
 */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { App, Button, Form, Result, Slider, Space, Spin, Switch, Tag, Typography } from "antd";
import { apiRetrievalConfig, apiSaveRetrievalConfig, RetrievalConfigItem } from "@/lib/api";

const SOURCE_TAGS: Record<string, { color: string; text: string }> = {
  db: { color: "green", text: "已修改" },
  default: { color: "default", text: "默认值" },
};

/** 数值统一为字符串再入状态，避免浮点抖动（如 0.7000000000000001） */
function numToStr(n: number): string {
  if (Number.isInteger(n)) return String(n);
  return String(Math.round(n * 10000) / 10000);
}

export default function RetrievalConfigPage() {
  const router = useRouter();
  const { message } = App.useApp();
  const [items, setItems] = useState<RetrievalConfigItem[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [initial, setInitial] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiRetrievalConfig();
      if (!res.success) {
        const msg = res.message || "加载失败";
        setError(msg);
        message.error(msg);
        return;
      }
      const list = res.data?.items || [];
      setItems(list);
      const v: Record<string, string> = {};
      list.forEach((it) => {
        v[it.code] = it.value;
      });
      setValues(v);
      setInitial(v);
    } catch {
      const msg = "加载检索参数失败";
      setError(msg);
      message.error(msg);
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
      const res = await apiSaveRetrievalConfig(
        items.map((it) => ({ code: it.code, value: values[it.code] ?? it.value })),
      );
      if (res.success) {
        message.success(`已保存 ${res.data?.count ?? 0} 项，检索即时生效`);
        void load();
      } else {
        // 后端 400 校验失败时 FastAPI 返回 { detail: 中文说明 }
        const errMsg = res.message || (res as unknown as { detail?: string }).detail || "保存失败";
        message.error(errMsg);
      }
    } catch {
      message.error("保存失败");
    } finally {
      setSaving(false);
    }
  };

  const restoreDefaults = () => {
    const v: Record<string, string> = {};
    items.forEach((it) => {
      v[it.code] = it.default;
    });
    setValues(v);
  };

  const dirty = items.some((it) => (values[it.code] ?? it.value) !== initial[it.code]);

  if (error && !loading) {
    return (
      <div style={{ padding: 8, height: "100%", overflow: "auto" }}>
        <Result
          status="error"
          title="加载检索参数失败"
          subTitle={error}
          extra={
            <Button type="primary" onClick={() => void load()}>
              重试
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%", padding: 8 }}>
      <Space style={{ width: "100%", justifyContent: "space-between", alignItems: "center" }}>
        <Space size={4}>
          <Button
            type="text"
            icon={<ArrowLeftOutlined />}
            onClick={() => router.push("/system/config")}
          />
          <Typography.Title level={4} style={{ margin: 0 }}>
            检索参数
          </Typography.Title>
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          全局检索配置（对齐 WeKnora RetrievalSettings），保存后即时生效
        </Typography.Text>
      </Space>
      <Form layout="vertical" style={{ maxWidth: 720 }}>
        <Spin spinning={loading}>
          {items.map((it) => {
            const tag = SOURCE_TAGS[it.source] || SOURCE_TAGS.default;
            const isBool = it.value_type === "bool";
            const bounds = it.range.map(Number);
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
                {isBool ? (
                  <Space size={12}>
                    <Switch
                      checked={values[it.code] === "true"}
                      onChange={(checked: boolean) =>
                        setValues((prev) => ({ ...prev, [it.code]: String(checked) }))
                      }
                    />
                    <Typography.Text>{values[it.code] === "true" ? "开" : "关"}</Typography.Text>
                  </Space>
                ) : (
                  <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
                    <Slider
                      style={{ flex: 1 }}
                      min={bounds[0] ?? 0}
                      max={bounds[1] ?? 1}
                      step={bounds[2] ?? 1}
                      value={Number(values[it.code] ?? it.value)}
                      onChange={(v: number | number[]) => {
                        const n = Array.isArray(v) ? v[0] : v;
                        setValues((prev) => ({ ...prev, [it.code]: numToStr(n) }));
                      }}
                    />
                    <Typography.Text strong style={{ minWidth: 56, textAlign: "right" }}>
                      {values[it.code] ?? it.value}
                    </Typography.Text>
                  </div>
                )}
              </Form.Item>
            );
          })}
          <Space size={8}>
            <Button type="primary" loading={saving} disabled={!dirty} onClick={save}>
              保存
            </Button>
            <Button onClick={restoreDefaults}>恢复默认</Button>
            {!dirty && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                无改动
              </Typography.Text>
            )}
          </Space>
        </Spin>
      </Form>
    </Space>
  );
}