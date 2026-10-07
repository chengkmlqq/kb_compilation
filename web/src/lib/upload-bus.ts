"use client";

/**
 * 全局上传事件总线（对齐 WeKnora 交互的轻量实现，无第三方依赖）：
 *
 * - `uploadTask`      —— 任意页面/组件上传时上报任务状态（进度/结果），由
 *                       UploadTaskPanel 订阅展示。
 * - `kbFileDropTarget`—— 页面注册「拖放上传目标」（如知识库页/技能页），
 *                       GlobalDropZone 据此路由 drop 并渲染遮罩文案。
 * - `kbFileDrop`      —— GlobalDropZone 在无 kbId 目标时派发的兜底事件，
 *                       交由业务页（如技能页）自行处理文件。
 *
 * 事件名与 detail 结构遵循设计约定，便于后续接入其他页面。
 */

export type UploadTaskStatus = "pending" | "uploading" | "success" | "error";

export interface UploadTaskDetail {
  id: string;
  name: string;
  size: number;
  status: UploadTaskStatus;
  /** 0-100 的百分比进度 */
  progress: number;
  error?: string;
  /** 重试回调（可选）：由发起方提供，供上传任务面板的「重试」按钮调用 */
  retry?: () => void;
}

export interface FileDropTarget {
  /** 知识库 ID：存在时 GlobalDropZone 直接调用该 KB 的文档上传接口 */
  kbId?: string;
  /** 遮罩文案中的目标名，如「知识库：xxx」「技能管理」 */
  label: string;
  /** 是否接受该文件（可按扩展名/大小过滤）；缺省全部接受 */
  accept?: (file: File) => boolean;
  /** kbId 路径下全部文件上传完成后回调（用于刷新文档列表） */
  onUploaded?: () => void;
  /** 非 kbId 路径：由业务页自行处理拖入的文件 */
  onDrop?: (files: File[]) => void;
}

export const UPLOAD_TASK_EVENT = "uploadTask";
export const DROP_TARGET_EVENT = "kbFileDropTarget";
export const FILE_DROP_EVENT = "kbFileDrop";

/** 生成任务 ID（时间戳 + 随机段，足够区分同批并发上传） */
export function makeUploadTaskId(prefix = "upload"): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// ---------------- uploadTask ----------------

export function emitUploadTask(detail: UploadTaskDetail): void {
  window.dispatchEvent(new CustomEvent<UploadTaskDetail>(UPLOAD_TASK_EVENT, { detail }));
}

/** 订阅上传任务事件，返回取消订阅函数（组件卸载时调用） */
export function onUploadTask(handler: (detail: UploadTaskDetail) => void): () => void {
  const listener = (e: Event) => {
    handler((e as CustomEvent<UploadTaskDetail>).detail);
  };
  window.addEventListener(UPLOAD_TASK_EVENT, listener);
  return () => window.removeEventListener(UPLOAD_TASK_EVENT, listener);
}

// ---------------- kbFileDropTarget（拖放目标注册） ----------------

let currentTarget: FileDropTarget | null = null;

export function getFileDropTarget(): FileDropTarget | null {
  return currentTarget;
}

/** 注册/注销拖放目标；注销时传 null。 */
export function setFileDropTarget(target: FileDropTarget | null): void {
  currentTarget = target;
  window.dispatchEvent(
    new CustomEvent<{ target: FileDropTarget | null }>(DROP_TARGET_EVENT, { detail: { target } }),
  );
}

/** 订阅拖放目标变更（GlobalDropZone 据此更新遮罩文案），返回取消订阅函数 */
export function onDropTargetChange(handler: (target: FileDropTarget | null) => void): () => void {
  const listener = (e: Event) => {
    handler((e as CustomEvent<{ target: FileDropTarget | null }>).detail.target);
  };
  window.addEventListener(DROP_TARGET_EVENT, listener);
  return () => window.removeEventListener(DROP_TARGET_EVENT, listener);
}

// ---------------- kbFileDrop（兜底派发） ----------------

let fileDropListenerCount = 0;

/** 派发全局文件掉落事件（detail: { files }），供无 kbId 的目标页处理 */
export function emitFileDrop(files: File[]): void {
  window.dispatchEvent(new CustomEvent<{ files: File[] }>(FILE_DROP_EVENT, { detail: { files } }));
}

/** 订阅全局文件掉落事件（detail: { files }），返回取消订阅函数 */
export function onFileDrop(handler: (files: File[]) => void): () => void {
  fileDropListenerCount += 1;
  const listener = (e: Event) => {
    handler((e as CustomEvent<{ files: File[] }>).detail.files);
  };
  window.addEventListener(FILE_DROP_EVENT, listener);
  return () => {
    fileDropListenerCount = Math.max(0, fileDropListenerCount - 1);
    window.removeEventListener(FILE_DROP_EVENT, listener);
  };
}

/** 当前是否有人监听 kbFileDrop（用于「无人处理」时的提示） */
export function hasFileDropListener(): boolean {
  return fileDropListenerCount > 0;
}
