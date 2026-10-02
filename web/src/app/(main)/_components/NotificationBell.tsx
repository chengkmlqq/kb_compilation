"use client";

import React, { useCallback, useEffect, useMemo, useState, forwardRef, useImperativeHandle } from "react";
import { Badge, Button, Dropdown, Empty, Modal, Pagination, Space, Spin, Tabs, Tag, Typography, message } from "antd";
import { BellOutlined, ReloadOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import {
  apiListNotifications,
  apiMarkAllNotificationsRead,
  apiMarkNotificationRead,
  apiNotificationUnreadCount,
  NotificationItem,
} from "@/lib/api";

const { Text } = Typography;

type TabKey = "unread" | "all";

function formatTime(value?: string) {
  if (!value) return "";
  const d = dayjs(value);
  if (!d.isValid()) return "";
  return d.format("YYYY-MM-DD HH:mm");
}

function typeColor(type?: string) {
  switch (type) {
    case "SUCCESS":
      return "green";
    case "WARNING":
      return "orange";
    case "ERROR":
      return "red";
    default:
      return "blue";
  }
}

export interface NotificationBellRef {
  refreshUnreadCount: () => void;
}

export const NotificationBell = forwardRef<NotificationBellRef, {}>(({}, ref) => {
  const [messageApi, contextHolder] = message.useMessage();

  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [tab, setTab] = useState<TabKey>("unread");
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [list, setList] = useState<NotificationItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const pageSize = 10;
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailMsg, setDetailMsg] = useState<NotificationItem | null>(null);

  const refreshUnread = useCallback(async () => {
    try {
      const res = await apiNotificationUnreadCount();
      if (res.success && res.data) setUnreadCount(res.data.unread_count ?? 0);
    } catch {
      /* ignore */
    }
  }, []);

  useImperativeHandle(ref, () => ({
    refreshUnreadCount: () => {
      refreshUnread();
    },
  }));

  const refreshList = useCallback(
    async (next?: { tab?: TabKey; page?: number }) => {
      const nextTab = next?.tab ?? tab;
      const nextPage = next?.page ?? page;
      setLoading(true);
      try {
        const res = await apiListNotifications({ tab: nextTab, page: nextPage, page_size: pageSize });
        if (res.success && res.data) {
          setList(res.data.items ?? []);
          setTotal(res.data.total ?? 0);
        } else {
          messageApi.error(res.message || "加载消息失败");
        }
      } catch {
        messageApi.error("加载消息失败");
      } finally {
        setLoading(false);
      }
    },
    [messageApi, page, tab],
  );

  const refreshAll = useCallback(async () => {
    setPage(1);
    await Promise.all([refreshUnread(), refreshList({ page: 1 })]);
  }, [refreshList, refreshUnread]);

  useEffect(() => {
    refreshUnread();
  }, [refreshUnread]);

  const handleDropdownOpenChange = (nextOpen: boolean) => {
    setDropdownOpen(nextOpen);
    if (!nextOpen) {
      setDetailOpen(false);
      setDetailMsg(null);
      return;
    }
    setDetailOpen(false);
    setDetailMsg(null);
    setPage(1);
    void Promise.all([refreshUnread(), refreshList({ page: 1 })]);
  };

  const handleMarkAllRead = async () => {
    setLoading(true);
    try {
      const res = await apiMarkAllNotificationsRead();
      if (!res.success) {
        messageApi.error(res.message || "操作失败");
        return;
      }
      messageApi.success("已全部标记为已读");
      await refreshAll();
    } finally {
      setLoading(false);
    }
  };

  const handleClickItem = async (item: NotificationItem) => {
    try {
      if (item.is_read === "0") {
        await apiMarkNotificationRead(item.id);
        await refreshUnread();
      }
      if (item.link_url) {
        setDropdownOpen(false);
        if (/^https?:\/\//i.test(item.link_url)) {
          window.location.href = item.link_url;
        } else {
          window.location.href = item.link_url;
        }
        return;
      }
      setDetailMsg(item);
      setDetailOpen(true);
    } catch {
      messageApi.error("操作失败");
    }
  };

  const tabs = useMemo(
    () => [
      { key: "unread", label: `未读${unreadCount ? ` (${unreadCount})` : ""}` },
      { key: "all", label: "全部" },
    ] as const,
    [unreadCount],
  );

  const dropdownContent = (
    <div
      style={{
        width: 520,
        background: "#fff",
        borderRadius: 8,
        boxShadow: "0 6px 16px 0 rgba(0, 0, 0, 0.08), 0 3px 6px -4px rgba(0, 0, 0, 0.12)",
        padding: "12px 12px 8px",
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <Text strong>系统消息</Text>
        <Space>
          <Button size="small" icon={<ReloadOutlined />} onClick={refreshAll} />
          <Button size="small" type="primary" disabled={!unreadCount} onClick={handleMarkAllRead}>
            全部已读
          </Button>
        </Space>
      </div>

      <Tabs
        activeKey={tab}
        items={tabs.map((t) => ({ key: t.key, label: t.label }))}
        onChange={(k) => {
          const nextTab = k as TabKey;
          setTab(nextTab);
          setPage(1);
          refreshList({ tab: nextTab, page: 1 });
        }}
      />

      <div style={{ maxHeight: 420, overflowY: "auto", padding: "0 4px" }}>
        {loading ? (
          <div style={{ padding: 24, display: "flex", justifyContent: "center" }}>
            <Spin />
          </div>
        ) : list.length === 0 ? (
          <div style={{ padding: 16 }}>
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无消息" />
          </div>
        ) : (
          list.map((item) => {
            const unread = item.is_read === "0";
            return (
              <div
                key={item.id}
                onClick={() => handleClickItem(item)}
                style={{ cursor: "pointer", padding: "10px 8px", borderBottom: "1px solid #f0f0f0" }}
              >
                <Space size={8} style={{ maxWidth: "100%" }}>
                  {unread && <span style={{ width: 6, height: 6, borderRadius: 6, background: "#1677ff" }} />}
                  <Text strong={unread} ellipsis style={{ maxWidth: 260 }}>
                    {item.title}
                  </Text>
                  <Tag color={typeColor(item.type)} style={{ marginInlineEnd: 0 }}>
                    {item.type || "INFO"}
                  </Tag>
                </Space>
                <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                  <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ marginBottom: 0 }}>
                    {item.content}
                  </Typography.Paragraph>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {formatTime(item.create_date)}
                  </Text>
                </div>
              </div>
            );
          })
        )}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
        <Pagination
          size="small"
          current={page}
          pageSize={pageSize}
          total={total}
          showSizeChanger={false}
          hideOnSinglePage
          onChange={(p) => {
            setPage(p);
            refreshList({ page: p });
          }}
        />
      </div>

      <Modal
        title={detailMsg?.title}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <div style={{ whiteSpace: "pre-wrap" }}>{detailMsg?.content}</div>
      </Modal>
    </div>
  );

  return (
    <>
      {contextHolder}
      <Dropdown
        open={dropdownOpen}
        onOpenChange={handleDropdownOpenChange}
        trigger={["click"]}
        placement="bottomRight"
        popupRender={() => dropdownContent}
      >
        <Badge count={unreadCount} overflowCount={99} showZero={false} offset={[-4, 4]}>
          <div
            style={{
              width: 28,
              height: 28,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              backgroundColor: "#EFF4F9",
              borderRadius: "50%",
              cursor: "pointer",
            }}
          >
            <BellOutlined style={{ fontSize: 14, color: "#4D5E7D" }} />
          </div>
        </Badge>
      </Dropdown>
    </>
  );
});

export default NotificationBell;
