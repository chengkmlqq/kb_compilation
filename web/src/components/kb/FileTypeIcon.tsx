/**
 * 知识库文档「按文件类型」图标（2026-10-17）
 *
 * 图标来源：data_supply_base 文档库（knowledge_lib_neo）逐类型 PNG 原样字节拷贝 ——
 *   src/pages/KnowledgeLibNeo/images/{doc,pdf,xlsx,ppt,txt,md,img,html,mp3,mp4}.png（80x80），
 *   落位 web/public/images/dsb/file-icons/，不自绘、不改色。
 * 映射口径对齐 DSB KnowledgeLog/index.tsx 的 fileTypeMap：
 *   docx/doc/wps→doc；xlsx/xls/csv/et→xlsx；ppt/pptx/dps→ppt；jpg/jpeg/png/gif/webp/img/image→img；
 *   md→md；txt→txt；html→html；mp3→mp3；mp4→mp4；未命中回落 txt.png（与 DSB 渲染分支一致）。
 *
 * 用法：fileTypeIcon(ext, size?) —— 返回 <img>；容器 font-size 不再影响图标尺寸，需显式传 size。
 * 注意：应用挂在 /wiki 前缀下（next.config basePath），静态资源路径需自带前缀。
 */
import type { CSSProperties, ReactNode } from "react";

/** 应用 basePath（与 next.config.ts 的 basePath 一致） */
const BASE_PATH = "/wiki";
const ICON_DIR = `${BASE_PATH}/images/dsb/file-icons`;

/** 扩展名 → 图标文件名（DSB fileTypeMap 口径） */
export const FILE_ICON_NAME: Record<string, string> = {
  // word
  doc: "doc",
  docx: "doc",
  wps: "doc",
  // pdf
  pdf: "pdf",
  // excel
  xlsx: "xlsx",
  xls: "xlsx",
  csv: "xlsx",
  et: "xlsx",
  // powerpoint
  ppt: "ppt",
  pptx: "ppt",
  dps: "ppt",
  // 文本 / markdown
  txt: "txt",
  md: "md",
  markdown: "md",
  // 图片
  jpg: "img",
  jpeg: "img",
  png: "img",
  gif: "img",
  webp: "img",
  bmp: "img",
  svg: "img",
  img: "img",
  image: "img",
  // 网页
  html: "html",
  htm: "html",
  // 音视频
  mp3: "mp3",
  m4a: "mp3",
  wav: "mp3",
  mp4: "mp4",
};

/** 未命中扩展名的回落图标（DSB 同款回落 txt） */
export const FILE_ICON_FALLBACK = "txt";

/** 扩展名 → 静态资源 URL */
export function fileIconSrc(ext?: string | null): string {
  const key = (ext || "").trim().toLowerCase().replace(/^\./, "");
  return `${ICON_DIR}/${FILE_ICON_NAME[key] || FILE_ICON_FALLBACK}.png`;
}

/** 文件类型图标（DSB 文档库同款 PNG，默认 16px） */
export function fileTypeIcon(ext?: string | null, size = 16, style?: CSSProperties): ReactNode {
  return (
    <img
      src={fileIconSrc(ext)}
      width={size}
      height={size}
      alt=""
      draggable={false}
      aria-hidden
      style={{ display: "inline-block", verticalAlign: "-2px", flexShrink: 0, ...style }}
    />
  );
}
