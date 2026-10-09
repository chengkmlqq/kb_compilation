"use client";

/** 操作日志（独立页，对齐 ds system/system-logs）。 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Button, Card, Empty, Form, Input, Space, Table, Tag } from "antd";
import { apiListOperationLogs, SysLogItem } from "@/lib/api";
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";

/** 动态选项卡条目（复用用户管理页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务内容 */
type LogTab = { key: string; title: string; children: ReactNode };

export default function SystemLogsPage() {
  const [rows, setRows] = useState<SysLogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [searchForm] = Form.useForm<{ keyword: string }>();

  // ---- 动态选项卡（复用用户管理页 ModoTabs 模式）----
  // 首个 tab「操作日志」= 页面标题 + 列表，不可关闭；后续内容较多的详情/编辑表单可 push 进 tabs 以选项卡打开。
  // 本轮先预留能力（容器 + 开关 tab 机制就位），具体接入哪些内容后续再定。
  const [tabs, setTabs] = useState<LogTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListOperationLogs(page, pageSize, keyword);
      if (res.success) {
        setRows(res.data?.items || []);
        setTotal(res.data?.total ?? 0);
      }
    } finally {
      setLoading(false);
    }
  }, [keyword, page, pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  const doSearch = (v: { keyword?: string }) => {
    setPage(1);
    setKeyword((v.keyword || "").trim());
  };

  const doReset = () => {
    searchForm.resetFields();
    setPage(1);
    setKeyword("");
  };

  // 列表区（首个选项卡内容）：筛选 + 表格 + 钉底分页
  const listPane = (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      <Card
        style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            padding: "12px 16px 0",
          },
        }}
      >
        {/* 筛选表单（对齐用户管理页：内联表单 + 查询/重置） */}
        <Form
          form={searchForm}
          layout="inline"
          style={{ marginBottom: 12, flexShrink: 0 }}
          onFinish={doSearch}
          initialValues={{ keyword: "" }}
        >
          <Form.Item name="keyword" label="关键字">
            <Input allowClear placeholder="按用户/类型/内容搜索" style={{ width: 240 }} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button onClick={doReset}>重置</Button>
            </Space>
          </Form.Item>
        </Form>

        <Table<SysLogItem>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={rows}
          pagination={false}
          scroll={{ x: 900, y: "calc(100vh - 252px)" }}
          locale={{ emptyText: <Empty description="暂无日志" /> }}
          columns={[
            { title: "用户", dataIndex: "user_name" },
            {
              title: "类型",
              dataIndex: "oper_type",
              width: 110,
              render: (v: string) => <Tag color={v === "LOGIN" ? "blue" : "default"}>{v}</Tag>,
            },
            { title: "内容", dataIndex: "oper_content", ellipsis: true },
            { title: "URL", dataIndex: "oper_url", width: 160, ellipsis: true },
            { title: "时间", dataIndex: "oper_time", width: 170 },
          ]}
        />
        <div style={{ flexShrink: 0, marginTop: "auto" }}>
          <ModoPagination
            current={page}
            pageSize={pageSize}
            total={total}
            showTotal={(t) => `共 ${t} 条日志`}
            onChange={(p, ps) => {
              setPage(p);
              setPageSize(ps);
            }}
          />
        </div>
      </Card>
    </div>
  );

  return (
    // 2026-10-08: 对齐用户管理页范式——外层固定视口高度不滚动
    // 2026-10-09: 顶部改为「动态选项卡」（复用用户管理页 ModoTabs editable-card 模式）——首 tab「操作日志」
    // = 页面标题 + 列表（不可关闭），后续内容较多的详情/编辑表单可作为可关闭 tab 打开；本轮先预留能力。
    // 表格高度偏移 264 → 252（tab 导航 44px 取代卡片头 56px）。
    <div
      className="logs-page"
      style={{
        padding: 8,
        height: "calc(100vh - 45px)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey={activeTab}
        onChange={setActiveTab}
        onEdit={(key, action) => {
          if (action === "remove") closeTab(String(key));
        }}
        items={[
          { key: "home", label: "操作日志", children: listPane },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: t.children })),
        ]}
      />
    </div>
  );
}
