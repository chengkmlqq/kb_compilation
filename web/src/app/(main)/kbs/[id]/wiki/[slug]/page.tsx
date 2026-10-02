"use client";

import { useCallback, useEffect, useState } from "react";
import {
  App,
  Breadcrumb,
  Button,
  Card,
  Empty,
  Form,
  Input,
  Modal,
  Radio,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { ApartmentOutlined, EditOutlined, MessageOutlined } from "@ant-design/icons";
import { useParams, useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  apiWikiListFeedback,
  apiWikiPage,
  apiWikiSubmitFeedback,
  apiWikiUpdatePage,
  WikiFeedbackItem,
  WikiPageDetail,
} from "@/lib/api";

const TYPE_COLOR: Record<string, string> = {
  entity: "purple",
  concept: "blue",
  summary: "gold",
};

// 把内容里的 [[slug]] 双链转成 markdown 链接（渲染成可点击的 wiki 页跳转）
// 转义已有 markdown 链接避免破坏，且只处理未被 [](...) 包裹的双链
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

const TYPE_OPTIONS = [
  { value: "entity", label: "实体" },
  { value: "concept", label: "概念" },
  { value: "summary", label: "摘要" },
];

export default function WikiPageDetailPage() {
  const { id, slug } = useParams<{ id: string; slug: string }>();
  const router = useRouter();
  const { message } = App.useApp();
  const [page, setPage] = useState<WikiPageDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  // 编辑弹窗
  const [editOpen, setEditOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editForm] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    const res = await apiWikiPage(id, slug);
    if (res.success && res.data) {
      setPage(res.data);
      setNotFound(false);
    } else {
      setNotFound(true);
    }
    setLoading(false);
  }, [id, slug]);

  useEffect(() => {
    void load();
  }, [load]);

  const openEdit = () => {
    if (!page) return;
    editForm.setFieldsValue({
      title: page.title,
      page_type: page.page_type,
      summary: page.summary || "",
      content: page.content || "",
    });
    setEditOpen(true);
  };

  const submitEdit = async () => {
    const values = await editForm.validateFields();
    setSaving(true);
    try {
      const res = await apiWikiUpdatePage(id, page!.slug, {
        title: values.title,
        page_type: values.page_type,
        summary: values.summary || undefined,
        content: values.content || "",
      });
      if (res.success) {
        message.success("页面已保存");
        setEditOpen(false);
        void load();
      } else {
        message.error(res.message || "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  // 反馈弹窗
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackType, setFeedbackType] = useState("helpful");
  const [feedbackContent, setFeedbackContent] = useState("");
  const [feedbackSaving, setFeedbackSaving] = useState(false);
  const [pageFeedback, setPageFeedback] = useState<WikiFeedbackItem[]>([]);

  const loadFeedback = useCallback(async () => {
    if (!page) return;
    const res = await apiWikiListFeedback(id, page.slug);
    if (res.success && res.data) setPageFeedback(res.data.items);
  }, [id, page]);

  useEffect(() => {
    if (feedbackOpen) void loadFeedback();
  }, [feedbackOpen, loadFeedback]);

  const submitFeedback = async () => {
    if (!page) return;
    setFeedbackSaving(true);
    try {
      const res = await apiWikiSubmitFeedback(id, page.slug, feedbackType, feedbackContent);
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

  if (loading) {
    return (
      <Card>
        <Spin />
      </Card>
    );
  }

  if (notFound || !page) {
    return (
      <Card>
        <Empty description={`Wiki 页面不存在: ${slug}`} />
      </Card>
    );
  }

  const renderLinks = (items: { slug: string; title: string; page_type: string }[]) => (
    <Space wrap>
      {items.map((link) => (
        <Tag
          key={link.slug}
          color={TYPE_COLOR[link.page_type] || "default"}
          style={{ cursor: "pointer" }}
          onClick={() => router.push(`/kbs/${id}/wiki/${link.slug}`)}
        >
          {link.title}
        </Tag>
      ))}
    </Space>
  );

  return (
    <Space direction="vertical" size="middle" style={{ display: "flex" }}>
      <Breadcrumb
        items={[
          { title: <a onClick={() => router.push("/kbs")}>知识库</a> },
          { title: <a onClick={() => router.push(`/kbs/${id}`)}>返回</a> },
          { title: page.title },
        ]}
      />
      <Card>
        <Space direction="vertical" size="small" style={{ display: "flex" }}>
          <Space style={{ justifyContent: "space-between", width: "100%" }}>
            <Space>
              <Typography.Title level={3} style={{ margin: 0 }}>
                {page.title}
              </Typography.Title>
              <Tag color={TYPE_COLOR[page.page_type] || "default"}>{page.page_type}</Tag>
            </Space>
            <Space>
              <Button size="small" icon={<EditOutlined />} onClick={openEdit}>
                编辑
              </Button>
              <Button
                size="small"
                icon={<MessageOutlined />}
                onClick={() => setFeedbackOpen(true)}
              >
                反馈
              </Button>
              <Button
                size="small"
                icon={<ApartmentOutlined />}
                onClick={() => router.push(`/kbs/${id}?wiki=graph&focus=${encodeURIComponent(page.slug)}`)}
              >
                图谱中查看
              </Button>
            </Space>
          </Space>
          {page.summary && (
            <Typography.Paragraph type="secondary">{page.summary}</Typography.Paragraph>
          )}
          <hr />
          <div className="kb-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {renderWikiLinks(page.content || "", id)}
            </ReactMarkdown>
          </div>
        </Space>
      </Card>
      {(page.links.length > 0 || page.in_links.length > 0) && (
        <Card title="双向链接">
          <Space direction="vertical" size="middle" style={{ display: "flex" }}>
            {page.in_links.length > 0 && (
              <div>
                <Typography.Text type="secondary">
                  被引用（{page.in_links.length} 个页面链接到此页）
                </Typography.Text>
                <div style={{ marginTop: 8 }}>{renderLinks(page.in_links)}</div>
              </div>
            )}
            {page.links.length > 0 && (
              <div>
                <Typography.Text type="secondary">引用（此页链接到 {page.links.length} 个页面）</Typography.Text>
                <div style={{ marginTop: 8 }}>{renderLinks(page.links)}</div>
              </div>
            )}
          </Space>
        </Card>
      )}

      <Modal
        title={`编辑页面：${page.title}`}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => void submitEdit()}
        confirmLoading={saving}
        width={680}
      >
        <Form form={editForm} layout="vertical">
          <Form.Item name="title" label="标题" rules={[{ required: true, message: "请输入标题" }]}>
            <Input maxLength={255} />
          </Form.Item>
          <Form.Item name="page_type" label="类型">
            <Select options={TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item name="summary" label="摘要">
            <Input.TextArea rows={2} maxLength={500} />
          </Form.Item>
          <Form.Item name="content" label="内容（支持 Markdown + [[slug]] 双链）">
            <Input.TextArea rows={12} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`反馈：${page.title}`}
        open={feedbackOpen}
        onCancel={() => setFeedbackOpen(false)}
        onOk={() => void submitFeedback()}
        confirmLoading={feedbackSaving}
      >
        <Space direction="vertical" style={{ display: "flex" }} size="small">
          <Radio.Group
            value={feedbackType}
            onChange={(e) => setFeedbackType(e.target.value)}
            optionType="button"
            buttonStyle="solid"
          >
            <Radio.Button value="helpful">有帮助</Radio.Button>
            <Radio.Button value="issue">问题上报</Radio.Button>
          </Radio.Group>
          <Input.TextArea
            rows={3}
            placeholder="补充说明（可选）"
            value={feedbackContent}
            onChange={(e) => setFeedbackContent(e.target.value)}
            maxLength={2000}
          />
          {pageFeedback.length > 0 && (
            <div style={{ maxHeight: 160, overflow: "auto" }}>
              <Typography.Text type="secondary">已有反馈：</Typography.Text>
              {pageFeedback.map((fb) => (
                <div key={fb.id} style={{ marginTop: 4 }}>
                  <Tag color={fb.feedback_type === "helpful" ? "green" : "red"}>
                    {fb.feedback_type === "helpful" ? "有帮助" : "问题"}
                  </Tag>
                  <Tag>{fb.status}</Tag>
                  <Typography.Text>{fb.content}</Typography.Text>
                </div>
              ))}
            </div>
          )}
        </Space>
      </Modal>
    </Space>
  );
}
