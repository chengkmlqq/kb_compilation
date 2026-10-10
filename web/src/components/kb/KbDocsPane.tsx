"use client";

/**
 * 知识库 · 文档面板（KbDocsPane）
 *
 * 从 kbs/[id]/page.tsx 抽出，供「动态选项卡」与详情路由复用：
 * KB 概览 + 文档工具条（筛选/上传/知识库配置(含切片配置)/切片核对）+ 文档卡片视图/表格 + 分页。
 * 仅依赖 kbId prop，自包含（自带文档列表与上传相关 state、Drawer、Modal）。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  App,
  Button,
  Descriptions,
  Empty,
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
  DownloadOutlined,
  RedoOutlined,
  ReloadOutlined,
  SearchOutlined,
  SettingOutlined,
  SlidersOutlined,
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
import DocBuildProcessDrawer from "@/components/DocBuildProcessDrawer";
import KBConfigModal from "@/components/KBConfigModal";
import {
  apiDeleteDocument,
  apiDownloadDocument,
  apiFixChunks,
  apiGenerateDocSummary,
  apiGetKb,
  apiListDocuments,
  apiReparseDocument,
  apiUploadDocumentWithProgress,
  apiVerifyChunks,
  ChunkVerifyReport,
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
  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());
  // 切片核对（向量库 ↔ MySQL）
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [verifyReport, setVerifyReport] = useState<ChunkVerifyReport | null>(null);
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [verifyFixing, setVerifyFixing] = useState(false);
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
  const [configOpen, setConfigOpen] = useState(false);
  const [buildDoc, setBuildDoc] = useState<DocItem | null>(null);

  // 加载知识库详情（含 WeKnora 对齐配置：索引开关/技能绑定/模型绑定等）
  const loadKb = useCallback(async () => {
    const res = await apiGetKb(kbId).catch(() => null);
    if (res?.success && res.data) setKb(res.data);
  }, [kbId]);

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
      });
      if (res.success) {
        setDocs(res.data?.items || []);
        setDocTotal(res.data?.total ?? 0);
      }
    } finally {
      setDocsLoading(false);
    }
  }, [kbId, docPage, docPageSize, docKeyword, docStatus, docType]);

  useEffect(() => {
    void load();
  }, [load]);

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
          setDocs((prev) =>
            prev.map((d) =>
              d.id === target.id
                ? { ...d, summary: res.data?.summary ?? null, summary_status: res.data?.summary_status ?? "READY" }
                : d,
            ),
          );
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

  // 切片核对：立即核对 MySQL 切片 ↔ 向量库
  const runVerify = async () => {
    setVerifyOpen(true);
    setVerifyLoading(true);
    setVerifyReport(null);
    try {
      const res = await apiVerifyChunks(kbId);
      if (res.success && res.data) setVerifyReport(res.data);
      else message.error(res.message || "核对失败");
    } catch (e) {
      message.error(`核对失败: ${String(e).slice(0, 80)}`);
    } finally {
      setVerifyLoading(false);
    }
  };

  // 一键修复：投递 worker 任务（清孤儿向量 + 重嵌入缺失切片）
  const runFix = async () => {
    setVerifyFixing(true);
    try {
      const res = await apiFixChunks(kbId);
      if (res.success) message.success("修复任务已投递，请在任务监控页查看进度");
      else message.error(res.message || "投递失败");
    } catch (e) {
      message.error(`投递失败: ${String(e).slice(0, 80)}`);
    } finally {
      setVerifyFixing(false);
    }
  };

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

  // 带进度上报的知识库文档上传（本地上传 & 全局拖放复用；经 uploadTask 事件驱动任务浮层）
  const startDocUpload = (file: File, taskId?: string) => {
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
        const res = await apiUploadDocumentWithProgress(kbId, file, (pct) => {
          emitUploadTask({ id, name: file.name, size: file.size, status: "uploading", progress: pct });
        });
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
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      {/* 对齐 WeKnora .doc-card-area + .doc-filter-bar：去 Card 包装，
          搜索独占 search 栅格、视图切换+操作在 trailing、筛选项在 filters 行 */}
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
            <Button icon={<SlidersOutlined />} onClick={() => void runVerify()}>
              切片核对
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
                if (files) {
                  // 逐文件上传，每个文件上传成功后由 startDocUpload 触发列表刷新
                  for (const f of Array.from(files)) {
                    startDocUpload(f);
                  }
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

      {/* 文档详情抽屉（对齐 WeKnora DocContent：元数据 + AI 摘要 + 分块预览 + 下载/重解析/删除） */}
      <DocDetailDrawer
        kbId={kbId}
        doc={detailDoc}
        hasSummaryModel={!!kb?.summary_model_id}
        onClose={() => setDetailDoc(null)}
        onDownload={onDownloadDoc}
        onReparse={onReparseDoc}
        onDelete={onDeleteDoc}
        onSummaryUpdated={(docId, summary, status) => {
          setDetailDoc((prev) =>
            prev && prev.id === docId ? { ...prev, summary, summary_status: status, summary_error: null } : prev,
          );
          setDocs((prev) =>
            prev.map((d) => (d.id === docId ? { ...d, summary, summary_status: status, summary_error: null } : d)),
          );
        }}
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

      {/* 切片核对弹窗（MySQL doc_chunk ↔ 向量库） */}
      <Modal
        title="切片核对"
        open={verifyOpen}
        onCancel={() => setVerifyOpen(false)}
        width={640}
        footer={
          <Space>
            <Button onClick={() => setVerifyOpen(false)}>关闭</Button>
            <Button type="primary" danger loading={verifyFixing} onClick={() => void runFix()}>
              一键修复
            </Button>
          </Space>
        }
      >
        {verifyLoading ? (
          <div style={{ padding: 24, textAlign: "center" }}>核对中…</div>
        ) : verifyReport ? (
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="MySQL 切片数">{verifyReport.doc_chunks}</Descriptions.Item>
            <Descriptions.Item label="向量库切片数">{verifyReport.vector_chunks}</Descriptions.Item>
            <Descriptions.Item label="未向量化（缺失）">
              <span style={{ color: verifyReport.missing_in_vector ? "#faad14" : "#52c41a" }}>
                {verifyReport.missing_in_vector}
              </span>
            </Descriptions.Item>
            <Descriptions.Item label="孤儿向量">
              <span style={{ color: verifyReport.orphan_vectors ? "#faad14" : "#52c41a" }}>
                {verifyReport.orphan_vectors}
              </span>
            </Descriptions.Item>
            <Descriptions.Item label="结论" span={2}>
              <span style={{ color: verifyReport.ok ? "#52c41a" : "#faad14" }}>
                {verifyReport.ok ? "一致，无需修复" : "存在不一致，可点击右下角「一键修复」"}
              </span>
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <div style={{ padding: 24, textAlign: "center" }}>核对失败或未执行</div>
        )}
      </Modal>
    </div>
  );
}
