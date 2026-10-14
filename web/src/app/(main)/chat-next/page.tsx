"use client";

/**
 * 新版智能问答页（assistant-ui 官方形态 + kb-compilation 后端，2026-10-14）
 *
 * 页面交互/布局/视觉完全遵循 assistant-ui 官方 elements 体系
 * （Thread/composer/message/reasoning 折叠/markdown 等，Tailwind v4 官方主题
 *  分层导入跳过 preflight、@source 限定，不影响现有 antd 页面）；
 * 仅后台接口对接 kb-compilation：POST /api/v1/qa/stream 自定义 SSE。
 * 无会话模式：前端组 history 传参，不建 ChatSession 不留库。
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  makeAssistantDataUI,
  useExternalStoreRuntime,
  useMessagePartData,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { App, Button, Modal, Select, Space, Tag } from "antd";
import {
  FileTextOutlined,
  MessageOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import {
  apiListKbs,
  apiListModels,
  ChatRefItem,
  KbItem,
  ModelItem,
  QaStreamEvent,
} from "@/lib/api";
import "./chat-next.css";

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
/* 引用渲染：assistant-ui data part + makeAssistantDataUI 官方通道     */
/* （Thread 官方实现对 source part 返回 null，引用走 data part 渲染）  */
/* ------------------------------------------------------------------ */

function KbRefsRenderer() {
  const part = useMessagePartData<ChatRefItem[]>(REFS_PART_NAME);
  const openRef = useContext(RefModalContext);
  const refs = part?.data || [];
  if (!refs.length) return null;
  return (
    <div className="mt-3 border-t pt-2">
      <div className="mb-1.5 text-xs text-muted-foreground">
        参考资料（{refs.length}）
      </div>
      <div className="flex flex-wrap gap-1.5">
        {refs.map((r, i) => {
          const title =
            r.document_title ||
            r.meta?.source_file ||
            r.meta?.title ||
            r.document_id ||
            r.chunk_id;
          const display = title.length > 36 ? `${title.slice(0, 34)}…` : title;
          return (
            <button
              key={`${r.chunk_id}-${i}`}
              type="button"
              className="inline-flex max-w-[320px] items-center gap-1 overflow-hidden text-ellipsis whitespace-nowrap rounded-full border bg-muted px-2.5 py-0.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
              onClick={() => openRef({ open: true, title, content: r.content || "" })}
            >
              <FileTextOutlined className="size-3 shrink-0" />
              <span className="truncate">{display}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** 渲染此组件即注册 kb_refs data part 渲染器（assistant-ui 官方机制） */
const KbRefsDataUI = makeAssistantDataUI<ChatRefItem[]>({
  name: REFS_PART_NAME,
  render: KbRefsRenderer,
});

/* ------------------------------------------------------------------ */
/* 页面主体                                                            */
/* ------------------------------------------------------------------ */

export default function ChatNextPage() {
  const { message: toast } = App.useApp();
  const [kbs, setKbs] = useState<KbItem[]>([]);
  const [kbId, setKbId] = useState<string>();
  const [chatModels, setChatModels] = useState<ModelItem[]>([]);
  const [modelId, setModelId] = useState<string>("");
  const [messages, setMessages] = useState<readonly ThreadMessageLike[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [refModal, setRefModal] = useState<RefModalState>({
    open: false,
    title: "",
    content: "",
  });
  const abortRef = useRef<AbortController | null>(null);
  const messagesRef = useRef<readonly ThreadMessageLike[]>([]);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

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
      const history = messagesRef.current
        .filter((m) => {
          const parts = typeof m.content === "string" ? [] : m.content;
          return (
            m.role === "user" ||
            (m.role === "assistant" &&
              parts.some((p) => p.type === "text" && "text" in p && p.text))
          );
        })
        .slice(-20)
        .map((m) => {
          const parts = typeof m.content === "string" ? [] : m.content;
          const t = parts.find((p) => p.type === "text");
          return {
            role: m.role,
            content: t && "text" in t ? t.text : "",
          };
        });

      const uid = `local-user-${Date.now()}`;
      const aiId = `local-ai-${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        { id: uid, role: "user", content: [{ type: "text", text: question }] },
        { id: aiId, role: "assistant", content: [] },
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
              setMessages((prev) => patchAi(prev, aiId, { text, thinking, refs }));
            } else if (ev.type === "thinking") {
              thinking += ev.text || "";
              setMessages((prev) => patchAi(prev, aiId, { text, thinking, refs }));
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
              setMessages((prev) => patchAi(prev, aiId, { text, thinking, refs }));
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
          toast.info("已停止生成");
        } else {
          toast.error((e as Error).message || "生成失败");
          // 出错时移除占位助手消息，避免半截残影留在对话里
          setMessages((prev) => prev.filter((m) => m.id !== aiId));
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [kbId, modelId, toast],
  );

  const runtime = useExternalStoreRuntime({
    messages,
    isRunning: streaming,
    setMessages,
    onNew,
    onCancel: async () => {
      abortRef.current?.abort();
    },
    convertMessage: (message) => message,
  });

  return (
    <div className="cn-app flex h-full min-h-0 flex-col">
      <div className="cn-toolbar shrink-0">
        <Space size="middle" wrap>
          <span className="cn-toolbar-label">
            <RobotOutlined /> 新版问答（assistant-ui 官方界面）
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
          <Tag color="blue" icon={<MessageOutlined />}>
            试验页：无会话模式，不留库
          </Tag>
        </Space>
      </div>

      <div className="min-h-0 flex-1">
        <RefModalContext.Provider value={setRefModal}>
          <AssistantRuntimeProvider runtime={runtime}>
            <KbRefsDataUI />
            <Thread />
          </AssistantRuntimeProvider>
        </RefModalContext.Provider>
      </div>

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

/** 流式增量：把 aiId 消息的 parts 重写为当前 text/reasoning/data 快照 */
function patchAi(
  prev: readonly ThreadMessageLike[],
  aiId: string,
  s: { text: string; thinking: string; refs: ChatRefItem[] },
): readonly ThreadMessageLike[] {
  const content: Array<
    | { type: "text"; text: string }
    | { type: "reasoning"; text: string }
    | { type: "data"; name: string; data: ChatRefItem[] }
  > = [];
  if (s.text) content.push({ type: "text", text: s.text });
  if (s.thinking) content.push({ type: "reasoning", text: s.thinking });
  if (s.refs.length) {
    content.push({ type: "data", name: REFS_PART_NAME, data: s.refs });
  }
  return prev.map((m) => (m.id === aiId ? { ...m, content } : m));
}