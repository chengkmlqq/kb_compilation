"use client";
/**
 * Wiki 浏览视图：左侧目录文档树 + 右侧 md 内容（对齐 WeKnora wiki 选项卡）。
 * 目录树懒加载（对齐 WeKnora 侧栏）：初始只拉根级分支，展开目录时按 folder 取直接子项；
 * 深链 focusSlug 时沿父链逐级加载并自动展开；编辑/删除页面后全树按展开状态刷新。
 * 双链 [[slug]] 原地切换页面，不跳独立阅读页；编辑/反馈弹窗内嵌。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  apiWikiBranch,
  apiWikiFolders,
  apiWikiListFeedback,
  apiWikiPage,
  apiWikiSubmitFeedback,
  apiWikiUpdatePage,
  WikiFeedbackItem,
  WikiFolderNode,
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

/** antd Tree loadData 模式：按 key 替换节点并挂上 children */
function updateTreeData(
  list: TreeDataNode[],
  key: React.Key,
  children: TreeDataNode[],
): TreeDataNode[] {
  return list.map((node) => {
    if (node.key === key) return { ...node, children };
    if (node.children) {
      return { ...node, children: updateTreeData(node.children, key, children) };
    }
    return node;
  });
}

interface Props {
  kbId: string;
  focusSlug?: string;
  onTreeChanged?: () => void;
  onOpenGraph?: (slug: string) => void;
}

export default function WikiBrowseView({ kbId, focusSlug, onTreeChanged, onOpenGraph }: Props) {
  const router = useRouter();
  const { message } = App.useApp();
  const [treeData, setTreeData] = useState<TreeDataNode[]>([]);
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string>(focusSlug || "");
  const [page, setPage] = useState<WikiPageDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const expandedRef = useRef<React.Key[]>([]);
  const selectedRef = useRef<string>(focusSlug || "");
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
      selectedRef.current = slug;
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

  const folderNode = useCallback(
    (f: WikiFolderNode): TreeDataNode => ({
      key: `folder:${f.id}`,
      title: (
        <Space size={4}>
          <span>{f.name}</span>
          {(f.child_count ?? 0) > 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {f.child_count}
            </Typography.Text>
          )}
        </Space>
      ),
      // 无直接子项（子目录+子页面）则为叶子，不显示展开箭头
      isLeaf: (f.child_count ?? 0) === 0,
    }),
    [],
  );

  const pageNode = useCallback((p: WikiPageItem): TreeDataNode => {
    return {
      key: `page:${p.slug}`,
      title: p.title,
      isLeaf: true,
    };
  }, []);

  const fetchBranch = useCallback(
    async (folderId: string): Promise<TreeDataNode[]> => {
      const res = await apiWikiBranch(kbId, folderId);
      if (!res.success || !res.data) return [];
      const d = res.data;
      const nodes = [
        ...d.folders.map(folderNode),
        ...d.pages.map(pageNode),
      ];
      // 目录在前、页面在后，目录按名称排序
      nodes.sort((a, b) => {
        const ak = String(a.key);
        const bk = String(b.key);
        if (ak.startsWith("folder:") && bk.startsWith("page:")) return -1;
        if (ak.startsWith("page:") && bk.startsWith("folder:")) return 1;
        const at = typeof a.title === "string" ? a.title : String(a.title);
        const bt = typeof b.title === "string" ? b.title : String(b.title);
        return at.localeCompare(bt);
      });
      return nodes;
    },
    [kbId, folderNode, pageNode],
  );

  // 初始加载：根级分支 + focusSlug 深链定位（沿父链逐级展开）
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let tree = await fetchBranch("");
      if (cancelled) return;
      if (focusSlug) {
        const pageRes = await apiWikiPage(kbId, focusSlug);
        if (!cancelled && pageRes.success && pageRes.data) {
          const folderId = pageRes.data.folder_id || "";
          if (folderId) {
            const fRes = await apiWikiFolders(kbId);
            const folders = fRes.success ? fRes.data || [] : [];
            const chain: string[] = [];
            let cur = folderId;
            let guard = 0;
            while (cur && guard < 20) {
              chain.unshift(cur);
              const f = folders.find((x) => x.id === cur);
              cur = f?.parent_id || "";
              guard += 1;
            }
            for (const fid of chain) {
              if (cancelled) return;
              const children = await fetchBranch(fid);
              tree = updateTreeData(tree, `folder:${fid}`, children);
              setExpandedKeys((prev) => [...new Set([...prev, `folder:${fid}`])]);
            }
          }
          setSelectedSlug(focusSlug);
          selectedRef.current = focusSlug;
          void loadPage(focusSlug);
        }
      } else {
        // 默认选中第一个根级页面
        const rootPageKey = tree.find((n) => String(n.key).startsWith("page:"))?.key;
        if (rootPageKey) {
          const slug = String(rootPageKey).slice(5);
          setSelectedSlug(slug);
          selectedRef.current = slug;
          void loadPage(slug);
        }
      }
      if (!cancelled) setTreeData(tree);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kbId, focusSlug]);

  const onExpand = (keys: React.Key[]) => {
    expandedRef.current = keys;
    setExpandedKeys(keys);
  };

  // 展开目录时懒加载该目录的直接子项（对齐 WeKnora）
  const onLoadData = async (node: TreeDataNode): Promise<void> => {
    const key = String(node.key);
    if (!key.startsWith("folder:")) return;
    const fid = key.slice(7);
    const children = await fetchBranch(fid);
    setTreeData((prev) => updateTreeData(prev, key, children));
  };

  // 编辑/删除等树外变更后：按当前展开状态刷新已加载分支
  const reloadTree = useCallback(async () => {
    let tree = await fetchBranch("");
    for (const k of expandedRef.current) {
      const key = String(k);
      if (key.startsWith("folder:")) {
        const children = await fetchBranch(key.slice(7));
        tree = updateTreeData(tree, key, children);
      }
    }
    setTreeData(tree);
    const cur = selectedRef.current;
    if (cur) void loadPage(cur);
  }, [fetchBranch, loadPage]);

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
        void reloadTree();
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
    <div style={{ display: "flex", gap: 16, height: "100%", minHeight: 0 }}>
      {/* 左侧目录文档树（懒加载） */}
      <Card
        size="small"
        style={{
          width: 280,
          flexShrink: 0,
          height: "100%",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
        styles={{ body: { flex: 1, minHeight: 0, overflowY: "auto" } }}
        title={
          <Space size={8}>
            <span>目录</span>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              已加载 {treeData.length} 项
            </Typography.Text>
          </Space>
        }
      >
        {treeData.length === 0 ? (
          <Empty description="暂无 wiki 页面" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <Tree
            loadData={onLoadData}
            expandedKeys={expandedKeys}
            onExpand={onExpand}
            selectedKeys={selectedSlug ? [`page:${selectedSlug}`] : []}
            onSelect={handleSelect}
            treeData={treeData}
          />
        )}
      </Card>
      {/* 右侧内容 */}
      <Card
        style={{
          flex: 1,
          minWidth: 0,
          height: "100%",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
        styles={{ body: { flex: 1, minHeight: 0, overflowY: "auto" } }}
      >
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
                    onOpenGraph
                      ? onOpenGraph(page.slug)
                      : router.push(`/kbs/${kbId}?wiki=graph&focus=${encodeURIComponent(page.slug)}`)
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