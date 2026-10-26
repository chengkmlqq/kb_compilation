/**
 * 目录上传（<input type="file" webkitdirectory>）辅助函数，2026-10-17。
 *
 * 语义对齐 WeKnora（frontend/src/views/knowledge/utils/uploadSources.ts 的
 * filterUploadFiles + KnowledgeBase.vue 的 getFolderUploadFileName）：
 *  - 路径中任一段以 "." 开头（隐藏目录 / .DS_Store 等）或文件名命中系统垃圾文件 → 跳过
 *  - 空文件（0 字节）→ 跳过（后端会以「文件内容为空」拒绝）
 *  - 取出目录及**子孙目录**中的全部文件；**保留完整相对路径层级**（2026-10-20 起，
 *    含顶层所选目录：相对路径 "所选目录/子目录/a.pdf" → 目录层级 ["所选目录","子目录"]，
 *    左侧树原样呈现文件夹结构；根级文件 → []）。
 */

/** 系统垃圾文件（不以 "." 开头但不应入库） */
const JUNK_FILE_NAMES = new Set(["thumbs.db", "desktop.ini", ".ds_store"]);

export interface DirFileEntry {
  /** 原始 File 对象（保留 webkitRelativePath） */
  file: File;
  /** 完整相对路径去掉文件名后的目录层级（含顶层所选目录，2026-10-20 起），
     *  如 ["制度", "2024"]；根级文件为 [] */
    dirs: string[];
}

export interface CollectDirFilesResult {
  entries: DirFileEntry[];
  /** 隐藏文件 / 隐藏目录内文件 / 系统垃圾文件计数 */
  hiddenSkipped: number;
  /** 空文件计数 */
  emptySkipped: number;
}

/** 文件在所选目录内的相对路径（普通文件选择为空字符串） */
export function relativePathOf(file: File): string {
  return (file as File & { webkitRelativePath?: string }).webkitRelativePath || "";
}

/**
 * 取路径最后一段（文件名）。
 *
 * 兼容两种「名字里带路径」的形态：`<input webkitdirectory>` 走
 * `webkitRelativePath`，而**拖入目录**时浏览器可能把相对路径直接写进
 * `File.name`（Chrome 实测）——兜底取 basename，避免路径被当成文件名入库。
 */
export function baseNameOfPath(pathOrName: string): string {
  const parts = pathOrName.split("/").filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : pathOrName;
}

/**
 * 由相对路径推导需要在知识库内创建的目录层级：**保留完整路径层级**（含顶层
 * 所选/拖入目录本身，2026-10-20 用户确认），只去掉文件名（"制度/2024/a.pdf"
 * → ["制度","2024"]；根级文件 → []）。
 */
export function dirsFromPath(pathOrName: string): string[] {
  const parts = pathOrName.split("/").filter(Boolean);
  return parts.slice(0, -1);
}

/** 本次选择是否来自「上传文件夹」（存在带相对路径的文件） */
export function isDirectoryUpload(files: File[]): boolean {
  return files.some((f) => relativePathOf(f) !== "");
}

/**
 * 目录上传文件分拣：返回有效文件（含推导出的子目录层级）与跳过统计。
 * 非目录上传（无 webkitRelativePath）时 dirs 恒为 []，可直接复用。
 */
export function collectDirFiles(files: File[]): CollectDirFilesResult {
  const entries: DirFileEntry[] = [];
  let hiddenSkipped = 0;
  let emptySkipped = 0;

  for (const file of files) {
    const rel = relativePathOf(file) || file.name;
    const parts = rel.split("/").filter(Boolean);
    if (parts.length === 0) parts.push(file.name);
    const baseName = parts[parts.length - 1];
    const dirParts = parts.slice(0, -1);
    const hidden =
      JUNK_FILE_NAMES.has(baseName.toLowerCase()) ||
      baseName.startsWith(".") ||
      dirParts.some((p) => p.startsWith(".") || JUNK_FILE_NAMES.has(p.toLowerCase()));
    if (hidden) {
      hiddenSkipped += 1;
      continue;
    }
    if (file.size === 0) {
      emptySkipped += 1;
      continue;
    }
    // 保留完整相对路径的目录层级（含顶层所选目录名，2026-10-20 用户确认），
    // 只去掉文件名 → 需要还原的目录层级
    entries.push({ file, dirs: dirsFromPath(rel) });
  }

  return { entries, hiddenSkipped, emptySkipped };
}
