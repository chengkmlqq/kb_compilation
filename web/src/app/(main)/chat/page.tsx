"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  theme,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import {
  AimOutlined,
  BulbOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DownOutlined,
  EditOutlined,
  FileTextOutlined,
  GlobalOutlined,
  PaperClipOutlined,
  PictureOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
  SendOutlined,
  StopOutlined,
  LikeOutlined,
  DislikeOutlined,
} from "@ant-design/icons";
import {
  apiCreateAgentSession,
  apiCreateSession,
  apiChatConfig,
  apiDeleteAttachment,
  apiDeleteSession,
  apiGenerateTitle,
  apiListAgents,
  apiListAttachments,
  apiListModels,
  apiListKbs,
  apiListSessions,
  apiLoadSessionMessages,
  apiRecommendQuestions,
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
  apiSubmitMessageFeedback,
} from "@/lib/api";
import MarkdownViewer from "@/components/MarkdownViewer";
import TypewriterText from "@/components/TypewriterText";
import RagPipelineProgress, { RagStep } from "@/components/RagPipelineProgress";
import AgentToolCalls, { ToolCallEvent } from "@/components/AgentToolCalls";
import { copyText } from "@/lib/clipboard";

const { Text } = Typography;

interface UiMessage extends ChatMessageItem {
  streaming?: boolean;
  error?: boolean;
    steps?: RagStep[];
    toolEvents?: ToolCallEvent[];
  /** 2026-10-13 对齐 WeKnora：智能体模式回复（隐藏思考大块，思考折叠进工具步骤） */
  agent?: boolean;
  }

// 切页续传锚点：qa-resume:{sessionId} → {stream_id, offset}
const QA_RESUME_KEY = (sid: string) => `qa-resume:${sid}`;

// 本地占位消息 id（local-user-* / local-ai-* / resume-ai-*）只存在于浏览器内存，
// 服务端没有对应记录——按此类 id 调反馈/删除接口会返回 404「消息不存在」。
const LOCAL_MSG_ID_RE = /^(local|resume)-/;

// 内容区统一宽度基准：消息列表 / 输入卡片 / 推荐问题共用，保证左右边缘对齐
const CONTENT_MAX_WIDTH = 960;

export default function ChatPage() {
  const { message: toast } = App.useApp();
  const { token } = theme.useToken();
  const router = useRouter();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [kbId, setKbId] = useState<string>();
  const [input, setInput] = useState("");
  const [msgs, setMsgs] = useState<UiMessage[]>([]);
  const [sessions, setSessions] = useState<ChatSessionItem[]>([]);
  const [activeSession, setActiveSession] = useState<string>();
  const [streaming, setStreaming] = useState(false);
  const [recommendations, setRecommendations] = useState<string[]>([])
  const [recommendLoading, setRecommendLoading] = useState(false);;
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
  // 2026-10-12 对齐 WeKnora：整文件拖放上传（拖入时输入卡片高亮）
  const [dragActive, setDragActive] = useState(false);
  // 2026-10-15 联网搜索按钮显隐由参数表控制（PLATFORM_CONFIG.CHAT_WEB_SEARCH_ENABLED，默认显示）
  const [webSearchVisible, setWebSearchVisible] = useState(true);
  // 2026-10-12 对齐 WeKnora 输入区联网搜索开关（普通问答/agent 模式通用，请求级生效）
  const [webSearch, setWebSearch] = useState(false);
  // 2026-10-12 对齐 WeKnora embeddedMode：iframe 嵌入 / ?embed=1 / ?noframe=1 时
  // 隐藏左侧会话列表，问答区满宽沉浸式展示（宿主页面嵌入问答小窗场景）
  const [embeddedMode, setEmbeddedMode] = useState(false);
  useEffect(() => {
    const inIframe = typeof window !== "undefined" && window.self !== window.top;
    const params = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
    const forced = params?.get("embed") === "1" || params?.get("noframe") === "1";
    setEmbeddedMode(inIframe || forced);
  }, []);
    // 2026-10-07 对齐 WeKnora：@提及多资源（消息级目标覆盖：知识库 / 智能体）
  // 声明放在 openSession 之前（openSession 恢复输入态时用到 atTarget）
  const [atTarget, setAtTarget] = useState<{ kind: "kb" | "agent"; id: string; name: string } | null>(null);
  const [atOpen, setAtOpen] = useState(false);
  // 2026-10-12 @提及选择器分组过滤关键字
  const [atSearch, setAtSearch] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
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
    /** 点击正文引用胶囊命中的 chunk_id（抽屉内高亮定位，WeKnora 同款交互） */
    activeChunk?: string;
  }>({ open: false, refs: [], answer: "" });
  // 切页续传：记录当前流 id 与已收事件数（localStorage），回来时 resume
  const [resuming, setResuming] = useState(false);

  const scrollBottom = () => {
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 60);
  };

  // 2026-10-12: 步骤树兜底——把消息里所有仍在 running 的步骤标为 done。
  // 后端此前只发 generation/running 从不发 done（已修复），但这里仍需要
  // 防御：任何路径（done 事件、用户停止、流中断）漏发 done 时，不能让
  // 「正在生成回答…」永远转 loading。
  const finalizeRunningSteps = (msgId: string) => {
      setMsgs((prev) =>
        prev.map((m) => {
          if (m.id !== msgId) return m;
          let updated = false;
          let steps = m.steps;
          if (steps) {
            const next = steps.map((s) =>
              s.status === "running" ? { ...s, status: "done" as const, title: s.title.replace(/…$/, "") } : s,
            );
            if (next.some((s, i) => s !== steps![i])) updated = true;
            steps = next;
          }
          let toolEvents = m.toolEvents;
          if (toolEvents && toolEvents.some((t) => t.status === "running")) {
            toolEvents = toolEvents.map((t) =>
              t.status === "running"
                ? { ...t, status: "done" as const, result: t.result ?? "（未返回结果）" }
                : t,
            );
            updated = true;
          }
          return updated ? { ...m, steps, toolEvents } : m;
        }),
      );
    };

    // tool 事件回填：把 tool_call 挂上的 running 卡片按 id（缺 id 时按顺序）标 done 并回填结果/耗时。
    // 主发送与续传两条 SSE 分支共用。
    const applyToolResults = (
      msgId: string,
      toolCalls: Array<{ id?: string; name?: string }>,
      results: Array<{ result?: string; name?: string }>,
      durations: number[],
    ) => {
      setMsgs((prev) =>
        prev.map((m) => {
          if (m.id !== msgId) return m;
          const toolEvents = [...(m.toolEvents || [])];
          (Array.isArray(toolCalls) ? toolCalls : []).forEach((tc, i) => {
            const res = (Array.isArray(results) ? results : [])[i];
            const dur = (Array.isArray(durations) ? durations : [])[i];
            const tid = tc.id || "";
            let idx = tid
              ? toolEvents.findIndex((t) => t.id === tid && t.status === "running")
              : -1;
            if (idx < 0) idx = toolEvents.findIndex((t) => t.status === "running");
            if (idx >= 0) {
              toolEvents[idx] = {
                ...toolEvents[idx],
                status: "done" as const,
                result: res?.result !== undefined ? String(res.result) : "",
                duration: dur,
              };
            } else {
              toolEvents.push({
                id: tid || `tool_done_${Date.now()}_${i}`,
                name: tc.name || "unknown",
                status: "done" as const,
                result: res?.result !== undefined ? String(res.result) : "",
                duration: dur,
              });
            }
          });
          return { ...m, toolEvents };
        }),
      );
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
    void (async () => {
      const res = await apiChatConfig();
      if (res.success) setWebSearchVisible(res.data?.web_search_enabled !== false);
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

  // 2026-10-12 对齐 WeKnora last_request_state：会话输入态快照（发送时写入会话）
  const snapshotInputState = useCallback(
    () => ({
      agent_id: agentMode || null,
      kb_id: kbId || null,
      model_id: modelId || null,
      web_search_enabled: webSearch,
      at_target: atTarget,
    }),
    [agentMode, kbId, modelId, webSearch, atTarget],
  );

  // 打开会话：加载历史 + 推荐问题
  const openSession = useCallback(
    async (sessionId: string) => {
      setActiveSession(sessionId);
      setRecommendations([]);
      setLoadingMsgs(true);
      try {
        const res = await apiLoadSessionMessages(sessionId);
        const s = sessions.find((x) => x.id === sessionId);
        // 2026-10-13 对齐 WeKnora：智能体会话的历史消息标记 agent=true（隐藏思考大块）
        setMsgs(
          (res.data?.items || []).map((m) => ({ ...m, agent: !!s?.agent_id })) as UiMessage[],
        );
        // 2026-10-15 纯对话模式：会话未绑定知识库时清空选择
        // （不沿袭上一会话/默认的知识库；st 存在时其 kb_id 覆盖此值）
        setKbId(s?.kb_id || undefined);
        if (s?.agent_id) {
          setAgentMode(s.agent_id);
        } else {
          setAgentMode(undefined);
        }
        // 2026-10-12 对齐 WeKnora last_request_state：恢复会话级输入态
        // （agent/kb/模型/联网开关/@目标；kb_id 由会话绑定覆盖）
        // 2026-10-15 纯对话模式：会话未绑定知识库时清空选择（不沿袭上一会话/默认的知识库）
        const st = s?.last_request_state;
        if (st) {
          setKbId(st.kb_id || undefined);
          if (st.agent_id) setAgentMode(st.agent_id);
          if (st.model_id) setModelId(st.model_id);
          if (st.web_search_enabled != null) setWebSearch(!!st.web_search_enabled);
          if (st.at_target) setAtTarget(st.at_target);
          else setAtTarget(null);
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
            agent: !!agentMode,
            created_at: new Date().toISOString(),
          },
        ]);
      }
      let applyId = needPlaceholder ? placeholderId : (lastMsg?.id ?? placeholderId);
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
            } else if (ev.type === "tool_call") {
              // 动作开始：LLM 决定调用工具 → 挂 running 卡片（AgentToolCalls）
              const pendingEvents: ToolCallEvent[] = (
                (ev.tool_calls || []) as Array<{ id?: string; name?: string; arguments?: string }>
              ).map((tc, i) => ({
                id: tc.id || `tool_pending_${Date.now()}_${i}`,
                name: tc.name || "unknown",
                arguments: tc.arguments,
                status: "running" as const,
              }));
              setMsgs((prev) =>
                prev.map((m) =>
                  m.id === applyId
                    ? { ...m, toolEvents: [...(m.toolEvents || []), ...pendingEvents] }
                    : m,
                ),
              );
            } else if (ev.type === "tool") {
              applyToolResults(
                applyId,
                (ev.tool_calls || []) as Array<{ id?: string; name?: string }>,
                (ev.results || []) as Array<{ result?: string; name?: string }>,
                (ev.durations || []) as number[],
              );
            } else if (ev.type === "done") {
              // 续传收到 done：立即结束，不再等连接关闭
              finalizeRunningSteps(applyId); // 兜底：续传场景步骤树不残留 loading
              // 2026-10-14 续传同样对齐服务端 id（resume-ai-* 占位 → 持久化消息 id），
              // 否则续传结束后点赞/反馈会按占位 id 请求，报「消息不存在」
              const serverId = (ev.message_id || "").trim();
              if (serverId && serverId !== applyId) {
                const localId = applyId;
                setMsgs((prev) =>
                  prev.map((m) => (m.id === localId ? { ...m, id: serverId } : m)),
                );
                applyId = serverId;
              }
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
      finalizeRunningSteps(applyId); // 兜底：续传流正常结束也复位步骤树
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

  // 2026-10-14 服务端消息 id 对齐：done 事件带回持久化后的消息 id，把本地占位 id
  // （local-user-* / local-ai-* / resume-ai-*）替换为服务端 id。
  // 占位 id 只存在于前端内存，反馈/删除接口按 id 查库，不替换会 404「消息不存在」。
  const adoptServerMessageIds = (
    evt: QaStreamEvent,
    assistantLocalId: string,
    userLocalId?: string,
  ): string => {
    const serverAssistantId = (evt.message_id || "").trim();
    const serverUserId = (evt.user_message_id || "").trim();
    if (serverAssistantId && serverAssistantId !== assistantLocalId) {
      setMsgs((prev) =>
        prev.map((m) => (m.id === assistantLocalId ? { ...m, id: serverAssistantId } : m)),
      );
    }
    if (userLocalId && serverUserId && serverUserId !== userLocalId) {
      setMsgs((prev) => prev.map((m) => (m.id === userLocalId ? { ...m, id: serverUserId } : m)));
    }
    return serverAssistantId || assistantLocalId;
  };

  const send = async (textOverride?: string) => {
    const question = (textOverride ?? input).trim();
    if (!question || streaming) return;
    // @提及：本条消息级目标覆盖（kb / agent），未提及则沿用顶部选择
    const effectiveKbId = atTarget?.kind === "kb" ? atTarget.id : kbId;
    const effectiveAgent = atTarget?.kind === "agent" ? atTarget.id : agentMode;
    // 2026-10-15 纯对话模式：知识库与智能体都未选择时直接走纯 LLM 对话（不再拦截）
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
      // 2026-10-13 对齐 WeKnora：智能体模式回复标记 agent（隐藏思考大块）
      agent: !!effectiveAgent,
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
          ? await apiCreateAgentSession(effectiveAgent, effectiveKbId, question.slice(0, 30), snapshotInputState())
          : await apiCreateSession(effectiveKbId ?? "", question.slice(0, 30), snapshotInputState());
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
          web_search_enabled: webSearch, // 2026-10-12 对齐 WeKnora 输入区联网开关
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
            } else if (evt.type === "tool_call") {
              const pendingEvents: ToolCallEvent[] = (
                (evt.tool_calls || []) as Array<{ id?: string; name?: string; arguments?: string }>
              ).map((tc, i) => ({
                id: tc.id || `tool_pending_${Date.now()}_${i}`,
                name: tc.name || "unknown",
                arguments: tc.arguments,
                status: "running" as const,
              }));
              setMsgs((prev) =>
                prev.map((m) =>
                  m.id === placeholder.id
                    ? { ...m, toolEvents: [...(m.toolEvents || []), ...pendingEvents] }
                    : m,
                ),
              );
            } else if (evt.type === "tool") {
              applyToolResults(
                placeholder.id,
                (evt.tool_calls || []) as Array<{ id?: string; name?: string }>,
                (evt.results || []) as Array<{ result?: string; name?: string }>,
                (evt.durations || []) as number[],
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
              finalizeRunningSteps(placeholder.id); // 兜底：漏发 stage done 时步骤树不残留 loading
              // 2026-10-14 对齐服务端 id：本轮流已持久化，改用服务端消息 id
              // （否则点赞/反馈仍按本地占位 id 请求，报「消息不存在」）
              placeholder.id = adoptServerMessageIds(evt, placeholder.id, userMsg.id);
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

      // 完成：标记 streaming 结束，触发标题
      finalizeRunningSteps(placeholder.id); // 兜底：HTTP 关闭但漏收 done 时同样复位步骤树
      setMsgs((prev) =>
        prev.map((m) =>
          m.id === placeholder.id
            ? { ...m, content: full, refs, thinking: fullThinking || undefined, streaming: false }
            : m,
        ),
      );
      setStreaming(false);
      if (sessionId) sessionStorage.removeItem(QA_RESUME_KEY(sessionId));
      // 2026-10-12 对齐 WeKnora last_request_state：发送成功后保存会话输入态快照
      if (sessionId) {
        void apiUpdateSession(sessionId, { last_request_state: snapshotInputState() }).then(
          () => loadSessions(),
        );
      }
      // 后台生成标题（成功会话）
      if (sessionId) {
        void apiGenerateTitle(sessionId).then(() => loadSessions());
      }
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        // 用户主动停止：保留已生成部分
        finalizeRunningSteps(placeholder.id); // 停止后步骤树不再转 loading
        setMsgs((prev) =>
          prev.map((m) => (m.id === placeholder.id ? { ...m, streaming: false } : m)),
        );
        // 2026-10-12 停止后同样保存输入态快照（下次打开恢复）
        if (sessionId) void apiUpdateSession(sessionId, { last_request_state: snapshotInputState() });
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

  // 本地占位 id 的回答（停止生成后未收到 done、或旧流未回传 id）：从会话消息里
  // 回捞该回答的真实 id —— 内容一致或为本地内容的续写（停止生成时本地只收到前半段）。
  // 回捞失败不猜测，交由调用方提示「尚未保存」。
  const resolveServerMessageId = async (
    sessionId: string,
    m: UiMessage,
  ): Promise<string> => {
    const res = await apiLoadSessionMessages(sessionId);
    const assistants = (res.data?.items || []).filter((x) => x.role === "assistant");
    const local = (m.content || "").trim();
    if (!local) return "";
    const target = [...assistants]
      .reverse()
      .find((x) => {
        const server = (x.content || "").trim();
        return server === local || server.startsWith(local);
      });
    if (!target) return "";
    // 同步到本地列表：后续点击/删除直接命中服务端 id
    setMsgs((prev) => prev.map((x) => (x.id === m.id ? { ...x, id: target.id } : x)));
    return target.id;
  };

  // 2026-10-13 问答反馈：喜欢/不喜欢（再点一次取消）。乐观更新，失败回滚。
  const toggleFeedback = useCallback(
    async (m: UiMessage, type: "like" | "dislike") => {
      if (!activeSession) {
        toast.warning("请先发送消息后再反馈");
        return;
      }
      // 2026-10-14 本地占位 id 在服务端不存在，先换成服务端 id 再提交（否则 404「消息不存在」）
      let messageId = m.id;
      if (LOCAL_MSG_ID_RE.test(messageId)) {
        messageId = await resolveServerMessageId(activeSession, m);
        if (!messageId) {
          toast.error("该回答尚未保存，请稍后重试");
          return;
        }
      }
      const prev = m.feedback ?? null;
      const next = prev === type ? null : type;
      setMsgs((prevMsgs) =>
        prevMsgs.map((x) => (x.id === m.id ? { ...x, feedback: next } : x)),
      );
      const res = await apiSubmitMessageFeedback(activeSession, messageId, next);
      if (!res.success) {
        setMsgs((prevMsgs) =>
          prevMsgs.map((x) => (x.id === m.id ? { ...x, feedback: prev } : x)),
        );
        toast.error(res.message || "反馈失败，请稍后重试");
      }
    },
    [activeSession, toast],
  );

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

  // 图片问答门禁：选了智能体时按其 image_upload_enabled 配置判断（对齐
  // WeKnora ImageUploadEnabled）；未选智能体（普通问答/纯对话模式）默认允许
  // 传图——后端 /qa/stream 图片链路不依赖知识库/智能体，只要求模型 supports_vision。
  const activeAgentCfg = agents.find((a) => a.id === agentMode)?.config ?? {};
  const canUploadImage = agentMode ? !!activeAgentCfg.image_upload_enabled : true;
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

  // 2026-10-12 对齐 WeKnora Input-field：整文件拖放 —— 图片→预览条，其余→直接上传
  const handleChatDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    const files = Array.from(e.dataTransfer?.files || []);
    if (!files.length) return;
    const images = files.filter((f) => f.type.startsWith("image/"));
    const others = files.filter((f) => !f.type.startsWith("image/"));
    if (images.length) {
      if (!canUploadImage) {
        toast.warning("当前未启用图片上传，图片已忽略");
      } else {
        addImageFiles(images);
      }
    }
    others.forEach((f) => void uploadAttachment(f));
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

  // 2026-10-12 @提及选择器分组过滤关键字（声明已上移，见组件顶部输入态区）
  return (
    // 2026-10-08: 对齐任务监控页「固定视口高度 + 统一 8px 边距」——外层锁定 视口 − 顶栏(45px)，
    // 左右两栏吃满剩余高度、各自内部滚动，页面本身不出现滚动条。
    <div
      className="chat-page"
      style={{
        display: "flex",
        height: "calc(100vh - var(--app-header-h, 45px))",
        gap: 8,
        padding: 8,
        minHeight: 0,
        overflow: "hidden",
        background: "#fff",
      }}
    >
      {/* 左：会话列表（2026-10-12 embeddedMode 时隐藏，问答区满宽沉浸式） */}
      {!embeddedMode && (
      <Card
        style={{
          width: 280,
          flexShrink: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          minHeight: 0,
          background: "#f9fbfd",
          borderRight: "1px solid #e3e9ef",
        }}
        styles={{
          body: {
            padding: 12,
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            background: "#f9fbfd",
          },
        }}
        title={
          <Space style={{ width: "100%", justifyContent: "space-between" }}>
            <span>会话</span>
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => void newSession()}>
              新会话
            </Button>
          </Space>
        }
      >
        {/* 工具条（固定，不随列表滚动） */}
        <div style={{ flexShrink: 0 }}>
        <Input
          allowClear
          placeholder="搜索会话"
          prefix={<SearchOutlined style={{ color: token.colorTextTertiary }} />}
          style={{ marginBottom: 8 }}
          value={sessionSearch}
          onChange={(e) => setSessionSearch(e.target.value)}
        />
        </div>
        {/* 会话列表：占满剩余高度，超高时内部滚动（对齐任务监控页表格区） */}
        <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
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
                  return (
                    <List.Item
                      style={{
                        padding: "6px 8px",
                        borderRadius: 6,
                        cursor: "pointer",
                        background: activeSession === s.id ? token.controlItemBgActive : "transparent",
                      }}
                      onClick={() => void openSession(s.id)}
                      actions={[
                        <Tooltip key="rename" title="重命名">
                          <EditOutlined
                            style={{ color: token.colorTextTertiary }}
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
                          <DeleteOutlined style={{ color: token.colorError }} onClick={(e) => e.stopPropagation()} />
                        </Popconfirm>,
                      ].filter(Boolean)}
                    >
                      <List.Item.Meta
                        title={<Text ellipsis style={{ maxWidth: 140 }}>{s.title}</Text>}
                        description={<Text type="secondary" style={{ fontSize: 12 }}>{s.updated_at.slice(5, 16).replace("T", " ")}</Text>}
                      />
                    </List.Item>
                  );
                }}
              />
            </div>
          ))
        )}
        </div>
      </Card>
      )}

      {/* 右：对话区 */}
      <Card
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          minHeight: 0,
        }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            padding: 0,
            background: "#f5f7fa",
          },
        }}
      >
        <div style={{ padding: "12px 16px", borderBottom: `1px solid ${token.colorSplit}`, display: "flex", alignItems: "center", gap: 12, flexShrink: 0, background: "#fff" }}>
          <Text strong>智能问答</Text>
          {/* 2026-10-15 纯对话模式：未选知识库时显示模式标识 */}
          {kbId ? (
            <Tag color="blue" icon={<DatabaseOutlined />} style={{ marginInlineEnd: 0 }}>
              {kbs.find((k) => k.id === kbId)?.name || "知识库问答"}
            </Tag>
          ) : (
            <Tag color="green" style={{ marginInlineEnd: 0 }}>纯对话（未选知识库）</Tag>
          )}
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
          style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 16, position: "relative" }}
        >
          {loadingMsgs ? (
            <div className="kb-chat-skeleton-list">
              <div className="kb-chat-skeleton kb-chat-skeleton--user">
                <div className="kb-chat-skeleton-line" style={{ width: "40%" }} />
              </div>
              <div className="kb-chat-skeleton kb-chat-skeleton--bot">
                <div className="kb-chat-skeleton-line" style={{ width: "70%" }} />
                <div className="kb-chat-skeleton-line" style={{ width: "90%" }} />
              </div>
              <div className="kb-chat-skeleton kb-chat-skeleton--user">
                <div className="kb-chat-skeleton-line" style={{ width: "35%" }} />
              </div>
              <div className="kb-chat-skeleton kb-chat-skeleton--bot">
                <div className="kb-chat-skeleton-line" style={{ width: "75%" }} />
                <div className="kb-chat-skeleton-line" style={{ width: "88%" }} />
              </div>
            </div>
          ) : msgs.length === 0 ? (
            <div className="kb-sq-container" style={{ textAlign: "center", padding: 32 }}>
              {(recommendations.length > 0 || recommendLoading) && (
                <div className="kb-sq-inner" style={{ maxWidth: CONTENT_MAX_WIDTH, marginLeft: "auto", marginRight: "auto" }}>
                  <div className="kb-sq-title-row">
                    <p className="kb-sq-caption">
                      <BulbOutlined />
                      <span>推荐问题</span>
                    </p>
                    <button
                      type="button"
                      className="kb-sq-refresh"
                      disabled={recommendLoading}
                      title="换一批"
                      onClick={async () => {
                        setRecommendLoading(true);
                        try {
                          const rec = await apiRecommendQuestions(kbId);
                          if (rec.success) setRecommendations(rec.data?.questions || []);
                        } finally {
                          setRecommendLoading(false);
                        }
                      }}
                    >
                      <ReloadOutlined className={recommendLoading ? "kb-sq-refresh-spin" : ""} />
                    </button>
                  </div>
                  <div className="kb-sq-grid">
                    {recommendLoading && recommendations.length === 0
                      ? Array.from({ length: 6 }).map((_, i) => (
                          <div className="kb-sq-card kb-sq-skeleton" key={`sq-skel-${i}`}>
                            <div className="kb-sq-skeleton-bar" style={{ width: "100%" }} />
                          </div>
                        ))
                      : recommendations.map((q, i) => (
                          <div className="kb-sq-card" key={i} onClick={() => void send(q)}>
                            <span className="kb-sq-badge">FAQ</span>
                            <span className="kb-sq-text">{q}</span>
                          </div>
                        ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
                      <div
            className="kb-chat-msgs"
            style={{ maxWidth: CONTENT_MAX_WIDTH, marginLeft: "auto", marginRight: "auto" }}
          >
                      {msgs.map((m, idx) => (
              <div key={m.id}>
                {/* 对齐 WeKnora：AI 全宽无气泡（.bot_msg max-width:100%），用户 76% 圆角气泡（.user_msg） */}
                <div
                  className={`kb-chat-msg ${m.role === "user" ? "kb-chat-msg--user" : "kb-chat-msg--bot"}`}
                >
                    {m.role === "assistant" && m.steps && m.steps.length > 0 && (
                      <RagPipelineProgress steps={m.steps} />
                    )}
                    {m.role === "assistant" && (
                      <AgentToolCalls events={m.toolEvents || []} />
                    )}
                    {/* 2026-10-13 对齐 WeKnora：智能体模式隐藏思考大块，思考折叠进工具步骤区 */}
                    {m.role === "assistant" && m.thinking && m.agent && (
                      <AgentThinkingRow thinking={m.thinking} streaming={!!m.streaming} />
                    )}
                    {m.role === "assistant" && m.thinking && !m.agent && (
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
                          <TypewriterText
                            text={m.content}
                            refs={m.refs}
                            active={!!m.streaming}
                            onCitationClick={(payload) =>
                              setRefDrawer({
                                open: true,
                                refs: m.refs || [],
                                answer: m.content,
                                activeChunk: payload.chunkId,
                              })
                            }
                          />
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
                {/* 对齐 WeKnora usermsg：用户消息图片缩略图展示 */}
                {m.role === "user" && m.images && m.images.length > 0 && (
                  <div
                    style={{
                      display: "flex",
                      gap: 6,
                      flexWrap: "wrap",
                      maxWidth: 360,
                      marginBottom: 6,
                    }}
                  >
                    {m.images.map((img) =>
                      img.url ? (
                        <img
                          key={img.id}
                          src={img.url}
                          alt={img.file_name || "图片"}
                          style={{
                            width: 72,
                            height: 72,
                            objectFit: "cover",
                            borderRadius: 6,
                            border: "1px solid rgba(0,0,0,0.08)",
                          }}
                        />
                      ) : (
                        <span
                          key={img.id}
                          style={{
                            fontSize: 11,
                            color: "rgba(0,0,0,0.45)",
                            border: "1px solid rgba(0,0,0,0.08)",
                            borderRadius: 6,
                            padding: "4px 8px",
                          }}
                        >
                          📷 {img.file_name || "图片"}
                        </span>
                      ),
                    )}
                  </div>
                )}
                {/* 答案工具栏：重新生成 + 复制 + 喜欢/不喜欢反馈，全部图标（2026-10-15） */}
                {m.role === "assistant" && !m.streaming && m.content && (
                  <div style={{ marginTop: 6, display: "flex", gap: 4, alignItems: "center" }}>
                    {!m.error && (
                      <Button
                        type="text"
                        size="small"
                        icon={<ReloadOutlined />}
                        style={{ fontSize: 12, padding: "0 4px", height: "auto", color: token.colorTextTertiary }}
                        onClick={() => regenerate(msgs, idx)}
                      />
                    )}
                    <Button
                      type="text"
                      size="small"
                      icon={<CopyOutlined />}
                      style={{ fontSize: 12, padding: "0 4px", height: "auto", color: token.colorTextTertiary }}
                      onClick={async () => {
                        // copyText：HTTP 非安全上下文下 navigator.clipboard 不可用，
                        // 自动降级 execCommand（否则固定提示「复制失败」）
                        const ok = await copyText(m.content);
                        if (ok) toast.success("已复制回答");
                        else toast.error("复制失败，请检查浏览器剪贴板权限");
                      }}
                    />
                    <Button
                      type="text"
                      size="small"
                      icon={<LikeOutlined />}
                      style={{
                        fontSize: 12,
                        padding: "0 4px",
                        height: "auto",
                        color: m.feedback === "like" ? token.colorPrimary : token.colorTextTertiary,
                      }}
                      onClick={() => void toggleFeedback(m, "like")}
                    />
                    <Button
                      type="text"
                      size="small"
                      icon={<DislikeOutlined />}
                      style={{
                        fontSize: 12,
                        padding: "0 4px",
                        height: "auto",
                        color: m.feedback === "dislike" ? token.colorError : token.colorTextTertiary,
                      }}
                      onClick={() => void toggleFeedback(m, "dislike")}
                    />
                  </div>
                )}
                {/* 2026-10-15：消息末尾不再展示来源文档面板（ReferencePanel），仅保留正文引用角标（点击开右侧抽屉） */}
                {/* 2026-10-16：回复下方「推荐追问」建议块已移除（用户要求） */}
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
            <div className="kb-global-wait" role="status" aria-label="思考中">
              <span className="kb-global-wait__spinner" aria-hidden="true" />
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

        <div style={{ padding: "12px 16px", borderTop: `1px solid ${token.colorSplit}`, flexShrink: 0 }}>
          {/* 底部内容统一居中列：输入卡片与消息列表同宽；模型/智能体选择收敛进卡片控制栏（对齐 WeKnora Input-field） */}
          <div style={{ maxWidth: CONTENT_MAX_WIDTH, margin: "0 auto" }}>
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
            {a.media_type === "image" ? <PictureOutlined /> : <PaperClipOutlined />} {a.file_name}
          </Tag>
        ))}
      </div>
      {/* 2026-10-08 对齐 WeKnora：图片 / 附件双入口 + 输入卡片式布局；2026-10-12 支持整文件拖放 */}
      <div
        onDragEnter={(e) => {
          e.preventDefault();
          setDragActive(true);
        }}
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDragLeave={(e) => {
          // 仅在离开卡片本身时取消高亮（子元素间移动不触发）
          if (e.currentTarget.contains(e.relatedTarget as Node)) return;
          setDragActive(false);
        }}
        onDrop={handleChatDrop}
        style={{
          border: `1px solid ${dragActive ? token.colorPrimary : token.colorBorderSecondary}`,
          borderRadius: 8,
          boxShadow: dragActive ? `0 0 0 2px ${token.colorPrimaryBorder}` : token.boxShadowTertiary,
          background: dragActive ? token.colorPrimaryBg : token.colorBgContainer,
          padding: "10px 14px 6px",
          transition: "border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease",
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
                  border: `1px solid ${token.colorBorderSecondary}`,
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
                    background: token.colorBgMask,
                    color: token.colorTextLightSolid,
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
        {/* 选中的 @提及目标（本消息级覆盖，常驻在输入框上方，可移除） */}
        {atTarget && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", padding: "4px 0 2px" }}>
            <Tag
              closable
              onClose={() => setAtTarget(null)}
              color={atTarget.kind === "kb" ? "blue" : "purple"}
              icon={atTarget.kind === "kb" ? <DatabaseOutlined /> : <RobotOutlined />}
            >
              {atTarget.kind === "kb" ? "知识库" : "智能体"}：{atTarget.name}
            </Tag>
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
          style={{ padding: "8px 18px", background: "#fff", borderRadius: 24, resize: "none", overflow: "auto" }}
        />
        {/* 控制栏：左 智能体选择 + 工具按钮 / 右 模型选择 + 发送·停止（对齐 WeKnora Input-field 布局） */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 8,
            padding: "6px 0 2px",
            borderTop: `1px solid ${token.colorSplit}`,
            marginTop: 4,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
            {/* 2026-10-15 纯对话模式：知识库选择（allowClear，清空=不检索直接对话） */}
            <Select
              value={kbId || undefined}
              onChange={(v) => setKbId(v || undefined)}
              placeholder="不选=直接对话"
              allowClear
              style={{ minWidth: 150, marginRight: 6 }}
              suffixIcon={<DownOutlined style={{ fontSize: 10 }} />}
              options={kbs.map((k) => ({ value: k.id, label: k.name }))}
            />
            {/* 左下角：智能体选择（对齐 WeKnora agent-mode-btn） */}
            <Select
              value={agentMode || undefined}
              onChange={(v) => setAgentMode(v || undefined)}
              placeholder="普通问答"
              allowClear
              style={{ minWidth: 130, marginRight: 6 }}
              suffixIcon={<DownOutlined style={{ fontSize: 10 }} />}
              options={agents.map((a) => ({ value: a.id, label: a.name }))}
              optionRender={(o) => (
                <Space size={6}>
                  <RobotOutlined style={{ color: "#1677ff" }} />
                  {o.label}
                </Space>
              )}
            />
            <Button
              type="text"
              size="small"
              icon={<AimOutlined />}
              title="@提及知识库 / 智能体"
              onClick={() => setAtOpen(true)}
            />
            {/* 2026-10-12 对齐 WeKnora Input-field：联网搜索开关（请求级生效）
                （2026-10-15 显隐由参数表 CHAT_WEB_SEARCH_ENABLED 控制） */}
            {webSearchVisible && (
              <Button
                type="text"
                size="small"
                icon={<GlobalOutlined />}
                title={
                  webSearch
                    ? "联网搜索已开启：回答可调用 web_search 检索实时信息"
                    : "联网搜索已关闭"
                }
                aria-pressed={webSearch}
                onClick={() => setWebSearch((v) => !v)}
                style={{
                  color: webSearch ? token.colorPrimary : undefined,
                  background: webSearch ? token.colorPrimaryBg : undefined,
                }}
              />
            )}
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
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {/* 右侧：模型选择（对齐 WeKnora model-selector-trigger） */}
            {chatModels.length > 0 && (
              <Select
                value={modelId || undefined}
                onChange={setModelId}
                placeholder="默认模型"
                allowClear
                style={{ width: 170 }}
                options={chatModels.map((m) => ({
                  value: m.id,
                  label: m.display_name || m.name || m.id,
                }))}
              />
            )}
            {streaming ? (
              <Button
                shape="circle"
                size="small"
                icon={<StopOutlined />}
                title="停止生成"
                onClick={() => abortRef.current?.abort()}
                style={{
                  width: 30,
                  height: 30,
                  background: token.colorPrimaryBg,
                  color: token.colorPrimary,
                  borderColor: token.colorPrimaryBorder,
                }}
              />
            ) : (
              <Button
                type="primary"
                shape="circle"
                size="small"
                icon={<SendOutlined />}
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
          </div>
        {/* 2026-10-07 @提及多资源：选择本次消息的目标知识库 / 智能体；
            2026-10-12 对齐 WeKnora MentionSelector：分组列表 + 关键字过滤，点击即选 */}
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
            <Input
              allowClear
              size="small"
              prefix={<SearchOutlined style={{ color: token.colorTextTertiary, fontSize: 12 }} />}
              placeholder="搜索知识库 / 智能体"
              value={atSearch}
              onChange={(e) => setAtSearch(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            <div style={{ maxHeight: 300, overflow: "auto", margin: "0 -4px", padding: "0 4px" }}>
              {/* 知识库分组 */}
              <Text type="secondary" style={{ fontSize: 12, display: "block", margin: "6px 0 2px" }}>
                知识库
              </Text>
              {(() => {
                const q = atSearch.trim().toLowerCase();
                const items = kbs.filter((k) => !q || k.name.toLowerCase().includes(q));
                if (items.length === 0) {
                  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无匹配知识库" style={{ padding: "8px 0" }} />;
                }
                return items.map((k) => (
                  <div
                    key={k.id}
                    onClick={() => {
                      setAtTarget({ kind: "kb", id: k.id, name: k.name });
                      setAtOpen(false);
                    }}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "5px 8px",
                      borderRadius: 6,
                      cursor: "pointer",
                      fontSize: 13,
                      background: atTarget?.kind === "kb" && atTarget.id === k.id ? token.controlItemBgActive : "transparent",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = atTarget?.kind === "kb" && atTarget.id === k.id ? token.controlItemBgActive : token.colorFillTertiary)}
                    onMouseLeave={(e) => (e.currentTarget.style.background = atTarget?.kind === "kb" && atTarget.id === k.id ? token.controlItemBgActive : "transparent")}
                  >
                    <DatabaseOutlined style={{ color: token.colorPrimary, fontSize: 12 }} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{k.name}</span>
                  </div>
                ));
              })()}
              {/* 智能体分组 */}
              <Text type="secondary" style={{ fontSize: 12, display: "block", margin: "8px 0 2px" }}>
                智能体
              </Text>
              {(() => {
                const q = atSearch.trim().toLowerCase();
                const items = agents.filter((a) => !q || a.name.toLowerCase().includes(q));
                if (items.length === 0) {
                  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无匹配智能体" style={{ padding: "8px 0" }} />;
                }
                return items.map((a) => (
                  <div
                    key={a.id}
                    onClick={() => {
                      setAtTarget({ kind: "agent", id: a.id, name: a.name });
                      setAtOpen(false);
                    }}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "5px 8px",
                      borderRadius: 6,
                      cursor: "pointer",
                      fontSize: 13,
                      background: atTarget?.kind === "agent" && atTarget.id === a.id ? token.controlItemBgActive : "transparent",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = atTarget?.kind === "agent" && atTarget.id === a.id ? token.controlItemBgActive : token.colorFillTertiary)}
                    onMouseLeave={(e) => (e.currentTarget.style.background = atTarget?.kind === "agent" && atTarget.id === a.id ? token.controlItemBgActive : "transparent")}
                  >
                    <RobotOutlined style={{ color: token.colorPrimary, fontSize: 12 }} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.name}</span>
                  </div>
                ));
              })()}
            </div>
            <div style={{ textAlign: "right", marginTop: 12 }}>
              <Button size="small" onClick={() => setAtOpen(false)}>
                取消
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
              <div key={r.id} style={{ padding: "8px 0", borderBottom: `1px solid ${token.colorSplit}` }}>
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
            <Card
              key={i}
              size="small"
              title={`引用 ${i + 1} · 相似度 ${(r.score || 0).toFixed(2)}`}
              style={
                r.chunk_id && r.chunk_id === refDrawer.activeChunk
                  ? { borderColor: "#1677ff", boxShadow: "0 0 0 2px rgba(22,119,255,.12)" }
                  : undefined
              }
            >
              <Space direction="vertical" size={4} style={{ display: "flex" }}>
                {r.content && (
                  <Text style={{ fontSize: 13 }}>{r.content.slice(0, 400)}{r.content.length > 400 ? "…" : ""}</Text>
                )}
                <Space size={8} wrap>
                  {(r.document_title || r.meta?.source_file) && (
                    <Tag color="geekblue">
                      <FileTextOutlined /> {r.document_title || r.meta?.source_file}
                    </Tag>
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
  const { token } = theme.useToken();
  const [open, setOpen] = useState(false);
  // 2026-10-08: 思考中强制展开跟随显示；完成后保持展开（不自动收起），
  // 让用户能看到完整思考过程。点击可折叠/展开。
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
          color: streaming ? token.colorWarning : token.colorTextTertiary,
          background: streaming ? token.colorWarningBg : token.colorFillTertiary,
          border: `1px solid ${streaming ? token.colorWarningBorder : token.colorSplit}`,
          borderRadius: 6,
          padding: "2px 10px",
          userSelect: "none",
        }}
      >
        {streaming && <span className="kb-think-indicator" />}
        <span>{streaming ? "思考中…" : <><BulbOutlined /> 思考过程</>}</span>
        <span style={{ fontSize: 10 }}>{expanded ? "▾" : "▸"}</span>
      </div>
      {expanded && (
        <div
          style={{
            marginTop: 6,
            padding: "8px 12px",
            background: token.colorFillQuaternary,
            borderLeft: `3px solid ${token.colorFill}`,
            borderRadius: 6,
            fontSize: 12,
            color: token.colorTextSecondary,
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

// ---------------------------------------------------------------------------
// 智能体模式思考折叠行（2026-10-13 对齐 WeKnora：AgentStreamDisplay 把 thinking
// 事件渲染成步骤时间线里可展开的「思考」行；kb 侧折叠进工具步骤区，不再显示大块）
// ---------------------------------------------------------------------------

function AgentThinkingRow({ thinking, streaming }: { thinking: string; streaming?: boolean }) {
  const [open, setOpen] = useState(false);
  // 思考中强制展开跟随显示；完成后保持展开（可点击折叠/展开）
  const expanded = streaming || open;
  return (
    <div className="kb-agent-tool-card" style={{ margin: "0 0 8px" }}>
      <div
        className="kb-agent-tool-card-head"
        onClick={() => setOpen((v) => !v)}
        style={streaming ? { cursor: "default" } : undefined}
      >
        <BulbOutlined className="kb-agent-tool-tool" style={streaming ? undefined : { color: "#fa8c16" }} />
        <span className="kb-agent-tool-name">{streaming ? "思考中…" : "思考过程"}</span>
        <span className="kb-agent-tool-status">
          {streaming ? "进行中" : open ? "收起" : "展开"}
        </span>
        <span className="kb-agent-tool-arrow">{expanded ? "▾" : "▸"}</span>
      </div>
      {expanded && (
        <div
          className="kb-agent-tool-details"
          style={{
            padding: "8px 12px",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            maxHeight: 240,
            overflow: "auto",
            fontSize: 12,
          }}
        >
          {thinking}
        </div>
      )}
    </div>
  );
}