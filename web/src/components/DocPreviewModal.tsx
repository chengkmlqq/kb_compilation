"use client";

/**
 * 文档预览（像素级对齐 WeKnora document-preview，2026-10-10 补齐批次）：
 * - docx  → docx-preview renderAsync（WeKnora 同款库）
 * - xlsx/xls → xlsx 库 sheet_to_html 多 sheet 拼接（WeKnora 同款）
 * - pptx/ppt → jszip 解压抽 slide 文本逐页展示（@vue-office/pptx 是 Vue 组件，
 *   React 用文本抽取方案，保证可读性 + 下载兜底）
 * - 图片（含 tiff）→ objectURL；pdf → blob iframe
 * - markdown → react-markdown + remark-math/rehype-katex 数学公式 +
 *   ```mermaid 代码块渲染（动态 import mermaid）
 * - 代码 → CodeViewer 高亮；html → render/source 双模 + sandbox iframe
 * - 音频 mp3/wav/m4a/flac/ogg → 原生 <audio> 播放器（零依赖）
 * - csv → antd Table；文本 → pre
 * 工具栏：下载 + 浏览器原生全屏；docx/excel/pptx 失败给下载兜底提示。
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
  CheckCircleOutlined,
} from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { apiFetchDocumentBlob, DocItem } from "@/lib/api";
import CodeViewer from "./CodeViewer";
import { fileTypeIcon } from "./DocCardView";
// 数学公式样式（WeKnora marked-katex 同款；katex CSS 含字体，走相对路径由 bundler 处理）
import "katex/dist/katex.min.css";

type PreviewKind =
  | "image"
  | "pdf"
  | "docx"
  | "excel"
  | "pptx"
  | "markdown"
  | "code"
  | "html"
  | "csv"
  | "text"
  | "audio"
  | "unsupported";

const CODE_EXTS = new Set([
  "py", "js", "jsx", "ts", "tsx", "json", "jsonc", "yaml", "yml",
  "sh", "bash", "sql", "java", "go", "c", "cpp", "h", "hpp", "rs",
  "kt", "swift", "xml", "css", "scss", "less", "vue", "ini", "conf", "properties",
]);
const IMAGE_EXTS = new Set(["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico", "tiff"]);
const AUDIO_EXTS = new Set(["mp3", "wav", "m4a", "flac", "ogg", "aac", "opus"]);
const EXCEL_EXTS = new Set(["xlsx", "xls", "xlsm", "xlsb"]);
const PPTX_EXTS = new Set(["pptx", "ppt", "odp"]);
const DOCX_EXTS = new Set(["docx", "doc", "odt", "rtf"]);

function resolveKind(ext: string | null | undefined, contentType: string): PreviewKind {
  const e = (ext || "").toLowerCase().replace(/^\\./, "");
  if (IMAGE_EXTS.has(e) || contentType.startsWith("image/")) return "image";
  if (e === "pdf" || contentType === "application/pdf") return "pdf";
  if (DOCX_EXTS.has(e)) return "docx";
  if (EXCEL_EXTS.has(e)) return "excel";
  if (PPTX_EXTS.has(e)) return "pptx";
  if (AUDIO_EXTS.has(e)) return "audio";
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

/** 简单 XML 实体反转义（pptx 文本抽取用）。 */
function decodeXml(s: string): string {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&#(\d+);/g, (_m, n) => String.fromCharCode(Number(n)));
}

/** 从 PPTX zip 中抽取每页 slide 文本（<a:t> 节点）。 */
async function extractPptxSlides(blob: Blob): Promise<string[]> {
  const JSZip = (await import("jszip")).default;
  const zip = await JSZip.loadAsync(await blob.arrayBuffer());
  const slideFiles = Object.keys(zip.files)
    .filter((p) => /^ppt\/slides\/slide\d+\.xml$/.test(p))
    .sort((a, b) => {
      const na = Number(a.match(/slide(\d+)/)?.[1] || 0);
      const nb = Number(b.match(/slide(\d+)/)?.[1] || 0);
      return na - nb;
    });
  if (slideFiles.length === 0) {
    // .ppt 老格式不是 zip，无法抽取文本
    throw new Error("该演示文稿为旧版二进 .ppt 格式，暂无法抽取内容，请下载查看。");
  }
  const slides: string[] = [];
  for (const p of slideFiles) {
    const xml = await zip.file(p)?.async("string");
    if (!xml) continue;
    // 每个 <a:t>…</a:t> 是文本 run；<a:p> 段落（含 <a:pPr>）内部文本连一行
    const paras = xml.match(/<a:p(?:\s[^>]*)?>[\s\S]*?<\/a:p>/g) || [];
    const lines = paras
      .map((para) => {
        const texts = para.match(/<a:t(?:\s[^>]*)?>([\s\S]*?)<\/a:t>/g) || [];
        return texts
          .map((t) => decodeXml(t.replace(/<\/?a:t(?:\s[^>]*)?>/g, "")))
          .join("");
      })
      .filter((l) => l.trim() !== "");
    slides.push(lines.join("\\n"));
  }
  return slides.length ? slides : ["（演示文稿无文本内容）"];
}

interface Props {
  kbId: string;
  doc: DocItem | null;
  onClose: () => void;
}

/** ```mermaid 代码块的动态渲染组件（避免首屏加载 mermaid）。 */
function MermaidBlock({ value, index }: { value: string; index: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "default",
          securityLevel: "loose",
        });
        const id = `kb-mermaid-${index}`;
        const { svg } = await mermaid.render(id, value);
        if (alive && ref.current) ref.current.innerHTML = svg;
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "图表渲染失败");
      }
    })();
    return () => {
      alive = false;
    };
  }, [value, index]);
  if (error) return <pre style={{ color: "#d54941", fontSize: 13 }}>{error}</pre>;
  return <div ref={ref} className="kb-markdown-mermaid" style={{ textAlign: "center" }} />;
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
  // docx 渲染容器 / excel 多 sheet HTML / pptx 幻灯片
  const [docxBlob, setDocxBlob] = useState<Blob | null>(null);
  const [excelHtml, setExcelHtml] = useState("");
  const [pptxSlides, setPptxSlides] = useState<string[]>([]);
  const [pptxIndex, setPptxIndex] = useState(0);
  const docxContainerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!doc) return;
    let alive = true;
    const urlBak: string[] = [];
    setLoading(true);
    setError("");
    setText("");
    setCsvRows([]);
    setHtmlMode("render");
    setDocxBlob(null);
    setExcelHtml("");
    setPptxSlides([]);
    setPptxIndex(0);
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
        if (k === "image" || k === "pdf" || k === "audio") {
          const u = URL.createObjectURL(blob);
          urlBak.push(u);
          setBlobUrl(u);
        } else if (k === "docx") {
          // 只存 blob：容器要等 loading=false 后才挂载（见下方渲染 effect）
          setDocxBlob(blob);
        } else if (k === "excel") {
          const XLSX = await import("xlsx");
          if (!alive) return;
          const workbook = XLSX.read(u8, { type: "array" });
          let html = "";
          workbook.SheetNames.forEach((name, sheetIdx) => {
            const sheet = workbook.Sheets[name];
            const sheetHtml = XLSX.utils.sheet_to_html(sheet, { id: `kb-sheet-${sheetIdx}` });
            html += `<div class="kb-excel-sheet">`;
            if (workbook.SheetNames.length > 1) {
              html += `<div class="kb-excel-sheet-name">${name}</div>`;
            }
            html += sheetHtml;
            html += `</div>`;
          });
          setExcelHtml(html);
        } else if (k === "pptx") {
          const slides = await extractPptxSlides(blob);
          if (!alive) return;
          setPptxSlides(slides);
        } else if (k === "markdown" || k === "code" || k === "text" || k === "csv") {
          if (!isValidUtf8(u8)) {
            setError("文件为二进制编码，暂无法在线预览，请下载原文件查看。");
          } else {
            const s = parseSortText(u8);
            setText(s);
            if (k === "csv") {
              const lines = s.split(/\\r?\\n/).filter((l) => l.trim() !== "");
              setCsvRows(lines.map((l) => l.split(/,(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)/).map((c) => c.replace(/^\"|\"$/g, "").trim())));
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

  // docx 渲染：必须在容器挂载后（loading=false 且 kind=docx）才跑，
  // 所以与下载 effect 分离——下载只负责拿 blob。
  useEffect(() => {
    if (kind !== "docx" || !docxBlob || loading) return;
    let alive = true;
    (async () => {
      try {
        const { renderAsync } = await import("docx-preview");
        const c = docxContainerRef.current;
        if (!c || !alive) return;
        c.innerHTML = "";
        await renderAsync(docxBlob, c, undefined, {
          className: "kb-docx-preview",
          inWrapper: true,
          ignoreWidth: false,
        });
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "docx 渲染失败");
      }
    })();
    return () => {
      alive = false;
    };
  }, [kind, docxBlob, loading]);

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
              onLoad={(e) => {
                // 对齐 WeKnora：宽图允许横向滚动
                const img = e.currentTarget;
                img.style.height = img.naturalHeight > img.naturalWidth ? "100%" : "auto";
              }}
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
      case "docx":
        return (
          <div style={{ height: "100%", overflow: "auto", background: "#fff", padding: "0 12px" }}>
            <div ref={docxContainerRef} className="kb-docx-preview" />
          </div>
        );
      case "excel":
        return (
          <div
            className="kb-excel-preview"
            style={{ height: "100%", overflow: "auto", background: "#fff" }}
            dangerouslySetInnerHTML={{ __html: excelHtml }}
          />
        );
      case "pptx":
        return pptxSlides.length > 1 ? (
          <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
            <Space style={{ justifyContent: "center", padding: "6px 0" }}>
              <Button size="small" disabled={pptxIndex === 0} onClick={() => setPptxIndex((i) => i - 1)}>
                上一页
              </Button>
              <span style={{ fontSize: 13, color: "rgba(0,0,0,.65)", minWidth: 56, textAlign: "center" }}>
                第 {pptxIndex + 1} / {pptxSlides.length} 页
              </span>
              <Button
                size="small"
                disabled={pptxIndex >= pptxSlides.length - 1}
                onClick={() => setPptxIndex((i) => i + 1)}
              >
                下一页
              </Button>
            </Space>
            <div style={{ flex: 1, overflow: "auto", background: "#fff", borderRadius: 6, padding: 16 }}>
              <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 14, lineHeight: 1.8 }}>
                {pptxSlides[pptxIndex]}
              </pre>
            </div>
            <div style={{ textAlign: "center", padding: 4, color: "#999", fontSize: 12 }}>
              文本预览模式（对齐 WeKnora 需 @vue-office/pptx，React 侧无等价组件）· 含表格图形的演示请下载查看
            </div>
          </div>
        ) : (
          <pre style={{ margin: 0, padding: "12px 16px", whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 13, height: "100%", overflow: "auto", lineHeight: 1.7 }}>
            {pptxSlides[0] || "（演示文稿无文本内容）"}
          </pre>
        );
      case "audio":
        return (
          <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", background: "#fff" }}>
            <audio controls src={blobUrl} style={{ width: "100%", maxWidth: 640 }} />
          </div>
        );
      case "markdown":
        return (
          <div className="kb-markdown" style={{ height: "100%", overflow: "auto", padding: "8px 20px 20px" }}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[rehypeKatex]}
              components={{
                code({ className, children, ...props }) {
                  const match = /language-(\\w+)/.exec(className || "");
                  const codeStr = String(children || "").replace(/\\n$/, "");
                  if (match?.[1] === "mermaid") {
                    return <MermaidBlock value={codeStr} index={Math.floor(Math.random() * 10000)} />;
                  }
                  return (
                    <code className={className} {...props}>
                      {children}
                    </code>
                  );
                },
              }}
            >
              {text}
            </ReactMarkdown>
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
            {(kind === "docx" || kind === "excel" || kind === "pptx") && !error && (
              <Tooltip title="渲染完成">
                <span style={{ fontSize: 12, color: "#52c41a", display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <CheckCircleOutlined /> 已渲染
                </span>
              </Tooltip>
            )}
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