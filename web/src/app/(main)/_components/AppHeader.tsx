"use client";

import React, { useState, useEffect } from "react";
import { Avatar, Dropdown, Layout, Menu } from "antd";
import type { MenuProps } from "antd";
import {
  CaretDownOutlined,
  LogoutOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { useMenuContext } from "./MenuContext";
import { apiMe } from "@/lib/api";

const { Header } = Layout;

/** 用户信息 */
interface HeaderUser {
  userName?: string;
  userId?: string;
}

export const AppHeader: React.FC = () => {
  const router = useRouter();
  const { topMenus, selectedTopMenuId, setSelectedTopMenu, loading, flatMode } = useMenuContext();
  const [user, setUser] = useState<HeaderUser>({});

  useEffect(() => {
    (async () => {
      try {
        const me = await apiMe();
        if (me.success && me.data) {
          setUser({
            userName: me.data.userName || "",
            userId: me.data.userId || "",
          });
        }
      } catch {
        /* ignore */
      }
    })();
  }, []);

  // 顶级菜单转 antd items（一级无图标，纯文字，对齐 data-synth headerMenuItems）
  const headerMenuItems: MenuProps["items"] = topMenus.map((menu) => ({
    key: menu.menu_id as string,
    label: menu.menu_label || menu.menu_name || "",
  }));

  const handleMenuClick: MenuProps["onClick"] = (e) => {
    setSelectedTopMenu(e.key);
  };

  const handleLogout = () => {
    document.cookie = "x-next-identity=; path=/; max-age=0";
    router.replace("/login");
  };

  // 用户下拉内容（对齐 data-synth menuPanel 精简版：用户名 + 退出）
  const userMenuPanel = (
    <div
      style={{
        width: 180,
        background: "#fff",
        borderRadius: 8,
        boxShadow: "0 6px 16px 0 rgba(0, 0, 0, 0.08), 0 3px 6px -4px rgba(0, 0, 0, 0.12)",
        padding: "8px 4px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px" }}>
        <Avatar size={36} style={{ backgroundColor: "#ff8c2f", flexShrink: 0 }} icon={<UserOutlined />} />
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.4, overflow: "hidden" }}>
          <span style={{ fontWeight: 500, fontSize: 14, color: "#242e43" }}>{user.userName || user.userId || "用户"}</span>
        </div>
      </div>
      <div
        onClick={handleLogout}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "8px 12px", cursor: "pointer", borderRadius: 4, fontSize: 12, color: "#333",
        }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "#f5f5f5")}
        onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
      >
        <LogoutOutlined />
        <span>退出</span>
      </div>
    </div>
  );

  return (
    <Header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 10,
        width: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        background: "#F9FBFD",
        padding: "0 24px",
        borderBottom: "1px solid #EFF4F9",
        height: "45px",
        lineHeight: "45px",
      }}
    >
      {/* Left: Logo + divider */}
      <div style={{ display: "flex", alignItems: "center", width: "176px", justifyContent: "space-between" }}>
        <div
          style={{ display: "flex", alignItems: "center", cursor: "pointer", fontSize: 16, fontWeight: 600, color: "#242e43" }}
          onClick={() => router.push("/kbs")}
        >
          知识库平台
        </div>
        <div style={{ width: "1px", height: "14px", background: "#E3E9EF" }} />
      </div>

      {/* Middle: Top Nav（两级模式显示顶级菜单；单级模式留空） */}
      {!flatMode && topMenus.length > 0 && (
        <div style={{ flex: 1, display: "flex", justifyContent: "flex-start" }}>
          <Menu
            mode="horizontal"
            selectedKeys={selectedTopMenuId ? [selectedTopMenuId] : []}
            onClick={handleMenuClick}
            style={{
              lineHeight: "44px",
              borderBottom: "none",
              background: "transparent",
              width: "100%",
              fontSize: 14,
              fontWeight: 600,
            }}
            items={headerMenuItems}
          />
        </div>
      )}
      {/* 单级模式：中间留弹性空位 */}
      {flatMode && <div style={{ flex: 1 }} />}

      {/* Right: User dropdown */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, height: 45 }}>
        <Dropdown
          trigger={["click"]}
          popupRender={() => userMenuPanel}
          placement="bottomRight"
        >
          <div style={{ display: "flex", alignItems: "center", cursor: "pointer", gap: 6, paddingRight: 12 }}>
            <Avatar size={28} icon={<UserOutlined />} />
            <span style={{ color: "#242e43", fontSize: 12, fontWeight: 500 }}>
              {user.userName || user.userId || "用户"}
            </span>
            <CaretDownOutlined style={{ fontSize: 12, color: "#4D5E7D" }} />
          </div>
        </Dropdown>
      </div>
    </Header>
  );
};
