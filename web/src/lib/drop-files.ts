"use client";

/**
 * 拖放文件收集：支持目录递归（webkitGetAsEntry 深度遍历），带数量/大小限制。
 *
 * 限制（对齐设计约定）：单次最多 200 个文件，单文件最大 100MB。
 */

export const MAX_DROP_FILES = 200;
export const MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024;
export const MAX_FILE_SIZE_MB = 100;

export interface CollectedFile {
  file: File;
  /** 相对路径（目录内为 "dir/sub/file.md"，根级文件仅为文件名） */
  path: string;
}

export interface DropCollectResult {
  /** 过滤后实际可上传的文件（≤200 个、每个 ≤100MB） */
  files: File[];
  /** 收集到的文件总数（过滤前，用于「目录含 N 个文件」提示） */
  total: number;
  /** 被拒绝的文件及原因 */
  rejected: { name: string; reason: string }[];
}

/** 深度遍历单个 FileSystemEntry，递归展开目录 */
async function collectEntry(entry: FileSystemEntry, basePath: string, out: CollectedFile[]): Promise<void> {
  if (entry.isFile) {
    const fileEntry = entry as FileSystemFileEntry;
    const file = await new Promise<File>((resolve, reject) => {
      fileEntry.file(resolve, reject);
    });
    out.push({ file, path: basePath ? `${basePath}/${file.name}` : file.name });
    return;
  }
  if (entry.isDirectory) {
    const dirEntry = entry as FileSystemDirectoryEntry;
    const reader = dirEntry.createReader();
    // readEntries 一次最多返回 100 条，需循环读取直到返回空数组
    const readAll = async (): Promise<FileSystemEntry[]> => {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => {
        reader.readEntries(resolve, reject);
      });
      if (batch.length === 0) return [];
      return batch.concat(await readAll());
    };
    const entries = await readAll();
    const prefix = basePath ? `${basePath}/${entry.name}` : entry.name;
    for (const child of entries) {
      await collectEntry(child, prefix, out);
    }
  }
}

function hasDirectorySupport(dt: DataTransfer): boolean {
  // 仅当 items 可用且首个条目支持 webkitGetAsEntry 时才走目录递归
  const items = dt.items;
  if (!items || items.length === 0) return false;
  try {
    return !!items[0]?.webkitGetAsEntry();
  } catch {
    return false;
  }
}

/** 从 DataTransfer 收集文件（目录递归展开），并应用数量/大小过滤 */
export async function collectDroppedFiles(dt: DataTransfer): Promise<DropCollectResult> {
  const collected: CollectedFile[] = [];
  if (hasDirectorySupport(dt)) {
    const items = dt.items;
    for (let i = 0; i < items.length; i++) {
      const entry = items[i]?.webkitGetAsEntry();
      if (entry) {
        try {
          await collectEntry(entry, "", collected);
        } catch {
          // 单个目录读取失败不阻断其他条目
        }
      }
    }
  } else {
    // 兜底：不支持目录的场景直接取 dt.files
    for (const f of Array.from(dt.files)) {
      collected.push({ file: f, path: f.name });
    }
  }

  const total = collected.length;
  const rejected: { name: string; reason: string }[] = [];
  let files: CollectedFile[] = collected;

  if (total > MAX_DROP_FILES) {
    const overflow = collected.slice(MAX_DROP_FILES);
    for (const o of overflow) {
      rejected.push({ name: o.path, reason: `超出单次 ${MAX_DROP_FILES} 个文件上限` });
    }
    files = collected.slice(0, MAX_DROP_FILES);
  }

  const sizeFiltered: CollectedFile[] = [];
  for (const c of files) {
    if (c.file.size > MAX_FILE_SIZE_BYTES) {
      rejected.push({ name: c.path, reason: `超过 ${MAX_FILE_SIZE_MB}MB 单文件上限` });
    } else {
      sizeFiltered.push(c);
    }
  }
  files = sizeFiltered;

  return {
    files: files.map((c) => c.file),
    total,
    rejected,
  };
}

/** 格式化文件大小（供遮罩/面板展示） */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}
