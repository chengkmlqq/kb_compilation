"use client";

/**
 * WeKnora 嵌入测试页：iframe 加载 WeKnora embed channel（b4464442）。
 * channel 的 allowed_origins 不含本系统域名 -> 浏览器跨域拦截/API 403 效果可见。
 */
import { Card } from "antd";

const EMBED_URL = "http://10.1.215.50:8086/login";

export default function WeknoraEmbedPage() {
  return (
    <div style={{ padding: 12, height: "100%", display: "flex", flexDirection: "column", minHeight: 0 }}>
      <Card
        title="WeKnora 嵌入测试（跨域拦截演示）"
        style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
        styles={{ body: { flex: 1, minHeight: 0, padding: 12, display: "flex", flexDirection: "column" } }}
      >
        <div
          style={{
            flex: 1,
            minHeight: 0,
            border: "1px solid #f0f0f0",
            borderRadius: 8,
            overflow: "hidden",
            background: "#fff",
          }}
        >
          <iframe
            src={EMBED_URL}
            style={{ width: "100%", height: "100%", border: "none" }}
            title="WeKnora Embed"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          />
        </div>
      </Card>
    </div>
  );
}