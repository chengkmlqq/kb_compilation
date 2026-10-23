"use client";

/**
 * FixedHeightVirtualList —— 固定行高虚拟滚动列表。
 *
 * 用途：wiki 左树等「行数可达数千」的列表。只渲染可视窗口 ± overscan 的行，
 * 其余用上下占位撑起滚动高度，避免目录/页面很多时一次性渲染数千 DOM 节点。
 *
 * 行高固定（本项目树行 34px + margin 1px×2 = 36px），无需动态测量；
 * 若未来出现可变行高列表请改用动态测量方案，勿直接复用本组件。
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";

interface Props<T> {
  items: T[];
  itemHeight: number;
  /** 可视窗口上下各多渲染的行数（缓冲，避免快速滚动白屏） */
  overscan?: number;
  renderRow: (item: T, index: number) => React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

function FixedHeightVirtualListInner<T>({
  items,
  itemHeight,
  overscan = 10,
  renderRow,
  className,
  style,
}: Props<T>) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [viewport, setViewport] = useState({ top: 0, height: 0 });

  // 滚动/尺寸变化时记录可视窗口（rAF 节流，避免高频 scroll 触发 state 风暴）
  const updateViewport = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    setViewport({ top: el.scrollTop, height: el.clientHeight });
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    updateViewport();
    const onScroll = () => {
      // 滚动事件高频触发，rAF 合并到帧内一次性更新
      requestAnimationFrame(() => updateViewport());
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => updateViewport()) : null;
    ro?.observe(el);
    return () => {
      el.removeEventListener("scroll", onScroll);
      ro?.disconnect();
    };
  }, [updateViewport]);

  // 可视切片 [start, end)
  const { start, end } = useMemo(() => {
    const total = items.length;
    if (total === 0) return { start: 0, end: 0 };
    const first = Math.max(0, Math.floor(viewport.top / itemHeight) - overscan);
    const last = Math.min(total, Math.ceil((viewport.top + viewport.height) / itemHeight) + overscan);
    return { start: first, end: last };
  }, [items.length, viewport.top, viewport.height, itemHeight, overscan]);

  const visibleItems = useMemo(() => items.slice(start, end), [items, start, end]);
  const totalHeight = items.length * itemHeight;

  return (
    <div ref={containerRef} className={className} style={{ ...style, overflowY: "auto" }}>
      <div style={{ height: totalHeight, position: "relative" }}>
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            transform: `translateY(${start * itemHeight}px)`,
          }}
        >
          {visibleItems.map((item, i) => (
            <div key={i} style={{ height: itemHeight, overflow: "hidden" }}>
              {renderRow(item, start + i)}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * memo 外层：props.items 引用不变时跳过整树重渲染。
 * 父组件里 items 是 useMemo 产物（依赖 branches/expandedKeys），
 * 只有树结构真正变化才重建——点击目录行等高频操作不再全量重渲。
 */
const FixedHeightVirtualList = memo(FixedHeightVirtualListInner) as <T>(
  props: Props<T>,
) => React.ReactElement | null;

export default FixedHeightVirtualList;
