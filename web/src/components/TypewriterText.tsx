"use client";

import { useEffect, useRef, useState } from "react";
import MarkdownViewer from "@/components/MarkdownViewer";
import { CitationRef, CitationPayload } from "@/lib/citation";

/**
 * 打字机平滑输出（对齐 WeKnora useTypewriter / botmsg 的 answerFullyRendered 机制）。
 *
 * WeKnora 行为：SSE 增量到达后不直接渲染全文，而是由打字机按帧「追平」已到达的文本，
 * 视觉上平滑；回答完全渲染后才展示操作栏。kb 现状是逐 token 直渲染 + 闪烁光标。
 * 此组件保持 kb 现有光标与消息结构不变，只把「显示文本」做成追平视图：
 *  - 流式期间（active）：慢速追平（步长 = 剩余/10，间隔 28ms），跟得上阅读；
 *  - 流式结束（active=false）：加速追平到全文（步长 = 剩余/4，间隔 16ms），无跳字；
 *  - 文本未变时不启动定时器，静态消息零开销。
 */

interface Props {
  text: string;
  refs?: CitationRef[] | null;
  /** 是否正在流式生成（影响追平速度） */
  active?: boolean;
  onCitationClick?: (payload: CitationPayload, ref?: CitationRef) => void;
}

export default function TypewriterText({ text, refs, active, onCitationClick }: Props) {
  const [shown, setShown] = useState(text);
  const shownRef = useRef(text);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    // 已追平：无须定时器（静态消息 / 相同文本）
    if (shownRef.current === text) return;
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    const divisor = active ? 10 : 4;
    const intervalMs = active ? 28 : 16;
    timerRef.current = setInterval(() => {
      const cur = shownRef.current;
      if (cur === text) {
        if (timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }
        return;
      }
      const pending = text.length - cur.length;
      const step = Math.max(1, Math.ceil(pending / divisor));
      const next = cur.length + step >= text.length ? text : cur.slice(0, cur.length + step);
      shownRef.current = next;
      setShown(next);
    }, intervalMs);
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [text, active]);

  return <MarkdownViewer text={shown} refs={refs} onCitationClick={onCitationClick} />;
}