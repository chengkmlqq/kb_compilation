"use client";

/**
 * 新版智能问答页（assistant-ui 试验页，2026-10-14）
 *
 * 与现有问答页（chat/page.tsx）并存，互不影响：
 * - 用 assistant-ui 的 useExternalStoreRuntime 接现有后端 /api/v1/qa/stream
 *   （自定义 SSE 协议：stream_meta/context/thinking/delta/done/error）
 * - 无会话模式：前端维护 history 数组传给后端，不创建 ChatSession，不留库
 * - 纯文本问答最小闭环：知识库选择 + 模型选择 + 流式回答 + 思考折叠 + 引用胶囊
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  useMessagePartData,
  useMessagePartReasoning,
  useMessagePartText,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { App, Button, Modal, Select, Space, Tag, Tooltip } from "antd";
import {
  FileTextOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
} from "@ant-design/icons";
import {
  apiListKbs,
  apiListModels,
  ChatRefItem,
  KbItem,
  ModelItem,
  QaStreamEvent,
} from "@/lib/api";
import MarkdownViewer from "@/components/MarkdownViewer";
import type { CitationRef as CitationRefType } from "@/lib/citation";
import "./chat-next.css";

/** 前端本地消息模型（不落库；refs 来自 context 事件） */
interface UiMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  refs?: ChatRefItem[];
}

/** 引用原文弹窗状态（页面级共享单个 Modal） */
interface RefModalState {
  open: boolean;
  title: string;
  content: string;
}

const NOOP = () => {};

/** 引用胶囊点击 → 打开页面级引用弹窗 */
const RefModalContext = createContext<(s: RefModalState) => void>(NOOP);

/** 引用胶囊数据 part 名（convertMessage 打包 refs 时使用） */
const REFS_PART_NAME = "kb_refs";

/* ------------------------------------------------------------------ */
/* 消息 part 渲染组件（Text 复用现有 MarkdownViewer；Reasoning 折叠；  */
/* kb_refs 渲染引用胶囊，点击弹窗看 chunk 原文）                       */
/* ------------------------------------------------------------------ */

function TextPart() {
  const part = useMessagePartText();
  const text = "text" in part ? part.text : "";
  const openRef = useContext(RefModalContext);
  if (!text) return null;
  // convertMessage 把本次检索 refs 打包在 text part 的 providerMetadata.kb_refs.items
  // （正文里 <kb doc=.../> 胶囊需要 refs 才能补显示名 + 点击看原文）
  const meta = (part as { providerMetadata?: Record<string, unknown> }).providerMetadata;
  const refs = (meta?.kb_refs as { items?: ChatRefItem[] } | undefined)?.items || null;
  return (
    <MarkdownViewer
      text={text}
      refs={refs as CitationRefType[] | null}
      onCitationClick={(payload, ref) => {
        const chunkId = payload.chunkId || "";
        const hit = (refs?.find((r) => r.chunk_id === chunkId) ||
          ref) as ChatRefItem | undefined;
        openRef({
          open: true,
          title:
            hit?.document_title ||
            hit?.meta?.source_file ||
            hit?.meta?.title ||
            payload.display ||
            "引用原文",
          content: hit?.content || "",
        });
      }}
    />
  );
}

function ReasoningPart() {
  const part = useMessagePartReasoning();
  const text = part?.text || "";
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="cn-thinking">
      <button type="button" className="cn-thinking-toggle" onClick={() => setOpen((v) => !v)}>
        <RobotOutlined /> 思考过程{open ? "（点击收起）" : "（点击展开）"}
      </button>
      {open && <div className="cn-thinking-body">{text}</div>}
    </div>
  );
}

function RefsPart() {
  const part = useMessagePartData<ChatRefItem[]>(REFS_PART_NAME);
  const openRef = useContext(RefModalContext);
  const refs = part?.data || [];
  if (!refs.length) return null;
  return (
    <div className="cn-refs">
      <span className="cn-refs-label">参考资料（{refs.length}）</span>
      <div className="cn-refs-list">
        {refs.map((r, i) => {
          const title =
            r.document_title ||
            r.meta?.source_file ||
            r.meta?.title ||
            r.document_id ||
            r.chunk_id;
          const display = title.length > 32 ? `${title.slice(0, 32)}…` : title;
          return (
            <Tooltip key={`${r.chunk_id}-${i}`} title={`查看引用原文：${title}`}>
              <button
                type="button"
                className="cn-ref-btn"
                onClick={() =>
                  openRef({ open: true, title, content: r.content || "" })
                }
              >
                <FileTextOutlined /> {display}
              </button>
            </Tooltip>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 消息条目：assistant-ui 按 ThreadPrimitive.Messages 提供的上下文     */
/* 渲染每条消息的 parts                                                */
/* ------------------------------------------------------------------ */

function MessageItem() {
  return (
    <MessagePrimitive.Root className="cn-msg">
      <MessagePrimitive.Parts
        components={{
          Empty: () => (
            <div className="cn-msg-loading">
              <RobotOutlined spin /> 正在生成回答…
            </div>
          ),
          Text: TextPart,
          Reasoning: ReasoningPart,
          data: { by_name: { [REFS_PART_NAME]: RefsPart } },
        }}
      />
    </MessagePrimitive.Root>
  );
}

/* ------------------------------------------------------------------ */
/* 页面主体                                                            */
/* ------------------------------------------------------------------ */

export default function ChatNextPage() {
  const { message: toast } = App.useApp();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [kbId, setKbId] = useState<string>();
  const [chatModels, setChatModels] = useState<ModelItem[]>([]);
  const [modelId, setModelId] = useState<string>("");
  const [msgs, setMsgs] = useState<UiMsg[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [refModal, setRefModal] = useState<RefModalState>({
    open: false,
    title: "",
    content: "",
  });
  const abortRef = useRef<AbortController | null>(null);
  const msgsRef = useRef<UiMsg[]>([]);
  useEffect(() => {
    msgsRef.current = msgs;
  }, [msgs]);

  // 加载知识库 / 模型列表
  useEffect(() => {
    (async () => {
      const res = await apiListKbs(1, 100);
      if (res.success) {
        const items = res.data?.items || [];
        setKbs(items);
        if (items.length > 0) setKbId((prev) => prev || items[0].id);
      }
    })();
    (async () => {
      const res = await apiListModels({ type: "chat" });
      if (res.success) setChatModels(res.data?.items || []);
    })();
  }, []);

  /** 更新本地消息（流式增量写入） */
  const updateMsg = useCallback((id: string, patch: Partial<UiMsg>) => {
    setMsgs((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }, []);

  /** 自有消息 → assistant-ui ThreadMessageLike（text/reasoning/data parts） */
  const convertMessage = useCallback((m: UiMsg, _idx: number): ThreadMessageLike => {
    const content: Array<
      | { type: "text"; text: string }
      | { type: "reasoning"; text: string }
      | { type: "data"; name: string; data: ChatRefItem[] }
    > = [];
    if (m.content) {
      content.push({
        type: "text",
        text: m.content,
        ...(m.refs?.length
          ? { providerMetadata: { kb_refs: { items: m.refs } } }
          : {}),
      });
    }
    if (m.thinking) {
      content.push({ type: "reasoning", text: m.thinking });
    }
    if (m.refs?.length) {
      content.push({ type: "data", name: REFS_PART_NAME, data: m.refs });
    }
    return { id: m.id, role: m.role, content };
  }, []);

  /** 发送一条新消息：追加用户消息 + 占位助手消息 → POST SSE → 增量写回 */
  const onNew = useCallback(
    async (message: AppendMessage) => {
      if (!kbId) {
        toast.error("请先选择知识库");
        return;
      }
      const textPart = message.content.find((p) => p.type === "text");
      const question = ("text" in textPart! ? textPart.text : "").trim();
      if (!question) return;

      // 发送前快照历史（不含刚追加的消息）
      const history = msgsRef.current
        .filter((m) => m.role === "user" || (m.role === "assistant" && m.content))
        .slice(-20)
        .map((m) => ({ role: m.role, content: m.content }));

      const uid = `local-user-${Date.now()}`;
      const aiId = `local-ai-${Date.now()}`;
      setMsgs((prev) => [
        ...prev,
        { id: uid, role: "user", content: question },
        { id: aiId, role: "assistant", content: "", thinking: "", refs: [] },
      ]);
      setStreaming(true);

      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const resp = await fetch("/api/v1/qa/stream", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            kb_id: kbId,
            question,
            history,
            model_id: modelId || null,
            web_search_enabled: false,
          }),
          signal: ctrl.signal,
        });
        if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        let text = "";
        let thinking = "";
        let refs: ChatRefItem[] = [];
        let finished = false;
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split("\n\n");
          buf = lines.pop() || "";
          for (const line of lines) {
            if (!line.startsWith("data:")) continue;
            let ev: QaStreamEvent;
            try {
              ev = JSON.parse(line.slice(5).trim()) as QaStreamEvent;
            } catch {
              continue;
            }
            if (ev.type === "delta") {
              text += ev.text || "";
              updateMsg(aiId, { content: text });
            } else if (ev.type === "thinking") {
              thinking += ev.text || "";
              updateMsg(aiId, { thinking });
            } else if (ev.type === "context") {
              refs = (ev.hits || []).map((h) => ({
                chunk_id: h.chunk_id,
                score: h.score || 0,
                content: h.content || "",
                document_id: h.document_id || "",
                kb_id: h.kb_id || "",
                meta: h.meta || {},
                document_title: h.document_title || "",
              }));
              updateMsg(aiId, { refs });
            } else if (ev.type === "error") {
              throw new Error(ev.message || "生成失败");
            } else if (ev.type === "done") {
              finished = true;
            }
          }
          if (finished) break;
        }
      } catch (e) {
        if ((e as Error).name === "AbortError") {
          // 用户停止：保留已生成内容
          toast.info("已停止生成");
        } else {
          toast.error((e as Error).message || "生成失败");
          // 出错时移除占位助手消息，避免半截残影留在对话里
          setMsgs((prev) => prev.filter((m) => m.id !== aiId));
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [kbId, modelId, toast, updateMsg],
  );

  const runtime = useExternalStoreRuntime({
    messages: msgs,
    isRunning: streaming,
    setMessages: (next) => setMsgs([...next]),
    onNew,
    onCancel: async () => {
      abortRef.current?.abort();
    },
    convertMessage,
  });

  return (
    <div className="cn-page">
      <div className="cn-toolbar">
        <Space size="middle" wrap>
          <span className="cn-toolbar-label">
            <RobotOutlined /> 新版问答（assistant-ui 试验）
          </span>
          <Select
            className="cn-kb-select"
            placeholder="选择知识库"
            value={kbId}
            onChange={setKbId}
            options={kbs.map((k) => ({ value: k.id, label: k.name }))}
            showSearch
            optionFilterProp="label"
            notFoundContent="暂无可选知识库"
          />
          <Select
            className="cn-model-select"
            placeholder="默认模型"
            allowClear
            value={modelId || undefined}
            onChange={(v) => setModelId(v || "")}
            options={chatModels.map((m) => ({ value: m.name, label: m.name }))}
            notFoundContent="无模型配置"
          />
          <Tag color="blue">试验页：无会话模式，不留库</Tag>
        </Space>
      </div>

      <RefModalContext.Provider value={setRefModal}>
        <AssistantRuntimeProvider runtime={runtime}>
          <ThreadPrimitive.Root className="cn-thread">
            <ThreadPrimitive.Viewport className="cn-viewport">
              <ThreadPrimitive.Empty>
                <div className="cn-empty">
                  <RobotOutlined className="cn-empty-icon" />
                  <p>你好，我是知识库问答助手（assistant-ui 新版界面）。</p>
                  <p>选择上方知识库后，输入问题开始提问。</p>
                </div>
              </ThreadPrimitive.Empty>
              <ThreadPrimitive.Messages>
                {() => <MessageItem />}
              </ThreadPrimitive.Messages>
              <ThreadPrimitive.ScrollToBottom className="cn-scroll-btn" />
            </ThreadPrimitive.Viewport>

            <ComposerPrimitive.Root className="cn-composer">
              <ComposerPrimitive.Input
                className="cn-composer-input"
                placeholder="输入问题，Enter 发送，Shift+Enter 换行"
                autoFocus
              />
              <div className="cn-composer-actions">
                <ComposerPrimitive.Cancel className="cn-composer-cancel" asChild>
                  <Button type="primary" danger icon={<StopOutlined />}>
                    停止
                  </Button>
                </ComposerPrimitive.Cancel>
                <ComposerPrimitive.Send className="cn-composer-send" asChild>
                  <Button type="primary" icon={<SendOutlined />}>
                    发送
                  </Button>
                </ComposerPrimitive.Send>
              </div>
            </ComposerPrimitive.Root>
          </ThreadPrimitive.Root>
        </AssistantRuntimeProvider>
      </RefModalContext.Provider>

      <Modal
        open={refModal.open}
        title={refModal.title || "引用原文"}
        footer={null}
        width={720}
        onCancel={() => setRefModal((s) => ({ ...s, open: false }))}
      >
        <pre className="cn-ref-content">
          {refModal.content || "（该引用未携带原文片段）"}
        </pre>
      </Modal>
    </div>
  );
}