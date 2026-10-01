"use client";

import { useState } from "react";
import { Button, Card, Form, Input, Typography, message } from "antd";
import { useRouter } from "next/navigation";
import { apiLogin } from "@/lib/api";

interface LoginValues {
  userId: string;
  pwd: string;
}

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  const onFinish = async (values: LoginValues) => {
    setLoading(true);
    try {
      const result = await apiLogin(values.userId, values.pwd);
      if (!result.success || !result.identity_cookie) {
        message.error(result.message || "登录失败");
        return;
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
        background: "linear-gradient(135deg, #1f3b73 0%, #3a6bc0 100%)",
      }}
    >
      <Card style={{ width: 380, boxShadow: "0 8px 24px rgba(0,0,0,0.2)" }}>
        <Typography.Title level={3} style={{ textAlign: "center", marginTop: 0 }}>
          知识库平台
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: "center" }}>
          文档解析 · 向量检索 · Wiki 生成 · 智能问答
        </Typography.Paragraph>
        <Form<LoginValues> layout="vertical" onFinish={onFinish} size="large">
          <Form.Item label="账号名称" name="userId" rules={[{ required: true, message: "请输入账号" }]}>
            <Input placeholder="请输入账号" autoComplete="username" />
          </Form.Item>
          <Form.Item label="登录密码" name="pwd" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password placeholder="请输入密码" autoComplete="current-password" />
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
