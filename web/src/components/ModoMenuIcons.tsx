"use client";

/**
 * modo-design（data_supply_shandong 菜单）官方彩色图标复刻（2026-10-16）
 *
 * 来源：modo-design@1.0.18 icon/react-icon/Icon*Color/index.js（viewBox 0 0 12 12）。
 * shandong 数据供给平台菜单实际使用的 9 个彩色图标，SVG 路径按官方源码原样内联（不做自绘）。
 * modo 图标上色语义：Color 变体 = 分层 opacity 的 currentColor（官方 CSS .modo-icon { fill: currentColor }），
 * 这里在 path 上显式 fill="currentColor" 保证 kb 无该 CSS 时同样渲染。
 * 尺寸统一走 1em（fontSize 由使用处控制，如 .kb-nav-card-icon 24px）。
 */

import React from "react";

type IconProps = {
  size?: number | string;
  color?: string;
  className?: string;
  style?: React.CSSProperties;
  title?: string;
};

function Svg({ size = "1em", color, className, style, title, children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 12 12"
      fill="none"
      stroke="none"
      className={`kb-modo-icon${className ? ` ${className}` : ""}`}
      style={{ fontSize: size, width: "1em", height: "1em", color, ...style }}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  );
}

/** IconBook1Color —— 知识库/文档库（shandong 语料库 knowledge_lib_neo） */
export function ModoIconBook1(props: IconProps) {
  return (
    <Svg {...props}>
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M3.5 10V2H10a.5.5 0 0 1 .5.5v8a.5.5 0 0 1-.5.5H2.5a1 1 0 1 1 1-1Zm1.625-6.5h3.75v2h-3.75Zm3.75 3h-3.75v1h3.75Z"
        opacity=".25"
      />
      <path
        fill="currentColor"
        d="M2.5 1a1 1 0 0 0-1 1v8a1 1 0 0 1 2 0V2a1 1 0 0 0-1-1Zm2.625 2.5h3.75v2h-3.75Zm0 3h3.75v1h-3.75Z"
      />
    </Svg>
  );
}

/** IconCloudAddColor —— 数据接入/上传（shandong 数据接入 col_data） */
export function ModoIconCloudAdd(props: IconProps) {
  return (
    <Svg {...props}>
      <path
        fill="currentColor"
        d="M6.767 10.097A3.25 3.25 0 0 1 9.25 4.75q.122 0 .242.009A3.5 3.5 0 0 0 2.5 5v.045A2.75 2.75 0 0 0 3 10.5h1.875a4.609 4.609 0 0 0 1.892-.403Z"
        opacity=".25"
      />
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M11.75 8a2.5 2.5 0 1 1-2.5-2.5 2.5 2.5 0 0 1 2.5 2.5ZM8.875 6.75v.875H8v.75h.875v.875h.75v-.875h.875v-.75h-.875V6.75Zm-2.108 3.347a3.272 3.272 0 0 0 .406.403H4.875a4.608 4.608 0 0 0 1.892-.403Z"
      />
    </Svg>
  );
}

/** IconCloudServerColor —— 云盘/主机（shandong 云盘 cloud_disk） */
export function ModoIconCloudServer(props: IconProps) {
  return (
    <Svg {...props}>
      <path
        fill="currentColor"
        d="M.5 7.75a2.75 2.75 0 0 0 2.75 2.75h3.532a1.003 1.003 0 0 1-.032-.25v-5a1 1 0 0 1 1-1h1.92A3.5 3.5 0 0 0 2.75 5v.045A2.75 2.75 0 0 0 .5 7.75Z"
        opacity=".25"
      />
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M7.75 5a.25.25 0 0 0-.25.25v5a.25.25 0 0 0 .25.25h3.5a.25.25 0 0 0 .25-.25v-5a.25.25 0 0 0-.25-.25Zm.875.875h1.75v.75h-1.75Zm0 1.5h1.75v.75h-1.75Zm0 1.5h1.75v.75h-1.75Z"
      />
    </Svg>
  );
}

/** IconDatabaseColor —— 知识集/数据源/数据库（shandong 知识集 corpus_info） */
export function ModoIconDatabase(props: IconProps) {
  return (
    <Svg {...props}>
      <path
        fill="currentColor"
        d="M6 4.25a10.627 10.627 0 0 0 3.052-.4A3.982 3.982 0 0 0 10 3.43V5c0 .552-1.79 1-4 1s-4-.448-4-1V3.43a3.982 3.982 0 0 0 .948.42A10.627 10.627 0 0 0 6 4.25ZM2 6.073V7.5c0 .552 1.79 1 4 1s4-.448 4-1V6.073a4.253 4.253 0 0 1-.99.362A12.959 12.959 0 0 1 6 6.75a12.958 12.958 0 0 1-3.01-.315A4.254 4.254 0 0 1 2 6.073Zm7.01 2.862a4.253 4.253 0 0 0 .99-.362V10c0 .552-1.79 1-4 1s-4-.448-4-1V8.573a4.254 4.254 0 0 0 .99.362A12.958 12.958 0 0 0 6 9.25a12.959 12.959 0 0 0 3.01-.315Z"
        opacity=".25"
      />
      <path fill="currentColor" d="M10 2.25c0 .69-1.79 1.25-4 1.25s-4-.56-4-1.25S3.79 1 6 1s4 .56 4 1.25Z" />
    </Svg>
  );
}

/** IconHomeColor —— 门户/首页（shandong 门户 home_page_new） */
export function ModoIconHome(props: IconProps) {
  return (
    <Svg {...props}>
      <path
        fill="currentColor"
        d="M5.494 1.486a.738.738 0 0 1 1.012 0L11.5 6.195l-.675.715-1.137-1.072v4.452a.492.492 0 0 1-.492.491H7.352V8.006a.983.983 0 1 0-.737 0v2.775H5.63V9.735a.86.86 0 0 0-.107-.417L5.17 8.68a.738.738 0 1 0-.645.38H4.538l.34.615a.123.123 0 0 1 .016.06v1.046h-2.09a.492.492 0 0 1-.492-.491V5.838L1.175 6.91.5 6.195Z"
        opacity=".25"
      />
      <path
        fill="currentColor"
        d="M7.352 8.006a.983.983 0 1 0-.737 0v2.775h.737Zm-1.72 1.729v1.046h-.738V9.735a.123.123 0 0 0-.016-.06l-.34-.615h-.013a.737.737 0 1 1 .645-.38l.354.638a.86.86 0 0 1 .107.417Z"
      />
    </Svg>
  );
}

/** IconResourceOrchestrationColor —— 知识编排/编排管理（shandong 知识编排 app_edit） */
export function ModoIconResourceOrchestration(props: IconProps) {
  return (
    <Svg {...props}>
      <path
        fill="currentColor"
        d="M2 1.25a.5.5 0 0 0-.5.5V6.5A.5.5 0 0 0 2 7h3a.5.5 0 0 0 .5-.5V1.75a.5.5 0 0 0-.5-.5Zm5 .5a.5.5 0 0 1 .5-.5H10a.5.5 0 0 1 .5.5V4a.5.5 0 0 1-.5.5H7.5A.5.5 0 0 1 7 4ZM7 6.5a.5.5 0 0 1 .5-.5H10a.5.5 0 0 1 .5.5v3.75a.5.5 0 0 1-.5.5H7.5a.5.5 0 0 1-.5-.5Z"
        opacity=".25"
      />
      <path
        fill="currentColor"
        d="M5.5 3.5H7v-1H5.5Zm2.75 1h1V6h-1ZM1.5 8.25a.5.5 0 0 1 .5-.5h3a.5.5 0 0 1 .5.5v2a.5.5 0 0 1-.5.5H2a.5.5 0 0 1-.5-.5Z"
      />
    </Svg>
  );
}

/** IconSemanticSearchColor —— 智能问答/智能体/语义搜索（shandong 智能体 ai-agent、数据分析 deep_report） */
export function ModoIconSemanticSearch(props: IconProps) {
  return (
    <Svg {...props}>
      <path
        fill="currentColor"
        d="m2.52 9.705 1.068-1.069a3.443 3.443 0 1 1 4.86-4.86L9.546 2.68a4.978 4.978 0 1 0-7.027 7.026Z"
        opacity=".25"
      />
      <path
        fill="currentColor"
        d="M10.842 6v4.975l-2.001-.972a4.952 4.952 0 0 1-6.3-.28l1.068-1.069a3.44 3.44 0 0 0 4.857-4.856L9.564 2.7A4.95 4.95 0 0 1 10.842 6ZM5.62 4.225h.76v3.55h-.76Zm-1.776.507h.761v2.536h-.76Zm4.312.507h-.761v1.522h.76Z"
      />
    </Svg>
  );
}

/** IconSystemMgrColor —— 系统管理（shandong 系统管理 appMgr） */
export function ModoIconSystemMgr(props: IconProps) {
  return (
    <Svg {...props}>
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M10.5 1.5h-9A.5.5 0 0 0 1 2v6a.5.5 0 0 0 .5.5h9A.5.5 0 0 0 11 8V2a.5.5 0 0 0-.5-.5ZM7.527 5a1.476 1.476 0 0 1-.022.242l.37.316-.445.758-.462-.156a1.532 1.532 0 0 1-.43.242l-.093.473h-.89l-.094-.473a1.532 1.532 0 0 1-.429-.242l-.462.157-.445-.759.37-.316a1.37 1.37 0 0 1 0-.484l-.37-.316.445-.758.462.156a1.529 1.529 0 0 1 .43-.242l.093-.473h.89l.094.473a1.529 1.529 0 0 1 .429.242l.462-.157.445.759-.37.316A1.476 1.476 0 0 1 7.527 5Zm-.824 0A.703.703 0 1 1 6 4.297.703.703 0 0 1 6.703 5Z"
        opacity=".25"
      />
      <path
        fill="currentColor"
        d="M6.75 9.496a3.605 3.605 0 0 0 1.241.253.264.264 0 0 1 .259.259v.49h-1.5v.002h-1.5v-.001h-1.5v-.491a.264.264 0 0 1 .259-.259 3.605 3.605 0 0 0 1.241-.253V8.5h1.5ZM5.032 3.84l-.462-.157-.445.759.37.316a1.37 1.37 0 0 0 0 .484l-.37.316.445.758.463-.156a1.529 1.529 0 0 0 .428.242l.094.473h.89l.094-.473a1.529 1.529 0 0 0 .429-.242l.462.157.445-.759-.37-.316a1.37 1.37 0 0 0 0-.484l.37-.316-.445-.759-.463.157a1.53 1.53 0 0 0-.428-.242l-.094-.473h-.89l-.094.473a1.53 1.53 0 0 0-.429.242ZM5.297 5A.703.703 0 1 1 6 5.703.703.703 0 0 1 5.297 5Z"
      />
    </Svg>
  );
}

/** IconTaskMonitorColor —— 任务监控/运维监控（shandong 运维监控 oper_report） */
export function ModoIconTaskMonitor(props: IconProps) {
  return (
    <Svg {...props}>
      <path fill="currentColor" d="M4.5 1h-1v1H2a.5.5 0 0 0-.5.5v1h9v-1A.5.5 0 0 0 10 2H8.5V1h-1v1h-3Z" />
      <path
        fill="currentColor"
        d="M1.5 3.5v7a.5.5 0 0 0 .5.5h8a.5.5 0 0 0 .5-.5v-7Zm7.75 4.348H7.415l-.21-.391L6.14 9.268l-.968-2.613-.491 1.193H2.75v-.61h1.523l.93-2.256 1.063 2.87.966-1.641.548 1.028h1.47Z"
        opacity=".25"
      />
      <path
        fill="currentColor"
        d="M6.266 7.853 5.203 4.982l-.93 2.257H2.75v.61h1.93l.492-1.194.968 2.613 1.066-1.811.209.391H9.25v-.61H7.78l-.548-1.027Z"
      />
    </Svg>
  );
}

/** 9 个 shandong 菜单彩色图标名（后端 MENU_ICONS 目录、菜单管理下拉可选） */
export const MODO_MENU_ICON_NAMES = [
  "IconBook1Color",
  "IconCloudAddColor",
  "IconCloudServerColor",
  "IconDatabaseColor",
  "IconHomeColor",
  "IconResourceOrchestrationColor",
  "IconSemanticSearchColor",
  "IconSystemMgrColor",
  "IconTaskMonitorColor",
] as const;

/** 图标名 -> 组件（AppSider ICON_MAP / 菜单管理页下拉预览共用） */
export const MODO_ICON_COMPONENTS: Record<string, React.ComponentType<IconProps>> = {
  IconBook1Color: ModoIconBook1,
  IconCloudAddColor: ModoIconCloudAdd,
  IconCloudServerColor: ModoIconCloudServer,
  IconDatabaseColor: ModoIconDatabase,
  IconHomeColor: ModoIconHome,
  IconResourceOrchestrationColor: ModoIconResourceOrchestration,
  IconSemanticSearchColor: ModoIconSemanticSearch,
  IconSystemMgrColor: ModoIconSystemMgr,
  IconTaskMonitorColor: ModoIconTaskMonitor,
};
