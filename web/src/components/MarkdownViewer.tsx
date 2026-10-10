"use client";

import { Fragment, ReactNode, useCallback } from "react";
import { CitationRef, preprocessCitations, parsePayload, CitationPayload } from "@/lib/citation";

/** 轻量 Markdown 渲染（标题/粗体/斜体/行内代码/代码块/列表/链接/引用/表格/引用胶囊）。
 * 对齐 WeKnora 消息渲染的常用子集，避免引入额外依赖。
 * 引用胶囊（WeKnora `<kb doc=.../>`）像素级对齐 WeKnora chat-citations.less。 */

interface Props {
  text: string;
  /** 本次检索命中的片段，用于引用胶囊的显示名 + 点击查看内容 */
  refs?: CitationRef[] | null;
  /** 点击引用胶囊的回调（payload 带 chunkId/doc/kbId/display） */
  onCitationClick?: (payload: CitationPayload, ref?: CitationRef) => void;
}

/** 渲染单个引用胶囊（圆点图标 + 文档名，hover 变色）。 */
function citationPill(
  payload: CitationPayload,
  ref: CitationRef | undefined,
  keyPrefix: string,
  onCitationClick: Props["onCitationClick"],
): ReactNode {
  const click = (ev: React.MouseEvent | React.KeyboardEvent) => {
    if (!onCitationClick) return;
    ev.preventDefault();
    ev.stopPropagation();
    onCitationClick(payload, ref);
  };
  return (
    <span
      key={keyPrefix}
      className="kb-citation kb-citation-kb"
      role="button"
      tabIndex={0}
      onClick={click}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") click(e);
      }}
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
        citationPill(payload, ref, `${keyPrefix}-cit${ci}`, onCitationClick),
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
): ReactNode[] {
  const lines = text.split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let codeBuf: string[] | null = null;
  let listBuf: { type: "ul" | "ol"; items: string[] } | null = null;

  const flushList = (key: string) => {
    if (!listBuf) return;
    const Tag = listBuf.type === "ul" ? "ul" : "ol";
    out.push(
      <Tag key={key} className="kb-chat-md-list">
        {listBuf.items.map((it, n) => (
          <li key={n}>{inline(it, `${key}-li${n}`, snippets, refs, onCitationClick)}</li>
        ))}
      </Tag>
    );
    listBuf = null;
  };

  for (i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("```")) {
      flushList(`list-${i}`);
      if (codeBuf !== null) {
        out.push(
          <pre key={`code-${i}`} className="kb-chat-md-pre">
            <code>{codeBuf.join("\n")}</code>
          </pre>
        );
        codeBuf = null;
      } else {
        codeBuf = [];
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
          {inline(h[2], `h${i}`, snippets, refs, onCitationClick)}
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
          {inline(q[1], `q${i}`, snippets, refs, onCitationClick)}
        </div>
      );
      continue;
    }
    const tbl = line.match(/^\|.+\|$/);
    if (tbl && lines[i + 1]?.match(/^\|[\s\-:|]+\|$/)) {
      flushList(`list-${i}`);
      const header = line.slice(1, -1).split("|").map((c) => c.trim());
      const body = lines[i + 2]?.match(/^\|.+\|$/)
        ? lines[i + 2].slice(1, -1).split("|").map((c) => c.trim())
        : null;
      out.push(
        <table key={`tbl${i}`} className="kb-chat-md-table">
          <thead>
            <tr>
              {header.map((c, n) => (
                <th key={n}>{inline(c, `th${n}`, snippets, refs, onCitationClick)}</th>
              ))}
            </tr>
          </thead>
          {body && (
            <tbody>
              <tr>
                {body.map((c, n) => (
                  <td key={n}>{inline(c, `td${n}`, snippets, refs, onCitationClick)}</td>
                ))}
              </tr>
            </tbody>
          )}
        </table>
      );
      i += body ? 2 : 1;
      continue;
    }
    flushList(`list-${i}`);
    if (line.trim() === "") {
      out.push(<div key={`sp${i}`} className="kb-chat-md-spacer" />);
    } else {
      out.push(
        <div key={`p${i}`} className="kb-chat-md-p">
          {inline(line, `p${i}`, snippets, refs, onCitationClick)}
        </div>
      );
    }
  }
  flushList(`list-end`);
  return out;
}

export default function MarkdownViewer({ text, refs, onCitationClick }: Props) {
  // 引用预处理：把 <kb doc=.../> / [chunk_id: ...] / 裸 32 位 id 换成 token
  const { content, snippets } = preprocessCitations(text, refs);
  const render = useCallback(
    () => renderBlock(content, snippets, refs, onCitationClick),
    [content, snippets, refs, onCitationClick],
  );
  return <div className="kb-chat-md">{render()}</div>;
}