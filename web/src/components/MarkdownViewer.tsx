"use client";

import { Fragment, ReactNode } from "react";

/** 轻量 Markdown 渲染（标题/粗体/斜体/行内代码/代码块/列表/链接/引用/表格）。
 * 对齐 WeKnora 消息渲染的常用子集，避免引入额外依赖。 */
function inline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // 代码块内联处理
  const parts = text.split(/(`[^`]+`)/g);
  parts.forEach((p, i) => {
    if (p.startsWith("`") && p.endsWith("`") && p.length > 2) {
      nodes.push(
        <code key={`${keyPrefix}-c${i}`} className="kb-chat-md-inline-code">
          {p.slice(1, -1)}
        </code>
      );
      return;
    }
    // 粗体 **x**
    const boldParts = p.split(/(\*\*[^*]+\*\*)/g);
    boldParts.forEach((bp, j) => {
      if (bp.startsWith("**") && bp.endsWith("**") && bp.length > 4) {
        nodes.push(<strong key={`${keyPrefix}-b${i}-${j}`}>{bp.slice(2, -2)}</strong>);
        return;
      }
      const linkParts = bp.split(/(\[[^\]]+\]\([^)]+\))/g);
      linkParts.forEach((lp, k) => {
        const m = lp.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
        if (m) {
          nodes.push(
            <a
              key={`${keyPrefix}-l${i}-${j}-${k}`}
              href={m[2]}
              target="_blank"
              rel="noreferrer"
              className="kb-chat-md-link"
            >
              {m[1]}
            </a>
          );
        } else {
          nodes.push(<Fragment key={`${keyPrefix}-t${i}-${j}-${k}`}>{lp}</Fragment>);
        }
      });
    });
  });
  return nodes;
}

function renderBlock(text: string): ReactNode[] {
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
          <li key={n} className="kb-chat-md-li">
            {inline(it, `${key}-li${n}`)}
          </li>
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
          {inline(h[2], `h${i}`)}
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
          {inline(q[1], `q${i}`)}
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
                <th key={n}>
                  {inline(c, `th${n}`)}
                </th>
              ))}
            </tr>
          </thead>
          {body && (
            <tbody>
              <tr>
                {body.map((c, n) => (
                  <td key={n}>
                    {inline(c, `td${n}`)}
                  </td>
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
          {inline(line, `p${i}`)}
        </div>
      );
    }
  }
  flushList(`list-end`);
  return out;
}

export default function MarkdownViewer({ text }: { text: string }) {
  return <div className="kb-chat-md">{renderBlock(text)}</div>;
}
