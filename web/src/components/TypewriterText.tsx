"use client";

import { useEffect, useRef, useState } from "react";
import MarkdownViewer from "@/components/MarkdownViewer";
import { CitationRef, CitationPayload } from "@/lib/citation";

/**
 * 打字机平滑输出（对齐 WeKnora composables/useTypewriter.ts）。
 *
 * WeKnora 行为：SSE 增量到达后不直接渲染全文，而是由打字机按「固定速率」追平
 * 已到达的文本——min 72 字/秒、max 240 字/秒，按真实帧时间累积 reveal credit，
 * 并按语义分组揭示（CJK 2-4 字一组、拉丁文整词），backlog 再大也不「一下全出」。
 *
 * kb 旧实现是几何级数追平（剩余/10 每 28ms），大段 SSE 到达后约 300ms 刷完，
 * 视觉上「卡两下然后一下子全部出来」。此处改为 WeKnora 同款固定速率算法：
 *  - 流式期间（active）：minCps=72 / maxCps=240 / drain=0.42s（WeKnora 默认）；
 *  - 流式结束（active=false）：drain 缩短到 0.15s，平滑加速追平到全文，无跳字；
 *  - 文本未变时不启动 rAF，静态消息零开销；
 *  - 尊重 prefers-reduced-motion（无障碍：直接显示全文）。
 */

interface Props {
  text: string;
  refs?: CitationRef[] | null;
  /** 是否正在流式生成（影响追平速度） */
  active?: boolean;
  onCitationClick?: (payload: CitationPayload, ref?: CitationRef) => void;
}

// ---- 对齐 WeKnora useTypewriter 的揭示边界算法 ----
const NATURAL_BREAK_RE = /[\s，。！？；：、,.!?;:)\]】》」』]/u;
const CJK_RE = /[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]/u;
const WORD_CHARACTER_RE = /[\p{L}\p{N}_]/u;

function nextCodePointEnd(text: string, index: number): number {
  if (index >= text.length) return text.length;
  const code = text.charCodeAt(index);
  return index + (code >= 0xd800 && code <= 0xdbff ? 2 : 1);
}

function previousCodePointStart(text: string, index: number): number {
  if (index <= 0) return 0;
  const code = text.charCodeAt(index - 1);
  return code >= 0xdc00 && code <= 0xdfff ? index - 2 : index - 1;
}

function advanceCodePoints(text: string, start: number, count: number): number {
  let end = start;
  for (let i = 0; i < count && end < text.length; i += 1) {
    end = nextCodePointEnd(text, end);
  }
  return end;
}

/** 取下一个可读的揭示边界（CJK 2-4 字短语、拉丁文整词）。 */
function nextTypewriterReveal(
  text: string,
  start: number,
  availableCharacters: number,
): number {
  if (start >= text.length) return text.length;

  const firstEnd = nextCodePointEnd(text, start);
  const firstCharacter = text.slice(start, firstEnd);
  const isCjk = CJK_RE.test(firstCharacter);
  const isWordCharacter = WORD_CHARACTER_RE.test(firstCharacter);
  const minimumGroup = isCjk ? 2 : 1;
  const maximumGroup = isCjk ? 4 : 14;
  const available = Math.max(0, Math.floor(availableCharacters));
  if (available < minimumGroup && text.length - start > available) return start;

  const hardEnd = advanceCodePoints(
    text,
    start,
    Math.min(maximumGroup, Math.max(minimumGroup, available)),
  );
  const minimumEnd = advanceCodePoints(text, start, minimumGroup);

  // 优先停在预算已覆盖的完整短语/词边界
  let cursor = hardEnd;
  while (cursor >= minimumEnd) {
    const characterStart = previousCodePointStart(text, cursor);
    if (NATURAL_BREAK_RE.test(text.slice(characterStart, cursor))) return cursor;
    cursor = characterStart;
  }

  // CJK 无空格分词，按小偶数分组揭示；拉丁文等整词（除非词太长需拆分）
  if (isCjk || !isWordCharacter || hardEnd === text.length || available >= maximumGroup) {
    return hardEnd;
  }
  return start;
}
// ----------------------------------------------------------------

export default function TypewriterText({ text, refs, active, onCitationClick }: Props) {
  const [shown, setShown] = useState(text);
  const shownRef = useRef(text);
  const rafRef = useRef<number | null>(null);
  const lastTsRef = useRef(0);
  const creditRef = useRef(0);
  const initializedRef = useRef(false);
  const reduceMotionRef = useRef(false);

  useEffect(() => {
    if (
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function"
    ) {
      const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
      reduceMotionRef.current = mq.matches;
      const onChange = (e: MediaQueryListEvent) => {
        reduceMotionRef.current = e.matches;
        if (e.matches) {
          shownRef.current = text;
          setShown(text);
        }
      };
      mq.addEventListener?.("change", onChange);
      return () => mq.removeEventListener?.("change", onChange);
    }
  }, [text]);

  const stop = () => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    lastTsRef.current = 0;
  };

  useEffect(() => {
    if (shownRef.current === text) {
      // 已追平：无须定时器（静态消息 / 相同文本）
      initializedRef.current = true;
      stop();
      return;
    }
    // 文本回退（例如重试/清空）：重置打字机
    if (shownRef.current.length > text.length) {
      shownRef.current = "";
      setShown("");
      creditRef.current = 0;
      initializedRef.current = false;
    }

    const minCps = 72;
    const maxCps = 240;
    const drainSeconds = active ? 0.42 : 0.15;
    const maxFrameSeconds = 0.05;

    const tick = (ts: number) => {
      const full = text;
      const target = full.length;
      if (shownRef.current.length > target) {
        shownRef.current = "";
        creditRef.current = 0;
        setShown("");
      }
      if (shownRef.current.length >= target) {
        stop();
        return;
      }
      const dt = lastTsRef.current
        ? Math.min((ts - lastTsRef.current) / 1000, maxFrameSeconds)
        : 0;
      lastTsRef.current = ts;
      const remaining = target - shownRef.current.length;
      const cps = Math.min(maxCps, Math.max(minCps, remaining / drainSeconds));
      creditRef.current += cps * dt;

      const next = nextTypewriterReveal(full, shownRef.current.length, creditRef.current);
      if (next > shownRef.current.length) {
        creditRef.current = Math.max(0, creditRef.current - (next - shownRef.current.length));
        shownRef.current = full.slice(0, next);
        setShown(full.slice(0, next));
      }
      rafRef.current = requestAnimationFrame(tick);
    };

    const ensure = () => {
      if (rafRef.current === null) {
        lastTsRef.current = 0;
        rafRef.current = requestAnimationFrame(tick);
      }
    };

    if (!initializedRef.current) {
      initializedRef.current = true;
      if (reduceMotionRef.current) {
        shownRef.current = text;
        setShown(text);
        return;
      }
    }
    if (reduceMotionRef.current) {
      shownRef.current = text;
      setShown(text);
    } else if (shownRef.current.length < text.length) {
      ensure();
    }
    return stop;
  }, [text, active]);

  return <MarkdownViewer text={shown} refs={refs} onCitationClick={onCitationClick} />;
}
