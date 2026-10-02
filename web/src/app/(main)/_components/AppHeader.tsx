"use client";

import React, { useState, useEffect } from "react";
import { App, Avatar, Dropdown, Input, Layout, Menu, Modal, Form, Select, Tag, Typography } from "antd";
import type { MenuProps } from "antd";
import {
  CaretDownOutlined,
  LogoutOutlined,
  SearchOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { useMenuContext } from "./MenuContext";
import { NotificationBell } from "./NotificationBell";
import {
  apiGetDefaultTeam,
  apiMe,
  apiMyTeams,
  apiSetDefaultTeam,
  apiSwitchTeam,
  UserTeamItem,
} from "@/lib/api";

const { Header } = Layout;
const { Text } = Typography;

/** 用户信息 */
interface HeaderUser {
  userName?: string;
  userId?: string;
  teamName?: string;
  teamLabel?: string;
}

export const AppHeader: React.FC = () => {
  const router = useRouter();
  const { topMenus, selectedTopMenuId, setSelectedTopMenu, loading, flatMode } = useMenuContext();
  const { message } = App.useApp();
  const [form] = Form.useForm();

  const [user, setUser] = useState<HeaderUser>({});
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [showTeamPanel, setShowTeamPanel] = useState(false);
  const [teamSearchVal, setTeamSearchVal] = useState("");
  const [userTeams, setUserTeams] = useState<UserTeamItem[]>([]);
  const [loadingTeams, setLoadingTeams] = useState(false);
  const [defaultTeamModalVisible, setDefaultTeamModalVisible] = useState(false);
  const [submittingDefaultTeam, setSubmittingDefaultTeam] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const me = await apiMe();
        if (me.success && me.data) {
          setUser({
            userName: me.data.userName || "",
            userId: me.data.userId || "",
            teamName: me.data.teamName || "",
            teamLabel: me.data.teamLabel || "",
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

  // ===== 团队能力（对齐 data-synth team-actions） =====

  const fetchUserTeams = async () => {
    if (!user.userId) return;
    setLoadingTeams(true);
    try {
      const res = await apiMyTeams();
      if (res.success && res.data) {
        setUserTeams(res.data);
      }
    } finally {
      setLoadingTeams(false);
    }
  };

  const filteredTeams = React.useMemo(() => {
    if (!teamSearchVal) return userTeams;
    return userTeams.filter(
      (t) =>
        (t.teamLabel || "").toLowerCase().includes(teamSearchVal.toLowerCase()) ||
        t.teamName.toLowerCase().includes(teamSearchVal.toLowerCase()),
    );
  }, [userTeams, teamSearchVal]);

  const handleSwitchTeam = async (team: UserTeamItem) => {
    const res = await apiSwitchTeam(team.teamName);
    if (res.success && res.data?.identity_cookie) {
      document.cookie = `x-next-identity=${encodeURIComponent(res.data.identity_cookie)}; path=/; max-age=172800`;
      message.success(`已切换到 ${team.teamLabel || team.teamName}`);
      setDropdownOpen(false);
      setShowTeamPanel(false);
      window.location.reload();
    } else {
      message.error("切换失败: " + (res.message || ""));
    }
  };

  const handleOpenDefaultTeamModal = async () => {
    setDropdownOpen(false);
    setDefaultTeamModalVisible(true);
    setLoadingTeams(true);
    try {
      const [teamsRes, defaultRes] = await Promise.all([apiMyTeams(), apiGetDefaultTeam()]);
      if (teamsRes.success && teamsRes.data) setUserTeams(teamsRes.data);
      if (defaultRes.success && defaultRes.data) form.setFieldValue("defaultTeam", defaultRes.data);
      else form.resetFields(["defaultTeam"]);
    } catch {
      /* ignore */
    } finally {
      setLoadingTeams(false);
    }
  };

  const handleDefaultTeamSubmit = async () => {
    try {
      const values = await form.validateFields();
      if (!values.defaultTeam) return;
      setSubmittingDefaultTeam(true);
      const res = await apiSetDefaultTeam(values.defaultTeam);
      if (res.success) {
        message.success("默认团队设置成功");
        setDefaultTeamModalVisible(false);
      } else {
        message.error("设置失败: " + (res.message || ""));
      }
    } catch {
      /* ignore */
    } finally {
      setSubmittingDefaultTeam(false);
    }
  };

  // 团队面板（左侧并排，对齐 data-synth teamPanel）
  const teamPanel = (
    <div
      style={{
        width: 320,
        background: "#fff",
        borderRadius: 8,
        boxShadow: "0 6px 16px 0 rgba(0, 0, 0, 0.08), 0 3px 6px -4px rgba(0, 0, 0, 0.12)",
        padding: "12px 16px",
        marginRight: 6,
      }}
    >
      <div style={{ fontSize: 14, fontWeight: 500, color: "#242e43", marginBottom: 12 }}>选择团队</div>
      <Input
        placeholder="请输入关键字"
        prefix={<SearchOutlined style={{ color: "#bbb" }} />}
        value={teamSearchVal}
        onChange={(e) => setTeamSearchVal(e.target.value)}
        allowClear
        style={{ background: "#f0f5ff", borderColor: "#d6e4ff", marginBottom: 12 }}
      />
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, maxHeight: 280, overflowY: "auto" }}>
        {filteredTeams.length === 0 && !loadingTeams && (
          <span style={{ color: "#999", fontSize: 13 }}>暂无可选团队</span>
        )}
        {filteredTeams.map((t) => (
          <Tag
            key={t.teamName}
            color={t.teamName === user.teamName ? "blue" : undefined}
            onClick={() => handleSwitchTeam(t)}
            style={{ cursor: "pointer", padding: "4px 12px", fontSize: 13, borderRadius: 4, userSelect: "none" }}
          >
            {t.teamLabel || t.teamName}
          </Tag>
        ))}
      </div>
    </div>
  );

  // 用户下拉菜单面板（对齐 data-synth menuPanel）
  const menuPanel = (
    <div
      style={{
        width: 200,
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
          <span style={{ fontSize: 12, color: "#B3C0CC", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {user.teamLabel || user.teamName || "未加入团队"}
          </span>
        </div>
      </div>
      {[
        {
          key: "switchTeam",
          label: "选择团队",
          active: showTeamPanel,
          onClick: () => {
            if (!showTeamPanel) {
              fetchUserTeams();
              setShowTeamPanel(true);
            } else {
              setShowTeamPanel(false);
            }
          },
        },
        {
          key: "defaultTeam",
          label: "设置默认团队",
          onClick: () => handleOpenDefaultTeamModal(),
        },
      ].map((item) => (
        <div
          key={item.key}
          onClick={item.onClick}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 12px",
            cursor: "pointer",
            borderRadius: 4,
            fontSize: 12,
            color: item.active ? "#1677ff" : "#333",
            transition: "background 0.2s",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "#f5f5f5")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <span>{item.label}</span>
        </div>
      ))}
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

  const dropdownContent = (
    <div style={{ display: "flex", alignItems: "flex-start" }}>
      {showTeamPanel && teamPanel}
      {menuPanel}
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

      {/* Right: 搜索 + 通知铃 + 用户下拉 */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, height: 45 }}>
        <div
          style={{
            width: 28, height: 28, display: "flex", alignItems: "center", justifyContent: "center",
            backgroundColor: "#EFF4F9", borderRadius: "50%", cursor: "pointer",
          }}
          onClick={() => router.push("/chat")}
        >
          <SearchOutlined style={{ fontSize: 14, color: "#4D5E7D" }} />
        </div>
        <NotificationBell />
        <Dropdown
          open={dropdownOpen}
          onOpenChange={(open) => {
            setDropdownOpen(open);
            if (!open) {
              setShowTeamPanel(false);
              setTeamSearchVal("");
            }
          }}
          trigger={["click"]}
          popupRender={() => dropdownContent}
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

      {/* 设置默认团队弹窗（对齐 data-synth） */}
      <Modal
        title="选择默认团队"
        open={defaultTeamModalVisible}
        onOk={handleDefaultTeamSubmit}
        onCancel={() => setDefaultTeamModalVisible(false)}
        confirmLoading={submittingDefaultTeam}
        afterClose={() => form.resetFields()}
        centered
        width={520}
      >
        <div style={{ paddingTop: 24 }}>
          <Form form={form} layout="horizontal" labelCol={{ span: 6 }} wrapperCol={{ span: 16 }}>
            <Form.Item name="defaultTeam" label="默认团队" rules={[{ required: true, message: "请选择默认团队" }]}>
              <Select
                placeholder="请选择默认团队"
                loading={loadingTeams}
                optionFilterProp="children"
                showSearch
                filterOption={(input, option) =>
                  (option?.label ?? "").toString().toLowerCase().includes(input.toLowerCase())
                }
                options={userTeams.map((t) => ({
                  label: t.teamLabel || t.teamName,
                  value: t.teamName,
                }))}
              />
            </Form.Item>
          </Form>
        </div>
      </Modal>
    </Header>
  );
};