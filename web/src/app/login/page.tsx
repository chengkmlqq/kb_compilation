"use client";

import { useState, useEffect } from "react";
import { Alert, App, Button, Checkbox, Form, Input, Typography } from "antd";
import { useRouter } from "next/navigation";
import { apiAuthMode, apiLogin, AuthModeConfig } from "@/lib/api";
import styles from "./login.module.css";

interface LoginValues {
  userId: string;
  pwd: string;
  remember?: boolean;
}

// 应用部署在 /wiki 前缀下（next.config basePath=/wiki），静态资源需带前缀
const ASSET_BASE = "/wiki";
const loginBg = `${ASSET_BASE}/login/app-login-bg.png`;
const loginBanner = `${ASSET_BASE}/login/app-login-banner.png`;
const loginLogo = `${ASSET_BASE}/login/modo-logo.png`;

export default function LoginPage() {
  const { message } = App.useApp();
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
      // 注意：不能 encodeURIComponent —— 后端 decode_identity_cookie 不做 URL
      // 解码，编码后 AES 解密失败 → 登录守卫 apiMe 401 → 跳回登录页。
      document.cookie = `x-next-identity=${result.identity_cookie}; path=/; max-age=172800`;
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
    <div className={styles.modoAppLogin}>
      <img className={styles.logo} src={loginLogo} alt="logo" />
      <img className={styles.bg} src={loginBg} alt="bg" />

      <div className={styles.loginAll}>
        <div className={styles.loginImg}>
          <img src={loginBanner} alt="" />
        </div>

        <div className={styles.loginDialog}>
          <div className={styles.loginInfo}>
            <div className={styles.loginHello}>Hello!</div>
            <div className={styles.loginTitle}>
              <span className={styles.loginWelcome}>欢迎登录</span>
              <span className={styles.loginTitleLabel}>领域知识构建</span>
            </div>
            {authMode?.ssoEnabled && (
              <Alert
                type="info"
                showIcon
                style={{ marginTop: 16 }}
                message="系统已启用统一认证（SSO），如无法登录请联系管理员"
              />
            )}
            <Form<LoginValues>
              form={form}
              className={styles.loginForm}
              onFinish={onFinish}
              layout="vertical"
              size="large"
            >
              <Form.Item
                label="账号名称"
                name="userId"
                rules={[{ required: true, message: "请输入账号", min: 3, max: 192 }]}
              >
                <Input placeholder="请输入账号" autoComplete="username" />
              </Form.Item>
              <Form.Item
                label="登录密码"
                name="pwd"
                rules={[{ required: true, message: "请输入密码", min: 3, max: 192 }]}
              >
                <Input.Password placeholder="请输入密码" autoComplete="current-password" />
              </Form.Item>
              <div className={styles.itemRemember}>
                <Form.Item name="remember" valuePropName="checked" noStyle>
                  <Checkbox>记住密码</Checkbox>
                </Form.Item>
              </div>
              <Form.Item style={{ marginBottom: 0 }}>
                <Button type="primary" htmlType="submit" block loading={loading}>
                  登录
                </Button>
              </Form.Item>
            </Form>
          </div>
        </div>
      </div>
    </div>
  );
}