"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { App } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import { usePathname } from "next/navigation";
import { apiErrorMessage, apiGetSupportedFileTypes, apiUploadArchive, apiUploadDocumentWithProgress } from "@/lib/api";
import { createDocFolderEnsurer, type DocFolderEnsurer } from "@/lib/doc-folder-ensure";
import { collectDroppedFiles, MAX_DROP_FILES, MAX_FILE_SIZE_MB } from "@/lib/drop-files";
import { dirsFromPath } from "@/lib/upload-dir";
import { DEFAULT_SUPPORTED_EXTS, isArchiveFileName } from "@/lib/upload-support";
import {
  emitFileDrop,
  emitUploadTask,
  getFileDropTarget,
  hasFileDropListener,
  makeUploadTaskId,
  onDropTargetChange,
  type FileDropTarget,
} from "@/lib/upload-bus";

/** 拖拽遮罩显隐防抖（对齐 WeKnora：避免子元素冒泡导致闪烁） */
const DEBOUNCE_MS = 150;

function dragHasFiles(e: DragEvent): boolean {
  return !!e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files");
}

/**
 * 全局拖放上传（对齐 WeKnora window 级 drag 监听 + 全屏遮罩）：
 *
 * - window 级 dragenter/dragover/dragleave/drop + dragCounter 计数；
 * - 拖入时显示全屏遮罩（半透明 + 虚线框 + 「释放以上传到 <目标>」）；
 * - drop 时递归收集文件（含目录），路由到已注册目标：
 *   1) 目标含 kbId → 直接调用该 KB 文档上传接口（进度上报 uploadTask）；
 *   2) 否则派发 CustomEvent('kbFileDrop') 交由业务页处理；
 *   3) 无人监听 → 提示「请先进入知识库/技能页再拖入文件」。
 *
 * 仅登录后主区域生效（挂载于 LayoutContent 内，/login 不渲染；此处再加 pathname 防御）。
 */
export default function GlobalDropZone() {
  const { message } = App.useApp();
  const pathname = usePathname();
  const enabled = pathname !== "/login";

  const [dragging, setDragging] = useState(false);
  const [target, setTarget] = useState<FileDropTarget | null>(null);
  // 系统可处理的格式白名单（与后端门禁同源；拉取失败用本地兜底清单）
  const [supportedExts, setSupportedExts] = useState<string[]>(DEFAULT_SUPPORTED_EXTS);

  const dragCounter = useRef(0);
  const showTimer = useRef<number | null>(null);
  const hideTimer = useRef<number | null>(null);
  const mounted = useRef(true);

  // 拉取上传白名单（只放行系统能解析的格式）
  useEffect(() => {
    void (async () => {
      try {
        const res = await apiGetSupportedFileTypes();
        if (res.success && res.data?.types?.length) setSupportedExts(res.data.types);
      } catch {
        // 拉取失败不阻断拖放：沿用本地兜底清单
      }
    })();
  }, []);

  // 订阅拖放目标（知识库页/技能页注册），用于遮罩文案
  useEffect(() => {
    const off = onDropTargetChange((t) => setTarget(t));
    return off;
  }, []);

  // 卸载清理定时器
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (showTimer.current !== null) window.clearTimeout(showTimer.current);
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    };
  }, []);

  // 单文件上传到 KB（进度/结果经 uploadTask 事件上报，支持重试）
  const uploadToKb = useCallback((kbId: string, file: File, taskId: string, folderId?: string) => {
    emitUploadTask({
      id: taskId,
      name: file.name,
      size: file.size,
      status: "uploading",
      progress: 0,
      retry: () => uploadToKb(kbId, file, taskId, folderId),
    });
    void (async () => {
      try {
        const res = await apiUploadDocumentWithProgress(
          kbId,
          file,
          (pct) => {
            emitUploadTask({ id: taskId, name: file.name, size: file.size, status: "uploading", progress: pct });
          },
          folderId || undefined,
        );
        if (res.success) {
          emitUploadTask({ id: taskId, name: file.name, size: file.size, status: "success", progress: 100 });
        } else {
          emitUploadTask({
            id: taskId,
            name: file.name,
            size: file.size,
            status: "error",
            progress: 100,
            error: res.message || "上传失败",
          });
        }
      } catch (e) {
        emitUploadTask({
          id: taskId,
          name: file.name,
          size: file.size,
          status: "error",
          progress: 100,
          error: e instanceof Error ? e.message : "上传失败",
        });
      }
    })();
  }, []);

  /**
   * 拖入 zip 压缩包：走服务端自动解包（POST /documents/upload-archive），
   * 包内目录结构由后端还原（保留目录层级），进度/结果仍经 uploadTask 上报。
   */
  const uploadArchiveToKb = useCallback((kbId: string, file: File, folderId?: string) => {
    const taskId = makeUploadTaskId("kb-drop-zip");
    const label = `${file.name}（压缩包自动解包）`;
    emitUploadTask({
      id: taskId,
      name: label,
      size: file.size,
      status: "uploading",
      progress: 0,
      retry: () => uploadArchiveToKb(kbId, file, folderId),
    });
    void (async () => {
      try {
        const res = await apiUploadArchive(
          kbId,
          file,
          (pct) => {
            emitUploadTask({ id: taskId, name: label, size: file.size, status: "uploading", progress: pct });
          },
          folderId || undefined,
        );
        if (res.success && res.data) {
          const d = res.data;
          const skippedTotal =
            d.skipped.hidden + d.skipped.empty + d.skipped.unsupported + d.skipped.oversize + d.skipped.failed;
          const summary = `解压上传 ${d.created_count} 个文件${skippedTotal > 0 ? `，跳过 ${skippedTotal} 个` : ""}`;
          if (d.created_count === 0) {
            emitUploadTask({
              id: taskId,
              name: label,
              size: file.size,
              status: "error",
              progress: 100,
              error: skippedTotal > 0 ? `${summary}（压缩包内没有可处理的文件）` : "压缩包内没有可处理的文件",
            });
          } else {
            emitUploadTask({ id: taskId, name: `${file.name}｜${summary}`, size: file.size, status: "success", progress: 100 });
          }
        } else {
          emitUploadTask({
            id: taskId,
            name: label,
            size: file.size,
            status: "error",
            progress: 100,
            error: apiErrorMessage(res, "压缩包上传失败"),
          });
        }
      } catch (e) {
        emitUploadTask({
          id: taskId,
          name: label,
          size: file.size,
          status: "error",
          progress: 100,
          error: e instanceof Error ? e.message : "压缩包上传失败",
        });
      }
    })();
  }, []);

  const handleDropFiles = useCallback(
    async (dt: DataTransfer) => {
      const result = await collectDroppedFiles(dt, { supported: supportedExts });
      if (result.entries.length === 0) {
        const reasons = Array.from(new Set(result.rejected.map((r) => r.reason)));
        message.warning(
          reasons.length > 0
            ? `文件被拒绝：${reasons.join("；")}`
            : "未识别到可上传的文件",
        );
        return;
      }
      const cur = getFileDropTarget();
      if (cur?.kbId) {
        const kbId = cur.kbId;
        const baseFolderId = cur.folderId || "";
        // 拖入目录时按相对路径在知识库内创建/复用文档目录（与「添加知识 → 上传文件夹」同语义；
        // 目录层级来自 collectDroppedFiles 的 path，保留完整层级含顶层目录，2026-10-20 起）
        let ensurer: DocFolderEnsurer | null = null;
        if (result.entries.some((e) => dirsFromPath(e.path).length > 0)) {
          ensurer = await createDocFolderEnsurer(kbId, baseFolderId).catch(() => null);
          if (!ensurer) message.warning("目录读取失败，已按当前目录平铺上传");
        }
        for (const entry of result.entries) {
          if (cur.accept && !cur.accept(entry.file)) continue;
          // 压缩包（zip / rar）：走服务端自动解包（包内目录结构由后端还原，无需逐级建目录）
          if (isArchiveFileName(entry.file.name)) {
            uploadArchiveToKb(kbId, entry.file, baseFolderId);
            continue;
          }
          let folderId = baseFolderId;
          const dirs = dirsFromPath(entry.path);
          if (ensurer && dirs.length > 0) {
            try {
              folderId = await ensurer.ensure(dirs);
            } catch (e) {
              console.error("ensure doc folder failed", e);
              folderId = baseFolderId;
            }
          }
          uploadToKb(kbId, entry.file, makeUploadTaskId("kb-drop"), folderId || undefined);
        }
        cur.onUploaded?.();
      } else if (hasFileDropListener()) {
        emitFileDrop(result.entries.map((e) => e.file));
      } else {
        message.warning("请先进入知识库或技能页，再拖入文件");
      }
    },
    [message, supportedExts, uploadArchiveToKb, uploadToKb],
  );

  useEffect(() => {
    if (!enabled) return;

    const onDragEnter = (e: DragEvent) => {
      if (!dragHasFiles(e)) return;
      e.preventDefault();
      dragCounter.current += 1;
      if (showTimer.current !== null) window.clearTimeout(showTimer.current);
      showTimer.current = window.setTimeout(() => {
        if (mounted.current) setDragging(true);
      }, DEBOUNCE_MS);
    };

    const onDragOver = (e: DragEvent) => {
      if (!dragHasFiles(e)) return;
      // 必须 preventDefault 浏览器才会允许 drop
      e.preventDefault();
    };

    const onDragLeave = (e: DragEvent) => {
      if (!dragHasFiles(e)) return;
      dragCounter.current = Math.max(0, dragCounter.current - 1);
      if (dragCounter.current === 0) {
        if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
        hideTimer.current = window.setTimeout(() => {
          if (mounted.current) setDragging(false);
        }, DEBOUNCE_MS);
      }
    };

    const onDrop = (e: DragEvent) => {
      if (!dragHasFiles(e)) return;
      e.preventDefault();
      dragCounter.current = 0;
      if (showTimer.current !== null) window.clearTimeout(showTimer.current);
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
      setDragging(false);
      if (e.dataTransfer) void handleDropFiles(e.dataTransfer);
    };

    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, [enabled, handleDropFiles]);

  if (!enabled || !dragging) return null;

  const label = target ? `释放以上传到「${target.label}」` : "释放以选择上传目标";

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        pointerEvents: "none",
        background: "rgba(22, 119, 255, 0.08)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          width: "min(520px, 80vw)",
          height: 240,
          border: "2px dashed #1677ff",
          borderRadius: 12,
          background: "rgba(255, 255, 255, 0.88)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 12,
        }}
      >
        <InboxOutlined style={{ fontSize: 48, color: "#1677ff" }} />
        <div style={{ fontSize: 18, fontWeight: 600, color: "#1677ff" }}>{label}</div>
        <div style={{ fontSize: 13, color: "#666" }}>
          支持目录与 zip / rar 压缩包（自动解包）· 单次 ≤ {MAX_DROP_FILES} 个文件 · 单文件 ≤ {MAX_FILE_SIZE_MB}MB
        </div>
      </div>
    </div>
  );
}
