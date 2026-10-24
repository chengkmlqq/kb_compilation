"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { App, Button, Col, Dropdown, Input, Row, Spin } from "antd";
import {
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  MessageOutlined,
  MoreOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  StopOutlined,
} from "@ant-design/icons";
import {
  apiCopyAgent,
  apiCreateAgent,
  apiDeleteAgent,
  apiListAgents,
  apiUpdateAgent,
  AgentItem,
} from "@/lib/api";
import AgentEditorModal, { AgentEditorValues } from "@/components/AgentEditorModal";
import AgentAvatar from "@/components/AgentAvatar";
import { ModoTabs } from "@/components/biz/modo-tabs";

// 2026-10-16 对齐知识库页（kbs/page.tsx）：页面标题图标 / 顶栏彩色搜索与新建图标 / 空态插画
// 均从同批 DSB 资源与内联 SVG 复用，保证两页 UI 一致。
const KBS_ASSET_BASE = "/wiki/kbs";
const headerTitleIcon = `${KBS_ASSET_BASE}/header-title-icon.png`;

/** 彩色放大镜（对齐 modo-design IconSearchColor：viewBox 12x12，圆圈 opacity .25 + 实心头） */
const SearchColorIcon = ({ size = 14 }: { size?: number }) => (
  <svg
    viewBox="0 0 12 12"
    fill="none"
    stroke="none"
    width={size}
    height={size}
    style={{ verticalAlign: "-2px" }}
    aria-hidden
  >
    <path
      fillRule="evenodd"
      d="M5 8a3 3 0 1 0-3-3 3 3 0 0 0 3 3Zm0 1a4 4 0 1 0-4-4 4 4 0 0 0 4 4Z"
      opacity=".25"
      fill="currentColor"
    />
    <path
      d="M7.64 8.005a4.018 4.018 0 0 0 .365-.364l.35.097a.5.5 0 0 1 .219.129L11 10.293l-.707.707-2.426-2.426a.5.5 0 0 1-.129-.22Z"
      fill="currentColor"
    />
  </svg>
);

/** 彩色加号（对齐 modo-design IconAddColor：圆圈 opacity .25 + 实心加号） */
const AddColorIcon = ({ size = 14 }: { size?: number }) => (
  <svg
    viewBox="0 0 12 12"
    fill="none"
    stroke="none"
    width={size}
    height={size}
    style={{ verticalAlign: "-2px" }}
    aria-hidden
  >
    <path
      fillRule="evenodd"
      d="M6 11a5 5 0 1 0-5-5 5 5 0 0 0 5 5Zm.5-4.5v2h-1v-2h-2v-1h2v-2h1v2h2v1Z"
      opacity=".25"
      fill="currentColor"
    />
    <path d="M5.5 8.5h1v-2h2v-1h-2v-2h-1v2h-2v1h2Z" fill="currentColor" />
  </svg>
);

/** arco Empty 默认插画（viewBox 48 / 描边 4）；容器 font-size 48px → 插画 48px（对齐 arco Empty 原生规格） */
const EmptyIllustration = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={4} aria-hidden>
    <path d="M24 5v6m7 1 4-4m-18 4-4-4m28.5 22H28s-1 3-4 3-4-3-4-3H6.5M40 41H8a2 2 0 0 1-2-2v-8.46a2 2 0 0 1 .272-1.007l6.15-10.54A2 2 0 0 1 14.148 18H33.85a2 2 0 0 1 1.728.992l6.149 10.541A2 2 0 0 1 42 30.541V39a2 2 0 0 1-2 2Z" />
  </svg>
);

const modeLabel = (c: Record<string, unknown> | undefined) =>
  c?.agent_mode === "smart-reasoning" ? "智能推理" : "快速问答";

const modeIcon = (
  c: Record<string, unknown> | undefined,
  builtin?: boolean,
  name?: string,
) => {
  if (builtin) {
    return c?.agent_mode === "smart-reasoning" ? (
      <RobotOutlined style={{ fontSize: 28, color: "#1677ff" }} />
    ) : (
      <MessageOutlined style={{ fontSize: 28, color: "#52c41a" }} />
    );
  }
  // 自定义智能体：名称派生的渐变首字头像（对齐 WeKnora AgentAvatar）；
  // 若后端存了自定义 avatar 字符串（emoji/图标名）则优先用它。
  if (c?.avatar) {
    return (
      <span style={{ fontSize: 28 }}>
        {String(c.avatar)}
      </span>
    );
  }
  return <AgentAvatar name={name || ""} size="medium" />;
};

const badgeText = (c: Record<string, unknown> | undefined, builtin?: boolean) => {
  const parts: string[] = [];
  if (builtin) parts.push("内置");
  const kbCount = Array.isArray(c?.knowledge_bases) ? c.knowledge_bases.length : 0;
  if (kbCount > 0) parts.push(`知识库 ${kbCount}`);
  const tools = Array.isArray(c?.allowed_tools) ? c.allowed_tools : [];
  if (tools.length > 0) parts.push(`工具 ${tools.length}`);
  if (c?.enable_query_expansion || c?.enable_rewrite) parts.push("意图改写");
  if (c?.vlm_model_id) parts.push("多模态");
  return parts.join(" · ") || "基础配置";
};

/** 动态选项卡条目（复用用户管理页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务内容 */
type AgentTab = { key: string; title: string; children: ReactNode };

export default function AgentsPage() {
  const { message, modal } = App.useApp();
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<AgentItem | null>(null);

  // ---- 动态选项卡（复用用户管理页 ModoTabs 模式）----
  // 首个 tab「智能体配置」= 页面标题 + 列表，不可关闭；后续内容较多的详情/编辑表单可 push 进 tabs 以选项卡打开。
  // 本轮先预留能力（容器 + 开关 tab 机制就位），具体接入哪些内容后续再定。
  const [tabs, setTabs] = useState<AgentTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListAgents(1, 100);
      if (res.success) setAgents(res.data?.items || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        (a.description || "").toLowerCase().includes(q)
    );
  }, [agents, search]);

  const handleSubmit = async (values: AgentEditorValues) => {
    if (editing) {
      return apiUpdateAgent(editing.id, {
        name: values.name,
        description: values.description,
        config: values.config,
      });
    }
    return apiCreateAgent({
      name: values.name,
      description: values.description,
      config: values.config,
    });
  };

  const onDelete = (agent: AgentItem) => {
    modal.confirm({
      title: `删除智能体「${agent.name}」？`,
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await apiDeleteAgent(agent.id);
        if (res.success) {
          message.success("已删除");
          void load();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  const onCopy = async (agent: AgentItem) => {
    const res = await apiCopyAgent(agent.id);
    if (res.success) {
      message.success(`已复制为「${res.data?.name || agent.name} - 副本」`);
      void load();
    } else {
      message.error(res.message || "复制失败");
    }
  };

  const onToggleDisabled = async (agent: AgentItem) => {
    const cfg = { ...(agent.config || {}) };
    const disabled = Boolean(cfg.disabled);
    const res = await apiUpdateAgent(agent.id, {
      config: { ...cfg, disabled: !disabled },
    });
    if (res.success) {
      message.success(disabled ? "已启用" : "已停用");
      void load();
    } else {
      message.error(res.message || "操作失败");
    }
  };

  const menuItems = (agent: AgentItem) => {
    const disabled = Boolean(agent.config?.disabled);
    return [
      { key: "edit", label: "编辑", icon: <EditOutlined /> },
      { key: "copy", label: "复制", icon: <CopyOutlined /> },
      { type: "divider" as const },
      disabled
        ? { key: "enable", label: "启用", icon: <PlayCircleOutlined /> }
        : { key: "disable", label: "停用", icon: <StopOutlined /> },
      { type: "divider" as const },
      { key: "delete", label: "删除", danger: true, icon: <DeleteOutlined /> },
    ];
  };

  // 列表区（首个选项卡内容）：卡片网格滚动 + 空态 + 编辑弹窗
  // 对齐知识库页（kbs/page.tsx）：顶部搜索与新建在 ModoTabs tabBarExtraContent，内容区直排卡片网格、无外层 Card。
  const listPane = (
    <div className="agents-list-page">
      <div className="agents-content">
        {loading && visible.length === 0 ? (
          <div style={{ display: "flex", justifyContent: "center", paddingTop: 96 }}>
            <Spin size="large" tip="正在加载..." />
          </div>
        ) : !loading && visible.length === 0 ? (
          /* 空态（对齐 arco Empty 原生规格：48px 插画 + 「暂无数据」） */
          <div className="agents-empty">
            <div className="agents-empty-wrapper">
              <div className="agents-empty-image">
                <EmptyIllustration />
              </div>
              <div className="agents-empty-description">暂无数据</div>
            </div>
          </div>
        ) : (
          <Spin spinning={loading}>
          <Row gutter={[16, 16]}>
            {visible.map((agent) => {
              const disabled = Boolean(agent.config?.disabled);
              return (
                <Col key={agent.id} xs={24} sm={12} lg={8} xl={6}>
                  {/* 智能体卡片（对齐知识库卡片 kb-kbs-card 三段式：header 图标+标题 / 2行描述 / footer 标签+操作） */}
                  <div
                    className="kb-agent-card"
                    style={{ opacity: disabled ? 0.55 : 1 }}
                    title={agent.description || "（无描述）"}
                  >
                    <div className="kb-agent-card-header">
                      <div className="kb-agent-card-icon">
                        {modeIcon(agent.config, agent.is_builtin, agent.name)}
                      </div>
                      <div className="kb-agent-card-detail">
                        <div className="kb-agent-card-title-row">
                          <div className="kb-agent-card-title-name" title={agent.name}>
                            {agent.name}
                          </div>
                        </div>
                        <div className="kb-agent-card-meta">
                          {modeLabel(agent.config)}
                          {disabled ? " · 已停用" : ""}
                        </div>
                      </div>
                    </div>
                    <div className="kb-agent-card-description">
                      {agent.description || "（无描述）"}
                    </div>
                    <div className="kb-agent-card-footer">
                      <div
                        className="kb-agent-card-footer-left"
                        title={badgeText(agent.config, agent.is_builtin)}
                      >
                        {badgeText(agent.config, agent.is_builtin)}
                      </div>
                      <div
                        className="kb-agent-card-footer-right"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Dropdown
                          menu={{
                            items: menuItems(agent),
                            onClick: ({ key }) => {
                              if (key === "edit") {
                                setEditing(agent);
                                setEditorOpen(true);
                              } else if (key === "copy") void onCopy(agent);
                              else if (key === "enable" || key === "disable")
                                void onToggleDisabled(agent);
                              else if (key === "delete") onDelete(agent);
                            },
                          }}
                          trigger={["click"]}
                        >
                          <Button type="text" size="small" icon={<MoreOutlined />} />
                        </Dropdown>
                      </div>
                    </div>
                  </div>
                </Col>
              );
            })}
          </Row>
          </Spin>
        )}

        <AgentEditorModal
          mode={editing ? "edit" : "create"}
          open={editorOpen}
          initial={
            editing
              ? { name: editing.name, description: editing.description ?? "", config: editing.config }
              : undefined
          }
          onClose={(changed) => {
            setEditorOpen(false);
            setEditing(null);
            if (changed) void load();
          }}
          onSubmit={handleSubmit}
        />
      </div>
    </div>
  );

  return (
    // 2026-10-16: 对齐知识库页范式——外层定高不滚动，顶栏 ModoTabs（标题图标 + 搜索 + 新建），内容区直排卡片网格
    <div
      className="agents-page"
      style={{
        height: "calc(100vh - var(--app-header-h, 48px))",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
        background: "#fff",
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
        tabBarExtraContent={
          <div className="agents-tab-actions">
            <Input
              className="agents-search"
              placeholder="输入并搜索..."
              prefix={<SearchColorIcon size={14} />}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onPressEnter={() => setSearch(search.trim())}
            />
            <Button
              className="agents-new-btn"
              type="primary"
              icon={<AddColorIcon size={12} />}
              onClick={() => {
                setEditing(null);
                setEditorOpen(true);
              }}
            >
              新建
            </Button>
          </div>
        }
        items={[
          {
            key: "home",
            // 页面标题：DSB header-title-icon.png（16px）置于标题左侧（对齐知识库页）
            label: (
              <span className="agents-tab-title">
                <img className="agents-tab-title-icon" src={headerTitleIcon} alt="" />
                智能体配置
              </span>
            ),
            children: listPane,
          },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: t.children })),
        ]}
      />
    </div>
  );
}