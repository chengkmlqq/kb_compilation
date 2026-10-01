"use client";

import { useEffect, useState } from "react";
import { Breadcrumb, Button, Card, Empty, Space, Spin, Tag, Typography } from "antd";
import { ApartmentOutlined } from "@ant-design/icons";
import { useParams, useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiWikiPage, WikiPageDetail } from "@/lib/api";

const TYPE_COLOR: Record<string, string> = {
  entity: "purple",
  concept: "blue",
  summary: "gold",
};

export default function WikiPageDetailPage() {
  const { id, slug } = useParams<{ id: string; slug: string }>();
  const router = useRouter();
  const [page, setPage] = useState<WikiPageDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    (async () => {
      setLoading(true);
      const res = await apiWikiPage(id, slug);
      if (res.success && res.data) {
        setPage(res.data);
        setNotFound(false);
      } else {
        setNotFound(true);
      }
      setLoading(false);
    })();
  }, [id, slug]);

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
            <Button
              size="small"
              icon={<ApartmentOutlined />}
              onClick={() => router.push(`/kbs/${id}?wiki=graph&focus=${encodeURIComponent(page.slug)}`)}
            >
              图谱中查看
            </Button>
          </Space>
          {page.summary && (
            <Typography.Paragraph type="secondary">{page.summary}</Typography.Paragraph>
          )}
          <hr />
          <div className="kb-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{page.content}</ReactMarkdown>
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
    </Space>
  );
}