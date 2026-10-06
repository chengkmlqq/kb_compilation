"use client";
/**
 * Wiki 浏览视图：左侧目录文档树 + 右侧 md 内容（对齐 WeKnora wiki 选项卡）。
 * 树数据来自详情页已加载的 WikiTree（folders + pages，folder_id="" 为根级）。
 * 双链 [[slug]] 原地切换页面，不跳独立阅读页；编辑/反馈弹窗内嵌。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Radio,
  Space,
  Spin,
  Tag,
  Tree,
  Typography,
} from "antd";
import { ApartmentOutlined, EditOutlined, MessageOutlined } from "@ant-design/icons";
import type { TreeDataNode } from "antd";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  apiWikiListFeedback,
  apiWikiPage,
  apiWikiSubmitFeedback,
  apiWikiUpdatePage,
  WikiFeedbackItem,
  WikiFolderItem,
  WikiPageDetail,
  WikiPageItem,
} from "@/lib/api";

const TYPE_COLOR: Record<string, string> = {
  entity: "purple",
  concept: "blue",
  summary: "gold",
};
const TYPE_OPTIONS = [
  { value: "entity", label: "实体" },
  { value: "concept", label: "概念" },
  { value: "summary", label: "摘要" },
];

// [[slug]] / [[slug|别名]] 双链 → markdown 链接（href 规整为 /kbs/{id}/wiki/{slug}，组件内拦截原地切换）
function renderWikiLinks(content: string, kbId: string): string {
  if (!content) return content;
  const wikiLinkRe = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;
  return content.replace(wikiLinkRe, (_m, slug: string, alias?: string) => {
    const clean = (slug || "").trim();
    if (!clean) return _m;
    const label = (alias || "").trim() || clean;
    const href = `/kbs/${encodeURIComponent(kbId)}/wiki/${encodeURIComponent(clean)}`;
    return `[${label}](${href})`;
  });
}

interface Props {
  kbId: string;
  folders: WikiFolderItem[];
  pages: WikiPageItem[];
  focusSlug?: string;
  onTreeChanged?: () => void;
}

export default function WikiBrowseView({ kbId, folders, pages, focusSlug, onTreeChanged }: Props) {
  const router = useRouter();
  const { message } = App.useApp();
  const [selectedSlug, setSelectedSlug] = useState<string>(focusSlug || "");
  const [page, setPage] = useState<WikiPageDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);
  // 编辑
  const [editOpen, setEditOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editForm] = Form.useForm();
  // 反馈
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackType, setFeedbackType] = useState("helpful");
  const [feedbackContent, setFeedbackContent] = useState("");
  const [feedbackSaving, setFeedbackSaving] = useState(false);
  const [pageFeedback, setPageFeedback] = useState<WikiFeedbackItem[]>([]);

  const loadPage = useCallback(
    async (slug: string) => {
      setLoading(true);
      setSelectedSlug(slug);
      const res = await apiWikiPage(kbId, slug);
      if (res.success && res.data) {
        setPage(res.data);
        setNotFound(false);
      } else {
        setPage(null);
        setNotFound(true);
      }
      setLoading(false);
    },
    [kbId],
  );

  // 默认选中第一个页面；focusSlug 变化时跟随
  useEffect(() => {
    if (pages.length === 0) return;
    const target =
      pages.find((p) => p.slug === (focusSlug || "") || p.slug === selectedSlug)?.slug ||
      pages[0].slug;
    void loadPage(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kbId, focusSlug, pages.length]);

  const loadFeedback = useCallback(async () => {
    if (!page) return;
    const res = await apiWikiListFeedback(kbId, page.slug);
    if (res.success && res.data) setPageFeedback(res.data.items);
  }, [kbId, page]);
  useEffect(() => {
    if (feedbackOpen) void loadFeedback();
  }, [feedbackOpen, loadFeedback]);

  const openEdit = useCallback(() => {
    if (!page) return;
    editForm.setFieldsValue({
      title: page.title,
      page_type: page.page_type,
      summary: page.summary || "",
      content: page.content || "",
    });
    setEditOpen(true);
  }, [page, editForm]);

  const submitEdit = async () => {
    if (!page) return;
    const values = await editForm.validateFields();
    setSaving(true);
    try {
      const res = await apiWikiUpdatePage(kbId, page.slug, {
        title: values.title,
        page_type: values.page_type,
        summary: values.summary || undefined,
        content: values.content || "",
      });
      if (res.success) {
        message.success("页面已保存");
        setEditOpen(false);
        void loadPage(page.slug);
        onTreeChanged?.();
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  const submitFeedback = async () => {
    if (!page) return;
    setFeedbackSaving(true);
    try {
      const res = await apiWikiSubmitFeedback(kbId, page.slug, feedbackType, feedbackContent);
      if (res.success) {
        message.success("反馈已提交");
        setFeedbackContent("");
        void loadFeedback();
      } else {
        message.error(res.message || "提交失败");
      }
    } finally {
      setFeedbackSaving(false);
    }
  };

  // 树构建：folder 层级 + 页面挂载（folder_id="" 页面挂根）
  const treeData = useMemo<TreeDataNode[]>(() => {
    const folderById = new Map<string, WikiFolderItem>();
    folders.forEach((f) => folderById.set(f.id, f));
    const pageByFolder = new Map<string, WikiPageItem[]>();
    const rootPages: WikiPageItem[] = [];
    pages.forEach((p) => {
      const fid = p.folder_id || "";
      if (fid === "") rootPages.push(p);
      else {
        const arr = pageByFolder.get(fid) || [];
        arr.push(p);
        pageByFolder.set(fid, arr);
      }
    });
    const pageNode = (p: WikiPageItem): TreeDataNode => ({
      key: `page:${p.slug}`,
      title: p.title,
      isLeaf: true,
    });
    const folderNode = (f: WikiFolderItem): TreeDataNode => ({
      key: `folder:${f.id}`,
      title: (
        <Space size={4}>
          <span>{f.name}</span>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {f.page_count}
          </Typography.Text>
        </Space>
      ),
      children: [
        ...[...folderById.values()]
          .filter((c) => c.parent_id === f.id)
          .sort((a, b) => a.name.localeCompare(b.name))
          .map(folderNode),
        ...(pageByFolder.get(f.id) || []).map(pageNode),
      ],
    });
    const rootFolderNodes = [...folderById.values()]
      .filter((f) => !f.parent_id)
      .sort((a, b) => a.name.localeCompare(b.name))
      .map(folderNode);
    return [...rootFolderNodes, ...rootPages.map(pageNode)];
  }, [folders, pages]);

  const handleSelect = (keys: React.Key[]) => {
    const key = keys[0] as string | undefined;
    if (key && key.startsWith("page:")) void loadPage(key.slice(5));
  };

  const onLocalLink = (e: React.MouseEvent<HTMLAnchorElement>) => {
    const href = e.currentTarget.getAttribute("href") || "";
    const m = href.match(/\/wiki\/([^/]+)$/);
    if (m) {
      e.preventDefault();
      void loadPage(decodeURIComponent(m[1]));
    }
  };

  const renderLinkTags = (items: { slug: string; title: string; page_type: string }[]) => (
    <Space wrap>
      {items.map((link) => (
        <Tag
          key={link.slug}
          color={TYPE_COLOR[link.page_type] || "default"}
          style={{ cursor: "pointer" }}
          onClick={() => void loadPage(link.slug)}
        >
          {link.title}
        </Tag>
      ))}
    </Space>
  );

  if (loading && !page) {
    return (
      <Card>
        <Spin />
      </Card>
    );
  }

  return (
    <div style={{ display: "flex", gap: 16 }}>
      {/* 左侧目录文档树 */}
      <Card
        size="small"
        style={{ width: 280, flexShrink: 0, maxHeight: 560, overflowY: "auto" }}
        title={
          <Space size={8}>
            <span>目录</span>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {treeData.length} 项
            </Typography.Text>
          </Space>
        }
      >
        {treeData.length === 0 ? (
          <Empty description="暂无 wiki 页面" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <Tree
            defaultExpandAll
            selectedKeys={selectedSlug ? [`page:${selectedSlug}`] : []}
            onSelect={handleSelect}
            treeData={treeData}
          />
        )}
      </Card>
      {/* 右侧内容 */}
      <Card style={{ flex: 1, minWidth: 0 }}>
        {notFound || !page ? (
          <Empty description={notFound ? `Wiki 页面不存在: ${selectedSlug}` : "从左侧选择页面"} />
        ) : (
          <Space direction="vertical" size="small" style={{ display: "flex" }}>
            <Space style={{ justifyContent: "space-between", width: "100%" }} wrap>
              <Space wrap>
                <Typography.Title level={4} style={{ margin: 0 }}>
                  {page.title}
                </Typography.Title>
                <Tag color={TYPE_COLOR[page.page_type] || "default"}>{page.page_type}</Tag>
              </Space>
              <Space>
                <Button size="small" icon={<EditOutlined />} onClick={openEdit}>
                  编辑
                </Button>
                <Button size="small" icon={<MessageOutlined />} onClick={() => setFeedbackOpen(true)}>
                  反馈
                </Button>
                <Button
                  size="small"
                  icon={<ApartmentOutlined />}
                  onClick={() =>
                    router.push(`/kbs/${kbId}?wiki=graph&focus=${encodeURIComponent(page.slug)}`)
                  }
                >
                  图谱中查看
                </Button>
              </Space>
            </Space>
            {page.summary && <Typography.Paragraph type="secondary">{page.summary}</Typography.Paragraph>}
            <hr />
            <div className="kb-markdown">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{ a: (props) => <a {...props} onClick={onLocalLink} /> }}
              >
                {renderWikiLinks(page.content || "", kbId)}
              </ReactMarkdown>
            </div>
            {(page.links.length > 0 || page.in_links.length > 0) && (
              <Card size="small" title="双向链接">
                <Space direction="vertical" size="middle" style={{ display: "flex" }}>
                  {page.in_links.length > 0 && (
                    <div>
                      <Typography.Text type="secondary">
                        被引用（{page.in_links.length} 个页面链接到此页）
                      </Typography.Text>
                      <div style={{ marginTop: 8 }}>{renderLinkTags(page.in_links)}</div>
                    </div>
                  )}
                  {page.links.length > 0 && (
                    <div>
                      <Typography.Text type="secondary">引用（{page.links.length}）</Typography.Text>
                      <div style={{ marginTop: 8 }}>{renderLinkTags(page.links)}</div>
                    </div>
                  )}
                </Space>
              </Card>
            )}
          </Space>
        )}
      </Card>
      {/* 编辑弹窗 */}
      <Modal
        title="编辑页面"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => void submitEdit()}
        confirmLoading={saving}
        width={720}
      >
        <Form form={editForm} layout="vertical">
          <Form.Item name="title" label="标题" rules={[{ required: true, message: "请输入标题" }]}>
            <Input />
          </Form.Item>
          <Form.Item name="page_type" label="类型">
            <Radio.Group options={TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item name="summary" label="摘要">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="content" label="内容（支持 Markdown + [[slug]] 双链）">
            <Input.TextArea rows={14} />
          </Form.Item>
        </Form>
      </Modal>
      {/* 反馈弹窗 */}
      <Modal
        title="页面反馈"
        open={feedbackOpen}
        onCancel={() => setFeedbackOpen(false)}
        onOk={() => void submitFeedback()}
        confirmLoading={feedbackSaving}
      >
        <Space direction="vertical" style={{ display: "flex" }}>
          <Radio.Group value={feedbackType} onChange={(e) => setFeedbackType(e.target.value)}>
            <Radio.Button value="helpful">有帮助</Radio.Button>
            <Radio.Button value="wrong">内容错误</Radio.Button>
            <Radio.Button value="suggestion">建议</Radio.Button>
          </Radio.Group>
          <Input.TextArea
            rows={3}
            placeholder="补充说明（可选）"
            value={feedbackContent}
            onChange={(e) => setFeedbackContent(e.target.value)}
          />
          {pageFeedback.length > 0 && (
            <Typography.Text type="secondary">已有 {pageFeedback.length} 条反馈</Typography.Text>
          )}
        </Space>
      </Modal>
    </div>
  );
}