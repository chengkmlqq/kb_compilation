"use client";

import { useEffect, useState } from "react";
import { Breadcrumb, Card, Empty, Space, Spin, Tag, Typography } from "antd";
import { useParams, useRouter } from "next/navigation";
import { apiWikiPage, WikiPageDetail } from "@/lib/api";

/** Minimal markdown-ish renderer: headings, bold, inline code, paragraphs. */
function SimpleMarkdown({ text }: { text: string }) {
  if (!text) return null;
  const lines = text.split("\n");
  const out: React.ReactNode[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("### ")) {
      out.push(<Typography.Title key={out.length} level={4}>{trimmed.slice(4)}</Typography.Title>);
    } else if (trimmed.startsWith("## ")) {
      out.push(<Typography.Title key={out.length} level={3}>{trimmed.slice(3)}</Typography.Title>);
    } else if (trimmed.startsWith("# ")) {
      out.push(<Typography.Title key={out.length} level={2}>{trimmed.slice(2)}</Typography.Title>);
    } else if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      out.push(
        <div key={out.length} style={{ paddingLeft: 16 }}>
          · {trimmed.slice(2)}
        </div>,
      );
    } else if (trimmed) {
      out.push(
        <Typography.Paragraph key={out.length}>
          {trimmed.replace(/`([^`]+)`/g, (_, code: string) => `「${code}」`)}
        </Typography.Paragraph>,
      );
    } else {
      out.push(<div key={out.length} style={{ height: 8 }} />);
    }
  }
  return <>{out}</>;
}

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
          <Space>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {page.title}
            </Typography.Title>
            <Tag color={page.page_type === "concept" ? "blue" : "purple"}>{page.page_type}</Tag>
          </Space>
          {page.summary && (
            <Typography.Paragraph type="secondary">{page.summary}</Typography.Paragraph>
          )}
          <hr />
          <SimpleMarkdown text={page.content} />
        </Space>
      </Card>
      {page.links.length > 0 && (
        <Card title="关联页面">
          <Space wrap>
            {page.links.map((link) => (
              <Tag
                key={link.slug}
                color="geekblue"
                style={{ cursor: "pointer" }}
                onClick={() => router.push(`/kbs/${id}/wiki/${link.slug}`)}
              >
                {link.title}
              </Tag>
            ))}
          </Space>
        </Card>
      )}
    </Space>
  );
}
