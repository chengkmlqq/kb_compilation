"use client";

/**
 * 文档预览（对齐 WeKnora document-preview）：
 * 全类型分发（图片/PDF/Markdown/代码/HTML(源码切换)/CSV 表格/纯文本），
 * 工具栏：下载 + 浏览器原生全屏；HTML 支持 render/source 双模式；
 * docx/xlsx/pptx/epub 等无渲染依赖的类型给出下载兜底提示。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  App,
  Button,
  Empty,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
} from "antd";
import {
  DownloadOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  CodeOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiFetchDocumentBlob, DocItem } from "@/lib/api";
import CodeViewer from "./CodeViewer";
import { fileTypeIcon } from "./DocCardView";

type PreviewKind =
  | "image"
  | "pdf"
  | "markdown"
  | "code"
  | "html"
  | "csv"
  | "text"
  | "unsupported";

const CODE_EXTS = new Set([
  "py", "js", "jsx", "ts", "tsx", "json", "jsonc", "yaml", "yml",
  "sh", "bash", "sql", "java", "go", "c", "cpp", "h", "hpp", "rs",
  "kt", "swift", "xml", "css", "scss", "less", "vue", "ini", "conf", "properties",
]);
const IMAGE_EXTS = new Set(["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico"]);

function resolveKind(ext: string | null | undefined, contentType: string): PreviewKind {
  const e = (ext || "").toLowerCase().replace(/^\./, "");
  if (IMAGE_EXTS.has(e) || contentType.startsWith("image/")) return "image";
  if (e === "pdf" || contentType === "application/pdf") return "pdf";
  if (e === "md" || e === "markdown") return "markdown";
  if (e === "html" || e === "htm") return "html";
  if (e === "csv" || e === "tsv") return "csv";
  if (CODE_EXTS.has(e)) return "code";
  if (
    e === "txt" || e === "log" || e === "text" || !e ||
    contentType.startsWith("text/")
  )
    return "text";
  return "unsupported";
}

/** 宽松 UTF-8 探测：无效字节序列则视为二进制 */
function isValidUtf8(u8: Uint8Array): boolean {
  for (let i = 0; i < u8.length; i++) {
    const b = u8[i];
    if (b < 0x80) continue;
    let n = 0;
    if ((b & 0xe0) === 0xc0) n = 1;
    else if ((b & 0xf0) === 0xe0) n = 2;
    else if ((b & 0xf8) === 0xf0) n = 3;
    else return false;
    if (i + n >= u8.length) return false;
    for (let j = 1; j <= n; j++) if ((u8[i + j] & 0xc0) !== 0x80) return false;
    i += n;
  }
  return true;
}

function parseSortText(u8: Uint8Array): string {
  return new TextDecoder("utf-8").decode(u8);
}

interface Props {
  kbId: string;
  doc: DocItem | null;
  onClose: () => void;
}

export default function DocPreviewModal({ kbId, doc, onClose }: Props) {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [blobUrl, setBlobUrl] = useState("");
  const [text, setText] = useState("");
  const [kind, setKind] = useState<PreviewKind>("text");
  const [isFullscreen, setFullscreen] = useState(false);
  const [htmlMode, setHtmlMode] = useState<"render" | "source">("render");
  const [csvRows, setCsvRows] = useState<string[][]>([]);
  const fullscreenRef = useRef<HTMLDivElement | null>(null);
  const [contentType, setContentType] = useState("");

  useEffect(() => {
    if (!doc) return;
    let alive = true;
    const urlBak: string[] = [];
    setLoading(true);
    setError("");
    setText("");
    setCsvRows([]);
    setHtmlMode("render");
    setKind(resolveKind(doc.file_ext, ""));
    (async () => {
      try {
        const { blob, contentType: ct } = await apiFetchDocumentBlob(kbId, doc.id);
        if (!alive) return;
        setContentType(ct);
        const k = resolveKind(doc.file_ext, ct);
        setKind(k);
        const ab = await blob.arrayBuffer();
        const u8 = new Uint8Array(ab);
        if (k === "image" || k === "pdf") {
          const u = URL.createObjectURL(blob);
          urlBak.push(u);
          setBlobUrl(u);
        } else if (k === "markdown" || k === "code" || k === "text" || k === "csv") {
          if (!isValidUtf8(u8)) {
            setError("文件为二进制编码，暂无法在线预览，请下载原文件查看。");
          } else {
            const s = parseSortText(u8);
            setText(s);
            if (k === "csv") {
              const lines = s.split(/\r?\n/).filter((l) => l.trim() !== "");
              setCsvRows(lines.map((l) => l.split(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/).map((c) => c.replace(/^"|"$/g, "").trim())));
            }
          }
        } else if (k === "html") {
          const u = URL.createObjectURL(new Blob([blob], { type: "text/html" }));
          urlBak.push(u);
          setBlobUrl(u);
          if (isValidUtf8(u8)) setText(parseSortText(u8));
        } else {
          setError("该文件类型暂不支持在线预览，请下载查看。");
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "预览加载失败");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
      urlBak.forEach((u) => URL.revokeObjectURL(u));
      setBlobUrl("");
    };
  }, [kbId, doc?.id, doc]);

  const toggleFullscreen = useCallback(() => {
    const el = fullscreenRef.current;
    if (!el) return;
    if (document.fullscreenElement === el || document.fullscreenElement) {
      void document.exitFullscreen().catch(() => undefined);
    } else {
      void el.requestFullscreen?.().catch(() => {
        message.warning("当前浏览器不支持全屏预览");
      });
    }
  }, [message]);

  useEffect(() => {
    const onFs = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  const ext = (doc?.file_ext || "").toUpperCase();
  const title =
    kind === "unsupported" || error ? (
      <Space>
        <span style={{ fontSize: 16 }}>{fileTypeIcon(doc?.file_ext)}</span>
        <span>{doc?.file_name}</span>
        {error ? <Tag color="orange">不支持预览</Tag> : null}
      </Space>
    ) : (
      <Space>
        <span style={{ fontSize: 16 }}>{fileTypeIcon(doc?.file_ext)}</span>
        <span>{doc?.file_name}</span>
        <Tag color="blue">{ext || "TEXT"}</Tag>
      </Space>
    );

  const renderBody = () => {
    if (loading) {
      return (
        <div style={{ textAlign: "center", padding: 60 }}>
          <Spin size="large" />
          <div style={{ marginTop: 12, color: "#888" }}>正在加载文件…</div>
        </div>
      );
    }
    if (error && (kind as string) === "unsupported") {
      return (
        <Empty description={error} image={Empty.PRESENTED_IMAGE_SIMPLE}>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            onClick={() => {
              if (doc) void import("@/lib/api").then((m) => m.apiDownloadDocument(kbId, doc.id, doc.file_name).catch((e) => message.error(e.message)));
            }}
          >
            下载原文件
          </Button>
        </Empty>
      );
    }
    if (error) {
      return (
        <Alert
          type="warning"
          showIcon
          message="预览失败"
          description={error}
          action={
            <Button
              size="small"
              icon={<DownloadOutlined />}
              onClick={() => {
                if (doc) void import("@/lib/api").then((m) => m.apiDownloadDocument(kbId, doc.id, doc.file_name).catch((e) => message.error(e.message)));
              }}
            >
              下载
            </Button>
          }
        />
      );
    }
    switch (kind) {
      case "image":
        return (
          <div style={{ textAlign: "center", height: "100%", overflow: "auto" }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={blobUrl}
              alt={doc?.file_name || "preview"}
              style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
            />
          </div>
        );
      case "pdf":
        return (
          <iframe
            src={blobUrl}
            title={doc?.file_name || "pdf"}
            style={{ width: "100%", height: "100%", border: "none" }}
          />
        );
      case "markdown":
        return (
          <div className="kb-markdown" style={{ height: "100%", overflow: "auto", padding: "8px 20px 20px" }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
          </div>
        );
      case "code":
        return (
          <div style={{ height: "100%", overflow: "auto" }}>
            <CodeViewer value={text} fileName={doc?.file_name || "code"} height={560} />
          </div>
        );
      case "html":
        return htmlMode === "render" ? (
          <iframe
            sandbox="allow-same-origin allow-scripts"
            title={doc?.file_name || "html"}
            style={{ width: "100%", height: "100%", border: "none", background: "#fff" }}
            src={blobUrl}
          />
        ) : (
          <div style={{ height: "100%", overflow: "auto" }}>
            <CodeViewer value={text} fileName={doc?.file_name || "code"} height={560} />
          </div>
        );
      case "csv":
        return (
          <div style={{ height: "100%", overflow: "auto" }}>
            <Table
              size="small"
              rowKey={(_, i) => String(i)}
              columns={csvRows[0]?.map((c, i) => ({
                title: c || `列${i + 1}`,
                dataIndex: String(i),
                key: String(i),
                ellipsis: true,
              })) || []}
              dataSource={csvRows.slice(1).map((r, ri) =>
                Object.fromEntries(r.map((v, ci) => [String(ci), v]))
              )}
              pagination={false}
              scroll={{ x: "max-content" }}
            />
          </div>
        );
      default:
        return (
          <pre
            style={{
              margin: 0,
              padding: "12px 16px",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontSize: 13,
              height: "100%",
              overflow: "auto",
              lineHeight: 1.7,
            }}
          >
            {text}
          </pre>
        );
    }
  };

  return (
    <Modal
      open={!!doc}
      onCancel={onClose}
      title={title}
      width={isFullscreen ? "100vw" : "92vw"}
      footer={null}
      destroyOnClose
      styles={{ body: { height: isFullscreen ? "100vh" : "78vh", padding: 12 } }}
      style={isFullscreen ? { top: 0, maxWidth: "100vw", padding: 0 } : undefined}
    >
      <div
        ref={fullscreenRef}
        style={{
          height: "100%",
          overflow: isFullscreen ? "auto" : "hidden",
          background: "#f5f5f5",
          borderRadius: 6,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 10px",
            background: "#fff",
            borderBottom: "1px solid #f0f0f0",
          }}
        >
          <Space style={{ flex: 1 }} />
          <Space>
            {kind === "html" && text ? (
              <Tooltip title="查看/源码切换">
                <Button
                  size="small"
                  icon={<CodeOutlined />}
                  onClick={() => setHtmlMode(htmlMode === "render" ? "source" : "render")}
                >
                  {htmlMode === "render" ? "查看源码" : "渲染预览"}
                </Button>
              </Tooltip>
            ) : null}
            <Button
              size="small"
              icon={<DownloadOutlined />}
              onClick={() => {
                if (doc) void import("@/lib/api").then((m) => m.apiDownloadDocument(kbId, doc.id, doc.file_name).catch((e) => message.error(e.message)));
              }}
            >
              下载
            </Button>
            <Button
              size="small"
              icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
              onClick={toggleFullscreen}
            >
              {isFullscreen ? "退出全屏" : "全屏"}
            </Button>
          </Space>
        </div>
        <div style={{ height: "calc(100% - 41px)", overflow: "auto", background: "#fff" }}>
          {renderBody()}
        </div>
      </div>
    </Modal>
  );
}