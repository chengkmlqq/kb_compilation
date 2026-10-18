"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  App,
  Button,
  Card,
  Drawer,
  Dropdown,
  Empty,
  Input,
  List,
  Modal,
  Select,
  Space,
  Tag,
  theme,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import {
  BulbOutlined,
  CheckOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DownOutlined,
  EditOutlined,
  FileTextOutlined,
  GlobalOutlined,
  PaperClipOutlined,
  PictureOutlined,
  RobotOutlined,
  SearchOutlined,
  ArrowUpOutlined,
} from "@ant-design/icons";
import {
  DsbIconAdd,
  DsbIconAgree,
  DsbIconChevronDown,
  DsbIconChevronUp,
  DsbIconCopy,
  DsbIconDataCatalog,
  DsbIconDataGovernance,
  DsbIconDisagree,
  DsbIconLayout3,
  DsbIconLink,
  DsbIconModelMgr,
  DsbIconOperate,
  DsbIconRefresh,
  DsbIconSwapRight,
  DsbStopLoading,
} from "@/components/DsbIcons";
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
  apiListKbCategories,
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
  KbCategoryItem,
  KbItem,
  ModelItem,
  apiSubmitMessageFeedback,
} from "@/lib/api";
import MarkdownViewer from "@/components/MarkdownViewer";
import TypewriterText from "@/components/TypewriterText";
import { RagStep } from "@/components/RagPipelineProgress";
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
  // 2026-10-16 DSB 门户对齐：侧边栏折叠 + 当前会话标题（header 居中显示）
  const [siderCollapsed, setSiderCollapsed] = useState(false);
  const [activeSessionTitle, setActiveSessionTitle] = useState("");
  // L3：Agent 模式 + 附件 + 消息搜索
  const [agents, setAgents] = useState<
    { id: string; name: string; description?: string | null; config?: Record<string, unknown> }[]
  >([]);
  const [agentMode, setAgentMode] = useState<string>(); // 选中的 agent id
  const [attachments, setAttachments] = useState<ChatAttachmentItem[]>([]);
  // 2026-10-08 对齐 WeKnora：图片独立入口 + 发送前预览（本地对象 URL）
  const [imagePreviews, setImagePreviews] = useState<{ file: File; url: string }[]>([]);
  const MAX_IMAGES = 5;
  // 2026-10-12 对齐 WeKnora：整文件拖放上传（拖入时输入卡片高亮）
  const [dragActive, setDragActive] = useState(false);
  // 2026-10-15 联网搜索按钮显隐由参数表控制（PLATFORM_CONFIG.CHAT_WEB_SEARCH_ENABLED，默认隐藏）
  const [webSearchVisible, setWebSearchVisible] = useState(false);
  // 2026-10-19 DSB 对齐：知识库选择面板（kb-select-btn 胶囊 + chat-kb-list 浮层：搜索 / 分类树 / 卡片单选）
  const [kbPanelOpen, setKbPanelOpen] = useState(false);
  const [kbSearch, setKbSearch] = useState("");
  const [kbCategory, setKbCategory] = useState<string>("all");
  const [categories, setCategories] = useState<KbCategoryItem[]>([]);
  const kbPanelRef = useRef<HTMLDivElement | null>(null);
  const kbBtnRef = useRef<HTMLButtonElement | null>(null);
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

  // 2026-10-19 DSB 对齐：知识库选择面板分类树（/api/v1/kb-categories，复用 kbs 页 loadCategories）
  useEffect(() => {
    (async () => {
      try {
        const res = await apiListKbCategories();
        if (res.success) setCategories(res.data?.items ?? []);
      } catch {
        // 分类加载失败不阻断（树回退为空）
      }
    })();
  }, []);

  // 知识库选择面板：点击面板 / 按钮外部时关闭（DSB handleClickOutside 同款）
  useEffect(() => {
    const onDown = (e: MouseEvent | TouchEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      if (kbPanelRef.current?.contains(t) || kbBtnRef.current?.contains(t)) return;
      setKbPanelOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
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
            description: a.description,
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
        if (s?.title) setActiveSessionTitle(s.title);
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
      setActiveSessionTitle("新对话");
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
    } else if (effectiveAgent) {
      // 复用已有会话且选了智能体：把智能体绑定到会话（否则后端图片门禁按
      // session.agent_id 校验会报「仅智能体会话支持图片上传」——前端门禁按下拉
      // 状态放行、后端按会话绑定校验，判据不一致导致传图被拒）。绑定失败不阻断
      // 发送（文本问答照常），仅提示。
      try {
        await apiUpdateSession(sessionId, { agent_id: effectiveAgent });
      } catch (e) {
        toast.warning(`智能体绑定失败: ${(e as Error)?.message || String(e)}`);
      }
    }

    // 对齐 WeKnora：图片随发送上传到当前会话（作为问答上下文附件）。
    // 收集本轮附件 id 传给问答接口：后端按当轮 id 注入图片（不再重发会话内历史图片）
    let turnImageIds: string[] = [];
    if (imagePreviews.length) {
      const pending = imagePreviews;
      setImagePreviews([]);
      for (const p of pending) {
        try {
          const attId = await uploadAttachment(p.file, sessionId);
          if (attId) turnImageIds.push(attId);
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
          // 2026-10-18 对齐 WeKnora 图片仅随当轮注入：本轮图片附件 id（[]=本轮无图）
          image_attachment_ids: turnImageIds,
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

  // L3：附件上传/删除。返回新附件 id（图片问答按当轮 id 注入，见 send()）
  const uploadAttachment = async (file: File, sid?: string): Promise<string | null> => {
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
        return null;
      }
    }
    const res = await apiUploadAttachment(sessionId!, file);
    if (res.success && res.data) {
      setAttachments((prev) => [...prev, res.data!]);
      toast.success(`附件已上传：${res.data.file_name}`);
      return res.data.id;
    }
    toast.error(res.message || "附件上传失败");
    return null;
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

  // 2026-10-16 DSB 对齐：左栏无搜索框（DSB ChatSider 无），会话列表直接按日期分组
  const filteredSessions = sessions;

  // 2026-10-19 DSB 对齐：知识库选择面板过滤（分类 + 关键字；kb 后端只支持单 kb_id，面板做单选）
  // 2026-10-19 DSB 对齐：空态首页问候语（DSB Portal getTimePeriod 同款，按时段）
  const homeGreeting = (() => {
    const hour = new Date().getHours();
    if (hour < 6) return "夜深了，有什么需要帮忙吗？";
    if (hour < 12) return "早上好，有什么需要帮忙吗？";
    if (hour < 14) return "中午好，有什么需要帮忙吗？";
    if (hour < 18) return "下午好，有什么需要帮忙吗？";
    return "晚上好，有什么需要帮忙吗？";
  })();
  const activeKb = kbs.find((k) => k.id === kbId);
  const panelKbs = useMemo(() => {
    const q = kbSearch.trim().toLowerCase();
    let list = kbs;
    if (kbCategory !== "all") list = list.filter((k) => (k.label || "无标签") === kbCategory);
    if (q) {
      list = list.filter(
        (k) =>
          k.name.toLowerCase().includes(q) ||
          (k.label || "").toLowerCase().includes(q) ||
          (k.description || "").toLowerCase().includes(q),
      );
    }
    return list;
  }, [kbs, kbSearch, kbCategory]);

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
      ["week", "7天内"],
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
    <div className="kb-chat-page">
      {/* 左：会话列表（DSB .arco-layout-sider-light 228px + ChatSider；收起时 slide-out 0.3s） */}
      {!embeddedMode && (
      <div className={`kb-chat-sider${siderCollapsed ? " hide" : ""}`}>
        <div className="kb-chat-sider-header">
          <div className="kb-chat-sider-header-inner">
            <Button
              className="kb-chat-new-btn"
              type="primary"
              shape="round"
              icon={<DsbIconAdd size={16} />}
              onClick={() => void newSession()}
            >
              新对话
            </Button>
            <Tooltip title="收起侧边栏">
              <span className="kb-chat-sider-collapse" onClick={() => setSiderCollapsed(true)}>
                <DsbIconLayout3 size={18} color="rgba(77,94,125,1)" />
              </span>
            </Tooltip>
          </div>
        </div>
        {/* 会话列表：DSB .search-chat-list-box + .arco-spin-children 内边距 */}
        <div className="kb-chat-sider-list">
          <div className="kb-chat-sider-list-inner">
          {groupedSessions.length === 0 ? (
            <div style={{ padding: "40px 0", textAlign: "center" }}>
              <Empty description="暂无会话" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            </div>
          ) : (
            groupedSessions.map((g) => (
              <div key={g.key} className="kb-chat-sider-group-outer">
                <div className="kb-chat-sider-group-title">{g.label}</div>
                <div className="kb-chat-sider-group-content">
                  {g.items.map((s) => {
                    const active = activeSession === s.id;
                    return (
                      <div
                        key={s.id}
                        className={`kb-chat-sider-item${active ? " active" : ""}`}
                        onClick={() => void openSession(s.id)}
                        title={s.title}
                      >
                        <div className="kb-chat-sider-item-label">{s.title}</div>
                        {/* DSB .item-oper：单个操作图标 + 下拉菜单（重命名 / 删除） */}
                        <div className="kb-chat-sider-item-oper">
                          <Dropdown
                            trigger={["click"]}
                            overlayClassName="kb-chat-sider-item-menu"
                            menu={{
                              items: [
                                { key: "rename", label: "重命名", icon: <EditOutlined /> },
                                { key: "delete", label: "删除", icon: <DeleteOutlined />, className: "kb-menu-danger" },
                              ],
                              onClick: ({ key, domEvent }) => {
                                domEvent.stopPropagation();
                                if (key === "rename") {
                                  setRenameTarget(s);
                                  setRenameValue(s.title);
                                } else if (key === "delete") {
                                  void deleteSession(s.id);
                                }
                              },
                            }}
                          >
                            <button
                              type="button"
                              className="kb-chat-sider-item-oper-btn"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <DsbIconOperate size={16} />
                            </button>
                          </Dropdown>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))
          )}
          </div>
        </div>
      </div>
      )}

      {/* 右：对话区 */}
      <div className={`kb-chat-main${!loadingMsgs && msgs.length === 0 ? " is-home" : ""}`}>
        {/* 顶栏：DSB Portal Header 面包屑（48px / 无边框；左侧会话栏展开时不存在顶栏） */}
        {(embeddedMode || siderCollapsed) && (
        <div className="kb-chat-header">
          <div className="kb-chat-header-actions">
            {!embeddedMode && (
              <Tooltip title="打开侧边栏">
                <span className="kb-chat-header-icon" onClick={() => setSiderCollapsed(false)}>
                  <DsbIconLayout3 size={18} color="rgba(77,94,125,1)" />
                </span>
              </Tooltip>
            )}
            <Tooltip title="新对话">
              <span className="kb-chat-header-icon" onClick={() => void newSession()}>
                <img src="/images/dsb/record-new-chat-icon.png" alt="" width={18} height={18} />
              </span>
            </Tooltip>
          </div>
          <span className="kb-chat-header-sep">|</span>
          <div className="kb-chat-header-title">
            {activeSessionTitle || "智能问答"}
          </div>
        </div>
        )}

        <div
          ref={scrollRef}
          className="kb-chat-content"
          onScroll={(e) => {
            const el = e.currentTarget;
            const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
            setShowScrollBtn(!atBottom);
          }}
          style={{ position: "relative" }}
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
            <div className="kb-chat-home">
              {/* 2026-10-19 DSB 对齐：问候大标题（对齐 Portal .header-title .home-title：30px/600 居中） */}
              <div className="kb-chat-home-greeting">{homeGreeting}</div>
              {(recommendations.length > 0 || recommendLoading) && (
                  <div className="kb-chat-home-recommend">
                    <div className="kb-chat-home-recommend-title">你可以这样问我：</div>
                    {recommendLoading && recommendations.length === 0
                      ? Array.from({ length: 3 }).map((_, i) => (
                          <div className="kb-chat-home-rela-item" key={`rela-skel-${i}`} style={{ opacity: 0.5 }}>加载中…</div>
                        ))
                      : recommendations.map((q, i) => (
                          <div className="kb-chat-home-rela-item" key={i} onClick={() => void send(q)}>
                            <span style={{ flex: 1, minWidth: 0 }}>{q}</span>
                            <DsbIconSwapRight size={14} className="kb-chat-home-rela-arrow" />
                          </div>
                        ))}
                  </div>
                )}
            </div>
          ) : (
          <div className="kb-chat-ai-list">
                      {msgs.map((m, idx) => (
              <div key={m.id} className={`kb-chat-item ${m.role === "user" ? "user" : "assistant"}`}>
                {m.role === "user" ? (
                  <div className="kb-chat-user-bubble">
                    <span className="user-content-html">{m.content}</span>
                    {m.images && m.images.length > 0 && (
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", maxWidth: 360, marginTop: 6 }}>
                        {m.images.map((img) =>
                          img.url ? (
                            <img
                              key={img.id}
                              src={img.url}
                              alt={img.file_name || "图片"}
                              style={{ width: 72, height: 72, objectFit: "cover", borderRadius: 6, border: "1px solid rgba(0,0,0,0.08)" }}
                            />
                          ) : (
                            <span key={img.id} style={{ fontSize: 11, color: "rgba(0,0,0,0.45)", border: "1px solid rgba(0,0,0,0.08)", borderRadius: 6, padding: "4px 8px" }}>
                              📷 {img.file_name || "图片"}
                            </span>
                          ),
                        )}
                      </div>
                    )}
                  </div>
                ) : (
                <div className="kb-chat-assistant">
                    {/* 2026-10-16 DSB 对齐：RAG 步骤树/工具卡移除，思考折叠为 think-btn + think-view */}
                    {m.thinking ? (
                      <AssistantThink
                        thinking={m.thinking}
                        streaming={!!m.streaming}
                        answer={m.content}
                        refs={m.refs}
                        answerStreaming={!!m.streaming}
                        onCitationClick={(payload) =>
                          setRefDrawer({
                            open: true,
                            refs: m.refs || [],
                            answer: m.content,
                            activeChunk: payload.chunkId,
                          })
                        }
                      />
                    ) : (
                      <div className="kb-chat-answer">
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
                          <span style={{ color: "rgba(121,135,156,1)", fontSize: 14 }}>思考中…</span>
                        ) : (
                          ""
                        )}
                      </div>
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
                    ) : null}
                    {/* 引用文档列表（对齐 DSB resource-list「引用N篇文档作为参考」） */}
                    {!m.streaming && m.role === "assistant" && m.refs && m.refs.length > 0 && (
                      <AssistantRefs refs={m.refs} onChunkClick={(r) =>
                        setRefDrawer({ open: true, refs: m.refs || [], answer: m.content, activeChunk: r.chunk_id })
                      } />
                    )}
                    {/* 答案工具条：对齐 DSB .assistant-footer / .ai-oper-left
                        （复制 · 重新生成 ｜ 喜欢 · 不喜欢，图标 20px、按钮 20px 高） */}
                    {!m.streaming && m.content && (
                      <div className="kb-chat-assistant-footer">
                        <div className="kb-chat-assistant-oper">
                          <Tooltip title="复制">
                            <button
                              type="button"
                              className="kb-chat-oper-btn"
                              onClick={async () => {
                                // copyText：HTTP 非安全上下文下 navigator.clipboard 不可用，
                                // 自动降级 execCommand（否则固定提示「复制失败」）
                                const ok = await copyText(m.content);
                                if (ok) toast.success("已复制回答");
                                else toast.error("复制失败，请检查浏览器剪贴板权限");
                              }}
                            >
                              <DsbIconCopy size={20} />
                            </button>
                          </Tooltip>
                          {!m.error && (
                            <Tooltip title="重新生成">
                              <button
                                type="button"
                                className="kb-chat-oper-btn"
                                onClick={() => regenerate(msgs, idx)}
                              >
                                <DsbIconRefresh size={20} />
                              </button>
                            </Tooltip>
                          )}
                          <span className="kb-chat-oper-split" />
                          <Tooltip title="喜欢">
                            <button
                              type="button"
                              className={`kb-chat-oper-btn${m.feedback === "like" ? " is-agree" : ""}`}
                              onClick={() => void toggleFeedback(m, "like")}
                            >
                              <DsbIconAgree size={20} />
                            </button>
                          </Tooltip>
                          <Tooltip title="不喜欢">
                            <button
                              type="button"
                              className={`kb-chat-oper-btn${m.feedback === "dislike" ? " is-disagree" : ""}`}
                              onClick={() => void toggleFeedback(m, "dislike")}
                            >
                              <DsbIconDisagree size={20} />
                            </button>
                          </Tooltip>
                        </div>
                      </div>
                    )}
                </div>
                )}
                {/* 2026-10-15：消息末尾不再展示来源文档面板（ReferencePanel），仅保留正文引用角标（点击开右侧抽屉） */}
                {/* 2026-10-16：回复下方「推荐追问」建议块已移除（用户要求） */}
                {/* 2026-10-16：流式回答期间不再显示消息末尾的 loading 图标（用户要求去掉） */}
              </div>
            ))}
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

        <div className={`kb-chat-footer${attachments.length > 0 ? " has-file" : ""}`}>
          {/* 输入卡片：对齐 DSB .chat-footer > @ant-design/x Sender（卡片描边/圆角/阴影 + 内容区 + 底栏） */}
          <div className="kb-chat-footer-inner">
      {/* 2026-10-08 对齐 WeKnora：图片 / 附件双入口 + 输入卡片式布局；2026-10-12 支持整文件拖放 */}
      <div
        className={`kb-chat-input-card${dragActive ? " is-drag" : ""}`}
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
      >
        <div className="kb-chat-input-body">
        {/* 附件区（会话级已上传附件；对齐 DSB Sender prefix 附件条） */}
        {attachments.length > 0 && (
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
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
        )}
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
          disabled={streaming}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          style={{ height: "100%" }}
        />
        </div>
        {/* 控制栏：对齐 DSB Sender .ant-sender-footer > .sender-footer-main
            （左 .footer-left：模型（最左）→ 反思模式 → 智能体 / 联网 / 图片；
             右 .footer-right：知识库 → 附件 → 发送·停止） */}
        <div className="kb-chat-input-actions">
          <div className="kb-chat-input-actions-left">
            {/* 2026-10-19 DSB 对齐：模型选择移到最左
                （.portal-chat-dropdown-btn 胶囊按钮 + .chat-sender-model-menu 菜单：模型名/描述/勾选） */}
            {chatModels.length > 0 && (
              <Dropdown
                trigger={["click"]}
                placement="bottomLeft"
                overlayClassName="kb-chat-model-menu"
                menu={{
                  items: chatModels.map((m) => ({
                    key: m.id,
                    label: (
                      <div className="kb-chat-model-item">
                        <div className="kb-chat-model-item-left">
                          <div className="kb-chat-model-name">{m.display_name || m.name}</div>
                          <div className="kb-chat-model-descr">{m.description || "暂无描述"}</div>
                        </div>
                        <div className="kb-chat-model-check">
                          {modelId === m.id && <CheckOutlined />}
                        </div>
                      </div>
                    ),
                  })),
                  selectable: false,
                  onClick: ({ key }) => setModelId(key),
                }}
              >
                <button type="button" className="kb-chat-model-btn" title="选择模型">
                  <DsbIconModelMgr size={12} />
                  <span className="kb-chat-model-btn-label">
                    {chatModels.find((m) => m.id === modelId)?.display_name ||
                      chatModels.find((m) => m.id === modelId)?.name ||
                      "默认模型"}
                  </span>
                  <DsbIconChevronDown size={12} className="kb-chat-model-btn-arrow" />
                </button>
              </Dropdown>
            )}
            {/* 2026-10-19 智能体选择与模型选择 UI 统一：胶囊按钮 + Dropdown 菜单（名称/描述/勾选） */}
            <Dropdown
              trigger={["click"]}
              placement="bottomLeft"
              overlayClassName="kb-chat-agent-menu"
              menu={{
                items: agents.map((a) => ({
                  key: a.id,
                  label: (
                    <div className="kb-chat-model-item">
                      <div className="kb-chat-model-item-left">
                        <div className="kb-chat-model-name">{a.name}</div>
                        <div className="kb-chat-model-descr">{a.description || "暂无描述"}</div>
                      </div>
                      <div className="kb-chat-model-check">
                        {agentMode === a.id && <CheckOutlined />}
                      </div>
                    </div>
                  ),
                })),
                selectable: false,
                onClick: ({ key }) => {
                  // 点击当前已选智能体 = 取消（切回普通问答）；否则选中并同步会话绑定
                  if (agentMode === key) {
                    setAgentMode(undefined);
                    if (activeSession) {
                      void apiUpdateSession(activeSession, { agent_id: "" });
                    }
                    return;
                  }
                  setAgentMode(key);
                  // 选/切智能体时同步活跃会话的绑定（后端图片门禁按 session.agent_id 校验）
                  if (activeSession) {
                    void apiUpdateSession(activeSession, { agent_id: key });
                  }
                },
              }}
            >
              <button
                type="button"
                className="kb-chat-agent-btn"
                title={
                  agents.find((a) => a.id === agentMode)?.name
                    ? `智能体：${agents.find((a) => a.id === agentMode)?.name}`
                    : "选择智能体"
                }
              >
                <RobotOutlined style={{ color: "rgba(50,97,206,1)", fontSize: 12 }} />
                <span className="kb-chat-agent-btn-label">
                  {agents.find((a) => a.id === agentMode)?.name || "普通问答"}
                </span>
                <DsbIconChevronDown size={12} className="kb-chat-agent-btn-arrow" />
              </button>
            </Dropdown>
            {/* 2026-10-12 对齐 WeKnora Input-field：联网搜索开关（请求级生效）
                （2026-10-15 显隐由参数表 CHAT_WEB_SEARCH_ENABLED 控制） */}
            {webSearchVisible && (
              <button
                type="button"
                className={`kb-chat-input-icon-btn${webSearch ? " active" : ""}`}
                title={
                  webSearch
                    ? "联网搜索已开启：回答可调用 web_search 检索实时信息"
                    : "联网搜索已关闭"
                }
                aria-pressed={webSearch}
                onClick={() => setWebSearch((v) => !v)}
              >
                <GlobalOutlined />
              </button>
            )}
            {canUploadImage && (
              <button
                type="button"
                className="kb-chat-input-icon-btn"
                title="上传图片（随问答发给模型识别）"
                onClick={() => imageInputRef.current?.click()}
              >
                <PictureOutlined />
              </button>
            )}
          </div>
          <div className="kb-chat-input-actions-right">
            {/* 2026-10-19 DSB 对齐：知识库选择移到最右（.kb-select-btn 胶囊 + .chat-kb-list 浮层面板）
                点击切换面板；未选时显示「知识库」（空 = 全库问答，后端语义） */}
            <button
              ref={kbBtnRef}
              type="button"
              className={`kb-chat-kb-btn${kbPanelOpen ? " active" : ""}`}
              title="选择知识库"
              onClick={() => setKbPanelOpen((v) => !v)}
            >
              <span className="kb-chat-kb-btn-label">
                {activeKb ? activeKb.name : "知识库"}
              </span>
              {kbPanelOpen ? (
                <DsbIconChevronUp size={12} className="kb-chat-kb-btn-arrow" />
              ) : (
                <DsbIconChevronDown size={12} className="kb-chat-kb-btn-arrow" />
              )}
            </button>
            {/* 附件（对齐 DSB .file-text-btn：radius 8px / padding 4px 8px / text-1 色） */}
            <button
              type="button"
              className="kb-chat-input-icon-btn"
              title="上传附件"
              onClick={() => attachmentInputRef.current?.click()}
            >
              <DsbIconLink size={12} />
              附件
            </button>
            {streaming ? (
              <button
                type="button"
                className="kb-chat-stop-btn"
                title="停止生成"
                onClick={stopGenerating}
              >
                <DsbStopLoading size={32} />
              </button>
            ) : (
              <button
                type="button"
                className="kb-chat-send-btn"
                title="发送"
                onClick={() => void send()}
              >
                <ArrowUpOutlined />
              </button>
            )}
          </div>
        </div>
        {/* 2026-10-19 DSB 对齐：知识库选择面板（.chat-kb-list：顶部搜索 + 左分类树 + 右侧已选 Tag + 卡片单选） */}
        <div ref={kbPanelRef} className={`kb-chat-kb-panel${kbPanelOpen ? " visible" : ""}`}>
          <div className="kb-chat-kb-panel-search">
            <Input
              allowClear
              prefix={<SearchOutlined style={{ color: "rgba(121,135,156,1)", fontSize: 14 }} />}
              placeholder="请输入关键字"
              value={kbSearch}
              onChange={(e) => setKbSearch(e.target.value)}
            />
          </div>
          <div className="kb-chat-kb-selector-layout">
            <div className="kb-chat-kb-selector-tree">
              <div
                className="kb-kbs-tree-node"
                onClick={() => setKbCategory("all")}
                title="全部"
              >
                <span className="kb-kbs-tree-indent" aria-hidden="true" />
                <span className="kb-kbs-tree-switcher" />
                <span className={`kb-kbs-tree-title${kbCategory === "all" ? " active" : ""}`}>
                  全部
                </span>
              </div>
              {categories.map((c) => (
                <div
                  key={c.name}
                  className="kb-kbs-tree-node"
                  onClick={() => setKbCategory(c.name)}
                  title={c.name}
                >
                  <span className="kb-kbs-tree-indent" aria-hidden="true">
                    <span className="kb-kbs-tree-indent-block" />
                  </span>
                  <span className="kb-kbs-tree-switcher" />
                  <span className={`kb-kbs-tree-title${kbCategory === c.name ? " active" : ""}`}>
                    {c.name}
                  </span>
                </div>
              ))}
            </div>
            <div className="kb-chat-kb-selector-list">
              {/* 已选知识库 Tag 行（对齐 .kb-tags：label 84px + 可关闭 Tag） */}
              <div className="kb-chat-kb-tags">
                <div className="kb-chat-kb-tags-label">已选择知识库：</div>
                <div className="kb-chat-kb-tags-list">
                  {activeKb ? (
                    <Tag
                      closable
                      onClose={(e) => {
                        e.preventDefault();
                        setKbId(undefined);
                      }}
                    >
                      {activeKb.name}
                    </Tag>
                  ) : null}
                </div>
              </div>
              {/* 卡片列表（对齐 .custom-checkbox-card：3 列 / fill-2 底 / 选中白底主色边框 + mask 圆点） */}
              <div className="kb-chat-kb-cards-wrap">
                <div className="kb-chat-kb-cards-inner">
                  {panelKbs.length === 0 ? (
                    <div className="kb-chat-kb-empty">暂无数据</div>
                  ) : (
                    <div className="kb-chat-kb-cards">
                      {panelKbs.map((k) => {
                        const checked = kbId === k.id;
                        return (
                          <div
                            key={k.id}
                            className={`kb-chat-kb-card${checked ? " checked" : ""}`}
                            onClick={() => setKbId(checked ? undefined : k.id)}
                            title={k.name}
                          >
                            <div className="kb-chat-kb-card-top">
                              <DsbIconDataCatalog size={20} className="kb-chat-kb-card-icon" />
                              <div className="kb-chat-kb-card-title">{k.name}</div>
                              <div className="kb-chat-kb-card-mask" />
                            </div>
                            <div className="kb-chat-kb-card-bottom">
                              <div className="kb-chat-kb-card-descr">
                                {k.description || "暂无描述"}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            </div>
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
                </div>

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
            共 {refGroupCount(refDrawer.refs, refDrawer.activeChunk).length} 篇文档 · {refDrawer.refs.length} 条命中片段 · 点击跳转 wiki 页面
          </Text>
          {refGroupCount(refDrawer.refs, refDrawer.activeChunk).map((g, gi) => (
            <Card
              key={gi}
              size="small"
              title={`引用 ${gi + 1} · ${g.title}`}
              style={
                g.active
                  ? { borderColor: "#1677ff", boxShadow: "0 0 0 2px rgba(22,119,255,.12)" }
                  : undefined
              }
            >
              <Space direction="vertical" size={8} style={{ display: "flex" }}>
                {g.items.map((item, ii) => (
                  <div key={ii}>
                    {item.content && (
                      <Text style={{ fontSize: 13 }}>
                        {item.content.slice(0, 400)}
                        {item.content.length > 400 ? "…" : ""}
                      </Text>
                    )}
                    <Space size={8} wrap style={{ marginTop: 4 }}>
                      <Tag color="geekblue">
                        <FileTextOutlined /> {g.title}
                      </Tag>
                      <Tag color="blue">相似度 {item.bestScore.toFixed(2)}</Tag>
                      {item.count > 1 && <Tag>含 {item.count} 条相同片段</Tag>}
                      <Button
                        size="small"
                        type="link"
                        style={{ fontSize: 12 }}
                        disabled={!item.kbId || !item.chunkId}
                        onClick={() => {
                          if (item.kbId && item.chunkId) {
                            void router.push(`/kbs/${item.kbId}/wiki/${item.chunkId}`);
                          }
                        }}
                      >
                        跳转原文 →
                      </Button>
                    </Space>
                  </div>
                ))}
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
// 引用文档分组（2026-10-18）：按文档归并 + 同内容片段合并计数。
// 修复「引用 N 篇文档」按 refs 条数统计的误导：多条命中可能同属一篇文档，
// 且父子分块会把多条子块提升为同一父块全文（内容完全相同）。
// ---------------------------------------------------------------------------

interface RefGroupItem {
  content: string;
  bestScore: number;
  count: number;
  kbId: string;
  chunkId: string;
}

interface RefGroup {
  title: string;
  active: boolean;
  items: RefGroupItem[];
}

/** 把 refs 按文档标题分组，组内按内容去重合并（同内容取最高分、计数）。 */
export function refGroupCount(refs: ChatRefItem[], activeChunk?: string): RefGroup[] {
  const byTitle = new Map<string, ChatRefItem[]>();
  for (const r of refs || []) {
    const title = r.document_title || r.meta?.source_file || "其他";
    const arr = byTitle.get(title) || [];
    arr.push(r);
    byTitle.set(title, arr);
  }
  const groups: RefGroup[] = [];
  for (const [title, chunks] of byTitle) {
    const byContent = new Map<string, RefGroupItem>();
    let active = false;
    for (const c of chunks) {
      if (c.chunk_id && c.chunk_id === activeChunk) active = true;
      const key = c.content || "";
      const score = c.score || 0;
      const existing = byContent.get(key);
      if (existing) {
        existing.count += 1;
        if (score > existing.bestScore) {
          existing.bestScore = score;
          existing.kbId = c.kb_id || "";
          existing.chunkId = c.chunk_id || "";
        }
      } else {
        byContent.set(key, {
          content: key,
          bestScore: score,
          count: 1,
          kbId: c.kb_id || "",
          chunkId: c.chunk_id || "",
        });
      }
    }
    const items = Array.from(byContent.values()).sort((a, b) => b.bestScore - a.bestScore);
    groups.push({ title, active, items });
  }
  return groups;
}

// ---------------------------------------------------------------------------
// DSB 门户对齐：思考折叠 + 答案 + 引用文档列表（2026-10-16）
// 基准：data_supply_base DataSupplyPortalRecordNew ChatMessageItem
// ---------------------------------------------------------------------------

/** AI 回答块：think-btn（已完成思考/思考中）+ think-view 左竖线 + 答案正文 */
function AssistantThink({
  thinking,
  streaming,
  answer,
  refs,
  answerStreaming,
  onCitationClick,
}: {
  thinking: string;
  streaming?: boolean;
  answer?: string;
  refs?: ChatRefItem[];
  answerStreaming?: boolean;
  onCitationClick?: (payload: { chunkId: string }) => void;
}) {
  const [thinkVisible, setThinkVisible] = useState(true);
  const [thinkEnded, setThinkEnded] = useState(false);
  // DSB：isThinkEnd 变 true（agent_message 开始）自动收起思考区
  useEffect(() => {
    if (!streaming && !thinkEnded) setThinkEnded(true);
  }, [streaming, thinkEnded]);
  useEffect(() => {
    if (thinkEnded && thinkVisible) setThinkVisible(false);
  }, [thinkEnded]);

  return (
    <div className="kb-chat-answer">
      <button
        type="button"
        className={`kb-chat-think-btn${streaming ? " thinking" : ""}`}
        onClick={() => setThinkVisible((v) => !v)}
      >
        <span>{streaming ? "思考中…" : thinkEnded ? "已完成思考" : "思考过程"}</span>
        <span style={{ fontSize: 12 }}>{thinkVisible ? "▾" : "▸"}</span>
      </button>
      {thinkVisible && (
        <div className="kb-chat-think-view">
          {/* 思考过程打字机打印（复用回答同款 TypewriterText：流式期间平滑追平，静态内容零开销） */}
          <div className="kb-chat-think-md">
            <TypewriterText text={thinking} active={!!streaming} />
          </div>
        </div>
      )}
      <div className="kb-chat-content-view">
        {answer ? (
          <TypewriterText
            text={answer}
            refs={refs}
            active={!!answerStreaming}
            onCitationClick={onCitationClick}
          />
        ) : answerStreaming ? (
          <span style={{ color: "rgba(121,135,156,1)" }}>思考中…</span>
        ) : (
          ""
        )}
      </div>
    </div>
  );
}

/** 引用文档列表（对齐 DSB resource-list「引用N篇文档作为参考」） */
function AssistantRefs({
  refs,
  onChunkClick,
}: {
  refs: ChatRefItem[];
  onChunkClick?: (ref: ChatRefItem) => void;
}) {
  const [open, setOpen] = useState(false);
  // 按文档分组（同文档多 chunk 归并；标题统计用文档数，避免「7 条片段=7 篇文档」误导）
  const groups = useMemo(() => {
    const map = new Map<string, ChatRefItem[]>();
    for (const r of refs) {
      const title = r.document_title || r.meta?.source_file || "其他";
      const arr = map.get(title) || [];
      arr.push(r);
      map.set(title, arr);
    }
    return Array.from(map.entries()).map(([title, chunks]) => ({ title, chunks }));
  }, [refs]);

  if (!refs || refs.length === 0) return null;
  const docCount = groups.length;
  return (
    <div className="kb-chat-resource">
      <div className="kb-chat-resource-oper" onClick={() => setOpen((v) => !v)}>
        <div className="kb-chat-resource-oper-title">
          引用{docCount} 篇文档作为参考
          {refs.length > docCount && (
            <span className="kb-chat-resource-oper-sub">（{refs.length} 条片段）</span>
          )}
        </div>
        <span className="kb-chat-resource-oper-arrow">{open ? "▾" : "▸"}</span>
      </div>
      {open && (
        <div className="kb-chat-resource-list active">
          {groups.map((g) => (
            <div
              key={g.title}
              className="kb-chat-resource-item"
              onClick={() => onChunkClick?.(g.chunks[0])}
              title={g.title}
            >
              <span className="kb-chat-resource-item-icon">
                <FileTextOutlined style={{ color: "rgba(50,97,206,1)", fontSize: 13 }} />
              </span>
              <span className="kb-chat-resource-item-label">
                {g.title}
              </span>
              <span className="kb-chat-resource-cut">{g.chunks.length}</span>
            </div>
          ))}
        </div>
      )}
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
        {/* 2026-10-16：思考中不再显示脉冲 loading 圆点（用户要求去掉），仅保留「思考中…」文字 */}
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