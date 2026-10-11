"use client";

/**
 * AgentAvatar — 智能体头像（对齐 WeKnora AgentAvatar.vue）。
 *
 * 生成机制（确定性，无后端存储）：
 *  1. 取智能体名称首字符（英文字母转大写，中文原样）
 *  2. 对名称做字符串哈希（java hashCode 等价），取模 16 组渐变方案
 *  3. 渲染 = 渐变色圆角块 + 白色首字符 + 两颗星星 SVG 装饰
 * 同名智能体永远是同色同字母；名称变化实时重算。
 */
import { useMemo } from "react";

const GRADIENTS: { from: string; to: string }[] = [
  { from: "#667eea", to: "#764ba2" }, // 紫蓝渐变
  { from: "#4facfe", to: "#00f2fe" }, // 蓝青渐变
  { from: "#43e97b", to: "#38f9d7" }, // 绿青渐变
  { from: "#11998e", to: "#38ef7d" }, // 深绿渐变
  { from: "#5ee7df", to: "#b490ca" }, // 青紫渐变
  { from: "#48c6ef", to: "#6f86d6" }, // 蓝紫渐变
  { from: "#a8edea", to: "#fed6e3" }, // 青粉渐变（柔和）
  { from: "#667db6", to: "#0082c8" }, // 蓝色渐变
  { from: "#36d1dc", to: "#5b86e5" }, // 青蓝渐变
  { from: "#56ab2f", to: "#a8e063" }, // 草绿渐变
  { from: "#614385", to: "#516395" }, // 深紫蓝渐变
  { from: "#02aab0", to: "#00cdac" }, // 青绿渐变
  { from: "#6a82fb", to: "#fc5c7d" }, // 蓝粉渐变（柔和）
  { from: "#834d9b", to: "#d04ed6" }, // 紫色渐变
  { from: "#4776e6", to: "#8e54e9" }, // 蓝紫渐变
  { from: "#00b09b", to: "#96c93d" }, // 青绿渐变
];

/** java String.hashCode 等价（与 WeKnora AgentAvatar.vue 完全一致，保证同名同色） */
function hashCode(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash = hash & hash;
  }
  return Math.abs(hash);
}

/** 首字符：英文大写，中文原样，空名 → '?' */
function firstLetter(name: string): string {
  const n = (name || "").trim();
  if (!n) return "?";
  const ch = n.charAt(0);
  return /[a-zA-Z]/.test(ch) ? ch.toUpperCase() : ch;
}

export interface AgentAvatarProps {
  name: string;
  size?: "small" | "medium" | "large";
}

export default function AgentAvatar({ name, size = "medium" }: AgentAvatarProps) {
  const { gradient, letter } = useMemo(() => {
    const g = GRADIENTS[hashCode(name || "") % GRADIENTS.length];
    return { gradient: g, letter: firstLetter(name) };
  }, [name]);

  const dims =
    size === "small"
      ? { box: 22, radius: 5, fontSize: 11, border: "none" as const }
      : size === "large"
        ? { box: 48, radius: 12, fontSize: 20, border: "none" as const }
        : { box: 32, radius: 8, fontSize: 14, border: "none" as const };
  const showSparkles = size !== "small";

  return (
    <div
      style={{
        position: "relative",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: dims.box,
        height: dims.box,
        borderRadius: dims.radius,
        flexShrink: 0,
        boxShadow: size === "medium" ? "0 1px 2px rgba(0,0,0,0.08), 0 2px 8px rgba(0,0,0,0.06)" : "none",
        overflow: "hidden",
        background: `linear-gradient(135deg, ${gradient.from} 0%, ${gradient.to} 100%)`,
      }}
    >
      {showSparkles && (
        <svg
          viewBox="0 0 32 32"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          style={{ position: "absolute", top: 0, left: 0, width: "100%", height: "100%", pointerEvents: "none", opacity: 0.85 }}
        >
          {/* 右上角小星星 */}
          <path
            d="M24 5L24.4 6.6C24.45 6.85 24.65 7.05 24.9 7.1L26.5 7.5L24.9 7.9C24.65 7.95 24.45 8.15 24.4 8.4L24 10L23.6 8.4C23.55 8.15 23.35 7.95 23.1 7.9L21.5 7.5L23.1 7.1C23.35 7.05 23.55 6.85 23.6 6.6L24 5Z"
            fill="rgba(255,255,255,0.6)"
          />
          {/* 左下角小星星 */}
          <path
            d="M7 22L7.4 23.6C7.45 23.85 7.65 24.05 7.9 24.1L9.5 24.5L7.9 24.9C7.65 24.95 7.45 25.15 7.4 25.4L7 27L6.6 25.4C6.55 25.15 6.35 24.95 6.1 24.9L4.5 24.5L6.1 24.1C6.35 24.05 6.55 23.85 6.6 23.6L7 22Z"
            fill="rgba(255,255,255,0.5)"
          />
        </svg>
      )}
      <span
        style={{
          position: "relative",
          zIndex: 1,
          color: "#fff",
          fontSize: dims.fontSize,
          fontWeight: 600,
          textShadow: `0 1px 2px ${gradient.to}80, 0 0 8px ${gradient.from}30`,
          lineHeight: 1,
        }}
      >
        {letter}
      </span>
    </div>
  );
}