"use client";

/**
 * 语义层绑定（2026-10-21）：四个语义层页面（元数据/文档库/知识本体/知识图谱）
 * 统一复用知识库详情/知识集页面组件，但需要锁定「电信语义层」知识库：
 *
 * - 绑定的知识库 id 存参数管理表（modo_dim，dim_group=SEMANTIC_LAYER），
 *   页面无硬编码 id——用户在参数管理页改一行即换绑知识库；
 * - 元数据/文档库页各锁一个根目录 id（同组参数），详情面板只展示该目录子树；
 * - 知识本体/知识图谱页直接复用 KbWikiPane / WikiGraphView（kbId 驱动）。
 *
 * 读取走 apiListDims（参数管理页同款接口），登录态/失败有兜底：
 * 参数缺失时回退到「自动发现」——按知识库名「电信语义层」匹配，再按根目录名
 * （元数据/文档库）匹配 id，保证参数没配时页面仍可用。
 */
import { useCallback, useEffect, useState } from "react";
import { apiGetKb, apiListDocFolders, apiListDims, apiListKbs, DocFolderItem, KbItem } from "@/lib/api";

/** 参数管理表里的参数组名（参数管理页 /system/dims 可见可改） */
export const SEMANTIC_DIM_GROUP = "SEMANTIC_LAYER";

/** 绑定的知识库名（自动发现兜底用） */
const KB_NAME = "电信语义层";

/** 根目录名 → 锁定目录的参数编码 */
export const ROOT_FOLDER_DIM_CODES = {
  metadata: "SEMANTIC_ROOT_FOLDER_METADATA",
  docs: "SEMANTIC_ROOT_FOLDER_DOCS",
} as const;

export interface SemanticBinding {
  /** 绑定的知识库（电信语义层）id；空 = 未绑定 */
  kbId: string;
  kb: KbItem | null;
  /** 元数据根目录 id（空 = 未配置/未找到） */
  metadataFolderId: string;
  /** 文档库根目录 id（空 = 未配置/未找到） */
  docsFolderId: string;
  loading: boolean;
  error: string;
}

/**
 * 解析 dim_value 里的绑定值：支持「单值」与「JSON」两种存法。
 * 单值 = 直接是 id；JSON = {"kb_id":"...","folder_id":"..."}。
 */
function parseDimValue(raw: string | null | undefined): { kbId: string; folderId: string } {
  const s = String(raw || "").trim();
  if (!s) return { kbId: "", folderId: "" };
  if (s.startsWith("{")) {
    try {
      const j = JSON.parse(s) as { kb_id?: string; folderId?: string; folder_id?: string };
      return { kbId: String(j.kb_id || ""), folderId: String(j.folderId || j.folder_id || "") };
    } catch {
      /* 非法 JSON 落单值 */
    }
  }
  return { kbId: s, folderId: "" };
}

/**
 * 读取语义层绑定（kb id + 两个根目录 id）。
 * 参数缺失时按名字自动发现，保证开箱可用。
 */
export function useSemanticBinding(): SemanticBinding {
  const [state, setState] = useState<Omit<SemanticBinding, "loading" | "error">>({
    kbId: "",
    kb: null,
    metadataFolderId: "",
    docsFolderId: "",
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const resolve = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      // 1) 参数管理表（modo_dim）拿绑定 id
      let kbId = "";
      let metadataFolderId = "";
      let docsFolderId = "";
      try {
        const res = await apiListDims(1, 100, { dimGroup: SEMANTIC_DIM_GROUP });
        const items = res.success ? res.data?.items || [] : [];
        for (const d of items) {
          const code = d.dim_code || "";
          if (code === "SEMANTIC_KB_ID") {
            kbId = parseDimValue(d.dim_value).kbId;
          } else if (code === ROOT_FOLDER_DIM_CODES.metadata) {
            metadataFolderId = parseDimValue(d.dim_value).folderId || parseDimValue(d.dim_value).kbId;
          } else if (code === ROOT_FOLDER_DIM_CODES.docs) {
            docsFolderId = parseDimValue(d.dim_value).folderId || parseDimValue(d.dim_value).kbId;
          }
        }
      } catch {
        // 参数接口失败走自动发现
      }

      // 2) 兜底：参数没配 → 按名字发现知识库
      if (!kbId) {
        const kres = await apiListKbs(1, 200).catch(() => null);
        const kbs: KbItem[] = kres?.success ? kres.data?.items || [] : [];
        const hit = kbs.find((k) => (k.name || "").trim() === KB_NAME);
        if (hit) kbId = hit.id;
      }

      let kb: KbItem | null = null;
      if (kbId) {
        const g = await apiGetKb(kbId).catch(() => null);
        kb = g?.success ? g.data ?? null : null;
      }

      // 3) 兜底：目录 id 没配 → 从该库的文档目录树按名字找根目录
      if (kbId && (!metadataFolderId || !docsFolderId)) {
        try {
          const res = await apiListDocFolders(kbId);
          const data = res.success ? ((res.data as unknown) || []) : [];
          const folders: DocFolderItem[] = Array.isArray(data) ? data : [];
          const roots = folders.filter((f) => !f.parent_id);
          if (!metadataFolderId) {
            const hit = roots.find((f) => f.name === "元数据");
            if (hit) metadataFolderId = hit.id;
          }
          if (!docsFolderId) {
            const hit = roots.find((f) => f.name === "文档库");
            if (hit) docsFolderId = hit.id;
          }
        } catch {
          /* 目录树拉取失败：留空，页面显示未配置 */
        }
      }

      setState({ kbId, kb, metadataFolderId, docsFolderId });
      if (!kbId) setError(`未绑定知识库：请在「参数管理」新建参数组 ${SEMANTIC_DIM_GROUP}（SEMANTIC_KB_ID=<知识库id>），或创建名为「${KB_NAME}」的知识库`);
    } catch (e) {
      setError((e as Error)?.message || "读取语义层绑定失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void resolve();
  }, [resolve]);

  return { ...state, loading, error };
}
