"use client";

/**
 * 新版智能问答页（assistant-ui 官方形态 + kb-compilation 后端，2026-10-14）
 *
 * 页面交互/布局/视觉完全遵循 assistant-ui 官方 elements 体系，零平台 UI：
 * - Thread/composer/message/reasoning 折叠/markdown/action bar 全官方
 * - 开放官方组件：空态推荐问题 + 回答后推荐追问（ThreadSuggestions /
 *   FollowupSuggestions，接后端 follow-up）、消息编辑（Pencil →
 *   EditComposer，本地改写重发）、错误官方形态（MessagePrimitive.Error
 *   红卡片，替代 toast 移除）；模型下拉（官方形态）透传 model_id
 * - 知识库固定取第一个；无会话模式：前端组 history 传参，不建会话不留库
 * 仅后台接口对接 kb-compilation：POST /api/v1/qa/stream 自定义 SSE。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
  type ThreadSuggestion,
} from "@assistant-ui/react";
import { App } from "antd";
import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { ModelSelectorContext } from "@/components/assistant-ui/elements/model-selector";
import {
  apiFollowUp,
  apiListKbs,
  apiListModels,
  KbItem,
  ModelItem,
  QaStreamEvent,
} from "@/lib/api";
import "./chat-next.css";

/** 空态推荐问题（官方 ThreadSuggestions 数据源；知识库固定第一个，用通用引导语） */
const INITIAL_SUGGESTIONS: ThreadSuggestion[] = [
  { title: "这个知识库主要包含哪些内容？", prompt: "这个知识库主要包含哪些内容？" },
  { title: "知识库里有哪些关键概念或实体？", prompt: "知识库里有哪些关键概念或实体？" },
  { title: "有没有相关制度或规范？", prompt: "有没有相关制度或规范？" },
];

export default function ChatNextPage() {
  const { message: toast } = App.useApp();
  const [kbId, setKbId] = useState<string>();
  const [models, setModels] = useState<ModelItem[]>([]);
  const [selectedModelId, setSelectedModelId] = useState("");
  const [messages, setMessages] = useState<readonly ThreadMessageLike[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [suggestions, setSuggestions] = useState<ThreadSuggestion[]>(INITIAL_SUGGESTIONS);
  const abortRef = useRef<AbortController | null>(null);
  const messagesRef = useRef<readonly ThreadMessageLike[]>([]);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // 知识库固定取第一个 + 聊天模型列表（composer 官方形态模型下拉）
  useEffect(() => {
    (async () => {
      const res = await apiListKbs(1, 100);
      if (res.success) {
        const items = res.data?.items || [];
        if (items.length > 0) setKbId((prev) => prev || items[0].id);
      }
    })();
    (async () => {
      const res = await apiListModels({ type: "chat" });
      if (res.success) setModels(res.data?.items || []);
    })();
  }, []);

  /** 流式问答核心：发送 question（调用方已维护消息列表），SSE 增量写回 aiId */
  const runQuestion = useCallback(
    async (question: string, aiId: string) => {
      if (!kbId) {
        toast.error("知识库尚未就绪，请稍后重试");
        return;
      }
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
            model_id: selectedModelId || null, // 空 = 后端默认模型
          }),
          signal: ctrl.signal,
        });
        if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        let text = "";
        let thinking = "";
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
              // 清洗 <kb doc=.../> 引用标签：官方 markdown 无 rehype-raw，裸标签会
              // 以字面量显示。标签可能被流式切片拆到多个 delta，故对累积全文整体清洗。
              text = (text + (ev.text || "")).replace(/<kb\b[^>]*\/?>/gi, "");
              setMessages((prev) => patchAi(prev, aiId, { text, thinking }));
            } else if (ev.type === "thinking") {
              thinking = (thinking + (ev.text || "")).replace(/<kb\b[^>]*\/?>/gi, "");
              setMessages((prev) => patchAi(prev, aiId, { text, thinking }));
            } else if (ev.type === "error") {
              throw new Error(ev.message || "生成失败");
            } else if (ev.type === "done") {
              finished = true;
            }
            // context 事件：官方页面不渲染引用（零平台 UI），hits 直接忽略
          }
          if (finished) break;
        }
        // 回答完成 → 推荐追问（后端 follow-up，session 可选）
        if (text.trim()) {
          void apiFollowUp("", text.trim(), 3).then((res) => {
            if (res.success && res.data?.questions?.length) {
              setSuggestions(
                res.data.questions.map((q) => ({ title: q, prompt: q })),
              );
            }
          });
        }
      } catch (e) {
        if ((e as Error).name === "AbortError") {
          toast.info("已停止生成");
        } else {
          // 官方错误形态：保留消息 + MessagePrimitive.Error 红卡片（替代 toast 移除）
          setMessages((prev) =>
            prev.map((m) =>
              m.id === aiId
                ? {
                    ...m,
                    status: {
                      type: "incomplete",
                      reason: "error",
                      error: { message: (e as Error).message || "生成失败" },
                    },
                  }
                : m,
            ),
          );
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [kbId, selectedModelId, toast],
  );

  /** 新消息：追加 user + assistant 占位 → 流式问答 */
  const onNew = useCallback(
    async (message: AppendMessage) => {
      const textPart = message.content.find((p) => p.type === "text");
      const question = ("text" in textPart! ? textPart.text : "").trim();
      if (!question) return;
      const aiId = `local-ai-${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        { id: `local-user-${Date.now()}`, role: "user", content: [{ type: "text", text: question }] },
        { id: aiId, role: "assistant", content: [] },
      ]);
      await runQuestion(question, aiId);
    },
    [runQuestion],
  );

  /** 编辑消息（Pencil → EditComposer）：替换原文 + 截断后续 + 用新文本重新生成 */
  const onEdit = useCallback(
    async (message: AppendMessage) => {
      const textPart = message.content.find((p) => p.type === "text");
      const question = ("text" in textPart! ? textPart.text : "").trim();
      if (!question || !message.sourceId) return;
      const aiId = `local-ai-${Date.now()}`;
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.id === message.sourceId);
        if (idx < 0) return prev;
        const updated = {
          ...prev[idx],
          content: [{ type: "text" as const, text: question }],
        };
        return [...prev.slice(0, idx), updated, { id: aiId, role: "assistant", content: [] }];
      });
      await runQuestion(question, aiId);
    },
    [runQuestion],
  );

  const runtime = useExternalStoreRuntime({
    messages,
    isRunning: streaming,
    setMessages,
    suggestions,
    onNew,
    onEdit,
    onCancel: async () => {
      abortRef.current?.abort();
    },
    convertMessage: (message) => message,
  });

  return (
    <div className="cn-app">
      <AssistantRuntimeProvider runtime={runtime}>
        <ModelSelectorContext.Provider
          value={{ models, value: selectedModelId, onChange: setSelectedModelId }}
        >
          <Thread />
        </ModelSelectorContext.Provider>
      </AssistantRuntimeProvider>
    </div>
  );
}

/** 流式增量：把 aiId 消息的 parts 重写为当前 text/reasoning 快照 */
function patchAi(
  prev: readonly ThreadMessageLike[],
  aiId: string,
  s: { text: string; thinking: string },
): readonly ThreadMessageLike[] {
  const content: Array<
    | { type: "text"; text: string }
    | { type: "reasoning"; text: string }
  > = [];
  if (s.text) content.push({ type: "text", text: s.text });
  if (s.thinking) content.push({ type: "reasoning", text: s.thinking });
  return prev.map((m) => (m.id === aiId ? { ...m, content } : m));
}