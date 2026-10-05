"use client";

/**
 * Wiki 管理面板 —— 本系统知识库的 wiki 页面管理（对齐 WeKnora 知识库功能）。
 *
 * 工具栏：统计 / 检查(lint) / 重建链接 / 新建目录 / 新建页面
 * 表格：页面标题(点击跳详情)/类型/目录/摘要 + 编辑/删除操作
 * 弹窗：新建/编辑页面（标题/类型/内容 markdown/摘要/目录）、新建目录
 */
import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  AuditOutlined,
  DeleteOutlined,
  EditOutlined,
  FileAddOutlined,
  FileSearchOutlined,
  FolderAddOutlined,
  MessageOutlined,
  ReloadOutlined,
  SearchOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import {
  apiWikiCreateFolder,
  apiWikiCreatePage,
  apiWikiDeleteFolder,
  apiWikiDeletePage,
  apiWikiIndex,
  apiWikiLint,
  apiWikiListAllFeedback,
  apiWikiLogs,
  apiWikiRebuildLinks,
  apiWikiSearch,
  apiWikiStats,
  apiWikiTree,
  apiWikiUpdateFeedbackStatus,
  apiWikiUpdatePage,
  WikiFeedbackItem,
  WikiIndexData,
  WikiLogItem,
  WikiSearchItem,
  WikiStatsData,
  WikiTree,
} from "@/lib/api";

const { Text } = Typography;

const PAGE_TYPE_OPTIONS = [
  { value: "entity", label: "实体" },
  { value: "concept", label: "概念" },
  { value: "summary", label: "摘要" },
];

const ISSUE_TYPE_LABEL: Record<string, string> = {
  empty_content: "空内容",
  orphan: "孤立页",
  broken_link: "断链",
};

const ACTION_LABEL: Record<string, string> = {
  page_create: "新建页面",
  page_update: "更新页面",
  page_delete: "删除页面",
  folder_create: "新建目录",
  folder_update: "更新目录",
  folder_delete: "删除目录",
  rebuild_links: "重建链接",
};

const ACTION_COLOR: Record<string, string> = {
  page_create: "green",
  page_update: "blue",
  page_delete: "red",
  folder_create: "green",
  folder_update: "blue",
  folder_delete: "red",
  rebuild_links: "cyan",
};

const TYPE_LABEL: Record<string, string> = {
  entity: "实体",
  concept: "概念",
  summary: "摘要",
};

const TYPE_COLOR: Record<string, string> = {
  entity: "purple",
  concept: "blue",
  summary: "gold",
};

export default function WikiManagePanel({ kbId }: { kbId: string }) {
  const { message } = App.useApp();
  const router = useRouter();

  const [wiki, setWiki] = useState<WikiTree | null>(null);
  const [stats, setStats] = useState<WikiStatsData | null>(null);
  const [loading, setLoading] = useState(false);

  // wiki 搜索
  const [searchQ, setSearchQ] = useState("");
  const [searchItems, setSearchItems] = useState<WikiSearchItem[] | null>(null);
  const [searching, setSearching] = useState(false);

  // wiki 索引 / 操作日志
  const [logOpen, setLogOpen] = useState(false);
  const [indexOpen, setIndexOpen] = useState(false);
  const [logs, setLogs] = useState<WikiLogItem[]>([]);
  const [indexData, setIndexData] = useState<WikiIndexData | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);
  const [indexLoading, setIndexLoading] = useState(false);

  const openLogs = async () => {
    setLogOpen(true);
    setLogsLoading(true);
    try {
      const res = await apiWikiLogs(kbId);
      if (res.success && res.data) setLogs(res.data.items);
      else message.error(res.message || "加载日志失败");
    } finally {
      setLogsLoading(false);
    }
  };

  const openIndex = async () => {
    setIndexOpen(true);
    setIndexLoading(true);
    try {
      const res = await apiWikiIndex(kbId);
      if (res.success && res.data) setIndexData(res.data);
      else message.error(res.message || "加载索引失败");
    } finally {
      setIndexLoading(false);
    }
  };

  // 反馈管理
  const [fbOpen, setFbOpen] = useState(false);
  const [fbItems, setFbItems] = useState<WikiFeedbackItem[]>([]);
  const [fbLoading, setFbLoading] = useState(false);
  const [fbStatus, setFbStatus] = useState("");

  const loadFeedbackList = useCallback(async (status = fbStatus) => {
    setFbLoading(true);
    try {
      const res = await apiWikiListAllFeedback(kbId, status || undefined);
      if (res.success && res.data) setFbItems(res.data.items);
      else message.error(res.message || "加载反馈失败");
    } finally {
      setFbLoading(false);
    }
  }, [kbId, fbStatus]);

  const openFeedback = async () => {
    setFbOpen(true);
    await loadFeedbackList();
  };

  const setFbStatusFilter = async (v: string) => {
    setFbStatus(v);
    setFbLoading(true);
    try {
      const res = await apiWikiListAllFeedback(kbId, v || undefined);
      if (res.success && res.data) setFbItems(res.data.items);
      else message.error(res.message || "加载反馈失败");
    } finally {
      setFbLoading(false);
    }
  };

  const changeFbStatus = async (id: string, status: string) => {
    const res = await apiWikiUpdateFeedbackStatus(kbId, id, status);
    if (res.success) {
      message.success("状态已更新");
      await loadFeedbackList();
    } else {
      message.error(res.message || "更新失败");
    }
  };

  // 页面编辑弹窗
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<{ slug: string; title: string } | null>(null);
  const [editForm] = Form.useForm();

  // 新建目录
  const [folderOpen, setFolderOpen] = useState(false);
  const [folderForm] = Form.useForm();

  // lint 结果
  const [lintOpen, setLintOpen] = useState(false);
  const [lintData, setLintData] = useState<{ total_issues: number; issues: any[]; broken_link_count: number } | null>(null);
  const [lintLoading, setLintLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [treeRes, statsRes] = await Promise.all([apiWikiTree(kbId), apiWikiStats(kbId)]);
      if (treeRes.success && treeRes.data) setWiki(treeRes.data);
      if (statsRes.success && statsRes.data) setStats(statsRes.data);
    } finally {
      setLoading(false);
    }
  }, [kbId]);

  useEffect(() => {
    void load();
  }, [load]);

  const doSearch = async () => {
    const q = searchQ.trim();
    if (!q) {
      setSearchItems(null);
      return;
    }
    setSearching(true);
    try {
      const res = await apiWikiSearch(kbId, q);
      if (res.success && res.data) setSearchItems(res.data.items);
      else message.error(res.message || "搜索失败");
    } finally {
      setSearching(false);
    }
  };

  const clearSearch = () => {
    setSearchQ("");
    setSearchItems(null);
  };

  const folderOptions = (wiki?.folders || []).map((f) => ({ value: f.id, label: f.name }));

  const openCreate = () => {
    setEditing(null);
    editForm.resetFields();
    setEditOpen(true);
  };

  const openEdit = (slug: string, title: string) => {
    setEditing({ slug, title });
    editForm.setFieldsValue({ title, page_type: "entity", content: "", summary: "", folder_id: undefined });
    setEditOpen(true);
  };

  const submitPage = async () => {
    const values = await editForm.validateFields();
    if (editing) {
      const res = await apiWikiUpdatePage(kbId, editing.slug, {
        title: values.title,
        page_type: values.page_type,
        content: values.content || "",
        summary: values.summary || undefined,
        folder_id: values.folder_id || undefined,
      });
      if (res.success) {
        message.success("页面已更新");
        setEditOpen(false);
        void load();
      } else {
        message.error(res.message || "更新失败");
      }
    } else {
      const res = await apiWikiCreatePage(kbId, {
        title: values.title,
        page_type: values.page_type,
        content: values.content || "",
        summary: values.summary || undefined,
        folder_id: values.folder_id || undefined,
      });
      if (res.success) {
        message.success(`页面已创建（slug=${res.data?.slug}）`);
        setEditOpen(false);
        void load();
      } else {
        message.error(res.message || "创建失败");
      }
    }
  };

  const deletePage = async (slug: string) => {
    const res = await apiWikiDeletePage(kbId, slug);
    if (res.success) {
      message.success("页面已删除（软删）");
      void load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const submitFolder = async () => {
    const values = await folderForm.validateFields();
    const res = await apiWikiCreateFolder(kbId, { name: values.name });
    if (res.success) {
      message.success("目录已创建");
      setFolderOpen(false);
      folderForm.resetFields();
      void load();
    } else {
      message.error(res.message || "创建目录失败");
    }
  };

  const deleteFolder = async (folderId: string, name: string) => {
    const res = await apiWikiDeleteFolder(kbId, folderId);
    if (res.success) {
      message.success(`目录「${name}」已删除`);
      void load();
    } else {
      message.error(res.message || `删除失败：${res.message || ""}`);
    }
  };

  const runLint = async () => {
    setLintLoading(true);
    try {
      const res = await apiWikiLint(kbId);
      if (res.success && res.data) {
        setLintData(res.data);
        setLintOpen(true);
      } else {
        message.error(res.message || "检查失败");
      }
    } finally {
      setLintLoading(false);
    }
  };

  const rebuildLinks = async () => {
    const res = await apiWikiRebuildLinks(kbId);
    if (res.success) {
      message.success(`双向链接已重建（新增 ${res.data?.added_links ?? 0} 条）`);
      void load();
    } else {
      message.error(res.message || "重建失败");
    }
  };

  const pages = wiki?.pages || [];
  // 搜索结果结构略异（无 folder_id），映射为表格行兼容结构
  const tableData = (
    searchItems
      ? searchItems.map((s) => ({
          id: s.slug,
          slug: s.slug,
          title: s.title,
          page_type: s.page_type,
          folder_id: "" as string,
          summary: s.summary ?? s.content_head ?? null,
        }))
      : pages
  );

  return (
    <>
      <Space direction="vertical" style={{ display: "flex" }} size="small">
        <Space wrap>
          <Input.Search
            placeholder="搜索 wiki 页面（标题/内容）"
            value={searchQ}
            onChange={(e) => setSearchQ(e.target.value)}
            onSearch={() => void doSearch()}
            loading={searching}
            allowClear
            onClear={clearSearch}
            style={{ width: 260 }}
          />
          <Button size="small" icon={<AuditOutlined />} onClick={() => void openLogs()}>
            操作日志
          </Button>
          <Button size="small" icon={<MessageOutlined />} onClick={() => void openFeedback()}>
            反馈
          </Button>
          <Button size="small" icon={<FileSearchOutlined />} onClick={() => void openIndex()}>
            索引
          </Button>
          {stats && (
            <>
              <Statistic title="页面" value={stats.total_pages} suffix={`/ ${stats.total_folders} 目录`} />
              <Statistic title="双向链接" value={stats.total_links} />
              <Statistic title="孤儿页" value={stats.orphan_count} valueStyle={{ color: stats.orphan_count > 0 ? "#cf1322" : undefined }} />
            </>
          )}
          <Tooltip title="检查 wiki 健康（空内容/孤立页/断链）">
            <Button icon={<AuditOutlined />} loading={lintLoading} onClick={() => void runLint()}>
              检查
            </Button>
          </Tooltip>
          <Tooltip title="按页面 [[slug]] 重建全部双向链接">
            <Button icon={<ReloadOutlined />} onClick={() => void rebuildLinks()}>
              重建链接
            </Button>
          </Tooltip>
          <Button icon={<FolderAddOutlined />} onClick={() => setFolderOpen(true)}>
            新建目录
          </Button>
          <Button type="primary" icon={<FileAddOutlined />} onClick={openCreate}>
            新建页面
          </Button>
        </Space>

        <Table
          rowKey="slug"
          size="small"
          loading={loading || searching}
          dataSource={tableData}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          locale={{
            emptyText: searchItems
              ? <Text type="secondary">未找到匹配「{searchQ}」的页面</Text>
              : <Text type="secondary">暂无 wiki 页面，文档解析入库后由后台自动生成，或手动新建</Text>,
          }}
          columns={[
            {
              title: "页面标题",
              dataIndex: "title",
              render: (v: string, row) => (
                <Typography.Link onClick={() => router.push(`/kbs/${kbId}/wiki/${row.slug}`)}>{v}</Typography.Link>
              ),
            },
            {
              title: "类型",
              dataIndex: "page_type",
              width: 90,
              render: (v: string) => <Tag>{v}</Tag>,
            },
            {
              title: "目录",
              dataIndex: "folder_id",
              width: 120,
              render: (v?: string) => {
                const f = (wiki?.folders || []).find((x) => x.id === v);
                return f ? f.name : <Text type="secondary">根</Text>;
              },
            },
            {
              title: "摘要",
              dataIndex: "summary",
              ellipsis: true,
              render: (v: string | null) => v || "-",
            },
            {
              title: "操作",
              width: 120,
              render: (_: unknown, row) => (
                <Space size={0}>
                  <Button
                    type="link"
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => openEdit(row.slug, row.title)}
                  >
                    编辑
                  </Button>
                  <Popconfirm title="确定删除该页面？" onConfirm={() => void deletePage(row.slug)}>
                    <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />

        {/* 目录行（含删除） */}
        {(wiki?.folders || []).length > 0 && (
          <Space wrap size={[8, 8]}>
            <Text type="secondary">目录：</Text>
            {(wiki?.folders || []).map((f) => (
              <Tag
                key={f.id}
                icon={<TeamOutlined />}
                closable
                onClose={(e) => {
                  e.preventDefault();
                  Modal.confirm({
                    title: `删除目录「${f.name}」？`,
                    content: "仅当目录下无页面时可删除",
                    onOk: () => deleteFolder(f.id, f.name),
                  });
                }}
              >
                {f.name}（{f.page_count}）
              </Tag>
            ))}
          </Space>
        )}
      </Space>

      {/* 新建/编辑页面弹窗 */}
      <Modal
        title={editing ? `编辑页面：${editing.title}` : "新建 wiki 页面"}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => void submitPage()}
        width={640}
      >
        <Form form={editForm} layout="vertical" initialValues={{ page_type: "entity" }}>
          <Form.Item name="title" label="标题" rules={[{ required: true, message: "请输入标题" }]}>
            <Input maxLength={255} />
          </Form.Item>
          <Form.Item name="page_type" label="类型">
            <Select options={PAGE_TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item name="folder_id" label="目录">
            <Select allowClear placeholder="根目录" options={folderOptions} />
          </Form.Item>
          <Form.Item name="summary" label="摘要">
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
          <Form.Item name="content" label="内容（支持 Markdown + [[slug]] 双链）">
            <Input.TextArea rows={8} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 新建目录弹窗 */}
      <Modal
        title="新建目录"
        open={folderOpen}
        onCancel={() => setFolderOpen(false)}
        onOk={() => void submitFolder()}
      >
        <Form form={folderForm} layout="vertical">
          <Form.Item name="name" label="目录名" rules={[{ required: true, message: "请输入目录名" }]}>
            <Input maxLength={255} />
          </Form.Item>
        </Form>
      </Modal>

      {/* lint 结果抽屉 */}
      <Drawer title="Wiki 健康检查" open={lintOpen} onClose={() => setLintOpen(false)} width={520}>
        {lintData && (
          <Space direction="vertical" style={{ display: "flex" }} size="small">
            <Space>
              <Statistic title="问题总数" value={lintData.total_issues} />
              <Statistic title="断链目标" value={lintData.broken_link_count} valueStyle={{ color: lintData.broken_link_count > 0 ? "#cf1322" : undefined }} />
            </Space>
            {lintData.issues.length === 0 ? (
              <Text type="success">✅ 未发现问题</Text>
            ) : (
              <Table
                rowKey="slug"
                size="small"
                dataSource={lintData.issues}
                pagination={false}
                columns={[
                  {
                    title: "类型",
                    dataIndex: "issue_type",
                    width: 90,
                    render: (v: string) => <Tag color="orange">{ISSUE_TYPE_LABEL[v] || v}</Tag>,
                  },
                  { title: "页面", dataIndex: "title", ellipsis: true },
                  {
                    title: "说明",
                    dataIndex: "detail",
                    render: (v: string) => <Text style={{ fontSize: 12 }}>{v}</Text>,
                  },
                ]}
              />
            )}
          </Space>
        )}
      </Drawer>

      <Drawer title="操作日志" open={logOpen} onClose={() => setLogOpen(false)} width={560}>
        <Table
          rowKey="id"
          size="small"
          loading={logsLoading}
          dataSource={logs}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          locale={{ emptyText: <Text type="secondary">暂无操作记录（页面/目录增删改、链接重建会记录）</Text> }}
          columns={[
            {
              title: "动作",
              dataIndex: "action",
              width: 130,
              render: (v: string) => <Tag color={ACTION_COLOR[v] || "default"}>{ACTION_LABEL[v] || v}</Tag>,
            },
            { title: "对象", dataIndex: "title", ellipsis: true },
            {
              title: "详情",
              dataIndex: "detail",
              render: (v: string) => <Text style={{ fontSize: 12 }}>{v}</Text>,
            },
            {
              title: "操作人",
              dataIndex: "operator",
              width: 90,
              render: (v: string) => <Text>{v || "-"}</Text>,
            },
            {
              title: "时间",
              dataIndex: "created_at",
              width: 150,
              render: (v: string) => <Text style={{ fontSize: 12 }}>{v ? v.replace("T", " ").slice(0, 16) : "-"}</Text>,
            },
          ]}
        />
      </Drawer>

      <Drawer title="Wiki 索引" open={indexOpen} onClose={() => setIndexOpen(false)} width={520}>
        {!indexData ? (
          <Spin />
        ) : (
          <Space direction="vertical" style={{ display: "flex" }} size="middle">
            <Space wrap>
              <Statistic title="页面" value={indexData.total_pages} />
              <Statistic title="目录" value={indexData.total_folders} />
            </Space>
            <div>
              <Typography.Text strong>页类型分布</Typography.Text>
              <div style={{ marginTop: 6 }}>
                {Object.entries(indexData.pages_by_type).map(([t, n]) => (
                  <Tag key={t} color={TYPE_COLOR[t] || "default"}>
                    {TYPE_LABEL[t] || t}: {n}
                  </Tag>
                ))}
              </div>
            </div>
            <div>
              <Typography.Text strong>最近更新</Typography.Text>
              <Table
                rowKey="slug"
                size="small"
                pagination={false}
                dataSource={indexData.recent_pages}
                columns={[
                  {
                    title: "页面",
                    dataIndex: "title",
                    render: (v: string, r) => (
                      <a
                        onClick={() => {
                          setIndexOpen(false);
                          void router.push(`/kbs/${kbId}/wiki/${r.slug}`);
                        }}
                      >
                        {v}
                      </a>
                    ),
                  },
                  {
                    title: "类型",
                    dataIndex: "page_type",
                    width: 90,
                    render: (v: string) => <Tag color={TYPE_COLOR[v] || "default"}>{TYPE_LABEL[v] || v}</Tag>,
                  },
                  {
                    title: "更新时间",
                    dataIndex: "updated_at",
                    width: 130,
                    render: (v: string) => <Text style={{ fontSize: 12 }}>{v ? v.replace("T", " ").slice(0, 16) : "-"}</Text>,
                  },
                ]}
              />
            </div>
          </Space>
        )}
      </Drawer>
      <Drawer
        title="页面反馈"
        open={fbOpen}
        onClose={() => setFbOpen(false)}
        width={640}
      >
        <Space direction="vertical" style={{ display: "flex" }} size="small">
          <Radio.Group
            value={fbStatus}
            onChange={(e) => void setFbStatusFilter(e.target.value)}
            optionType="button"
            size="small"
          >
            <Radio.Button value="">全部</Radio.Button>
            <Radio.Button value="open">待处理</Radio.Button>
            <Radio.Button value="resolved">已解决</Radio.Button>
            <Radio.Button value="ignored">已忽略</Radio.Button>
          </Radio.Group>
          <Table
            rowKey="id"
            size="small"
            loading={fbLoading}
            dataSource={fbItems}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            locale={{ emptyText: <Text type="secondary">暂无反馈</Text> }}
            columns={[
              {
                title: "页面",
                dataIndex: "slug",
                width: 140,
                ellipsis: true,
                render: (v: string) => (
                  <a onClick={() => void router.push(`/kbs/${kbId}/wiki/${v}`)}>{v}</a>
                ),
              },
              {
                title: "类型",
                dataIndex: "feedback_type",
                width: 80,
                render: (v: string) =>
                  <Tag color={v === "helpful" ? "green" : "red"}>{v === "helpful" ? "有帮助" : "问题"}</Tag>,
              },
              { title: "内容", dataIndex: "content", ellipsis: true },
              {
                title: "状态",
                dataIndex: "status",
                width: 90,
                render: (v: string) => (
                  <Tag color={v === "open" ? "orange" : v === "resolved" ? "green" : "default"}>
                    {v === "open" ? "待处理" : v === "resolved" ? "已解决" : "已忽略"}
                  </Tag>
                ),
              },
              {
                title: "操作",
                width: 130,
                render: (_: unknown, r: WikiFeedbackItem) => (
                  <Space size={4}>
                    {r.status !== "resolved" && (
                      <Button size="small" type="link" onClick={() => void changeFbStatus(r.id, "resolved")}>
                        解决
                      </Button>
                    )}
                    {r.status !== "ignored" && (
                      <Button size="small" type="link" onClick={() => void changeFbStatus(r.id, "ignored")}>
                        忽略
                      </Button>
                    )}
                    {r.status !== "open" && (
                      <Button size="small" type="link" onClick={() => void changeFbStatus(r.id, "open")}>
                        重开
                      </Button>
                    )}
                  </Space>
                ),
              },
            ]}
          />
        </Space>
      </Drawer>
    </>
  );
}
