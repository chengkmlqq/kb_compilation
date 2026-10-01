"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import {
  DeleteOutlined,
  EditOutlined,
  MessageOutlined,
  PaperClipOutlined,
  PlusOutlined,
  PushpinOutlined,
  SearchOutlined,
  SendOutlined,
  StopOutlined,
} from "@ant-design/icons";
import {
  apiCreateAgentSession,
  apiCreateSession,
  apiDeleteAttachment,
  apiDeleteSession,
  apiFollowUp,
  apiGenerateTitle,
  apiListAgents,
  apiListAttachments,
  apiListKbs,
  apiListSessions,
  apiLoadSessionMessages,
  apiRecommendQuestions,
  apiSearchMessages,
  apiUpdateSession,
  apiUploadAttachment,
  ChatAttachmentItem,
  ChatMessageItem,
  ChatSessionItem,
  KbItem,
} from "@/lib/api";

const { Text } = Typography;

interface UiMessage extends ChatMessageItem {
  streaming?: boolean;
  followUps?: string[];
  followUpLoading?: boolean;
  followUpsDismissed?: boolean;
}

export default function ChatPage() {
  const { message: toast } = App.useApp();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [kbId, setKbId] = useState<string>();
  const [input, setInput] = useState("");
  const [msgs, setMsgs] = useState<UiMessage[]>([]);
  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const [activeSession, setActiveSession] = useState<string>();
  const [streaming, setStreaming] = useState(false);
  const [recommendations, setRecommendations] = useState<string[]>([]);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const [renameTarget, setRenameTarget] = useState<ChatSessionItem | null>(null);
  const [renameValue, setRenameValue] = useState("");
  // L3：Agent 模式 + 附件 + 消息搜索
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [agentMode, setAgentMode] = useState<string>(); // 选中的 agent id
  const [attachments, setAttachments] = useState<ChatAttachmentItem[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchKeyword, setSearchKeyword] = useState("");
  const [searchResults, setSearchResults] = useState<(ChatMessageItem & { session_id: string; session_title: string })[]>([]);
  const [searching, setSearching] = useState(false);

  const scrollBottom = () => {
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 60);
  };

  // 加载知识库列表
  useEffect(() => {
    (async () => {
      const res = await apiListKbs(1, 100);
      if (res.success) {
        const items = res.data?.items || [];
        setKbs(items);
        if (items.length > 0 && !kbId) setKbId(items[0].id);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 加载会话列表
  const loadSessions = useCallback(async () => {
    const res = await apiListSessions(1, 100);
    if (res.success) setSessions(res.data?.items || []);
  }, []);

  useEffect(() => {
    void loadSessions();
    void (async () => {
      const res = await apiListAgents(1, 100);
      if (res.success) {
        setAgents((res.data?.items || []).map((a) => ({ id: a.id, name: a.name })));
      }
    })();
  }, [loadSessions]);

  // 打开会话：加载历史 + 推荐问题
  const openSession = useCallback(
    async (sessionId: string) => {
      setActiveSession(sessionId);
      setRecommendations([]);
      setLoadingMsgs(true);
      try {
        const res = await apiLoadSessionMessages(sessionId);
        setMsgs((res.data?.items || []) as UiMessage[]);
        const s = sessions.find((x) => x.id === sessionId);
        if (s?.kb_id) setKbId(s.kb_id);
        if (s?.agent_id) {
          setAgentMode(s.agent_id);
        } else {
          setAgentMode(undefined);
        }
        // 加载附件
        const att = await apiListAttachments(sessionId);
        if (att.success) setAttachments(att.data?.items || []);
      } finally {
        setLoadingMsgs(false);
        scrollBottom();
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sessions],
  );

  // 新建会话
  const newSession = async () => {
    const res = await apiCreateSession(kbId);
    if (res.success && res.data) {
      setActiveSession(res.data.id);
      setMsgs([]);
      setRecommendations([]);
      await loadSessions();
      // 拉推荐问题（新会话，无消息）
      const rec = await apiRecommendQuestions(kbId);
      if (rec.success) setRecommendations(rec.data?.questions || []);
      scrollBottom();
    } else {
      toast.error(res.message || "创建会话失败");
    }
  };

  // 选择知识库时：若新会话则更新推荐问题
  const onKbChange = async (id: string) => {
    setKbId(id);
    if (!activeSession || msgs.length === 0) {
      const rec = await apiRecommendQuestions(id);
      if (rec.success) setRecommendations(rec.data?.questions || []);
    }
  };

  const send = async (textOverride?: string) => {
    const question = (textOverride ?? input).trim();
    if (!question || streaming) return;
    if (!kbId) {
      toast.warning("请先选择知识库");
      return;
    }
    const userMsg: UiMessage = {
      id: `local-user-${Date.now()}`,
      role: "user",
      content: question,
      created_at: new Date().toISOString(),
    };
    const placeholder: UiMessage = {
      id: `local-ai-${Date.now()}`,
      role: "assistant",
      content: "",
      refs: [],
      streaming: true,
      created_at: new Date().toISOString(),
    };
    setMsgs((prev) => [...prev, userMsg, placeholder]);
    setInput("");
    setRecommendations([]);
    setStreaming(true);
    scrollBottom();

    const controller = new AbortController();
    abortRef.current = controller;

    // 若还没有会话，先建一个（用第一条问题当会话）
    let sessionId = activeSession;
    if (!sessionId) {
      const created = agentMode
        ? await apiCreateAgentSession(agentMode, kbId, question.slice(0, 30))
        : await apiCreateSession(kbId, question.slice(0, 30));
      if (created.success && created.data) {
        sessionId = created.data.id;
        setActiveSession(sessionId);
        await loadSessions();
      } else {
        toast.error(created.message || "创建会话失败");
        setMsgs((prev) => prev.filter((m) => m.id !== userMsg.id && m.id !== placeholder.id));
        setStreaming(false);
        return;
      }
    }

    try {
      const endpoint = agentMode
        ? `/api/v1/agents/${agentMode}/qa/stream`
        : "/api/v1/qa/stream";
      const resp = await fetch(endpoint, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          kb_id: kbId,
          question,
          session_id: sessionId,
          top_k: 5,
          threshold: 0.2,
          embed_query: true,
        }),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let full = "";
      let refs: { chunk_id: string; score: number }[] = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          try {
            const evt = JSON.parse(line.slice(5).trim());
            if (evt.type === "delta") {
              full += evt.text || "";
              setMsgs((prev) =>
                prev.map((m) => (m.id === placeholder.id ? { ...m, content: full } : m)),
              );
              scrollBottom();
            } else if (evt.type === "context") {
              refs = (evt.hits || []).map((h: { chunk_id: string; score: number }) => ({
                chunk_id: h.chunk_id,
                score: h.score,
              }));
            } else if (evt.type === "error") {
              toast.error(evt.message || "问答失败");
            }
          } catch {
            // 非 JSON 行（如 keep-alive）忽略
          }
        }
      }

      // 完成：标记 streaming 结束，触发追问建议 + 标题
      setMsgs((prev) =>
        prev.map((m) =>
          m.id === placeholder.id ? { ...m, content: full, refs, streaming: false } : m,
        ),
      );
      setStreaming(false);
      // 后台生成标题（成功会话）
      if (sessionId) {
        void apiGenerateTitle(sessionId).then(() => loadSessions());
        if (full) {
          void apiFollowUp(sessionId, full).then((res) => {
            if (res.success) {
              setMsgs((prev) =>
                prev.map((m) =>
                  m.id === placeholder.id ? { ...m, followUps: res.data?.questions || [] } : m,
                ),
              );
            }
          });
        }
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        // 用户主动停止：保留已生成部分
        const finalContent = (() => {
          let c = "";
          setMsgs((prev) => {
            const target = prev.find((m) => m.id === placeholder.id);
            c = target?.content || "";
            return prev.map((m) => (m.id === placeholder.id ? { ...m, streaming: false } : m));
          });
          return c;
        })();
        void apiFollowUp(sessionId!, finalContent).then((res) => {
          if (res.success) {
            setMsgs((prev) =>
              prev.map((m) =>
                m.id === placeholder.id ? { ...m, followUps: res.data?.questions || [] } : m,
              ),
            );
          }
        });
      } else {
        toast.error("对话请求失败，请重试");
        setMsgs((prev) => prev.filter((m) => m.id !== placeholder.id));
      }
      setStreaming(false);
      scrollBottom();
    }
  };

  const stopGenerating = () => {
    abortRef.current?.abort();
  };

  // 触发追问
  const askFollowUp = (q: string) => {
    void send(q);
  };

  const deleteSession = async (id: string) => {
    await apiDeleteSession(id);
    if (activeSession === id) {
      setActiveSession(undefined);
      setMsgs([]);
      setRecommendations([]);
      setAttachments([]);
      setAgentMode(undefined);
    }
    await loadSessions();
  };

  // L3：附件上传/删除
  const uploadAttachment = async (file: File) => {
    let sessionId = activeSession;
    if (!sessionId) {
      const created = agentMode
        ? await apiCreateAgentSession(agentMode, kbId)
        : await apiCreateSession(kbId);
      if (created.success && created.data) {
        sessionId = created.data.id;
        setActiveSession(sessionId);
        await loadSessions();
      } else {
        toast.error("创建会话失败，无法上传附件");
        return;
      }
    }
    const res = await apiUploadAttachment(sessionId!, file);
    if (res.success && res.data) {
      setAttachments((prev) => [...prev, res.data!]);
      toast.success(`附件已上传：${res.data.file_name}`);
    } else {
      toast.error(res.message || "附件上传失败");
    }
  };

  const removeAttachment = async (attId: string) => {
    if (!activeSession) return;
    const res = await apiDeleteAttachment(activeSession, attId);
    if (res.success) {
      setAttachments((prev) => prev.filter((a) => a.id !== attId));
    } else {
      toast.error(res.message || "删除附件失败");
    }
  };

  // L3：消息搜索
  const doSearch = async () => {
    if (!searchKeyword.trim()) {
      setSearchResults([]);
      return;
    }
    setSearching(true);
    try {
      const res = await apiSearchMessages(searchKeyword.trim(), 20);
      if (res.success) {
        setSearchResults(res.data?.items || []);
      } else {
        toast.error(res.message || "搜索失败");
      }
    } finally {
      setSearching(false);
    }
  };

  const togglePin = async (s: ChatSessionItem) => {
    await apiUpdateSession(s.id, { pinned: !s.pinned });
    await loadSessions();
  };

  const renameSession = async () => {
    if (!renameTarget || !renameValue.trim()) return;
    await apiUpdateSession(renameTarget.id, { title: renameValue.trim() });
    setRenameTarget(null);
    await loadSessions();
  };

  return (
    <div style={{ display: "flex", height: "calc(100vh - 64px - 48px)", gap: 16 }}>
      {/* 左：会话列表 */}
      <Card
        style={{ width: 280, flexShrink: 0, overflow: "auto" }}
        styles={{ body: { padding: 12 } }}
        title={
          <Space style={{ width: "100%", justifyContent: "space-between" }}>
            <span>会话</span>
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => void newSession()}>
              新会话
            </Button>
          </Space>
        }
      >
        <List
          dataSource={sessions}
          locale={{ emptyText: <Empty description="暂无会话" /> }}
          renderItem={(s) => (
            <List.Item
              style={{
                padding: "6px 8px",
                borderRadius: 6,
                cursor: "pointer",
                background: activeSession === s.id ? "#e6f4ff" : "transparent",
              }}
              onClick={() => void openSession(s.id)}
              actions={[
                <Tooltip key="pin" title={s.pinned ? "取消置顶" : "置顶"}>
                  <PushpinOutlined
                    style={{ color: s.pinned ? "#1677ff" : "#999" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      void togglePin(s);
                    }}
                  />
                </Tooltip>,
                <Tooltip key="rename" title="重命名">
                  <EditOutlined
                    style={{ color: "#999" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      setRenameTarget(s);
                      setRenameValue(s.title);
                    }}
                  />
                </Tooltip>,
                <Popconfirm
                  key="del"
                  title="删除该会话？"
                  onConfirm={(e) => {
                    e?.stopPropagation();
                    void deleteSession(s.id);
                  }}
                >
                  <DeleteOutlined style={{ color: "#ff4d4f" }} onClick={(e) => e.stopPropagation()} />
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                avatar={s.pinned ? <PushpinOutlined style={{ color: "#1677ff" }} /> : <MessageOutlined />}
                title={<Text ellipsis style={{ maxWidth: 140 }}>{s.title}</Text>}
                description={<Text type="secondary" style={{ fontSize: 12 }}>{s.updated_at.slice(5, 16).replace("T", " ")}</Text>}
              />
            </List.Item>
          )}
        />
      </Card>

      {/* 右：对话区 */}
      <Card style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }} styles={{ body: { display: "flex", flexDirection: "column", height: "100%", padding: 0 } }}>
        <div style={{ padding: "12px 16px", borderBottom: "1px solid #f0f0f0", display: "flex", alignItems: "center", gap: 12 }}>
          <Text strong>智能问答（RAG）</Text>
          <Select
            style={{ width: 180 }}
            placeholder="Agent 模式"
            value={agentMode}
            allowClear
            onChange={(v) => {
              setAgentMode(v || undefined);
              if (v) {
                setActiveSession(undefined);
                setMsgs([]);
                setRecommendations([]);
                setAttachments([]);
              }
            }}
            options={agents.map((a) => ({ value: a.id, label: `🤖 ${a.name}` }))}
          />
          <Select
            style={{ width: 240 }}
            placeholder="选择知识库"
            value={kbId}
            onChange={(v) => void onKbChange(v)}
            options={kbs.map((k) => ({ value: k.id, label: k.name }))}
          />
          <Button icon={<SearchOutlined />} onClick={() => setSearchOpen(true)}>
            搜消息
          </Button>
          {streaming && (
            <Button size="small" danger icon={<StopOutlined />} onClick={stopGenerating}>
              停止
            </Button>
          )}
        </div>

        <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
          {loadingMsgs ? (
            <div style={{ textAlign: "center", padding: 40 }}>
              <Spin />
            </div>
          ) : msgs.length === 0 ? (
            <div style={{ textAlign: "center", padding: 32 }}>
              <Text type="secondary" style={{ fontSize: 15 }}>输入问题开始问答，回答基于知识库检索片段生成。</Text>
              {recommendations.length > 0 && (
                <div style={{ marginTop: 20, display: "flex", flexDirection: "column", gap: 8, alignItems: "center" }}>
                  <Text type="secondary">推荐问题</Text>
                  {recommendations.map((q, i) => (
                    <Button key={i} onClick={() => void send(q)} style={{ maxWidth: 420 }}>
                      {q}
                    </Button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            msgs.map((m, idx) => (
              <div key={m.id} style={{ marginBottom: 16 }}>
                <div style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
                  <div
                    style={{
                      maxWidth: "80%",
                      padding: "10px 14px",
                      borderRadius: 10,
                      background: m.role === "user" ? "#1677ff" : "#f5f5f5",
                      color: m.role === "user" ? "#fff" : "#333",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                    }}
                  >
                    {m.content || (m.streaming ? "思考中…" : "")}
                    {m.streaming && m.content && (
                      <span style={{ display: "inline-block", animation: "none", marginLeft: 2 }}>
                        <span style={{ animation: "kb-blink 1s infinite" }}>▋</span>
                      </span>
                    )}
                  </div>
                </div>
                {/* 引用 */}
                {m.role === "assistant" && m.refs && m.refs.length > 0 && !m.streaming && (
                  <div style={{ marginTop: 6 }}>
                    <Space size={4} wrap>
                      {m.refs.slice(0, 5).map((r, i) => (
                        <Tag key={i} color="blue" style={{ fontSize: 11 }}>
                          引用 {i + 1} · {(r.score || 0).toFixed(2)}
                        </Tag>
                      ))}
                    </Space>
                  </div>
                )}
                {/* 追问建议 */}
                {m.role === "assistant" && !m.streaming && m.followUps && m.followUps.length > 0 && (
                  <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {m.followUps.map((q, i) => (
                      <Button key={i} size="small" onClick={() => askFollowUp(q)} style={{ fontSize: 12 }}>
                        {q}
                      </Button>
                    ))}
                  </div>
                )}
                {idx === msgs.length - 1 && m.role === "assistant" && m.streaming && (
                  <div style={{ marginTop: 4 }}>
                    <Spin size="small" />
                  </div>
                )}
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>

        <div style={{ padding: "12px 16px", borderTop: "1px solid #f0f0f0" }}>
          {/* 附件区 */}
          {(attachments.length > 0 || true) && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
              {attachments.map((a) => (
                <Tag key={a.id} closable onClose={() => void removeAttachment(a.id)} style={{ maxWidth: 240 }}>
                  📎 {a.file_name}
                </Tag>
              ))}
              <Upload
                accept=".pdf,.doc,.docx,.md,.txt,.html,.xlsx,.pptx"
                showUploadList={false}
                beforeUpload={(file) => {
                  void uploadAttachment(file as File);
                  return false;
                }}
              >
                <Button size="small" icon={<PaperClipOutlined />}>
                  上传附件
                </Button>
              </Upload>
            </div>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <Input.TextArea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="输入问题，Enter 发送，Shift+Enter 换行"
              autoSize={{ minRows: 1, maxRows: 4 }}
              disabled={streaming}
              onPressEnter={(e) => {
                if (!e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              style={{ flex: 1 }}
            />
            <Button type="primary" icon={<SendOutlined />} loading={streaming} disabled={!kbId} onClick={() => void send()}>
              发送
            </Button>
          </div>
        </div>
      </Card>

      {/* 重命名弹窗 */}
      <Modal
        title="重命名会话"
        open={!!renameTarget}
        onCancel={() => setRenameTarget(null)}
        onOk={() => void renameSession()}
      >
        <Input value={renameValue} onChange={(e) => setRenameValue(e.target.value)} placeholder="会话标题" />
      </Modal>

      {/* 消息搜索弹窗 */}
      <Modal
        title="消息搜索"
        open={searchOpen}
        onCancel={() => setSearchOpen(false)}
        footer={null}
        width={640}
      >
        <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
          <Input
            value={searchKeyword}
            onChange={(e) => setSearchKeyword(e.target.value)}
            placeholder="输入关键词，搜索所有会话的历史消息"
            onPressEnter={() => void doSearch()}
          />
          <Button type="primary" loading={searching} onClick={() => void doSearch()}>
            搜索
          </Button>
        </Space.Compact>
        <div style={{ maxHeight: 420, overflow: "auto" }}>
          {searchResults.length === 0 ? (
            <Empty description="无匹配消息" style={{ padding: 24 }} />
          ) : (
            searchResults.map((r) => (
              <div key={r.id} style={{ padding: "8px 0", borderBottom: "1px solid #f0f0f0" }}>
                <Tag color="blue">{r.session_title || "未知会话"}</Tag>
                <Tag>{r.role === "user" ? "问" : "答"}</Tag>
                <Text style={{ fontSize: 13 }} ellipsis>
                  {r.content.slice(0, 120)}
                </Text>
              </div>
            ))
          )}
        </div>
      </Modal>

      <style jsx global>{`
        @keyframes kb-blink { 0%,100% { opacity: 1 } 50% { opacity: 0 } }
      `}</style>
    </div>
  );
}