"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { App, Button, Card, Empty, Input, Select, Space, Spin, Tag, Typography } from "antd";
import { SendOutlined } from "@ant-design/icons";
import { apiListKbs, KbItem } from "@/lib/api";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  refs?: { chunk_id: string; score: number }[];
}

interface StreamEvent {
  type?: string;
  content?: string;
  message?: string;
  search_results?: { chunk_id: string; score: number; content?: string }[];
  refs?: { chunk_id: string; score: number }[];
}

export default function ChatPage() {
  const { message } = App.useApp();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [kbId, setKbId] = useState<string>();
  const [input, setInput] = useState("");
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    (async () => {
      const res = await apiListKbs(1, 100);
      if (res.success) {
        const items = res.data?.items || [];
        setKbs(items);
        if (items.length > 0 && !kbId) setKbId(items[0].id);
      }
    })();
  }, [kbId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);

  const send = useCallback(async () => {
    const question = input.trim();
    if (!question || streaming) return;
    if (!kbId) {
      message.warning("请先选择知识库");
      return;
    }
    setInput("");
    setMsgs((prev) => [...prev, { role: "user", content: question }]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const history = msgs
        .filter((m) => m.content)
        .map((m) => ({ role: m.role, content: m.content }));

      const res = await fetch(`/api/v1/qa/stream`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ kb_id: kbId, question, history, top_k: 5, threshold: 0.2, embed_query: true }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        message.error(`流式请求失败 HTTP ${res.status}`);
        return;
      }

      setMsgs((prev) => [...prev, { role: "assistant", content: "" }]);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE lines: data: {json}\n\n
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          const line = block.trim();
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (payload === "[DONE]") continue;
          try {
            const evt = JSON.parse(payload) as StreamEvent;
            if (evt.type === "error") {
              message.error(evt.message || "回答出错");
            } else if (typeof evt.content === "string" && evt.content) {
              setMsgs((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last?.role === "assistant") {
                  next[next.length - 1] = { ...last, content: last.content + evt.content };
                }
                return next;
              });
            } else if (evt.type === "context" && Array.isArray(evt.search_results)) {
              const results: { chunk_id: string; score: number }[] = evt.search_results || [];
              setMsgs((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last?.role === "assistant") {
                  next[next.length - 1] = {
                    ...last,
                    refs: results.map((r) => ({ chunk_id: r.chunk_id, score: r.score })),
                  };
                }
                return next;
              });
            }
          } catch {
            // ignore non-JSON keep-alive lines
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        console.error(e);
        message.error("问答请求失败");
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  }, [input, streaming, kbId, msgs, message]);

  return (
    <Card title="智能问答（RAG）">
      <Space direction="vertical" size="middle" style={{ display: "flex", height: "calc(100vh - 220px)" }}>
        <Space>
          <Typography.Text>选择知识库：</Typography.Text>
          <Select
            style={{ width: 260 }}
            placeholder="选择知识库"
            value={kbId}
            onChange={setKbId}
            options={kbs.map((kb) => ({ value: kb.id, label: kb.name }))}
          />
        </Space>

        <div style={{ flex: 1, overflowY: "auto", border: "1px solid #eee", borderRadius: 8, padding: 16 }}>
          {msgs.length === 0 ? (
            <Empty description="输入问题开始问答。回答会基于知识库检索片段生成。" style={{ marginTop: 80 }} />
          ) : (
            msgs.map((m, idx) => (
              <div key={idx} style={{ marginBottom: 12, textAlign: m.role === "user" ? "right" : "left" }}>
                <Tag color={m.role === "user" ? "blue" : "green"} style={{ marginBottom: 4 }}>
                  {m.role === "user" ? "我" : "助手"}
                </Tag>
                <div
                  style={{
                    display: "inline-block",
                    maxWidth: "85%",
                    background: m.role === "user" ? "#e6f4ff" : "#f6ffed",
                    padding: "8px 12px",
                    borderRadius: 8,
                    whiteSpace: "pre-wrap",
                    textAlign: "left",
                  }}
                >
                  {m.content}
                  {streaming && idx === msgs.length - 1 && m.role === "assistant" && (
                    <Spin size="small" style={{ marginLeft: 8 }} />
                  )}
                  {m.refs && m.refs.length > 0 && (
                    <div style={{ marginTop: 8, opacity: 0.7, fontSize: 12 }}>
                      {m.refs.map((r) => (
                        <Tag key={r.chunk_id} style={{ marginRight: 4 }}>
                          #{r.chunk_id.slice(0, 8)} {r.score.toFixed(3)}
                        </Tag>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>

        <Space.Compact style={{ width: "100%" }}>
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={() => void send()}
            placeholder="输入问题，回车发送"
            disabled={streaming}
          />
          <Button type="primary" icon={<SendOutlined />} onClick={() => void send()} loading={streaming}>
            发送
          </Button>
        </Space.Compact>
      </Space>
    </Card>
  );
}
