"use client";

/**
 * 文档目录「按需创建/复用」工具（2026-10-17）。
 *
 * 目录上传有两个入口，必须共用同一套目录映射语义（同级同名复用、缺失层级依次创建）：
 *  1. 知识库详情「添加知识 → 上传文件夹」（KbDocsPane.uploadDirectoryBatch）；
 *  2. 全局拖放目录（GlobalDropZone，window drop 递归收集的相对路径）。
 * 两处都要求「目录建到知识库的目录树里」，而**不是**把相对路径拼进文件名。
 */
import { apiCreateDocFolder, apiListDocFolders, type DocFolderItem } from "@/lib/api";

export interface DocFolderEnsurer {
  /** 逐级确保目录存在，返回末级目录 id；dirs 为空时返回基准目录 id */
  ensure(dirs: string[]): Promise<string>;
}

/**
 * 建立目录映射器：一次性预取该知识库现有目录（同级同名复用），
 * 缺失层级按需调用创建接口。批量上传时复用同一实例，避免逐文件重复建目录。
 */
export async function createDocFolderEnsurer(
  kbId: string,
  baseFolderId = "",
): Promise<DocFolderEnsurer> {
  // 相对路径 key（"" = 基准目录）→ 目录 id
  const byPath = new Map<string, string>([["", baseFolderId]]);
  // parentId + name → 目录 id
  const byParentName = new Map<string, string>();

  const res = await apiListDocFolders(kbId).catch(() => null);
  if (res?.success) {
    const data = res.data as unknown;
    const folders = Array.isArray(data)
      ? (data as DocFolderItem[])
      : (data as { folders?: DocFolderItem[] })?.folders || [];
    for (const f of folders) byParentName.set(`${f.parent_id || ""}\u0000${f.name}`, f.id);
  }

  return {
    async ensure(dirs: string[]): Promise<string> {
      let parent = baseFolderId;
      let key = "";
      for (const seg of dirs) {
        key = key ? `${key}/${seg}` : seg;
        const cached = byPath.get(key);
        if (cached) {
          parent = cached;
          continue;
        }
        const scoped = `${parent}\u0000${seg}`;
        let id = byParentName.get(scoped);
        if (!id) {
          const created = await apiCreateDocFolder(kbId, {
            name: seg,
            parent_id: parent || undefined,
          });
          if (!created.success || !created.data?.id) {
            throw new Error(created.message || `创建目录「${seg}」失败`);
          }
          id = created.data.id;
          byParentName.set(scoped, id);
        }
        byPath.set(key, id);
        parent = id;
      }
      return parent;
    },
  };
}
