"use client";

/**
 * 知识库 · 文档面板（KbDocsPane）
 *
 * 从 kbs/[id]/page.tsx 抽出，供「动态选项卡」与详情路由复用：
 * KB 概览 + 文档工具条（筛选/上传/知识库配置(含切片配置)）+ 文档卡片视图/表格 + 分页。
 * 仅依赖 kbId prop，自包含（自带文档列表与上传相关 state、Drawer、Modal）。
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  App,
  Button,
  Empty,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Tree,
  Typography,
} from "antd";
import {
  AppstoreOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  FolderAddOutlined,
  FolderOutlined,
  PlusOutlined,
  RedoOutlined,
  ReloadOutlined,
  SearchOutlined,
  SettingOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import DocCardView, {
  STATE_LABEL,
  fileTypeIcon,
  formatSize,
} from "@/components/DocCardView";
import ModoTable from "@/components/biz/modo-table";
import ModoPagination from "@/components/biz/modo-pagination";
import DocDetailDrawer from "@/components/DocDetailDrawer";
import UploadConfirmDialog, { UploadProcessConfig } from "@/components/UploadConfirmDialog";
import DocBuildProcessDrawer from "@/components/DocBuildProcessDrawer";
import KBConfigModal from "@/components/KBConfigModal";
import {
  apiCreateDocFolder,
  apiDeleteDocFolder,
  apiDeleteDocument,
  apiDownloadDocument,
  apiGenerateDocSummary,
  apiGetKb,
  apiListDocFolders,
  apiListDocuments,
  apiMoveDocumentsToFolder,
  apiReparseDocument,
  apiUploadDocumentWithProgress,
  DocFolderItem,
  DocItem,
  KbItem,
} from "@/lib/api";
import { emitUploadTask, makeUploadTaskId, setFileDropTarget } from "@/lib/upload-bus";

const PARSE_STATE_COLOR: Record<string, string> = {
  PENDING: "default",
  PARSING: "processing",
  EMBEDDING: "processing",
  READY: "success",
  FAILED: "error",
};

export interface KbDocsPaneProps {
  kbId: string;
}

export default function KbDocsPane({ kbId }: KbDocsPaneProps) {
  const { message, modal } = App.useApp();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [kb, setKb] = useState<KbItem | null>(null);
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [docPage, setDocPage] = useState(1);
  const [docPageSize, setDocPageSize] = useState(20);
  const [docTotal, setDocTotal] = useState(0);
  const [docKeyword, setDocKeyword] = useState("");
  const [docStatus, setDocStatus] = useState("");
  const [docType, setDocType] = useState("");
  // 文档多级目录（doc_folder）：选中目录 id（"" = 全部/根），递归子树浏览
  const [docFolders, setDocFolders] = useState<DocFolderItem[]>([]);
  const [docFolderId, setDocFolderId] = useState("");
  // 移至目录（批量）
  const [moveOpen, setMoveOpen] = useState(false);
  const [moveFolderId, setMoveFolderId] = useState("");
  const [moveBusy, setMoveBusy] = useState(false);
  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());
  const [docView, setDocView] = useState<"card" | "list">(
    () => (localStorage.getItem("kb.docs.viewMode") as "card" | "list") || "card",
  );

  /** 视图模式切换 + localStorage 持久化（对齐 WeKnora doc-view-toggle） */
  const setDocViewPersist = (mode: "card" | "list") => {
    setDocView(mode);
    try {
      localStorage.setItem("kb.docs.viewMode", mode);
    } catch {
      /* ignore */
    }
  };

  const [detailDoc, setDetailDoc] = useState<DocItem | null>(null);
  // 上传确认弹窗（对齐 WeKnora：选文件 → 弹确认框可选处理配置 → 确认后上传）
  const [pendingUploadFiles, setPendingUploadFiles] = useState<File[]>([]);
  const [uploadConfirmOpen, setUploadConfirmOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [buildDoc, setBuildDoc] = useState<DocItem | null>(null);

  // 加载知识库详情（含 WeKnora 对齐配置：索引开关/技能绑定/模型绑定等）
  const loadKb = useCallback(async () => {
    const res = await apiGetKb(kbId).catch(() => null);
    if (res?.success && res.data) setKb(res.data);
  }, [kbId]);

  // 文档目录树（多级）：全量轻量元数据，前端按 parent_id 构建缩进树
  const loadDocFolders = useCallback(async () => {
    try {
      const res = await apiListDocFolders(kbId);
      if (res.success) {
        const data = res.data as unknown;
        const folders = Array.isArray(data)
          ? data
          : (data as { folders?: DocFolderItem[] })?.folders || [];
        setDocFolders(folders);
      }
    } catch (e) {
      console.error("load doc folders failed", e);
    }
  }, [kbId]);

  // 目录下拉选项：按 parent 树缩进（"-" 前缀），用于筛选/移动/上传目标
  const folderOptions = (() => {
    const byParent = new Map<string, DocFolderItem[]>();
    for (const f of docFolders) {
      const key = f.parent_id || "";
      if (!byParent.has(key)) byParent.set(key, []);
      byParent.get(key)!.push(f);
    }
    const out: { label: string; value: string }[] = [];
    const walk = (parentId: string, depth: number) => {
      for (const f of byParent.get(parentId) || []) {
        out.push({ label: `${"　".repeat(depth)}${f.name}`, value: f.id });
        walk(f.id, depth + 1);
      }
    };
    walk("", 0);
    return out;
  })();

  // 目录树展开状态（antd Tree 受控展开）
  const [folderExpandedKeys, setFolderExpandedKeys] = useState<string[]>([]);
  useEffect(() => {
    // 目录加载后默认展开第一层
    const roots = docFolders.filter((f) => !f.parent_id);
    setFolderExpandedKeys((prev) => [...prev, ...roots.map((f) => f.id)]);
  }, [docFolders]);

  // 树形数据（antd Tree 用）：节点 = 目录，title 带文档数 + 操作按钮
  const folderTreeData = (() => {
    const byParent = new Map<string, DocFolderItem[]>();
    for (const f of docFolders) {
      const key = f.parent_id || "";
      if (!byParent.has(key)) byParent.set(key, []);
      byParent.get(key)!.push(f);
    }
    const build = (parentId: string): { key: string; title: ReactNode; children?: unknown[] }[] =>
      (byParent.get(parentId) || []).map((f) => ({
        key: f.id,
        title: (
          <div style={{ display: "flex", alignItems: "center", gap: 6, width: "100%" }}>
            <FolderOutlined style={{ color: "#faad14", fontSize: 13 }} />
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {f.name}
            </span>
            <span style={{ color: "#999", fontSize: 11 }}>{f.child_count || 0}</span>
            <span className="kb-doc-folder-actions" style={{ display: "none", gap: 2 }}>
              <Tooltip title="新建子目录">
                <FolderAddOutlined style={{ fontSize: 12 }} onClick={(e) => { e.stopPropagation(); createFolder(f.id); }} />
              </Tooltip>
              <Tooltip title="重命名">
                <EditOutlined style={{ fontSize: 12 }} onClick={(e) => { e.stopPropagation(); renameFolder(f); }} />
              </Tooltip>
              <Tooltip title="删除">
                <DeleteOutlined style={{ fontSize: 12, color: "#ff4d4f" }} onClick={(e) => { e.stopPropagation(); deleteFolder(f); }} />
              </Tooltip>
            </span>
          </div>
        ),
        children: build(f.id),
      }));
    return build("");
  })();

  useEffect(() => {
    void loadKb();
  }, [loadKb]);

  const load = useCallback(async () => {
    setDocsLoading(true);
    try {
      const res = await apiListDocuments(kbId, docPage, docPageSize, {
        keyword: docKeyword.trim() || undefined,
        parseStatus: docStatus || undefined,
        fileType: docType || undefined,
        folderId: docFolderId || undefined,
      });
      if (res.success) {
        setDocs(res.data?.items || []);
        setDocTotal(res.data?.total ?? 0);
      }
    } finally {
      setDocsLoading(false);
    }
  }, [kbId, docPage, docPageSize, docKeyword, docStatus, docType, docFolderId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadDocFolders();
  }, [loadDocFolders]);

  // Auto-poll while any document is still being parsed (PENDING/PARSING/EMBEDDING).
  const hasInFlight = docs.some((d) =>
    ["PENDING", "PARSING", "EMBEDDING"].includes(d.parse_state),
  );
  useEffect(() => {
    if (!hasInFlight) return;
    const timer = setInterval(() => {
      void load();
    }, 3000);
    return () => clearInterval(timer);
  }, [hasInFlight, load]);

  // 自动生成 AI 摘要（对齐 WeKnora）：KB 配了 LLM 时，解析完成的文档逐个自动补摘要。
  const autoSummaryRunning = useRef(false);
  const autoSummarySeen = useRef<Set<string>>(new Set());
  const [autoTick, setAutoTick] = useState(0);

  useEffect(() => {
    if (!kb?.summary_model_id) return;
    if (autoSummaryRunning.current) return;
    const target = docs.find(
      (d) => d.parse_state === "READY" && !d.summary_status && !autoSummarySeen.current.has(d.id),
    );
    if (!target) return;
    autoSummarySeen.current.add(target.id);
    autoSummaryRunning.current = true;
    apiGenerateDocSummary(kbId, target.id)
      .then((res) => {
        if (res.success && res.data) {
          const updated = {
            ...target,
            summary: res.data?.summary ?? null,
            summary_status: res.data?.summary_status ?? "READY",
            summary_error: null,
          };
          setDocs((prev) => prev.map((d) => (d.id === target.id ? updated : d)));
          // 抽屉正打开该文档时同步刷新展示（原 onSummaryUpdated 职责，按钮移除后由自动摘要接管）
          setDetailDoc((prev) => (prev && prev.id === target.id ? updated : prev));
        }
      })
      .catch(() => {
        // 后端已置 summary_status=FAILED；不再自动重试（手动可重试）
      })
      .finally(() => {
        autoSummaryRunning.current = false;
        setAutoTick((t) => t + 1);
      });
  }, [docs, kbId, kb?.summary_model_id, autoTick]);

  const onDeleteDoc = async (doc: DocItem) => {
    const res = await apiDeleteDocument(kbId, doc.id);
    if (res.success) {
      message.success("文档已删除");
      if (detailDoc?.id === doc.id) setDetailDoc(null);
      // 删除当前页最后一条且不在第 1 页时回退一页，避免空页
      if (docs.length === 1 && docPage > 1) setDocPage(docPage - 1);
      else void load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const onDownloadDoc = async (doc: DocItem) => {
    try {
      await apiDownloadDocument(kbId, doc.id, doc.file_name);
      message.success("开始下载");
    } catch (e) {
      message.error((e as Error).message || "下载失败");
    }
  };

  const onReparseDoc = async (doc: DocItem) => {
    const res = await apiReparseDocument(kbId, doc.id);
    if (res.success) {
      message.success("已重新入队解析");
      setDetailDoc(null);
      void load();
    } else {
      message.error(res.message || "重新解析失败");
    }
  };

  const onViewTrace = (doc: DocItem) => {
    // 就地打开构建过程抽屉（优先 wiki 构建任务，否则回退解析任务）
    setBuildDoc(doc);
  };

  const selectedList = docs.filter((d) => selectedDocs.has(d.id));

  const onBatchReparse = async () => {
    if (selectedList.length === 0) return;
    for (const d of selectedList) {
      await apiReparseDocument(kbId, d.id).catch(() => undefined);
    }
    message.success(`已重新入队 ${selectedList.length} 个文档解析`);
    setSelectedDocs(new Set());
    void load();
  };

  const onBatchDelete = () => {
    const n = selectedList.length;
    if (n === 0) return;
    modal.confirm({
      title: `删除选中的 ${n} 个文档？`,
      content: "删除后不可恢复（关联分块与向量一并清理）。",
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        for (const d of selectedList) {
          await apiDeleteDocument(kbId, d.id).catch(() => undefined);
        }
        message.success(`已删除 ${n} 个文档`);
        setSelectedDocs(new Set());
        void load();
      },
    });
  };

  // ---- 文档目录：管理（新建/重命名/删除）+ 批量移动 ----
  const createFolder = (parentId: string) => {
    let name = "";
    modal.confirm({
      title: parentId ? "新建子目录" : "新建根目录",
      content: (
        <Input
          placeholder="目录名称"
          autoFocus
          onChange={(e) => {
            name = e.target.value;
          }}
        />
      ),
      okText: "创建",
      onOk: async () => {
        if (!name.trim()) {
          message.warning("请输入目录名称");
          return;
        }
        try {
          await apiCreateDocFolder(kbId, { name: name.trim(), parent_id: parentId || undefined });
          message.success("目录已创建");
          await loadDocFolders();
        } catch (e) {
          message.error((e as Error).message || "创建失败");
          throw e;
        }
      },
    });
  };

  const renameFolder = (folder: DocFolderItem) => {
    let name = folder.name;
    modal.confirm({
      title: "重命名目录",
      content: (
        <Input
          defaultValue={folder.name}
          autoFocus
          onChange={(e) => {
            name = e.target.value;
          }}
        />
      ),
      okText: "保存",
      onOk: async () => {
        if (!name.trim() || name.trim() === folder.name) return;
        try {
          const { apiUpdateDocFolder } = await import("@/lib/api");
          await apiUpdateDocFolder(kbId, folder.id, { name: name.trim() });
          message.success("目录已更新");
          await loadDocFolders();
        } catch (e) {
          message.error((e as Error).message || "重命名失败");
          throw e;
        }
      },
    });
  };

  const deleteFolder = (folder: DocFolderItem) => {
    modal.confirm({
      title: `删除目录「${folder.name}」？`,
      content: "仅空目录可删除；若目录下仍有子目录或文档，请先移走。",
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await apiDeleteDocFolder(kbId, folder.id);
          message.success("目录已删除");
          if (docFolderId === folder.id) setDocFolderId("");
          await loadDocFolders();
          void load();
        } catch (e) {
          message.error((e as Error).message || "删除失败");
          throw e;
        }
      },
    });
  };

  const openMove = () => {
    setMoveFolderId(docFolderId);
    setMoveOpen(true);
  };

  const confirmMove = async () => {
    const ids = [...selectedDocs];
    if (ids.length === 0) return;
    setMoveBusy(true);
    try {
      const res = await apiMoveDocumentsToFolder(kbId, ids, moveFolderId);
      if (res.success) {
        message.success(`已移动 ${res.data?.moved ?? ids.length} 个文档`);
        setMoveOpen(false);
        setSelectedDocs(new Set());
        await loadDocFolders();
        void load();
      } else {
        message.error(res.message || "移动失败");
      }
    } catch (e) {
      message.error((e as Error).message || "移动失败");
    } finally {
      setMoveBusy(false);
    }
  };

  // 带进度上报的知识库文档上传（本地上传 & 全局拖放复用；经 uploadTask 事件驱动任务浮层）
  const startDocUpload = (file: File, taskId?: string, folderId = docFolderId, processConfig?: UploadProcessConfig | null) => {
    const id = taskId ?? makeUploadTaskId("kb-upload");
    emitUploadTask({
      id,
      name: file.name,
      size: file.size,
      status: "uploading",
      progress: 0,
      retry: () => startDocUpload(file, id),
    });
    void (async () => {
      try {
        const res = await apiUploadDocumentWithProgress(
          kbId,
          file,
          (pct) => {
            emitUploadTask({ id, name: file.name, size: file.size, status: "uploading", progress: pct });
          },
          folderId || undefined,
          processConfig as Record<string, unknown> | null | undefined,
        );
        if (res.success) {
          emitUploadTask({ id, name: file.name, size: file.size, status: "success", progress: 100 });
          message.success(`「${file.name}」已上传，后台解析中`);
          // 上传成功后回到第一页并重新查询列表，立即展示新文档
          setDocPage(1);
          void load();
        } else {
          emitUploadTask({
            id,
            name: file.name,
            size: file.size,
            status: "error",
            progress: 100,
            error: res.message || "上传失败",
          });
          message.error(res.message || `「${file.name}」上传失败`);
        }
      } catch (e) {
        emitUploadTask({
          id,
          name: file.name,
          size: file.size,
          status: "error",
          progress: 100,
          error: e instanceof Error ? e.message : "上传失败",
        });
        message.error(`「${file.name}」上传失败`);
      }
    })();
  };

  // 注册为全局拖放目标：drop 时由 GlobalDropZone 直接上传到本 KB，完成后刷新列表
  useEffect(() => {
    setFileDropTarget({
      kbId,
      label: kb?.name ? `知识库：${kb.name}` : "当前知识库",
      onUploaded: () => {
        setDocPage(1);
        void load();
      },
    });
    return () => setFileDropTarget(null);
  }, [kbId, kb?.name, load]);

  return (
    <div style={{ height: "100%", display: "flex", overflow: "hidden", minHeight: 0 }}>
      {/* 左侧：文档目录树（多级，点选递归筛选） */}
      <div
        className="kb-doc-folder-sidebar"
        style={{
          width: 230,
          flex: "0 0 auto",
          borderRight: "1px solid var(--kb-border, rgba(5, 5, 5, 0.08))",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          padding: "10px 8px",
          boxSizing: "border-box",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8, padding: "0 4px" }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>文档目录</span>
          <Space size={4}>
            <Tooltip title="新建根目录">
              <Button size="small" type="text" icon={<PlusOutlined />} onClick={() => createFolder("")} />
            </Tooltip>
          </Space>
        </div>
        <div className="kb-doc-folder-tree" style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {/* 全部文档（根层级） */}
          <div
            className="kb-doc-folder-root"
            onClick={() => {
              setDocFolderId("");
              setDocPage(1);
            }}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "5px 6px",
              borderRadius: 4,
              cursor: "pointer",
              background: docFolderId === "" ? "var(--kb-primary-light, rgba(22,119,255,0.1))" : undefined,
              color: docFolderId === "" ? "#1677ff" : undefined,
            }}
          >
            <FolderOutlined style={{ color: "#faad14" }} />
            <span style={{ fontSize: 13 }}>全部文档</span>
          </div>
          {docFolders.length === 0 ? (
            <div style={{ padding: "12px 8px", color: "#999", fontSize: 12 }}>
              暂无目录，点击右上角 + 新建
            </div>
          ) : (
            <Tree
              blockNode
              showLine={{ showLeafIcon: false }}
              selectedKeys={docFolderId ? [docFolderId] : []}
              expandedKeys={folderExpandedKeys}
              onExpand={(keys) => setFolderExpandedKeys(keys as string[])}
              onSelect={(keys) => {
                const key = keys[0] as string | undefined;
                setDocFolderId(key || "");
                setDocPage(1);
              }}
              treeData={folderTreeData as any}
              style={{ background: "transparent" }}
            />
          )}
        </div>
      </div>

      {/* 右侧：文档内容区（筛选 + 列表） */}
      <div style={{ flex: 1, minWidth: 0, height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div className="kb-doc-area">
        <div className="kb-doc-filter-bar">
          <Input
            className="kb-doc-search-input"
            allowClear
            placeholder="搜索文档"
            prefix={<SearchOutlined style={{ color: "#bfbfbf" }} />}
            value={docKeyword}
            onChange={(e) => {
              setDocKeyword(e.target.value);
              setDocPage(1);
            }}
            onPressEnter={() => void load()}
          />
          <div className="kb-doc-filter-trailing">
            {/* 视图切换（对齐 .doc-view-toggle：28×24 图标按钮组） */}
            <div className="kb-doc-view-toggle" role="group">
              <Tooltip title="卡片视图">
                <button
                  type="button"
                  className={`kb-doc-view-toggle-btn${docView === "card" ? " active" : ""}`}
                  aria-pressed={docView === "card"}
                  onClick={() => setDocViewPersist("card")}
                >
                  <AppstoreOutlined />
                </button>
              </Tooltip>
              <Tooltip title="列表视图">
                <button
                  type="button"
                  className={`kb-doc-view-toggle-btn${docView === "list" ? " active" : ""}`}
                  aria-pressed={docView === "list"}
                  onClick={() => setDocViewPersist("list")}
                >
                  <UnorderedListOutlined />
                </button>
              </Tooltip>
            </div>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            <Button icon={<SettingOutlined />} onClick={() => setConfigOpen(true)}>
              知识库配置
            </Button>
            <Button
              type="primary"
              icon={<CloudUploadOutlined />}
              onClick={() => fileInputRef.current?.click()}
            >
              上传文档
            </Button>
            {/* 隐藏的文件选择触发器（本地上传） */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              style={{ display: "none" }}
              onChange={(e) => {
                const files = e.target.files;
                if (files && files.length > 0) {
                  // 对齐 WeKnora：先收集到确认弹窗，用户可选处理配置后确认上传
                  setPendingUploadFiles((prev) => [...prev, ...Array.from(files)]);
                  setUploadConfirmOpen(true);
                }
                e.target.value = "";
              }}
            />
          </div>
          <div className="kb-doc-filter-fields">
            <div className="kb-doc-filter-field">
              <Select
                className="kb-doc-filter-control"
                allowClear
                placeholder="解析状态"
                value={docStatus || undefined}
                onChange={(v) => {
                  setDocStatus(v || "");
                  setDocPage(1);
                  void load();
                }}
                options={["PENDING", "PARSING", "EMBEDDING", "READY", "FAILED"].map((s) => ({
                  label: STATE_LABEL[s] || s,
                  value: s,
                }))}
              />
            </div>
            <div className="kb-doc-filter-field">
              <Select
                className="kb-doc-filter-control"
                allowClear
                placeholder="文件类型"
                value={docType || undefined}
                onChange={(v) => {
                  setDocType(v || "");
                  setDocPage(1);
                  void load();
                }}
                options={["md", "pdf", "docx", "xlsx", "pptx", "txt", "epub"].map((t) => ({
                  label: t.toUpperCase(),
                  value: t,
                }))}
              />
            </div>
          </div>
        </div>
        {selectedList.length > 0 && (
          <Space
            style={{
              display: "flex",
              marginBottom: 12,
              padding: "8px 12px",
              background: "#e6f4ff",
              borderRadius: 8,
            }}
          >
            <Typography.Text strong>
              已选 {selectedList.length} 个文档
            </Typography.Text>
            <Button size="small" icon={<RedoOutlined />} onClick={() => void onBatchReparse()}>
              批量重建
            </Button>
            <Button size="small" icon={<FolderOutlined />} onClick={openMove}>
              移至目录
            </Button>
            <Button size="small" danger icon={<DownloadOutlined />} onClick={onBatchDelete}>
              批量删除
            </Button>
            <Button size="small" type="link" onClick={() => setSelectedDocs(new Set())}>
              取消选择
            </Button>
          </Space>
        )}
        {docView === "card" ? (
          <>
            <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              <DocCardView
                items={docs}
                onOpen={setDetailDoc}
                onDownload={onDownloadDoc}
                onReparse={onReparseDoc}
                onDelete={onDeleteDoc}
                onTrace={onViewTrace}
              />
            </div>
            {/* 卡片视图分页(与列表视图同源 docPage/docPageSize)，统一用 ModoPagination 对齐展示 */}
            <ModoPagination
              current={docPage}
              pageSize={docPageSize}
              total={docTotal}
              showTotal={(t) => `共 ${t} 个文档`}
              onChange={(p, ps) => {
                setDocPage(p);
                setDocPageSize(ps);
              }}
            />
          </>
        ) : (
          <>
            <ModoTable<DocItem>
              rowKey="id"
              size="small"
              loading={docsLoading}
              dataSource={docs}
              rowSelection={{
                selectedRowKeys: [...selectedDocs],
                onChange: (keys) => setSelectedDocs(new Set(keys as string[])),
              }}
              onRow={(doc) => ({ onClick: () => setDetailDoc(doc) })}
              locale={{ emptyText: <Empty description="暂无文档，拖拽文件到右上角上传" /> }}
              columns={[
                {
                  title: "文件名",
                  dataIndex: "file_name",
                  render: (v: string, doc: DocItem) => (
                    <Space>
                      <span style={{ fontSize: 16 }}>{fileTypeIcon(doc.file_ext)}</span>
                      <a onClick={() => setDetailDoc(doc)}>{v}</a>
                    </Space>
                  ),
                },
                {
                  title: "状态",
                  dataIndex: "parse_state",
                  render: (v: string, doc: DocItem) => (
                    <Space size={4}>
                      {["PENDING", "PARSING", "EMBEDDING"].includes(v) && <Spin size="small" />}
                      <Tag color={PARSE_STATE_COLOR[v] || "default"}>{STATE_LABEL[v] || v}</Tag>
                      {v === "FAILED" && (
                        <a onClick={() => onViewTrace(doc)}>查看原因</a>
                      )}
                    </Space>
                  ),
                },
                {
                  title: "Wiki 构建",
                  dataIndex: "wiki_build",
                  width: 150,
                  render: (v: DocItem["wiki_build"], doc: DocItem) => {
                    if (!v) return <span style={{ color: "#bbb", fontSize: 12 }}>-</span>;
                    const dur = v.duration_ms ? `${Math.round(v.duration_ms / 60000)}min` : "";
                    const colorMap: Record<string, string> = {
                      SUCCESS: "green",
                      FAILED: "red",
                      RUNNING: "blue",
                      PENDING: "default",
                    };
                    const labelMap: Record<string, string> = {
                      SUCCESS: "成功",
                      FAILED: "失败",
                      RUNNING: "构建中",
                      PENDING: "排队中",
                    };
                    return (
                      <Space size={4}>
                        {v.state === "RUNNING" && <Spin size="small" />}
                        <Tag color={colorMap[v.state] || "default"}>{labelMap[v.state] || v.state}</Tag>
                        {dur && <span style={{ fontSize: 12, color: "#888" }}>{dur}</span>}
                        {(v.state === "FAILED" || v.state === "SUCCESS") && (
                          <a style={{ fontSize: 12 }} onClick={() => onViewTrace(doc)}>
                            详情
                          </a>
                        )}
                      </Space>
                    );
                  },
                },
                {
                  title: "分块数",
                  dataIndex: "chunk_count",
                  width: 90,
                },
                {
                  title: "AI 摘要",
                  dataIndex: "summary",
                  ellipsis: true,
                  render: (v: string | null | undefined, doc: DocItem) =>
                    v ? v : doc.summary_status === "FAILED" ? (
                      <Typography.Text type="danger" style={{ fontSize: 12 }}>
                        生成失败
                      </Typography.Text>
                    ) : doc.parse_state === "READY" ? (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        —
                      </Typography.Text>
                    ) : null,
                },
                {
                  title: "大小",
                  dataIndex: "file_size",
                  width: 110,
                  render: (v: number | null) => formatSize(v),
                },
                {
                  title: "操作",
                  width: 150,
                  render: (_: unknown, doc: DocItem) => (
                    <Space size={4}>
                      <Button type="link" size="small" icon={<DownloadOutlined />} onClick={(e) => { e.stopPropagation(); void onDownloadDoc(doc); }}>
                        下载
                      </Button>
                      <Button type="link" size="small" icon={<RedoOutlined />} onClick={(e) => { e.stopPropagation(); void onReparseDoc(doc); }}>
                        重解析
                      </Button>
                      <Button type="link" danger size="small" onClick={(e) => { e.stopPropagation(); void onDeleteDoc(doc); }}>
                        删除
                      </Button>
                    </Space>
                  ),
                },
              ]}
            />
            <ModoPagination
              current={docPage}
              pageSize={docPageSize}
              total={docTotal}
              showTotal={(t) => `共 ${t} 个文档`}
              onChange={(p, ps) => {
                setDocPage(p);
                setDocPageSize(ps);
              }}
            />
          </>
        )}
      </div>
      </div>
      {/* 右侧内容区结束: 上面的 </div> 闭合 kb-doc-area, 这句闭合右侧 flex wrapper */}

      {/* 上传确认弹窗（对齐 WeKnora：文件列表 + 处理配置，确认后逐文件上传） */}
      <UploadConfirmDialog
        open={uploadConfirmOpen}
        initialFiles={pendingUploadFiles}
        onCancel={() => {
          setUploadConfirmOpen(false);
          setPendingUploadFiles([]);
        }}
        onConfirm={({ files: filesToUpload, processConfig }) => {
          setUploadConfirmOpen(false);
          setPendingUploadFiles([]);
          for (const f of filesToUpload) {
            startDocUpload(f, undefined, docFolderId, processConfig);
          }
        }}
      />

      {/* 文档详情抽屉（对齐 WeKnora DocContent：元数据 + AI 摘要展示 + 三视图 + 下载/重解析/删除） */}
      <DocDetailDrawer
        kbId={kbId}
        doc={detailDoc}
        onClose={() => setDetailDoc(null)}
        onDownload={onDownloadDoc}
        onReparse={onReparseDoc}
        onDelete={onDeleteDoc}
      />

      {/* 知识库配置（WeKnora 对齐：索引开关/类型/技能绑定/模型绑定/图谱/FAQ + 切片配置） */}
      <KBConfigModal
        kb={kb}
        open={configOpen}
        onClose={(changed) => {
          setConfigOpen(false);
          if (changed) void loadKb();
        }}
      />

      {/* 构建过程抽屉：文档列表就地查看 wiki 构建执行轨迹/LLM 明细/日志 */}
      <DocBuildProcessDrawer
        visible={!!buildDoc}
        jobId={
          buildDoc?.wiki_build?.job_id || (buildDoc ? `DOC_${buildDoc.id}` : null)
        }
        docTitle={buildDoc?.file_name}
        onClose={() => setBuildDoc(null)}
      />

      {/* 移至目录（批量移动文档到目录） */}
      <Modal
        title="移至目录"
        open={moveOpen}
        onCancel={() => setMoveOpen(false)}
        onOk={() => void confirmMove()}
        okText="移动"
        okButtonProps={{ loading: moveBusy }}
        width={400}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            已选 {selectedList.length} 个文档；选择目标目录（不选 = 移出所有目录到根层级）
          </Typography.Text>
          <Select
            style={{ width: "100%" }}
            allowClear
            placeholder="选择目录（留空 = 根层级）"
            value={moveFolderId || undefined}
            onChange={(v) => setMoveFolderId(v || "")}
            options={folderOptions}
          />
        </Space>
      </Modal>
    </div>
  );
}
