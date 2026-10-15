"use client";

import { Fragment, ReactNode, useCallback, useState } from "react";
import { CheckOutlined, CopyOutlined } from "@ant-design/icons";
import { CitationRef, preprocessCitations, parsePayload, CitationPayload } from "@/lib/citation";
import { tokenizeCode, hasHighlight } from "@/lib/highlight";
import { copyText } from "@/lib/clipboard";

/** 轻量 Markdown 渲染（标题/粗体/斜体/行内代码/代码块/列表/链接/引用/表格/引用胶囊）。
 * 对齐 WeKnora 消息渲染的常用子集，避免引入额外依赖。
 * 引用胶囊（WeKnora `<kb doc=.../>`）像素级对齐 WeKnora chat-citations.less。
 * 代码块：```lang 识别 + 自研轻量高亮（对齐 WeKnora hljs 亮色主题配色）+ 标题栏（语言名 + 复制）。 */

interface Props {
  text: string;
  /** 本次检索命中的片段，用于引用胶囊的显示名 + 点击查看内容 */
  refs?: CitationRef[] | null;
  /** 点击引用胶囊的回调（payload 带 chunkId/doc/kbId/display） */
  onCitationClick?: (payload: CitationPayload, ref?: CitationRef) => void;
}

/** 代码块：标题栏（语言 + 复制按钮）+ 高亮代码（对齐 WeKnora chat-markdown.less 代码块）。 */
function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const [copied, setCopied] = useState(false);
  const label = lang || "text";
  const copy = async (ev: React.MouseEvent) => {
    ev.preventDefault();
    ev.stopPropagation();
    const ok = await copyText(code);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  let nodes: ReactNode;
  if (hasHighlight(lang)) {
    nodes = (
      <>
        {tokenizeCode(code, lang).map((t, i) =>
          t.type === "plain" ? (
            <Fragment key={i}>{t.text}</Fragment>
          ) : (
            <span key={i} className={`kb-hl-${t.type}`}>
              {t.text}
            </span>
          )
        )}
      </>
    );
  } else {
    nodes = (
      <>
        {code.split("\n").map((ln, i) => (
          <div key={i}>{ln === "" ? " " : ln}</div>
        ))}
      </>
    );
  }
  return (
    <div className="kb-chat-md-codeblock">
      <div className="kb-chat-md-codeblock-bar">
        <span className="kb-chat-md-codeblock-lang">{label}</span>
        <button
          type="button"
          className="kb-chat-md-codeblock-copy"
          onClick={copy}
          title="复制代码"
          aria-label="复制代码"
        >
          {copied ? <CheckOutlined /> : <CopyOutlined />}
          <span>{copied ? "已复制" : "复制"}</span>
        </button>
      </div>
      <pre className="kb-chat-md-pre">
        <code>{nodes}</code>
      </pre>
    </div>
  );
}

/** 引用胶囊的悬停态（内容片段浮层 —— 对齐 WeKnora ChatCitationFloat）。 */
interface CitationHover {
  payload: CitationPayload;
  ref?: CitationRef;
  rect: DOMRect;
}

/** 渲染单个引用胶囊（书本图标 + 文档名，hover 变色；hover 弹出片段浮层）。
 *  kb 引用：点击打开引用抽屉；web 引用：点击新窗口打开 URL（对齐 WeKnora web 胶囊）。 */
function citationPill(
  payload: CitationPayload,
  ref: CitationRef | undefined,
  keyPrefix: string,
  onCitationClick: Props["onCitationClick"],
  onHoverChange: (h: CitationHover | null) => void,
): ReactNode {
  const isWeb = payload.type === "web";
  const click = (ev: React.MouseEvent | React.KeyboardEvent) => {
    if (isWeb) {
      ev.preventDefault();
      ev.stopPropagation();
      if (payload.url) window.open(payload.url, "_blank", "noopener,noreferrer");
      return;
    }
    if (!onCitationClick) return;
    ev.preventDefault();
    ev.stopPropagation();
    onCitationClick(payload, ref);
  };
  return (
    <span
      key={keyPrefix}
      className={`kb-citation ${isWeb ? "kb-citation-web" : "kb-citation-kb"}`}
      role="button"
      tabIndex={0}
      onClick={click}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") click(e);
      }}
      onMouseEnter={(e) =>
        onHoverChange({ payload, ref, rect: e.currentTarget.getBoundingClientRect() })
      }
      onMouseLeave={() => onHoverChange(null)}
    >
      <span className="kb-citation-icon kb-citation-icon--book" aria-hidden="true" />
      <span className="kb-citation-text">{payload.display}</span>
    </span>
  );
}

function inline(
  text: string,
  keyPrefix: string,
  snippets: string[],
  refs: CitationRef[] | null | undefined,
  onCitationClick: Props["onCitationClick"],
  onHoverChange: (h: CitationHover | null) => void,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  // 引用胶囊 token（@@KB_CITATION_n@@）先切出来，避免被后续 inline 解析吞掉
  const citParts = text.split(/(@@KB_CITATION_\d+@@)/g);
  citParts.forEach((p, ci) => {
    const citMatch = p.match(/^@@KB_CITATION_(\d+)@@$/);
    if (citMatch) {
      const payload = parsePayload(snippets[Number(citMatch[1])] || "");
      if (!payload) return;
      const ref = refs?.find((r) => r.chunk_id === payload.chunkId);
      nodes.push(
        citationPill(payload, ref, `${keyPrefix}-cit${ci}`, onCitationClick, onHoverChange),
      );
      return;
    }
    if (!p) return;

    // 代码块内联处理
    const parts = p.split(/(`[^`]+`)/g);
    parts.forEach((q, i) => {
      if (q.startsWith("`") && q.endsWith("`") && q.length > 2) {
        nodes.push(
          <code key={`${keyPrefix}-c${ci}-${i}`} className="kb-chat-md-inline-code">
            {q.slice(1, -1)}
          </code>
        );
        return;
      }
      // 粗体 **x**
      const boldParts = q.split(/(\*\*[^*]+\*\*)/g);
      boldParts.forEach((bp, j) => {
        if (bp.startsWith("**") && bp.endsWith("**") && bp.length > 4) {
          nodes.push(<strong key={`${keyPrefix}-b${ci}-${i}-${j}`}>{bp.slice(2, -2)}</strong>);
          return;
        }
        const linkParts = bp.split(/(\[[^\]]+\]\([^)]+\))/g);
        linkParts.forEach((lp, k) => {
          const m = lp.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
          if (m) {
            nodes.push(
              <a
                key={`${keyPrefix}-l${ci}-${i}-${j}-${k}`}
                href={m[2]}
                target="_blank"
                rel="noreferrer"
                className="kb-chat-md-link"
              >
                {m[1]}
              </a>
            );
          } else {
            nodes.push(<Fragment key={`${keyPrefix}-t${ci}-${i}-${j}-${k}`}>{lp}</Fragment>);
          }
        });
      });
    });
  });
  return nodes;
}

function renderBlock(
  text: string,
  snippets: string[],
  refs: CitationRef[] | null | undefined,
  onCitationClick: Props["onCitationClick"],
  onHoverChange: (h: CitationHover | null) => void,
): ReactNode[] {
  const lines = text.split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let codeBuf: string[] | null = null;
  let codeLang = "";
  let listBuf: { type: "ul" | "ol"; items: string[] } | null = null;

  const flushList = (key: string) => {
    if (!listBuf) return;
    const Tag = listBuf.type === "ul" ? "ul" : "ol";
    out.push(
      <Tag key={key} className="kb-chat-md-list">
        {listBuf.items.map((it, n) => (
          <li key={n}>{inline(it, `${key}-li${n}`, snippets, refs, onCitationClick, onHoverChange)}</li>
        ))}
      </Tag>
    );
    listBuf = null;
  };

  for (i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trimStart().startsWith("```")) {
      flushList(`list-${i}`);
      if (codeBuf !== null) {
        out.push(
          <CodeBlock
            key={`code-${i}`}
            code={codeBuf.join("\n")}
            lang={codeLang}
          />,
        );
        codeBuf = null;
        codeLang = "";
      } else {
        codeBuf = [];
        codeLang = line.trim().slice(3).trim().split(/[\s,]+/)[0] || "";
      }
      continue;
    }
    if (codeBuf !== null) {
      codeBuf.push(line);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      flushList(`list-${i}`);
      const level = h[1].length;
      out.push(
        <div key={`h${i}`} className={`kb-chat-md-h${level}`}>
          {inline(h[2], `h${i}`, snippets, refs, onCitationClick, onHoverChange)}
        </div>
      );
      continue;
    }
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ul) {
      if (!listBuf || listBuf.type !== "ul") {
        flushList(`list-${i}`);
        listBuf = { type: "ul", items: [] };
      }
      listBuf.items.push(ul[1]);
      continue;
    }
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ol) {
      if (!listBuf || listBuf.type !== "ol") {
        flushList(`list-${i}`);
        listBuf = { type: "ol", items: [] };
      }
      listBuf.items.push(ol[1]);
      continue;
    }
    const q = line.match(/^\s*>\s?(.*)$/);
    if (q) {
      flushList(`list-${i}`);
      out.push(
        <div key={`q${i}`} className="kb-chat-md-quote">
          {inline(q[1], `q${i}`, snippets, refs, onCitationClick, onHoverChange)}
        </div>
      );
      continue;
    }
    const tbl = line.match(/^\|.+\|$/);
    if (tbl && lines[i + 1]?.match(/^\|[\s\-:|]+\|$/)) {
      flushList(`list-${i}`);
      /** 去掉首尾竖线后按 | 拆列 */
      const parseRow = (ln: string) => ln.slice(1, -1).split("|").map((c) => c.trim());
      const header = parseRow(line);
      // 数据行：分隔线之后连续的所有表格行（修复：原实现只取首行，
      // 其余行被当普通段落渲染成带竖线的原始文本）
      const bodyRows: string[][] = [];
      let j = i + 2;
      while (j < lines.length) {
        const rowLine = lines[j];
        if (!rowLine || !rowLine.match(/^\|.+\|$/)) break;
        bodyRows.push(parseRow(rowLine));
        j += 1;
      }
      out.push(
        <table key={`tbl${i}`} className="kb-chat-md-table">
          <thead>
            <tr>
              {header.map((c, n) => (
                <th key={n}>{inline(c, `th${n}`, snippets, refs, onCitationClick, onHoverChange)}</th>
              ))}
            </tr>
          </thead>
          {bodyRows.length > 0 && (
            <tbody>
              {bodyRows.map((cells, r) => (
                <tr key={r}>
                  {cells.map((c, n) => (
                    <td key={n}>{inline(c, `td${r}-${n}`, snippets, refs, onCitationClick, onHoverChange)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          )}
        </table>
      );
      i = j - 1; // 循环再 i++ → 指向表格后第一行，避免数据行当段落重复渲染
      continue;
    }
    flushList(`list-${i}`);
    if (line.trim() === "") {
      out.push(<div key={`sp${i}`} className="kb-chat-md-spacer" />);
    } else {
      out.push(
        <div key={`p${i}`} className="kb-chat-md-p">
          {inline(line, `p${i}`, snippets, refs, onCitationClick, onHoverChange)}
        </div>
      );
    }
  }
  flushList(`list-end`);
  return out;
}

export default function MarkdownViewer({ text, refs, onCitationClick }: Props) {
  // 引用胶囊悬停浮层（对齐 WeKnora ChatCitationFloat：跟鼠标显示片段内容）
  const [hover, setHover] = useState<CitationHover | null>(null);
  const onHoverChange = useCallback(
    (h: CitationHover | null) => setHover(h),
    [],
  );
  // 引用预处理：把 <kb doc=.../> / [chunk_id: ...] / 裸 32 位 id 换成 token
  const { content, snippets } = preprocessCitations(text, refs);
  const render = useCallback(
    () => renderBlock(content, snippets, refs, onCitationClick, onHoverChange),
    [content, snippets, refs, onCitationClick, onHoverChange],
  );
  // 浮层定位：胶囊下方，视口底部放不下时翻到上方
  const floatStyle: React.CSSProperties | undefined = hover
    ? (() => {
        const gap = 6;
        const floatH = 220;
        const below = hover.rect.bottom + gap;
        const top = below + floatH > window.innerHeight
          ? Math.max(8, hover.rect.top - floatH - gap)
          : below;
        return {
          position: "fixed" as const,
          left: Math.min(hover.rect.left, Math.max(8, window.innerWidth - 320 - 8)),
          top,
        };
      })()
    : undefined;
  return (
    <div className="kb-chat-md">
      {render()}
      {hover && (
        <div className="kb-citation-float" style={floatStyle} role="tooltip">
          <div className="kb-citation-float__title">
            {hover.payload.type === "web"
              ? hover.payload.title || hover.payload.display
              : hover.ref?.document_title || hover.payload.display}
          </div>
          <div className="kb-citation-float__body">
            {hover.payload.type === "web"
              ? hover.payload.url
              : hover.ref?.content || "（该片段未返回正文，点击可查看详情）"}
          </div>
          <div className="kb-citation-float__muted">
            {hover.payload.type === "web" ? (
              "点击在新窗口打开"
            ) : (
              <>
                片段 {hover.payload.chunkId.slice(0, 8)}
                {typeof hover.ref?.score === "number"
                  ? ` · 相似度 ${hover.ref.score.toFixed(2)}`
                  : ""}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}