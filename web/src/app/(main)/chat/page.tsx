"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  App,
  Button,
  Card,
  Col,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import {
  AimOutlined,
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  MessageOutlined,
  PaperClipOutlined,
  PictureOutlined,
  PlusOutlined,
  PushpinOutlined,
  ReloadOutlined,
  SearchOutlined,
  SendOutlined,
  StopOutlined,
} from "@ant-design/icons";
import {
  apiBatchDeleteSessions,
  apiCreateAgentSession,
  apiCreateSession,
  apiDeleteAttachment,
  apiDeleteSession,
  apiFollowUp,
  apiForkSession,
  apiGenerateTitle,
  apiListAgents,
  apiListAttachments,
  apiListModels,
  apiListKbs,
  apiListSessions,
  apiLoadSessionMessages,
  apiRecommendQuestions,
  apiRewindSession,
  apiSearchMessages,
  apiUpdateSession,
  apiUploadAttachment,
  ChatAttachmentItem,
  ChatMessageItem,
  ChatRefItem,
  QaStreamEvent,
  ChatSessionItem,
  KbItem,
  ModelItem,
} from "@/lib/api";
import MarkdownViewer from "@/components/MarkdownViewer";
import RagPipelineProgress, { RagStep } from "@/components/RagPipelineProgress";

const { Text } = Typography;

interface UiMessage extends ChatMessageItem {
  streaming?: boolean;
  followUps?: string[];
  followUpLoading?: boolean;
  followUpsDismissed?: boolean;
  error?: boolean;
  steps?: RagStep[];
}

// 切页续传锚点：qa-resume:{sessionId} → {stream_id, offset}
const QA_RESUME_KEY = (sid: string) => `qa-resume:${sid}`;

export default function ChatPage() {
  const { message: toast } = App.useApp();
  const router = useRouter();
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
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const msgsRef = useRef<UiMessage[]>([]);
  // 保持 msgsRef 与 msgs 同步（供 tryResume 读最新列表）
  useEffect(() => {
    msgsRef.current = msgs;
  }, [msgs]);
  const [renameTarget, setRenameTarget] = useState<ChatSessionItem | null>(null);
  const [renameValue, setRenameValue] = useState("");
  // L3：Agent 模式 + 附件 + 消息搜索
  const [agents, setAgents] = useState<{ id: string; name: string; config?: Record<string, unknown> }[]>([]);
  const [agentMode, setAgentMode] = useState<string>(); // 选中的 agent id
  const [attachments, setAttachments] = useState<ChatAttachmentItem[]>([]);
  // 2026-10-08 对齐 WeKnora：图片独立入口 + 发送前预览（本地对象 URL）
  const [imagePreviews, setImagePreviews] = useState<{ file: File; url: string }[]>([]);
  const MAX_IMAGES = 5;
  const [searchOpen, setSearchOpen] = useState(false);
  const [refsDrawer, setRefsDrawer] = useState<{ title: string; refs: ChatRefItem[] } | null>(null);
  const [searchKeyword, setSearchKeyword] = useState("");
  const [searchResults, setSearchResults] = useState<(ChatMessageItem & { session_id: string; session_title: string })[]>([]);
  const [searching, setSearching] = useState(false);
  // 2026-10-06: 模型切换——chat 模型下拉（空=默认模型）
  const [chatModels, setChatModels] = useState<ModelItem[]>([]);
  const [modelId, setModelId] = useState<string>("");
  // 引用抽屉（WeKnora 对齐：气泡角标 → 右侧抽屉看全部引用原文）
  const [refDrawer, setRefDrawer] = useState<{
    open: boolean;
    refs: ChatRefItem[];
    answer: string;
  }>({ open: false, refs: [], answer: "" });
  // 切页续传：记录当前流 id 与已收事件数（localStorage），回来时 resume
  const [resuming, setResuming] = useState(false);

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
        setAgents(
          (res.data?.items || []).map((a) => ({
            id: a.id,
            name: a.name,
            config: a.config,
          })),
        );
      }
    })();
    // 2026-10-06: 模型切换——加载 chat 模型列表
    void (async () => {
      const res = await apiListModels({ type: "chat" });
      if (res.success) setChatModels(res.data?.items || []);
    })();
  }, [loadSessions]);

  // 切页续传：挂载时检测是否有未完成流锚点（qa-resume:{sessionId}），
  // 有则自动打开该会话（openSession 内部会 tryResume 续拉）
  useEffect(() => {
    const keys: string[] = [];
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i);
      if (k && k.startsWith("qa-resume:")) keys.push(k);
    }
    if (keys.length === 0) return;
    // 取最新的锚点会话
    const lastKey = keys[keys.length - 1];
    const sid = lastKey.slice("qa-resume:".length);
    if (sid) void openSession(sid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        // 切页续传：若该会话有未完成的流（后台仍在生成），继续拉取
        await tryResume(sessionId);
      } finally {
        setLoadingMsgs(false);
        scrollBottom();
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sessions],
  );

  // 2026-10-07 对齐 WeKnora 分支交互：从某条消息分叉出新会话
  const forkFrom = useCallback(
    async (msgId: string) => {
      if (!activeSession) return;
      try {
        const res = await apiForkSession(activeSession, msgId);
        if (!res.success || !res.data) {
          void toast.error(res.message || "分叉失败");
          return;
        }
        const sid = res.data.id;
        await loadSessions();
        await openSession(sid);
        void toast.success(`已从该消息分叉出新会话（复制 ${res.data.message_count} 条消息）`);
      } catch {
        void toast.error("分叉失败，请稍后重试");
      }
    },
    [activeSession, loadSessions, openSession, toast],
  );

  // 2026-10-07 对齐 WeKnora 分支交互：回溯到某条消息（删除其后消息）
  const rewindTo = useCallback(
    async (msgId: string) => {
      if (!activeSession) return;
      try {
        const res = await apiRewindSession(activeSession, msgId);
        if (!res.success || !res.data) {
          void toast.error(res.message || "回溯失败");
          return;
        }
        const msgsRes = await apiLoadSessionMessages(activeSession);
        setMsgs((msgsRes.data?.items || []) as UiMessage[]);
        void toast.success(`已回溯到该消息（清理 ${res.data.removed} 条后续消息）`);
      } catch {
        void toast.error("回溯失败，请稍后重试");
      }
    },
    [activeSession, toast],
  );

  // 切页续传：读取 localStorage 锚点，从 offset 续拉后台仍在生成的流
  const tryResume = async (sessionId: string) => {
    const raw = sessionStorage.getItem(QA_RESUME_KEY(sessionId));
    if (!raw) return;
    let anchor: { stream_id: string; offset: number };
    try {
      anchor = JSON.parse(raw);
    } catch {
      sessionStorage.removeItem(QA_RESUME_KEY(sessionId));
      return;
    }
    const { stream_id, offset } = anchor;
    setResuming(true);
    try {
      const resp = await fetch(`/api/v1/qa/stream/${stream_id}?after=${offset}`, {
        headers: { "content-type": "application/json" },
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let evtCount = offset;
      let full = "";
      let fullThinking = "";
      let refs: ChatRefItem[] = [];
      let resumeDone = false;
      // 补一个续传占位消息（若最后一条不是本流助手消息）
      const lastMsg = msgsRef.current?.[msgsRef.current.length - 1];
      const needPlaceholder =
        !lastMsg || lastMsg.role !== "assistant" || lastMsg.streaming !== true;
      const placeholderId = `resume-ai-${Date.now()}`;
      if (needPlaceholder) {
        setMsgs((prev) => [
          ...prev,
          {
            id: placeholderId,
            role: "assistant",
            content: "",
            refs: [],
            streaming: true,
            created_at: new Date().toISOString(),
          },
        ]);
      }
      const applyId = needPlaceholder ? placeholderId : (lastMsg?.id ?? placeholderId);
      while (true) {
        if (resumeDone) break;
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          try {
            const ev = JSON.parse(line.slice(5).trim()) as QaStreamEvent;
            if (ev.type === "delta") {
              full += ev.text || "";
              setMsgs((prev) =>
                prev.map((m) => (m.id === applyId ? { ...m, content: full } : m)),
              );
              scrollBottom();
            } else if (ev.type === "thinking") {
              fullThinking += ev.text || "";
              setMsgs((prev) =>
                prev.map((m) => (m.id === applyId ? { ...m, thinking: fullThinking } : m)),
              );
            } else if (ev.type === "context") {
              refs = (ev.hits || []).map((h) => ({
                chunk_id: h.chunk_id,
                content: h.content || "",
                document_id: h.document_id || "",
                kb_id: h.kb_id || "",
                score: h.score || 0,
                meta: h.meta || {},
              }));
            } else if (ev.type === "stage") {
              setMsgs((prev) =>
                prev.map((m) => {
                  if (m.id !== applyId) return m;
                  const steps = [...(m.steps || [])];
                  const idx = steps.findIndex((st) => st.stage === ev.stage);
                  const step: RagStep = {
                    stage: ev.stage || "",
                    status: ev.status === "done" ? "done" : "running",
                    title: ev.title || "",
                    summary: ev.summary,
                    hitsCount: ev.hits_count,
                  };
                  if (idx >= 0) steps[idx] = step;
                  else steps.push(step);
                  return { ...m, steps };
                }),
              );
            } else if (ev.type === "tool") {
              const toolSteps: RagStep[] = ((ev.tool_calls || []) as Array<{ id?: string; name?: string }>).map(
                (tc, i) => {
                  const res = ((ev.results || []) as Array<{ result?: string }>)[i];
                  const brief = res?.result ? String(res.result) : "";
                  return {
                    stage: `tool:${tc.name || "unknown"}`,
                    status: "done" as const,
                    title: `调用工具 ${tc.name || "unknown"}`,
                    summary: brief
                      ? brief.slice(0, 60) + (brief.length > 60 ? "…" : "")
                      : undefined,
                  };
                },
              );
              setMsgs((prev) =>
                prev.map((m) =>
                  m.id === applyId
                    ? { ...m, steps: [...(m.steps || []), ...toolSteps] }
                    : m,
                ),
              );
            } else if (ev.type === "done") {
              // 续传收到 done：立即结束，不再等连接关闭
              resumeDone = true;
              break;
            }
            evtCount += 1;
          } catch {
            // ignore
          }
        }
        if (resumeDone) break;
      }
      setMsgs((prev) =>
        prev.map((m) =>
          m.id === applyId
            ? { ...m, content: full, refs, thinking: fullThinking || undefined, streaming: false }
            : m,
        ),
      );
      sessionStorage.removeItem(QA_RESUME_KEY(sessionId));
      setStreaming(false);
    } catch (e) {
      console.warn("resume failed", e);
      sessionStorage.removeItem(QA_RESUME_KEY(sessionId));
      setStreaming(false);
    } finally {
      setResuming(false);
    }
  };

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
    // @提及：本条消息级目标覆盖（kb / agent），未提及则沿用顶部选择
    const effectiveKbId = atTarget?.kind === "kb" ? atTarget.id : kbId;
    const effectiveAgent = atTarget?.kind === "agent" ? atTarget.id : agentMode;
    if (!effectiveKbId && !effectiveAgent) {
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
    if (atTarget) setAtTarget(null);
    scrollBottom();

    const controller = new AbortController();
    abortRef.current = controller;

    // 若还没有会话，先建一个（用第一条问题当会话）。
    // 注意：setStreaming(true) 发生在 try 块外，这里若抛异常（网络失败等）
    // 必须复位 streaming，否则界面永远显示"正在生成回答…"。
    let sessionId = activeSession;
    if (!sessionId) {
      try {
        const created = effectiveAgent
          ? await apiCreateAgentSession(effectiveAgent, effectiveKbId, question.slice(0, 30))
          : await apiCreateSession(effectiveKbId ?? "", question.slice(0, 30));
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
      } catch (e) {
        toast.error(`创建会话失败: ${(e as Error)?.message || String(e)}`);
        setMsgs((prev) => prev.filter((m) => m.id !== userMsg.id && m.id !== placeholder.id));
        setStreaming(false);
        return;
      }
    }

    // 对齐 WeKnora：图片随发送上传到当前会话（作为问答上下文附件）
    if (imagePreviews.length) {
      const pending = imagePreviews;
      setImagePreviews([]);
      for (const p of pending) {
        try {
          await uploadAttachment(p.file, sessionId);
        } finally {
          URL.revokeObjectURL(p.url);
        }
      }
    }

    try {
      const endpoint = effectiveAgent
        ? `/api/v1/agents/${effectiveAgent}/qa/stream`
        : "/api/v1/qa/stream";
      const resp = await fetch(endpoint, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          kb_id: effectiveKbId,
          question,
          session_id: sessionId,
          top_k: 8, // 2026-10-07: 5→8——多主题综合提问时 top_k 太小会漏召回(实测跨文档综合题)
          threshold: 0.2,
          embed_query: true,
          model_id: modelId || undefined, // 2026-10-06: 模型切换（空=默认模型）
        }),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let full = "";
      let fullThinking = "";
      let refs: ChatRefItem[] = [];
      let streamId = "";
      let eventCount = 0;
      let streamDone = false;

      while (true) {
        if (streamDone) {
          // 已收到 done 事件：主动释放连接，不再等待 HTTP 关闭
          try {
            await reader.cancel();
          } catch {
            // ignore
          }
          break;
        }
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          try {
            const evt: QaStreamEvent = JSON.parse(line.slice(5).trim());
            if (evt.type === "delta") {
              full += evt.text || "";
              setMsgs((prev) =>
                prev.map((m) => (m.id === placeholder.id ? { ...m, content: full } : m)),
              );
              scrollBottom();
            } else if (evt.type === "thinking") {
              fullThinking += evt.text || "";
              setMsgs((prev) =>
                prev.map((m) =>
                  m.id === placeholder.id ? { ...m, thinking: fullThinking } : m,
                ),
              );
            } else if (evt.type === "context") {
              refs = (evt.hits || []).map((h) => ({
                chunk_id: h.chunk_id,
                content: h.content || "",
                document_id: h.document_id || "",
                kb_id: h.kb_id || "",
                score: h.score || 0,
                meta: h.meta || {},
              }));
            } else if (evt.type === "stage") {
              setMsgs((prev) =>
                prev.map((m) => {
                  if (m.id !== placeholder.id) return m;
                  const steps = [...(m.steps || [])];
                  const idx = steps.findIndex((st) => st.stage === evt.stage);
                  const step: RagStep = {
                    stage: evt.stage || "",
                    status: evt.status === "done" ? "done" : "running",
                    title: evt.title || "",
                    summary: evt.summary,
                    hitsCount: evt.hits_count,
                  };
                  if (idx >= 0) steps[idx] = step;
                  else steps.push(step);
                  return { ...m, steps };
                }),
              );
            } else if (evt.type === "tool") {
              const toolSteps: RagStep[] = ((evt.tool_calls || []) as Array<{ id?: string; name?: string }>).map(
                (tc, i) => {
                  const res = ((evt.results || []) as Array<{ result?: string }>)[i];
                  const brief = res?.result ? String(res.result) : "";
                  return {
                    stage: `tool:${tc.name || "unknown"}`,
                    status: "done" as const,
                    title: `调用工具 ${tc.name || "unknown"}`,
                    summary: brief
                      ? brief.slice(0, 60) + (brief.length > 60 ? "…" : "")
                      : undefined,
                  };
                },
              );
              setMsgs((prev) =>
                prev.map((m) =>
                  m.id === placeholder.id
                    ? { ...m, steps: [...(m.steps || []), ...toolSteps] }
                    : m,
                ),
              );
            } else if (evt.type === "stream_meta") {
              streamId = evt.stream_id || "";
              // 记录续传锚点：切页回来时从已收事件数继续
              if (sessionId && streamId) {
                sessionStorage.setItem(
                  QA_RESUME_KEY(sessionId),
                  JSON.stringify({ stream_id: streamId, offset: 0 }),
                );
              }
            } else if (evt.type === "error") {
              toast.error(evt.message || "问答失败");
            } else if (evt.type === "done") {
              // 后端已确认生成结束：立即退出循环并复位 streaming，
              // 不再依赖 HTTP 连接关闭（连接可能因 keep-alive/代理延迟关闭，
              // 否则 setStreaming(false) 永远执行不到，界面一直显示"正在回答"）。
              streamDone = true;
              break;
            }
            eventCount += 1;
            if (sessionId && streamId) {
              sessionStorage.setItem(
                QA_RESUME_KEY(sessionId),
                JSON.stringify({ stream_id: streamId, offset: eventCount }),
              );
            }
          } catch {
            // 非 JSON 行（如 keep-alive）忽略
          }
        }
      }

      // 完成：标记 streaming 结束，触发追问建议 + 标题
      setMsgs((prev) =>
        prev.map((m) =>
          m.id === placeholder.id
            ? { ...m, content: full, refs, thinking: fullThinking || undefined, streaming: false }
            : m,
        ),
      );
      setStreaming(false);
      if (sessionId) sessionStorage.removeItem(QA_RESUME_KEY(sessionId));
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
        // 失败：保留用户消息，助手消息标记 error（提供「重试」入口）
        toast.error("对话请求失败，请重试");
        setMsgs((prev) =>
          prev.map((m) =>
            m.id === placeholder.id
              ? { ...m, content: "", streaming: false, error: true }
              : m,
          ),
        );
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

  // 重新生成：重发该回答前一条用户消息
  const regenerate = (msgs_: UiMessage[], idx: number) => {
    for (let k = idx - 1; k >= 0; k--) {
      const prev = msgs_[k];
      if (prev.role === "user" && prev.content) {
        void send(prev.content);
        return;
      }
    }
    toast.warning("未找到可重新生成的问题");
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
  const uploadAttachment = async (file: File, sid?: string) => {
    let sessionId = sid ?? activeSession;
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

  // 图片问答门禁（对齐 WeKnora ImageUploadEnabled）：仅绑定智能体开启时可传图
  const activeAgentCfg = agents.find((a) => a.id === agentMode)?.config ?? {};
  const canUploadImage = !!activeAgentCfg.image_upload_enabled;
  const UPLOAD_ACCEPT = canUploadImage
    ? ".pdf,.doc,.docx,.md,.txt,.html,.xlsx,.pptx,.png,.jpg,.jpeg,.gif,.webp,.bmp"
    : ".pdf,.doc,.docx,.md,.txt,.html,.xlsx,.pptx";

  // ── 2026-10-08 对齐 WeKnora：图片 / 附件两个独立入口 ─────────────────
  const imageInputRef = useRef<HTMLInputElement>(null);
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const IMAGE_ACCEPT = "image/*";

  const addImageFiles = (files: FileList | File[] | null) => {
    if (!files || !files.length) return;
    if (!canUploadImage) return;
    const arr = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (!arr.length) return;
    setImagePreviews((prev) => {
      const room = MAX_IMAGES - prev.length;
      if (room <= 0) {
        toast.warning(`最多同时上传 ${MAX_IMAGES} 张图片`);
        return prev;
      }
      const added = arr.slice(0, room).map((file) => ({
        file,
        url: URL.createObjectURL(file),
      }));
      if (arr.length > room) toast.warning(`最多同时上传 ${MAX_IMAGES} 张图片`);
      return [...prev, ...added];
    });
  };

  const removeImagePreview = (idx: number) => {
    setImagePreviews((prev) => {
      const next = [...prev];
      URL.revokeObjectURL(next[idx].url);
      next.splice(idx, 1);
      return next;
    });
  };

  const handleChatPaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const images = Array.from(items)
      .filter((it) => it.kind === "file" && it.type.startsWith("image/"))
      .map((it) => it.getAsFile())
      .filter((f): f is File => !!f);
    if (images.length) {
      e.preventDefault();
      addImageFiles(images);
      return;
    }
    const files = Array.from(items)
      .filter((it) => it.kind === "file")
      .map((it) => it.getAsFile())
      .filter((f): f is File => !!f);
    if (files.length) {
      e.preventDefault();
      files.forEach((f) => uploadAttachment(f));
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

  const [sessionSearch, setSessionSearch] = useState("");

  const filteredSessions = useMemo(() => {
    const q = sessionSearch.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => s.title.toLowerCase().includes(q));
  }, [sessions, sessionSearch]);

  // 2026-10-07 对齐 WeKnora：批量删除模式
  const [bulkMode, setBulkMode] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);

  const batchDelete = async () => {
    if (selected.length === 0) return;
    try {
      const res = await apiBatchDeleteSessions(selected);
      setSelected([]);
      setBulkMode(false);
      if (activeSession && selected.includes(activeSession)) {
        setActiveSession(undefined);
        setMsgs([]);
        setRecommendations([]);
        setAttachments([]);
      }
      await loadSessions();
      void toast.success(`已删除 ${res.data?.deleted ?? selected.length} 个会话`);
    } catch {
      void toast.error("批量删除失败");
    }
  };

  // 2026-10-07 对齐 WeKnora：会话按日期分组（今天 / 昨天 / 7 天内 / 更早）
  const groupedSessions = useMemo(() => {
    const now = new Date();
    const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const today = startOfDay(now);
    const labels: { label: string; key: string; items: ChatSessionItem[] }[] = [];
    const buckets: Record<string, ChatSessionItem[]> = { today: [], yesterday: [], week: [], older: [] };
    for (const s2 of filteredSessions) {
      const t = startOfDay(new Date(s2.updated_at));
      const diff = today - t;
      const key = diff <= 0 ? "today" : diff <= 86400000 ? "yesterday" : diff <= 7 * 86400000 ? "week" : "older";
      buckets[key].push(s2);
    }
    const order: [string, string][] = [
      ["today", "今天"],
      ["yesterday", "昨天"],
      ["week", "7 天内"],
      ["older", "更早"],
    ];
    for (const [k, label] of order) {
      if (buckets[k].length > 0) labels.push({ label, key: k, items: buckets[k] });
    }
    return labels;
  }, [filteredSessions]);

  // 2026-10-07 对齐 WeKnora：当前会话流式生成中 → 侧栏指示
  const activeStreaming = msgs.some((m) => m.streaming && m.role === "assistant");

  // 2026-10-07 对齐 WeKnora：@提及多资源（消息级目标覆盖：知识库 / 智能体）
  const [atTarget, setAtTarget] = useState<{ kind: "kb" | "agent"; id: string; name: string } | null>(null);
  const [atOpen, setAtOpen] = useState(false);

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
        {bulkMode ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              已选 {selected.length} 项
            </Text>
            <Popconfirm title={`删除所选 ${selected.length} 个会话？`} onConfirm={() => void batchDelete()}>
              <Button size="small" danger disabled={selected.length === 0}>
                删除所选
              </Button>
            </Popconfirm>
            <Button size="small" onClick={() => { setBulkMode(false); setSelected([]); }}>
              完成
            </Button>
          </div>
        ) : (
          <Button size="small" style={{ marginBottom: 8 }} onClick={() => setBulkMode(true)}>
            多选
          </Button>
        )}
        <Input
          allowClear
          placeholder="搜索会话"
          prefix={<SearchOutlined style={{ color: "#999" }} />}
          style={{ marginBottom: 8 }}
          value={sessionSearch}
          onChange={(e) => setSessionSearch(e.target.value)}
        />
        {groupedSessions.length === 0 ? (
          <Empty description="暂无会话" />
        ) : (
          groupedSessions.map((g) => (
            <div key={g.key}>
              <Text type="secondary" style={{ fontSize: 12, display: "block", margin: "8px 0 4px" }}>
                {g.label}
              </Text>
              <List<ChatSessionItem>
                dataSource={g.items}
                locale={{ emptyText: null }}
                renderItem={(s) => {
                  const checked = selected.includes(s.id);
                  return (
                    <List.Item
                      style={{
                        padding: "6px 8px",
                        borderRadius: 6,
                        cursor: "pointer",
                        background: activeSession === s.id ? "#e6f4ff" : "transparent",
                      }}
                      onClick={() => {
                        if (bulkMode) {
                          setSelected((prev) => (checked ? prev.filter((x) => x !== s.id) : [...prev, s.id]));
                        } else {
                          void openSession(s.id);
                        }
                      }}
                      actions={[
                        activeSession === s.id && activeStreaming ? (
                          <Tag key="streaming" color="processing" style={{ fontSize: 11 }}>
                            生成中…
                          </Tag>
                        ) : null,
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
                      ].filter(Boolean)}
                    >
                      {bulkMode && (
                        <input
                          type="checkbox"
                          checked={checked}
                          readOnly
                          style={{ marginRight: 6, pointerEvents: "none" }}
                        />
                      )}
                      <List.Item.Meta
                        avatar={s.pinned ? <PushpinOutlined style={{ color: "#1677ff" }} /> : <MessageOutlined />}
                        title={<Text ellipsis style={{ maxWidth: bulkMode ? 110 : 140 }}>{s.title}</Text>}
                        description={<Text type="secondary" style={{ fontSize: 12 }}>{s.updated_at.slice(5, 16).replace("T", " ")}</Text>}
                      />
                    </List.Item>
                  );
                }}
              />
            </div>
          ))
        )}
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

        <div
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
            setShowScrollBtn(!atBottom);
          }}
          style={{ flex: 1, overflow: "auto", padding: 16, position: "relative" }}
        >
          {loadingMsgs ? (
            <div style={{ textAlign: "center", padding: 40 }}>
              <Spin />
            </div>
          ) : msgs.length === 0 ? (
            <div style={{ textAlign: "center", padding: 32 }}>
              <Text type="secondary" style={{ fontSize: 15 }}>输入问题开始问答，回答基于知识库检索片段生成。</Text>
              {recommendations.length > 0 && (
                <div style={{ marginTop: 20, maxWidth: 720, marginLeft: "auto", marginRight: "auto" }}>
                  <Space style={{ justifyContent: "center", width: "100%", marginBottom: 12 }}>
                    <Text type="secondary" style={{ fontSize: 13 }}>💡 推荐问题</Text>
                    <Button
                      type="text"
                      size="small"
                      icon={<ReloadOutlined />}
                      onClick={async () => {
                        const rec = await apiRecommendQuestions(kbId);
                        if (rec.success) setRecommendations(rec.data?.questions || []);
                      }}
                    >
                      换一批
                    </Button>
                  </Space>
                  <Row gutter={[12, 12]} justify="center">
                    {recommendations.map((q, i) => (
                      <Col key={i} xs={24} sm={12} lg={8}>
                        <Card
                          size="small"
                          hoverable
                          onClick={() => void send(q)}
                          style={{ textAlign: "left", minHeight: 76 }}
                        >
                          <Space size={6}>
                            <Tag color="cyan">FAQ</Tag>
                            <Text style={{ fontSize: 13 }} ellipsis={{ tooltip: q }}>
                              {q}
                            </Text>
                          </Space>
                        </Card>
                      </Col>
                    ))}
                  </Row>
                </div>
              )}
            </div>
          ) : (
                      <div style={{ maxWidth: 960, margin: "0 auto", display: "flex", flexDirection: "column", gap: 16 }}>
                      {msgs.map((m, idx) => (
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
                    {m.role === "assistant" && m.steps && m.steps.length > 0 && (
                      <RagPipelineProgress steps={m.steps} />
                    )}
                    {m.role === "assistant" && m.thinking && (
                      <ThinkingBlock thinking={m.thinking} streaming={!!m.streaming} />
                    )}
                    {m.error ? (
                      <div>
                        <Text type="danger">回答失败，请重试</Text>
                        <Button
                          size="small"
                          type="primary"
                          danger
                          style={{ marginTop: 8, display: "block" }}
                          onClick={() => regenerate(msgs, idx)}
                        >
                          重试
                        </Button>
                      </div>
                    ) : m.role === "assistant" ? (
                      <div>
                        {m.content ? (
                          <MarkdownViewer text={m.content} />
                        ) : m.streaming ? (
                          "思考中…"
                        ) : (
                          ""
                        )}
                        {m.streaming && m.content && (
                          <span style={{ display: "inline-block", marginLeft: 2 }}>
                            <span style={{ animation: "kb-blink 1s infinite" }}>▋</span>
                          </span>
                        )}
                      </div>
                    ) : (
                      <span style={{ whiteSpace: "pre-wrap" }}>{m.content}</span>
                    )}
                  </div>
                </div>
                {/* 2026-10-07 对齐 WeKnora 分支交互：用户消息 → 分叉 / 回溯 */}
                {m.role === "user" && (
                  <div style={{ marginTop: 4, display: "flex", gap: 2, justifyContent: "flex-end" }}>
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: "0 4px", height: "auto", fontSize: 12 }}
                      onClick={() => void forkFrom(m.id)}
                    >
                      分叉
                    </Button>
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: "0 4px", height: "auto", fontSize: 12 }}
                      onClick={() => void rewindTo(m.id)}
                    >
                      回溯到此
                    </Button>
                  </div>
                )}
                {/* 答案工具栏：复制 */}
                {m.role === "assistant" && !m.streaming && m.content && (
                  <div style={{ marginTop: 2, display: "flex", gap: 4 }}>
                    <Button
                      type="text"
                      size="small"
                      icon={<CopyOutlined />}
                      style={{ fontSize: 12, padding: "0 4px", height: "auto", color: "#999" }}
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(m.content);
                          toast.success("已复制回答");
                        } catch {
                          toast.error("复制失败");
                        }
                      }}
                    >
                      复制
                    </Button>
                  </div>
                )}
                {/* 引用 */}
                {m.role === "assistant" && m.refs && m.refs.length > 0 && !m.streaming && (
                  <div style={{ marginTop: 6 }}>
                    <Space size={4} wrap>
                      {m.refs.slice(0, 5).map((r, i) => (
                        <Tooltip
                          key={i}
                          title={
                            <div style={{ maxWidth: 360 }}>
                              {r.content ? (
                                <div style={{ maxHeight: 160, overflow: "auto", marginBottom: 6 }}>
                                  {r.content.slice(0, 300)}
                                  {r.content.length > 300 ? "…" : ""}
                                </div>
                              ) : (
                                <Text type="secondary">（无原文）</Text>
                              )}
                              {r.meta?.source_file && (
                                <Text type="secondary" style={{ fontSize: 11 }}>
                                  来源：{r.meta.source_file}
                                </Text>
                              )}
                            </div>
                          }
                          mouseEnterDelay={0.3}
                        >
                          <Tag
                            color="blue"
                            style={{ fontSize: 11, cursor: "pointer" }}
                            onClick={() => setRefDrawer({ open: true, refs: m.refs || [], answer: m.content })}
                          >
                            引用 {i + 1} · {(r.score || 0).toFixed(2)}
                          </Tag>
                        </Tooltip>
                      ))}
                      {m.refs.length > 5 && (
                        <Tag
                          style={{ fontSize: 11, cursor: "pointer" }}
                          onClick={() => setRefDrawer({ open: true, refs: m.refs || [], answer: m.content })}
                        >
                          +{m.refs.length - 5}
                        </Tag>
                      )}
                    </Space>
                  </div>
                )}
                {/* 重新生成（对齐 WeKnora 回答操作） */}
                {m.role === "assistant" && !m.streaming && !m.error && m.content && (
                  <div style={{ marginTop: 4 }}>
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: 0, height: "auto", fontSize: 12 }}
                      onClick={() => regenerate(msgs, idx)}
                    >
                      重新生成
                    </Button>
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: 0, height: "auto", fontSize: 12, marginLeft: 8 }}
                      onClick={() => void forkFrom(m.id)}
                    >
                      分叉
                    </Button>
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
            ))}
            </div>
          )}
          {streaming && (
            <div style={{ textAlign: "center", padding: "8px 0 4px" }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                🤖 AI 正在回答…
              </Text>
            </div>
          )}
          {showScrollBtn && (
            <div style={{ textAlign: "center", padding: 4 }}>
              <Button size="small" shape="round" onClick={() => scrollBottom()}>
                ↓ 回到最新
              </Button>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div style={{ padding: "12px 16px", borderTop: "1px solid #f0f0f0" }}>
          {/* 2026-10-06: 模型切换——chat 模型下拉（空=默认模型） */}
          {chatModels.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <Select
                value={modelId || undefined}
                onChange={setModelId}
                placeholder="默认模型"
                allowClear
                style={{ width: 180 }}
                options={chatModels.map((m) => ({
                  value: m.id,
                  label: m.display_name || m.name || m.id,
                }))}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                选择问答模型（留空=系统默认）
              </Text>
            </div>
          )}
          {/* 附件区（会话级已上传附件） */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
        {attachments.map((a) => (
          <Tag
            key={a.id}
            closable
            onClose={() => void removeAttachment(a.id)}
            style={{ maxWidth: 240 }}
            title={a.media_type === "image" ? `图片：${a.file_name}` : a.file_name}
          >
            {a.media_type === "image" ? "🖼️" : "📎"} {a.file_name}
          </Tag>
        ))}
      </div>
      {/* 2026-10-08 对齐 WeKnora：图片 / 附件双入口 + 输入卡片式布局 */}
      <div
        style={{
          maxWidth: 960,
          width: "100%",
          margin: "0 auto",
          border: "1px solid #dcdcdc",
          borderRadius: 12,
          boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
          background: "#fff",
          padding: "10px 14px 6px",
        }}
      >
        {/* 图片预览条（发送前本地预览） */}
        {imagePreviews.length > 0 && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", padding: "8px 0 6px" }}>
            {imagePreviews.map((p, i) => (
              <div
                key={p.url}
                style={{
                  position: "relative",
                  width: 60,
                  height: 60,
                  borderRadius: 8,
                  overflow: "hidden",
                  border: "1px solid #e7e7e7",
                  flexShrink: 0,
                  cursor: "default",
                }}
              >
                <img src={p.url} alt={p.file.name} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                <span
                  title="移除图片"
                  onClick={() => removeImagePreview(i)}
                  style={{
                    position: "absolute",
                    top: 2,
                    right: 2,
                    width: 16,
                    height: 16,
                    borderRadius: "50%",
                    background: "rgba(0,0,0,0.55)",
                    color: "#fff",
                    fontSize: 10,
                    lineHeight: "16px",
                    textAlign: "center",
                    cursor: "pointer",
                  }}
                >
                  ✕
                </span>
              </div>
            ))}
          </div>
        )}
        <Input.TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPaste={handleChatPaste}
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          autoSize={{ minRows: 1, maxRows: 4 }}
          disabled={streaming}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          variant="borderless"
          style={{ padding: "4px 0", background: "transparent", resize: "none", overflow: "auto" }}
        />
        {/* 控制栏：左 工具按钮 / 右 发送·停止 */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 8,
            padding: "6px 0 2px",
            borderTop: "1px solid #f0f0f0",
            marginTop: 4,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
            <Button
              type="text"
              size="small"
              icon={<AimOutlined />}
              title="@提及知识库 / 智能体"
              onClick={() => setAtOpen(true)}
            />
            {canUploadImage && (
              <Button
                type="text"
                size="small"
                icon={<PictureOutlined />}
                title="上传图片（随问答发给模型识别）"
                onClick={() => imageInputRef.current?.click()}
              />
            )}
            <Button
              type="text"
              size="small"
              icon={<PaperClipOutlined />}
              title="上传附件"
              onClick={() => attachmentInputRef.current?.click()}
            />
            {canUploadImage && (
              <Text type="secondary" style={{ fontSize: 12, marginLeft: 6 }}>
                可传文档或图片（随问答发送）
              </Text>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {streaming ? (
              <Button
                type="text"
                size="small"
                icon={<StopOutlined />}
                title="停止生成"
                onClick={() => abortRef.current?.abort()}
                style={{
                  width: 30,
                  height: 30,
                  borderRadius: "50%",
                  background: "rgba(22,119,255,0.08)",
                  color: "#1677ff",
                  border: "1.5px solid rgba(22,119,255,0.2)",
                }}
              />
            ) : (
              <Button
                type="primary"
                shape="circle"
                size="small"
                icon={<SendOutlined />}
                disabled={!kbId && !agentMode}
                onClick={() => void send()}
                style={{ width: 30, height: 30 }}
              />
            )}
          </div>
        </div>
        <input
          ref={imageInputRef}
          type="file"
          accept={IMAGE_ACCEPT}
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            addImageFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <input
          ref={attachmentInputRef}
          type="file"
          accept={UPLOAD_ACCEPT}
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            const files = e.target.files;
            if (files) Array.from(files).forEach((f) => void uploadAttachment(f));
            e.target.value = "";
          }}
        />
      </div>
        {/* 2026-10-07 @提及多资源：选择本次消息的目标知识库 / 智能体 */}
          <Modal
            title="@ 提及目标"
            open={atOpen}
            onCancel={() => setAtOpen(false)}
            footer={null}
            width={360}
          >
            <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
              选择后本条消息将针对该资源问答（不改变会话绑定）
            </Text>
            <div style={{ marginBottom: 8 }}>
              <Text strong style={{ fontSize: 13 }}>知识库</Text>
              <Select
                style={{ width: "100%", marginTop: 4 }}
                placeholder="选择知识库"
                value={atTarget?.kind === "kb" ? atTarget.id : undefined}
                onChange={(id) => {
                  const kb = kbs.find((k) => k.id === id);
                  if (kb) setAtTarget({ kind: "kb", id, name: kb.name });
                }}
                options={kbs.map((k) => ({ value: k.id, label: k.name }))}
              />
            </div>
            <div>
              <Text strong style={{ fontSize: 13 }}>智能体</Text>
              <Select
                style={{ width: "100%", marginTop: 4 }}
                placeholder="选择智能体"
                value={atTarget?.kind === "agent" ? atTarget.id : undefined}
                onChange={(id) => {
                  const a = agents.find((x) => x.id === id);
                  if (a) setAtTarget({ kind: "agent", id, name: `🤖 ${a.name}` });
                }}
                options={agents.map((a) => ({ value: a.id, label: `🤖 ${a.name}` }))}
              />
            </div>
            <div style={{ textAlign: "right", marginTop: 12 }}>
              <Button type="primary" onClick={() => setAtOpen(false)}>
                确定
              </Button>
            </div>
          </Modal>
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

      {/* 引用抽屉（WeKnora 对齐） */}
      <Drawer
        title="回答引用"
        open={refDrawer.open}
        onClose={() => setRefDrawer((p) => ({ ...p, open: false }))}
        width={560}
      >
        <Space direction="vertical" style={{ display: "flex" }} size={12}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            共 {refDrawer.refs.length} 条引用 · 点击跳转 wiki 页面
          </Text>
          {refDrawer.refs.map((r, i) => (
            <Card key={i} size="small" title={`引用 ${i + 1} · 相似度 ${(r.score || 0).toFixed(2)}`}>
              <Space direction="vertical" size={4} style={{ display: "flex" }}>
                {r.content && (
                  <Text style={{ fontSize: 13 }}>{r.content.slice(0, 400)}{r.content.length > 400 ? "…" : ""}</Text>
                )}
                <Space size={8} wrap>
                  {r.meta?.source_file && (
                    <Tag color="geekblue">📄 {r.meta.source_file}</Tag>
                  )}
                  <Button
                    size="small"
                    type="link"
                    style={{ fontSize: 12 }}
                    disabled={!r.kb_id || !r.chunk_id}
                    onClick={() => {
                      if (r.kb_id && r.chunk_id) {
                        void router.push(`/kbs/${r.kb_id}/wiki/${r.chunk_id}`);
                      }
                    }}
                  >
                    跳转原文 →
                  </Button>
                </Space>
              </Space>
            </Card>
          ))}
        </Space>
      </Drawer>

      <style jsx global>{`
        @keyframes kb-blink { 0%,100% { opacity: 1 } 50% { opacity: 0 } }
      `}</style>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 思考过程折叠块（WeKnora 对齐：LLM reasoning_content 可展开查看）
// ---------------------------------------------------------------------------

function ThinkingBlock({ thinking, streaming }: { thinking: string; streaming?: boolean }) {
  const [open, setOpen] = useState(false);
  // 对齐 WeKnora deepThink：思考中强制展开跟随显示；完成后自动折叠
  useEffect(() => {
    if (!streaming) setOpen(false);
  }, [streaming]);
  const expanded = streaming || open;
  return (
    <div style={{ marginBottom: 8 }}>
      <div
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          cursor: "pointer",
          fontSize: 12,
          color: streaming ? "#fa8c16" : "#888",
          background: streaming ? "#fff7e6" : "#f5f5f5",
          border: `1px solid ${streaming ? "#ffd591" : "#e8e8e8"}`,
          borderRadius: 6,
          padding: "2px 10px",
          userSelect: "none",
        }}
      >
        {streaming && <span className="kb-think-indicator" />}
        <span>{streaming ? "思考中…" : "💭 思考过程"}</span>
        <span style={{ fontSize: 10 }}>{expanded ? "▾" : "▸"}</span>
      </div>
      {expanded && (
        <div
          style={{
            marginTop: 6,
            padding: "8px 12px",
            background: "#fffbe6",
            borderLeft: "3px solid #faad14",
            borderRadius: 4,
            fontSize: 12,
            color: "#666",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 240,
            overflow: "auto",
          }}
        >
          {thinking}
        </div>
      )}
    </div>
  );
}