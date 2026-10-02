"use client";

import { useState, useEffect } from "react";
import { Alert, Button, Card, Checkbox, Form, Input, Typography, message } from "antd";
import { useRouter } from "next/navigation";
import { apiAuthMode, apiLogin, AuthModeConfig } from "@/lib/api";

interface LoginValues {
  userId: string;
  pwd: string;
  remember?: boolean;
}

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<LoginValues>();
  const [authMode, setAuthMode] = useState<AuthModeConfig | null>(null);

  // 运行时认证配置探测（对齐 data-synth /api/open/auth-mode）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiAuthMode();
        if (!cancelled && res.success && res.data) setAuthMode(res.data);
      } catch {
        /* ignore — 默认本地登录 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 回填记住的账号（对齐 data-synth 登录页 localStorage 逻辑）
  useEffect(() => {
    try {
      const userId = localStorage.getItem("loginUserId");
      const pwd = localStorage.getItem("loginPassword");
      if (userId || pwd) {
        form.setFieldsValue({
          userId: userId || "",
          pwd: pwd || "",
          remember: Boolean(userId && pwd),
        });
      }
    } catch {
      /* ignore */
    }
  }, [form]);

  const onFinish = async (values: LoginValues) => {
    setLoading(true);
    try {
      const result = await apiLogin(values.userId, values.pwd);
      if (!result.success || !result.identity_cookie) {
        message.error(result.message || "登录失败");
        return;
      }
      // 记住密码（对齐 data-synth saveRemember）
      try {
        if (values.remember) {
          localStorage.setItem("loginUserId", values.userId);
          localStorage.setItem("loginPassword", values.pwd);
        } else {
          localStorage.removeItem("loginUserId");
          localStorage.removeItem("loginPassword");
        }
      } catch {
        /* ignore */
      }
      // Persist the identity cookie on the frontend host (same contract as
      // the source platform: x-next-identity holds the AES identity payload).
      document.cookie = `x-next-identity=${encodeURIComponent(result.identity_cookie)}; path=/; max-age=172800`;
      message.success("登录成功");
      router.replace("/kbs");
      router.refresh();
    } catch (e) {
      console.error(e);
      message.error("登录异常");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(45, 132, 251, 0.05)",
        position: "relative",
      }}
    >
      <Card
        style={{
          width: 400,
          borderRadius: 6,
          boxShadow: "0 1px 30px 0 rgb(117 145 175 / 18%)",
        }}
      >
        <Typography.Title level={3} style={{ textAlign: "center", marginTop: 0 }}>
          知识库平台
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: "center" }}>
          文档解析 · 向量检索 · Wiki 生成 · 智能问答
        </Typography.Paragraph>
        {authMode?.ssoEnabled && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="系统已启用统一认证（SSO），如无法登录请联系管理员"
          />
        )}
        <Form<LoginValues> form={form} layout="vertical" onFinish={onFinish} size="large">
          <Form.Item label="账号名称" name="userId" rules={[{ required: true, message: "请输入账号" }]}>
            <Input placeholder="请输入账号" autoComplete="username" />
          </Form.Item>
          <Form.Item label="登录密码" name="pwd" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password placeholder="请输入密码" autoComplete="current-password" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 12 }}>
            <Form.Item name="remember" valuePropName="checked" noStyle>
              <Checkbox>记住密码</Checkbox>
            </Form.Item>
          </Form.Item>
          <Form.Item style={{ marginBottom: 0 }}>
            <Button type="primary" htmlType="submit" block loading={loading}>
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
