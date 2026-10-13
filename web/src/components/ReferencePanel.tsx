import { useMemo, useState } from "react";
import { FileTextOutlined, JumpOutlined } from "@ant-design/icons";
import { Tooltip } from "antd";
import type { ChatRefItem } from "@/lib/api";

export interface ReferencePanelProps {
  refs: ChatRefItem[];
  /** 点击某个 chunk（正文引用胶囊 / 组内条目）时回调（kb_id/document_id 可跳转文档） */
  onChunkClick?: (ref: ChatRefItem) => void;
}

/** 引用面板：对齐 WeKnora docInfo——可折叠、按文档分组、组头文件名 + chunk 数 + 跳转、chunk hover 预览。 */
export default function ReferencePanel({ refs, onChunkClick }: ReferencePanelProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});

  const groups = useMemo(() => {
    const map = new Map<string, ChatRefItem[]>();
    for (const r of refs) {
      const title = r.document_title || "其他";
      const arr = map.get(title) || [];
      arr.push(r);
      map.set(title, arr);
    }
    return Array.from(map.entries()).map(([title, chunks]) => ({ title, chunks }));
  }, [refs]);

  const toggleGroup = (title: string) => {
    setExpandedGroups((prev) => ({ ...prev, [title]: !prev[title] }));
  };

  if (refs.length === 0) return null;

  return (
    <div className="kb-ref">
      <div className="kb-ref-header" onClick={() => setCollapsed((v) => !v)}>
        <div className="kb-ref-title">
          <FileTextOutlined className="kb-ref-title-icon" />
          <span>引用来源（{refs.length}）</span>
          <span className={`kb-ref-chevron ${collapsed ? "is-collapsed" : ""}`}>›</span>
        </div>
      </div>
      {!collapsed && (
        <div className="kb-ref-box">
          {groups.map((group) => {
            const open = expandedGroups[group.title] !== false;
            const kbId = group.chunks[0]?.kb_id;
            const docId = group.chunks[0]?.document_id;
            return (
              <div className="kb-ref-group" key={group.title}>
                <div className="kb-ref-group-header" onClick={() => toggleGroup(group.title)}>
                  <div className="kb-ref-group-left">
                    <FileTextOutlined className="kb-ref-group-icon" />
                    <span className="kb-ref-group-title" title={group.title}>
                      {group.title}
                    </span>
                    <span className="kb-ref-group-count">{group.chunks.length}</span>
                  </div>
                  {kbId && docId && (
                    <a
                      className="kb-ref-group-navigate"
                      title="查看文档"
                      onClick={(e) => e.stopPropagation()}
                      href={`/kbs/${kbId}/documents?doc=${docId}`}
                    >
                      <JumpOutlined />
                    </a>
                  )}
                </div>
                {open && (
                  <div className="kb-ref-group-chunks">
                    {group.chunks.map((chunk, i) => (
                      <Tooltip
                        key={`${chunk.chunk_id}-${i}`}
                        title={
                          <div style={{ maxWidth: 380, maxHeight: 240, overflow: "auto" }}>
                            {chunk.content ? chunk.content.slice(0, 600) : "（无原文）"}
                            {chunk.content && chunk.content.length > 600 ? "…" : ""}
                          </div>
                        }
                        mouseEnterDelay={0.2}
                      >
                        <span
                          className="kb-ref-chunk-text"
                          onClick={() => onChunkClick?.(chunk)}
                        >
                          {chunk.content ? chunk.content.slice(0, 60) : chunk.chunk_id}
                          {(chunk.score ?? 0) > 0 ? ` · ${(chunk.score ?? 0).toFixed(2)}` : ""}
                        </span>
                      </Tooltip>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
