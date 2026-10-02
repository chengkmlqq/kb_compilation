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
  Select,
  Space,
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
  FolderAddOutlined,
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
  apiWikiLint,
  apiWikiRebuildLinks,
  apiWikiSearch,
  apiWikiStats,
  apiWikiTree,
  apiWikiUpdatePage,
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
          pagination={false}
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
    </>
  );
}
