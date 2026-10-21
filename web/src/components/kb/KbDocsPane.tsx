"use client";

/**
 * 知识库 · 文档面板（KbDocsPane）
 *
 * 从 kbs/[id]/page.tsx 抽出，供「动态选项卡」与详情路由复用：
 * KB 概览 + 文档工具条（筛选/上传）+ 文档卡片视图/表格 + 分页。
 * 文档详情不再内置抽屉，点击文件时通过 onOpenDoc 回调交由父级以动态选项卡打开。
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  App,
  Button,
  Card,
  Dropdown,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  AppstoreOutlined,
  CloudUploadOutlined,
  DeleteOutlined,
  DownOutlined,
  DownloadOutlined,
  EditOutlined,
  FolderAddOutlined,
  FolderOutlined,
  PlusOutlined,
  ProfileOutlined,
  RedoOutlined,
  ReloadOutlined,
  TableOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import DocCardView, {
  STATE_LABEL,
  docStageTag,
  fileTypeIcon,
} from "@/components/DocCardView";
import ModoTable from "@/components/biz/modo-table";
import ModoButton from "@/components/biz/modo-button";
import ModoSelect from "@/components/biz/modo-select";
import KbsSimplePagination from "@/components/kb/KbsSimplePagination";
import KbsEmpty from "@/components/kb/KbsEmpty";
import { CaretDownIcon, CaretRightIcon, FolderIcon, SearchColorIcon } from "@/components/kb/KbsIcons";
import UploadConfirmDialog, { UploadProcessConfig } from "@/components/UploadConfirmDialog";
import SelectTableDialog from "@/components/SelectTableDialog";
import DocBuildProcessDrawer from "@/components/DocBuildProcessDrawer";
import ReparseConfigDialog from "@/components/ReparseConfigDialog";
import {
  apiCreateDocFolder,
  apiDeleteDocFolder,
  apiDeleteDocument,
  apiDownloadDocument,
  apiErrorMessage,
  apiGetKb,
  apiGetSupportedFileTypes,
  apiListBuildEngines,
  apiListDocFolders,
  apiListDocuments,
  apiMoveDocumentsToFolder,
  apiReparseDocument,
  apiUploadArchive,
  apiUploadDocumentWithProgress,
  BuildEngineItem,
  DocFolderItem,
  DocItem,
  KbItem,
} from "@/lib/api";
import { emitUploadTask, makeUploadTaskId, setFileDropTarget } from "@/lib/upload-bus";
import { createDocFolderEnsurer } from "@/lib/doc-folder-ensure";
import { collectDirFiles, isDirectoryUpload, relativePathOf } from "@/lib/upload-dir";
import {
  ARCHIVE_EXTS,
  DEFAULT_SUPPORTED_EXTS,
  acceptAttr,
  isArchiveFileName,
  isSupportedFileName,
  partitionBySupport,
  supportedLabel,
} from "@/lib/upload-support";

/** 目录上传文件数超过该值时先二次确认（避免误选巨型目录后批量入队解析） */
const DIR_UPLOAD_CONFIRM_THRESHOLD = 200;

export interface KbDocsPaneProps {
  kbId: string;
  /** 父级刷新信号：值变化时重新拉取文档列表与目录（用于详情页签中删除/重解析后同步） */
  refreshTick?: number;
  /** 打开文档详情（由父级以动态选项卡承载，不再用抽屉） */
  onOpenDoc: (doc: DocItem) => void;
  /** 文档列表变化回调（父级用于同步已打开详情页签的 doc 数据，如自动摘要回填） */
  onDocsChange?: (docs: DocItem[]) => void;
  /** 单个文档被删除后回调（父级关闭对应详情页签） */
  onDocRemoved?: (docId: string) => void;
  /**
   * 锁定到指定根目录（2026-10-21 语义层页面复用）：
   * 语义层「元数据」「文档库」页只展示电信语义层知识库对应根目录下的数据，
   * 传入该目录 id 后：左侧目录树只渲染该目录及其子树、默认选中该目录，
   * 文档列表固定按该目录过滤（切换到其他目录的入口被移除）。
   */
  lockedFolderId?: string;
}

export default function KbDocsPane({
  kbId,
  refreshTick,
  onOpenDoc,
  onDocsChange,
  onDocRemoved,
  lockedFolderId,
}: KbDocsPaneProps) {
  const { message, modal } = App.useApp();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // 目录上传（webkitdirectory）隐藏选择器
  const dirInputRef = useRef<HTMLInputElement | null>(null);
  // 所在页签可见性：antd Tabs 默认不销毁隐藏 pane，隐藏时应暂停轮询/摘要，
  // 否则多开页签会各自在后台持续打接口（文档解析中每 3s 一轮）。
  const paneRef = useRef<HTMLDivElement | null>(null);
  const [paneActive, setPaneActive] = useState(true);

  const [kb, setKb] = useState<KbItem | null>(null);
  // 构建服务下拉数据源（服务编排中已发布的编排）
  const [buildEngines, setBuildEngines] = useState<BuildEngineItem[]>([]);
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [docsLoading, setDocsLoading] = useState(false);
  const [docPage, setDocPage] = useState(1);
  const [docPageSize, setDocPageSize] = useState(20);
  const [docTotal, setDocTotal] = useState(0);
  const [docKeyword, setDocKeyword] = useState("");
  const [docStatus, setDocStatus] = useState("");
  const [docType, setDocType] = useState("");
  // 文档多级目录（doc_folder）：选中目录 id（"" = 全部/根），递归子树浏览。
  // 2026-10-21 锁定模式（lockedFolderId 非空）：初始即选中锁定目录，且列表固定按它过滤
  const [docFolders, setDocFolders] = useState<DocFolderItem[]>([]);
  const [docFolderId, setDocFolderId] = useState(lockedFolderId || "");
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

  // 上传确认弹窗（对齐 WeKnora：选文件 → 弹确认框可选处理配置 → 确认后上传）
  const [pendingUploadFiles, setPendingUploadFiles] = useState<File[]>([]);
  const [uploadConfirmOpen, setUploadConfirmOpen] = useState(false);
  // 添加表弹窗（表→wiki：从数据源选维度表绑定到知识库）
  const [selectTableOpen, setSelectTableOpen] = useState(false);
  const [buildDoc, setBuildDoc] = useState<DocItem | null>(null);
  // 重新解析配置弹窗（2026-10-14：重解析前可选处理配置，对齐 WeKnora 重解析换配置）
  const [reparseDoc, setReparseDoc] = useState<DocItem | null>(null);
  // 系统可处理的格式白名单（2026-10-20）：上传 accept / 预校验与后端格式门禁同源，
  // 拉取失败回落 lib/upload-support 的本地兜底清单
  const [supportedExts, setSupportedExts] = useState<string[]>(DEFAULT_SUPPORTED_EXTS);

  // 加载知识库详情（含 WeKnora 对齐配置：索引开关/技能绑定/模型绑定等）
  const loadKb = useCallback(async () => {
    const res = await apiGetKb(kbId).catch(() => null);
    if (res?.success && res.data) setKb(res.data);
  }, [kbId]);

  // 构建服务下拉（上传/选表可重选覆盖 KB 默认；加载失败不阻断）
  const loadBuildEngines = useCallback(async () => {
    const res = await apiListBuildEngines().catch(() => null);
    if (res?.success && res.data) setBuildEngines(res.data.items || []);
  }, []);

  // 上传白名单：系统只能处理这些格式（与后端格式门禁同源；后端也会拦，这里先给前端提示）
  useEffect(() => {
    void (async () => {
      try {
        const res = await apiGetSupportedFileTypes();
        if (res.success && res.data?.types?.length) setSupportedExts(res.data.types);
      } catch {
        // 拉取失败沿用本地兜底清单，不阻断上传
      }
    })();
  }, []);

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

  // 目录树渲染（对齐知识库管理页 left tree：36px 行 / 12px 字 / 14px 图标槽，
  // hover 时文件夹图标让位给展开箭头、叶子节点图标常显），支持多级递归 + 逐层展开。
  // 原为 antd <Tree> + showLine（观感与知识库管理页不一致），改为自绘行复用 kb-doc-tree-* 样式族。
  const toggleFolderExpanded = (id: string) => {
    setFolderExpandedKeys((prev) =>
      prev.includes(id) ? prev.filter((k) => k !== id) : [...prev, id],
    );
  };

  /**
   * 递归渲染目录节点。默认 parentId=""/0 = 全部根目录；
   * 锁定模式（asRoot=true，语义层页）渲染「指定目录 + 其子树」，
   * 传入 lockedFolderId 即只见该目录分支，不出现兄弟根目录。
   */
  const renderFolderNodes = (parentId: string, depth: number, asRoot = false): ReactNode => {
    const items = docFolders.filter((f) => (f.parent_id || "") === parentId);
    if (asRoot && items.length === 0 && parentId) {
      // 锁定目录尚未加载（首次渲染时 docFolders 可能为空）：占位空态
      return <div className="kb-doc-folder-empty">目录加载中…</div>;
    }
    return items
      .map((f) => {
        const children = docFolders.filter((x) => x.parent_id === f.id);
        const expanded = folderExpandedKeys.includes(f.id);
        const isLeaf = children.length === 0;
        return (
          <div key={f.id}>
            <div
              className={`kb-doc-tree-row${docFolderId === f.id ? " active" : ""}`}
              style={{ paddingLeft: 8 + depth * 12, paddingRight: 4 }}
              title={f.name}
              onClick={() => {
                setDocFolderId(f.id);
                setDocPage(1);
              }}
            >
              {/* 图标槽 14px：文件夹图标默认显示，hover 时由展开箭头接替（DSB .icon-container 同款） */}
              <span className="kb-doc-tree-icon">
                <span
                  className={`kb-doc-tree-node-icon${isLeaf ? " kb-doc-tree-node-icon--visible" : ""}`}
                >
                  <FolderIcon />
                </span>
                {!isLeaf && (
                  <span
                    className="kb-doc-tree-expand-icon"
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleFolderExpanded(f.id);
                    }}
                  >
                    {expanded ? <CaretDownIcon /> : <CaretRightIcon />}
                  </span>
                )}
              </span>
              <span className="kb-doc-tree-title">{f.name}</span>
              {(f.doc_total ?? 0) > 0 && <span className="kb-doc-tree-count">{f.doc_total}</span>}
              <span className="kb-doc-folder-actions">
                <Tooltip title="新建子目录">
                  <FolderAddOutlined
                    onClick={(e) => {
                      e.stopPropagation();
                      createFolder(f.id);
                    }}
                  />
                </Tooltip>
                <Tooltip title="重命名">
                  <EditOutlined
                    onClick={(e) => {
                      e.stopPropagation();
                      renameFolder(f);
                    }}
                  />
                </Tooltip>
                <Tooltip title="删除">
                  <DeleteOutlined
                    style={{ color: "#ff4d4f" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteFolder(f);
                    }}
                  />
                </Tooltip>
              </span>
            </div>
            {expanded && renderFolderNodes(f.id, depth + 1)}
          </div>
        );
      });
  };

  useEffect(() => {
    void loadKb();
  }, [loadKb]);

  useEffect(() => {
    void loadBuildEngines();
  }, [loadBuildEngines]);

  const load = useCallback(async () => {
    setDocsLoading(true);
    try {
      const res = await apiListDocuments(kbId, docPage, docPageSize, {
        keyword: docKeyword.trim() || undefined,
        parseStatus: docStatus || undefined,
        fileType: docType || undefined,
        // 锁定模式（语义层页）：固定按锁定目录过滤，不随左侧树选中变化
        folderId: (lockedFolderId || docFolderId) || undefined,
      });
      if (res.success) {
        setDocs(res.data?.items || []);
        setDocTotal(res.data?.total ?? 0);
      }
    } finally {
      setDocsLoading(false);
    }
  }, [kbId, docPage, docPageSize, docKeyword, docStatus, docType, docFolderId, lockedFolderId]);

  useEffect(() => {
    if (!paneActive) return;
    void load();
  }, [load, paneActive]);

  useEffect(() => {
    void loadDocFolders();
  }, [loadDocFolders]);

  // 父级刷新信号：refreshTick 变化时重新拉取文档列表与目录。
  // 用 useRef 记录上次值，避免挂载时（值未变）重复加载。
  const lastRefreshTick = useRef(refreshTick);
  useEffect(() => {
    if (refreshTick === undefined || lastRefreshTick.current === refreshTick) return;
    lastRefreshTick.current = refreshTick;
    void load();
    void loadDocFolders();
  }, [refreshTick, load, loadDocFolders]);

  // 文档列表变化后通知父级（同步已打开详情页签的 doc 数据）。
  // 用 ref 保存回调，effect 仅依赖 docs，避免父级内联函数每次渲染触发循环。
  const onDocsChangeRef = useRef(onDocsChange);
  onDocsChangeRef.current = onDocsChange;
  useEffect(() => {
    onDocsChangeRef.current?.(docs);
  }, [docs]);

  // 页签可见性守卫：隐藏页签（display:none，rect 为零 → 不交叉）时置 false，停掉轮询与摘要
  useEffect(() => {
    const el = paneRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((ents) => {
      setPaneActive(ents.some((e) => e.isIntersecting));
    });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // Auto-poll while any document is still being parsed (PENDING/PARSING/EMBEDDING).
  const hasInFlight = docs.some((d) =>
    ["PENDING", "PARSING", "EMBEDDING"].includes(d.parse_state),
  );
  useEffect(() => {
    if (!hasInFlight || !paneActive) return;
    const timer = setInterval(() => {
      void load();
    }, 3000);
    return () => clearInterval(timer);
  }, [hasInFlight, paneActive, load]);

// 注：文档摘要统一由摘要编排（Stuff/Map-Reduce/Refine）生成 wiki 摘要页，
  // 不再走直连 LLM（generate_document_summary 已移除，2026-10-14）。
  // 打开中的详情页签由 onDocsChange 文档列表同步机制回填展示。

  const onDeleteDoc = (doc: DocItem) => {
    // 2026-10-12: 单文档删除此前无确认动作，直接调 API（不可恢复：级联清分块+向量）。
    // 补齐与批量删除一致的确认弹窗，防止误点/误触。
    modal.confirm({
      title: `删除文档「${doc.file_name}」？`,
      content: "删除后不可恢复（关联分块与向量一并清理）。",
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await apiDeleteDocument(kbId, doc.id);
        if (res.success) {
          message.success("文档已删除");
          onDocRemoved?.(doc.id);
          // 删除当前页最后一条且不在第 1 页时回退一页，避免空页
          if (docs.length === 1 && docPage > 1) setDocPage(docPage - 1);
          else void load();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  const onDownloadDoc = async (doc: DocItem) => {
    try {
      await apiDownloadDocument(kbId, doc.id, doc.file_name);
      message.success("开始下载");
    } catch (e) {
      message.error((e as Error).message || "下载失败");
    }
  };

  const onReparseDoc = (doc: DocItem) => {
    // 2026-10-14：改为先弹处理配置表单（切片/解析引擎/构建配置），确认后带配置重解析
    setReparseDoc(doc);
  };

  const handleReparseConfirm = async (result: { processConfig: Record<string, unknown> | null; buildTapeId?: string }) => {
    const doc = reparseDoc;
    if (!doc) return;
    setReparseDoc(null);
    const body: { process_config?: string; build_tape_id?: string } = {};
    if (result.processConfig && Object.keys(result.processConfig).length > 0) {
      body.process_config = JSON.stringify(result.processConfig);
    }
    if (result.buildTapeId) body.build_tape_id = result.buildTapeId;
    const res = await apiReparseDocument(kbId, doc.id, body);
    if (res.success) {
      message.success("已重新入队解析（按所选配置）");
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

  /**
   * 单文件上传（Promise 版）：进度上报到上传任务浮层，返回是否成功。
   * 目录上传需要按文件串行 await 并复用同一套进度/重试语义，故从 startDocUpload 拆出。
   */
  const uploadSingleDoc = async (
    file: File,
    taskId: string,
    folderId: string,
    processConfig?: UploadProcessConfig | null,
    buildTapeId?: string,
    opts: { silent?: boolean } = {},
  ): Promise<boolean> => {
    emitUploadTask({
      id: taskId,
      name: file.name,
      size: file.size,
      status: "uploading",
      progress: 0,
      retry: () => void uploadSingleDoc(file, taskId, folderId, processConfig, buildTapeId, opts),
    });
    try {
      const res = await apiUploadDocumentWithProgress(
        kbId,
        file,
        (pct) => {
          emitUploadTask({ id: taskId, name: file.name, size: file.size, status: "uploading", progress: pct });
        },
        folderId || undefined,
        processConfig as Record<string, unknown> | null | undefined,
        buildTapeId,
      );
      if (res.success) {
        emitUploadTask({ id: taskId, name: file.name, size: file.size, status: "success", progress: 100 });
        if (!opts.silent) message.success(`「${file.name}」已上传，后台解析中`);
        return true;
      }
      emitUploadTask({
        id: taskId,
        name: file.name,
        size: file.size,
        status: "error",
        progress: 100,
        error: res.message || "上传失败",
      });
      if (!opts.silent) message.error(res.message || `「${file.name}」上传失败`);
      return false;
    } catch (e) {
      const msg = e instanceof Error ? e.message : "上传失败";
      emitUploadTask({
        id: taskId,
        name: file.name,
        size: file.size,
        status: "error",
        progress: 100,
        error: msg,
      });
      if (!opts.silent) message.error(`「${file.name}」上传失败`);
      return false;
    }
  };

  // 带进度上报的知识库文档上传（本地上传 & 全局拖放复用；经 uploadTask 事件驱动任务浮层）
  const startDocUpload = (
    file: File,
    taskId?: string,
    folderId = docFolderId,
    processConfig?: UploadProcessConfig | null,
    buildTapeId?: string,
  ) => {
    const id = taskId ?? makeUploadTaskId("kb-upload");
    void (async () => {
      const ok = await uploadSingleDoc(file, id, folderId, processConfig, buildTapeId);
      if (ok) {
        // 上传成功后回到第一页并重新查询列表，立即展示新文档
        setDocPage(1);
        void load();
      }
    })();
  };

  /**
   * 目录上传：取出目录及子孙目录中的文件落库（2026-10-17）。
   *
   * - mirror=true：按相对路径在知识库内创建/复用文档目录（**保留完整层级含顶层所选
   *   目录**，2026-10-20 用户确认），再把文件传到对应目录；
   * - mirror=false：全部文件直接传到当前所选目录。
   * 逐个串行上传（大目录并发会把存储与解析队列同时打满），聚合进度用同 key 的
   * message 就地更新，单文件明细仍进上传任务浮层。
   */
  const uploadDirectoryBatch = async (
    files: File[],
    options: { processConfig?: UploadProcessConfig | null; buildEngine?: string; mirror: boolean },
  ) => {
    const { entries, hiddenSkipped, emptySkipped } = collectDirFiles(files);
    if (entries.length === 0) {
      message.warning("所选目录内没有可上传的文件");
      return;
    }
    const skipped = hiddenSkipped + emptySkipped;
    const baseFolderId = docFolderId;
    const progressKey = "kb-dir-upload";
    // 目录映射（同级同名复用 + 缺失层级创建）：与全局拖放目录共用 doc-folder-ensure
    const folderEnsurer = options.mirror
      ? await createDocFolderEnsurer(kbId, baseFolderId).catch(() => null)
      : null;

    /** 逐级确保目录存在，返回末级目录 id（同级同名复用，缺失的依次创建） */
    const ensureFolder = async (dirs: string[]): Promise<string> => {
      if (!folderEnsurer) throw new Error("目录映射不可用");
      return folderEnsurer.ensure(dirs);
    };

    const total = entries.length;
    let ok = 0;
    let fail = 0;
    let folderFail = 0;
    let archives = 0;
    message.open({
      key: progressKey,
      type: "loading",
      content: `正在上传目录内 ${total} 个文件…`,
      duration: 0,
    });
    for (let i = 0; i < total; i += 1) {
      const { file, dirs } = entries[i];
      let targetFolderId = baseFolderId;
      if (options.mirror && dirs.length > 0) {
        try {
          targetFolderId = await ensureFolder(dirs);
        } catch (e) {
          folderFail += 1;
          console.error("ensure doc folder failed", e);
          continue;
        }
      }
      // zip 压缩包（2026-10-21）：服务端自动解包，解包内容落到 zip 所在目录下
      // （mirror 关闭时落到当前所选目录，与「平铺上传」一致）
      if (isArchiveFileName(file.name)) {
        archives += 1;
        message.open({
          key: progressKey,
          type: "loading",
          content: `正在解包 ${i + 1}/${total}：${relativePathOf(file) || file.name}`,
          duration: 0,
        });
        const done = await handleArchiveUpload(file, targetFolderId);
        if (done) ok += 1;
        else fail += 1;
        continue;
      }
      message.open({
        key: progressKey,
        type: "loading",
        content: `正在上传 ${i + 1}/${total}：${relativePathOf(file) || file.name}`,
        duration: 0,
      });
      const done = await uploadSingleDoc(
        file,
        makeUploadTaskId("kb-dir"),
        targetFolderId,
        options.processConfig,
        options.buildEngine,
        { silent: true },
      );
      if (done) ok += 1;
      else fail += 1;
    }

    const parts: string[] = [];
    if (ok > 0) {
      parts.push(archives > 0 ? `成功 ${ok} 个（含 ${archives} 个压缩包已解包）` : `成功 ${ok} 个`);
    }
    if (fail > 0) parts.push(`失败 ${fail} 个`);
    if (folderFail > 0) parts.push(`目录创建失败未上传 ${folderFail} 个`);
    if (skipped > 0) parts.push(`跳过隐藏/空文件 ${skipped} 个`);
    message.open({
      key: progressKey,
      type: ok > 0 && fail === 0 && folderFail === 0 ? "success" : "warning",
      content: `目录上传完成：${parts.join("，") || "无文件"}`,
      duration: 5,
    });
    if (ok > 0) {
      setDocPage(1);
      await loadDocFolders();
      void load();
    }
  };

  /**
   * 上传 zip 压缩包（服务端**自动解包**，2026-10-20）：
   * 包内目录结构由后端还原到指定目录下（同级同名复用），逐个文件入队解析；
   * 进度/结果用同 key 的 message 就地更新，完成后刷新列表与目录树。
   * @param folderId 解包目标目录（缺省 = 当前所选目录；文件夹内 zip 传 zip 所在子目录）
   */
  const handleArchiveUpload = async (file: File, folderId?: string): Promise<boolean> => {
    if (!isArchiveFileName(file.name)) {
      message.warning(`仅支持 zip 压缩包自动解包（当前文件：${file.name}）`);
      return false;
    }
    const progressKey = `kb-archive-upload-${file.name}`;
    message.open({
      key: progressKey,
      type: "loading",
      content: `正在上传并解包「${file.name}」…`,
      duration: 0,
    });
    const res = await apiUploadArchive(
      kbId,
      file,
      (pct) =>
        message.open({
          key: progressKey,
          type: "loading",
          content: `正在上传并解包「${file.name}」… ${pct}%`,
          duration: 0,
        }),
      folderId || docFolderId || undefined,
    ).catch(() => null);
    if (!res) {
      message.open({ key: progressKey, type: "error", content: "压缩包上传失败", duration: 6 });
      return false;
    }
    if (!res.success || !res.data) {
      message.open({
        key: progressKey,
        type: "error",
        content: apiErrorMessage(res, "压缩包上传失败"),
        duration: 8,
      });
      return false;
    }
    const d = res.data;
    const detail: string[] = [];
    if (d.skipped.unsupported > 0) {
      const types = d.unsupported_types.filter(Boolean).map((t) => `.${t}`).join(" ");
      detail.push(`${d.skipped.unsupported} 个格式不支持${types ? `（${types}）` : ""}`);
    }
    if (d.skipped.hidden > 0) detail.push(`${d.skipped.hidden} 个隐藏/系统文件`);
    if (d.skipped.empty > 0) detail.push(`${d.skipped.empty} 个空文件`);
    if (d.skipped.oversize > 0) detail.push(`${d.skipped.oversize} 个超过单文件上限`);
    if (d.skipped.failed > 0) detail.push(`${d.skipped.failed} 个处理失败`);
    const suffix = detail.length > 0 ? `；跳过 ${detail.join("、")}` : "";
    message.open({
      key: progressKey,
      type: d.created_count > 0 ? "success" : "warning",
      content:
        d.created_count > 0
          ? `压缩包已解包：上传 ${d.created_count} 个文件${
              d.folders_created > 0 ? `、新建 ${d.folders_created} 个目录` : ""
            }${suffix}`
          : `压缩包内没有可上传的文件${suffix}`,
      duration: 8,
    });
    if (d.created_count > 0) {
      setDocPage(1);
      await loadDocFolders();
      void load();
    }
    return d.created_count > 0;
  };

  /** 目录选择（webkitdirectory）回调：先按格式白名单分拣（zip 保留，确认后自动解包），再进上传确认弹窗 */
  const handleDirPicked = (files: File[]) => {
    if (files.length === 0) return;
    const { entries, hiddenSkipped, emptySkipped } = collectDirFiles(files);
    // 只保留系统能解析的格式 + zip 压缩包（zip 由 uploadDirectoryBatch 分流服务端解包，
    // 2026-10-21 起文件夹内的压缩包不再跳过）
    const keptNames = new Set(
      entries
        .filter(
          (e) =>
            isSupportedFileName(e.file.name, supportedExts) || isArchiveFileName(e.file.name),
        )
        .map((e) => e.file.name),
    );
    const validEntries = entries.filter((e) => keptNames.has(e.file.name));
    const unsupportedSkipped = entries.length - validEntries.length;
    if (validEntries.length === 0) {
      message.warning(
        `所选目录内没有可上传的文件（已跳过 ${hiddenSkipped + emptySkipped} 个隐藏/空文件` +
          `${unsupportedSkipped > 0 ? `、${unsupportedSkipped} 个格式不支持` : ""}）`,
      );
      return;
    }
    const skipped = hiddenSkipped + emptySkipped + unsupportedSkipped;
    const open = () => {
      setPendingUploadFiles(validEntries.map((e) => e.file));
      setUploadConfirmOpen(true);
    };
    if (validEntries.length > DIR_UPLOAD_CONFIRM_THRESHOLD) {
      modal.confirm({
        title: `所选目录包含 ${validEntries.length} 个文件`,
        content: `将按目录结构逐个上传并解析${
          skipped > 0 ? `（另有 ${skipped} 个隐藏/空/格式不支持的文件已跳过）` : ""
        }，是否继续？`,
        okText: "继续上传",
        onOk: open,
      });
      return;
    }
    if (skipped > 0) {
      message.warning(
        `已跳过 ${skipped} 个隐藏/空/格式不支持的文件；系统可处理的格式：${supportedLabel(supportedExts)}`,
      );
    }
    open();
  };

  // 注册为全局拖放目标：drop 时由 GlobalDropZone 直接上传到本 KB（拖入目录按子目录建目录），
  // 完成后刷新列表与目录树（新增目录要在左树可见）
  useEffect(() => {
    setFileDropTarget({
      kbId,
      folderId: docFolderId || undefined,
      label: kb?.name ? `知识库：${kb.name}` : "当前知识库",
      onUploaded: () => {
        setDocPage(1);
        void load();
        void loadDocFolders();
      },
    });
    return () => setFileDropTarget(null);
  }, [kbId, kb?.name, docFolderId, load, loadDocFolders]);

  return (
    <div ref={paneRef} style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      {/* 白底直角内容面板（标题由页签承担）：左侧目录树贴面板边缘，右侧内容区按系统内边距 12/16 排布 */}
      <Card
        style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
        styles={{ body: { flex: 1, minHeight: 0, display: "flex", overflow: "hidden", padding: 0 } }}
      >
      {/* 左侧：文档目录树（多级，点选递归筛选）
          样式对齐知识库管理页左树：复用 kb-doc-tree-* 样式族（36px 行 / 14px 图标槽 /
          Hover 展开箭头 / 选中 primary-light 底），宽度与右边框同管理页 sider */}
      <div className="kb-doc-folder-sidebar">
        <div className="kb-doc-folder-header">
          <span className="kb-doc-folder-header-title">文档目录</span>
          <Space size={4}>
            <Tooltip title="新建根目录">
              <Button size="small" type="text" icon={<PlusOutlined />} onClick={() => createFolder("")} />
            </Tooltip>
          </Space>
        </div>
        <div className="kb-doc-folder-tree">
          {/* 锁定模式（语义层页）：不显示「全部文档」根节点与兄弟根目录，
              只渲染锁定目录及其子树；点目录仍可切换锁内子目录 */}
          {lockedFolderId ? (
            renderFolderNodes(lockedFolderId, 0, true)
          ) : (
            <>
              {/* 全部文档（根层级，对齐知识库管理页「全部」一级节点） */}
              <div
                className={`kb-doc-tree-row kb-doc-tree-row--root${docFolderId === "" ? " active" : ""}`}
                style={{ paddingLeft: 8 }}
                title="全部文档"
                onClick={() => {
                  setDocFolderId("");
                  setDocPage(1);
                }}
              >
                <span className="kb-doc-tree-icon">
                  <span className="kb-doc-tree-node-icon kb-doc-tree-node-icon--visible">
                    <FolderIcon />
                  </span>
                </span>
                <span className="kb-doc-tree-title">全部文档</span>
              </div>
              {docFolders.length === 0 ? (
                <div className="kb-doc-folder-empty">暂无目录，点击右上角 + 新建</div>
              ) : (
                renderFolderNodes("", 0)
              )}
            </>
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
            prefix={<SearchColorIcon size={14} />}
            value={docKeyword}
            onChange={(e) => {
              setDocKeyword(e.target.value);
              setDocPage(1);
            }}
            onPressEnter={() => void load()}
          />
          {/* 解析状态 / 文件类型 过滤紧跟搜索框之后（宽屏 flex 行按 DOM 顺序排列） */}
          <div className="kb-doc-filter-fields">
            <div className="kb-doc-filter-field">
              <ModoSelect
                className="kb-doc-filter-control"
                placeholder="解析状态"
                value={docStatus || undefined}
                onChange={(v) => {
                  setDocStatus((v as string) || "");
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
              <ModoSelect
                className="kb-doc-filter-control"
                placeholder="文件类型"
                value={docType || undefined}
                onChange={(v) => {
                  setDocType((v as string) || "");
                  setDocPage(1);
                  void load();
                }}
                // 选项与上传白名单同源（系统能处理的格式 = 库里可能存在的格式）
                options={supportedExts.map((t) => ({ label: t.toUpperCase(), value: t }))}
              />
            </div>
          </div>
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
            {/* 2026-10-21 对齐参数管理页 UI：按钮去胶囊（ModoButton 默认 shape=round，
                显式 shape=default 走全局 antd borderRadius=2，与搜索框统一） */}
            <ModoButton shape="default" icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </ModoButton>
            <Dropdown
              menu={{
                items: [
                  {
                    key: "upload-files",
                    icon: <CloudUploadOutlined />,
                    label: "选择文件",
                    // 2026-10-21 合并入口：文件/文件夹同一按钮展开——
                    // 「选择文件」支持普通文件 + zip（自动解包，保留包内子孙目录）；
                    // 「选择文件夹」保留完整子目录结构（webkitdirectory），
                    // 文件夹内的 zip 同样自动解包（2026-10-21）
                    onClick: () => fileInputRef.current?.click(),
                  },
                  {
                    key: "upload-folder",
                    icon: <FolderOutlined />,
                    label: "选择文件夹",
                    onClick: () => dirInputRef.current?.click(),
                  },
                  {
                    key: "table",
                    icon: <TableOutlined />,
                    label: "添加表",
                    onClick: () => setSelectTableOpen(true),
                  },
                ],
              }}
            >
              <ModoButton shape="default" type="primary" icon={<CloudUploadOutlined />}>
                添加知识
                <DownOutlined style={{ fontSize: 10, marginLeft: 2 }} />
              </ModoButton>
            </Dropdown>
            {/* 隐藏的文件选择触发器（本地上传）：accept 只列系统可解析的格式 + zip
                （zip 自动解包，2026-10-21 起并入「选择文件」，不再单列压缩包入口） */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={acceptAttr(supportedExts, ARCHIVE_EXTS)}
              style={{ display: "none" }}
              onChange={(e) => {
                const picked = e.target.files ? Array.from(e.target.files) : [];
                e.target.value = "";
                if (picked.length === 0) return;
                // zip 压缩包分流：走服务端自动解包（保留包内子孙目录结构，同「拖拽 zip」语义）
                const archives = picked.filter((f) => isArchiveFileName(f.name));
                for (const f of archives) void handleArchiveUpload(f);
                const others = picked.filter((f) => !isArchiveFileName(f.name));
                if (others.length === 0) return;
                // 其余按格式白名单分拣
                const { kept, skipped } = partitionBySupport(others, supportedExts);
                if (skipped.length > 0) {
                  const names = skipped.slice(0, 3).map((f) => f.name).join("、");
                  message.warning(
                    `已跳过 ${skipped.length} 个不支持的文件（${names}${
                      skipped.length > 3 ? " 等" : ""
                    }）；系统可处理的格式：${supportedLabel(supportedExts)}`,
                  );
                }
                if (kept.length === 0) return;
                // 对齐 WeKnora：先收集到确认弹窗，用户可选处理配置后确认上传
                setPendingUploadFiles((prev) => [...prev, ...kept]);
                setUploadConfirmOpen(true);
              }}
            />
            {/* 隐藏的目录选择触发器（上传文件夹：取出目录及子孙目录中的全部文件）
                webkitdirectory 不在 @types/react 的 input 属性里，用展开传原生属性 */}
            <input
              ref={dirInputRef}
              type="file"
              multiple
              {...{ webkitdirectory: "" }}
              style={{ display: "none" }}
              onChange={(e) => {
                const files = e.target.files ? Array.from(e.target.files) : [];
                e.target.value = "";
                handleDirPicked(files);
              }}
            />
          </div>
        </div>
        {selectedList.length > 0 && (
          <div className="kb-doc-batch-bar">
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
          </div>
        )}
        {docView === "card" ? (
          <>
            <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
              <DocCardView
                items={docs}
                onOpen={onOpenDoc}
                onDownload={onDownloadDoc}
                onReparse={onReparseDoc}
                onDelete={onDeleteDoc}
                onTrace={onViewTrace}
              />
            </div>
            {/* 卡片视图分页：统一由内容列底部的 DSB simple 分页条承担（见 kb-doc-pagination-bar） */}
          </>
        ) : (
          <>
            <ModoTable<DocItem>
              containerClassName="kb-doc-table"
              rowKey="id"
              size="small"
              // ModoTable 默认 scroll.x=1000，会把表格撑到 1000px；内容面板窄于此宽度时
              // 最右「操作」列被挤出面板外看不见。改为 100%（table-layout 仍为 fixed，列宽可控），
              // 表格始终贴合面板宽度，操作列常驻可见。
              scroll={{ x: "100%" }}
              loading={docsLoading}
              dataSource={docs}
              rowSelection={{
                selectedRowKeys: [...selectedDocs],
                onChange: (keys) => setSelectedDocs(new Set(keys as string[])),
              }}
              onRow={(doc) => ({ onClick: () => onOpenDoc(doc) })}
              locale={{
                emptyText: (
                  <KbsEmpty
                    variant="inline"
                    description="暂无文档，拖拽文件到右上角上传"
                  />
                ),
              }}
              columns={[
                {
                  title: "文件名",
                  dataIndex: "file_name",
                  render: (v: string, doc: DocItem) => (
                    <Space>
                      {fileTypeIcon(doc.file_ext, 16)}
                      <a onClick={() => onOpenDoc(doc)}>{v}</a>
                    </Space>
                  ),
                },
                {
                  title: "状态",
                  dataIndex: "parse_state",
                  render: (v: string, doc: DocItem) => (
                    <Space size={4}>
                      {["PENDING", "PARSING", "EMBEDDING"].includes(v) && <Spin size="small" />}
                      {/* 三段阶段（解析/摘要/构建）合并为单一标签，非 READY 退化为单解析标签 */}
                      {v === "FAILED" ? (
                        <>
                          <Tag color="error">失败</Tag>
                          <a onClick={() => onViewTrace(doc)}>查看原因</a>
                        </>
                      ) : (
                        docStageTag(doc)
                      )}
                    </Space>
                  ),
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
                  title: "操作",
                  // 四个「图标+文字」链接按钮（构建过程/下载/重解析/删除）约需 280px，
                  // 210px 会被全局 td overflow:hidden 从右裁掉，故留足宽度。
                  width: 290,
                  render: (_: unknown, doc: DocItem) => (
                    <Space size={4}>
                      <Button type="link" size="small" icon={<ProfileOutlined />} onClick={(e) => { e.stopPropagation(); onViewTrace(doc); }}>
                        构建过程
                      </Button>
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
            {/* 表格视图分页：统一由内容列底部的 DSB simple 分页条承担（见 kb-doc-pagination-bar） */}
          </>
        )}
      </div>
      {/* 分页条：与知识库管理页同款 DSB simple 分页（共 N 个文档 + 页码输入 + 条/页），
          仅当总数超过一页时渲染；置于内容区内边距之外，横贯内容列底部 */}
      <KbsSimplePagination
        containerClassName="kb-doc-pagination-bar"
        current={docPage}
        pageSize={docPageSize}
        total={docTotal}
        showTotal={(t) => `共 ${t} 个文档`}
        onChange={(p, ps) => {
          setDocPage(p);
          setDocPageSize(ps);
        }}
      />
      </div>
      {/* 右侧内容区结束: 上面的 </div> 闭合 kb-doc-area, 这句闭合右侧 flex wrapper */}
      </Card>

      {/* 上传确认弹窗（对齐 WeKnora：文件列表 + 处理配置 + 构建服务，确认后逐文件上传） */}
      <UploadConfirmDialog
        open={uploadConfirmOpen}
        initialFiles={pendingUploadFiles}
        buildEngines={buildEngines}
        defaultBuildTapeId={kb?.build_tape_id || ""}
        onCancel={() => {
          setUploadConfirmOpen(false);
          setPendingUploadFiles([]);
        }}
        onConfirm={({ files: filesToUpload, processConfig, buildEngine, mirrorFolders }) => {
          setUploadConfirmOpen(false);
          setPendingUploadFiles([]);
          // 目录上传（files 带 webkitRelativePath）：按子目录结构串行落库
          if (isDirectoryUpload(filesToUpload)) {
            void uploadDirectoryBatch(filesToUpload, {
              processConfig,
              buildEngine,
              mirror: mirrorFolders !== false,
            });
            return;
          }
          for (const f of filesToUpload) {
            // zip 分流服务端解包（弹窗「继续添加」无 accept 限制，可能混入压缩包）
            if (isArchiveFileName(f.name)) {
              void handleArchiveUpload(f, docFolderId || undefined);
              continue;
            }
            startDocUpload(f, undefined, docFolderId, processConfig, buildEngine);
          }
        }}
      />

      {/* 重新解析配置弹窗（2026-10-14：重解析前可选处理配置，对齐 WeKnora 重解析换配置） */}
      <ReparseConfigDialog
        open={!!reparseDoc}
        doc={reparseDoc}
        buildEngines={buildEngines}
        defaultBuildTapeId={kb?.build_tape_id || ""}
        onCancel={() => setReparseDoc(null)}
        onConfirm={(r) => void handleReparseConfirm(r)}
      />

      {/* 添加表弹窗（表→wiki：选数据源维度表绑定） */}
      <SelectTableDialog
        open={selectTableOpen}
        kbId={kbId}
        buildEngines={buildEngines}
        defaultBuildTapeId={kb?.build_tape_id || ""}
        onCancel={() => setSelectTableOpen(false)}
        onBound={() => {
          setSelectTableOpen(false);
          setDocPage(1);
          void load();
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
