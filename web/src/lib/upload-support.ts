/**
 * 上传格式白名单（前端侧，2026-10-20）。
 *
 * 与后端 `api/services/parser_registry.py::SUPPORTED_UPLOAD_TYPES` 同源：
 * 页面打开时经 `GET /api/v1/kbs/file-types/supported` 拉取；拉取失败回落本文件的
 * DEFAULT_SUPPORTED_EXTS（离线兜底，仍能做预校验，只是可能滞后于后端）。
 *
 * 目的：只允许上传系统真能解析的格式（原先任意文件都能传，worker 解析时才
 * 抛 `Unsupported file type` → 文档 FAILED），并让前端 accept / 预校验与后端门禁一致。
 */

/** 兜底白名单（与后端一致：内置 docreader 解析器注册的格式） */
export const DEFAULT_SUPPORTED_EXTS: string[] = [
  // Office / 文档
  "doc", "docx", "pdf", "ppt", "pptx", "xls", "xlsx",
  // 纯文本类
  "txt", "md", "markdown", "csv", "epub", "mhtml",
  // 图片
  "png", "jpg", "jpeg", "gif", "bmp", "tiff", "webp",
  // 代码文件（2026-10 代码仓库知识库：与 docreader CodeParser 同源）
  "py", "java", "js", "mjs", "cjs", "jsx", "ts", "tsx", "go", "rs",
  "c", "h", "cpp", "cc", "cxx", "hpp", "cs", "kt", "kts", "swift",
  "php", "rb", "sh", "bash", "zsh", "sql", "yaml", "yml", "json",
  "xml", "html", "htm", "css", "scss", "less", "vue", "proto",
  "dockerfile",
];

/** 可自动解包的压缩包格式（服务端解包，见 apiUploadArchive） */
export const ARCHIVE_EXTS: string[] = ["zip"];

/** 取扩展名（小写、不含点） */
export function extOf(fileName: string): string {
  const base = (fileName || "").split(/[\\/]/).pop() || "";
  const i = base.lastIndexOf(".");
  return i > 0 ? base.slice(i + 1).toLowerCase() : "";
}

/** 是否压缩包（可自动解包） */
export function isArchiveFileName(fileName: string): boolean {
  return ARCHIVE_EXTS.includes(extOf(fileName));
}

/** 是否为可解析格式 */
export function isSupportedFileName(
  fileName: string,
  supported: string[] = DEFAULT_SUPPORTED_EXTS,
): boolean {
  return supported.includes(extOf(fileName));
}

/** <input accept> 取值（含压缩包） */
export function acceptAttr(
  supported: string[] = DEFAULT_SUPPORTED_EXTS,
  extra: string[] = [],
): string {
  return [...supported, ...extra].map((e) => `.${e.replace(/^\./, "")}`).join(",");
}

/** 按格式分拣：kept = 可解析文件，skipped = 不支持的（压缩包不算「支持的文件」，由调用方单独处理） */
export function partitionBySupport(
  files: File[],
  supported: string[] = DEFAULT_SUPPORTED_EXTS,
): { kept: File[]; skipped: File[] } {
  const kept: File[] = [];
  const skipped: File[] = [];
  for (const f of files) {
    if (isSupportedFileName(f.name, supported)) kept.push(f);
    else skipped.push(f);
  }
  return { kept, skipped };
}

/** 提示文案里的格式清单（最多展示 max 个，避免长列表刷屏） */
export function supportedLabel(
  supported: string[] = DEFAULT_SUPPORTED_EXTS,
  max = 12,
): string {
  const shown = supported.slice(0, max).map((e) => `.${e.replace(/^\./, "")}`);
  return supported.length > max ? `${shown.join(" ")} 等` : shown.join(" ");
}
