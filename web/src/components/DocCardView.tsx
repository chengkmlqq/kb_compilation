"use client";
/**
 * 文档卡片视图（对齐知识库卡片 kb-kbs-card 三段式，2026-10-20 统一详情页与知识库页观感）：
 * header 44px（32px 文件类型图标 + 文件名 14px/600 + 右侧状态标签 + meta 12px）
 * → 两行摘要（40px 截断）→ footer 28px（右：更多操作）。
 * 悬浮 Popover 保留原有的完整信息（大小/分块/时间/失败原因）与操作入口。
 */
import type { ReactNode } from "react";
import { Button, Dropdown, Popover, Space, Tag, Typography } from "antd";
import {
  DeleteOutlined,
  DownloadOutlined,
  MoreOutlined,
  ProfileOutlined,
  RedoOutlined,
  TableOutlined,
} from "@ant-design/icons";
import type { DocItem } from "@/lib/api";
import KbsEmpty from "@/components/kb/KbsEmpty";

export const PARSE_STATE_COLOR: Record<string, string> = {
  PENDING: "default",
  PARSING: "processing",
  EMBEDDING: "processing",
  READY: "success",
  FAILED: "error",
};

export const STATE_LABEL: Record<string, string> = {
  PENDING: "待解析",
  PARSING: "解析中",
  EMBEDDING: "向量化中",
  READY: "已就绪",
  FAILED: "失败",
};

/** 摘要阶段标签（kb_document.summary_status："" 未生成 / READY / FAILED） */
export const SUMMARY_STAGE_LABEL: Record<string, { text: string; color: string }> = {
  READY: { text: "摘要完成", color: "success" },
  FAILED: { text: "摘要失败", color: "error" },
  "": { text: "摘要未生成", color: "default" },
};

/**
 * wiki 构建阶段标签（doc.wiki_build.state：WIKI 任务 Job.state 大写，或
 * 编排 ORCH_RUN run.status 转大写）。SUCCESS=完成 / FAILED|STOPPED|CANCELLED=失败 /
 * PENDING|RUNNING|QUEUED=构建中 / 无 wiki_build=未生成（未配置构建引擎或未触发）。
 */
export const WIKI_STAGE_LABEL: Record<string, { text: string; color: string }> = {
  SUCCESS: { text: "构建完成", color: "success" },
  FAILED: { text: "构建失败", color: "error" },
  STOPPED: { text: "构建失败", color: "error" },
  CANCELLED: { text: "构建失败", color: "error" },
  PENDING: { text: "构建中", color: "processing" },
  RUNNING: { text: "构建中", color: "processing" },
  QUEUED: { text: "构建中", color: "processing" },
};

/**
 * 文档处理三段阶段标签（解析/摘要/构建，方案A 展示层聚合，2026-10-16）：
 * parse_state=READY 时在「已就绪」主标签旁附加摘要与构建两个阶段小标签，
 * 使用户一眼看到整条管线（解析+向量化 / 摘要 / wiki 构建）的完成度，
 * 而不仅是解析就绪。parse_state 非 READY 时只返回解析主标签。
 * （列表视图表格「状态」列仍使用彩色 Tag；卡片视图改为右上角合并的单一标签，见 docStageTag）
 */
export function docStageTags(d: DocItem): ReactNode {
  if (d.parse_state !== "READY") {
    return (
      <Tag color={PARSE_STATE_COLOR[d.parse_state] || "default"}>
        {STATE_LABEL[d.parse_state] || d.parse_state}
      </Tag>
    );
  }
  const sum = SUMMARY_STAGE_LABEL[d.summary_status || ""] || SUMMARY_STAGE_LABEL[""];
  const wikiState = d.wiki_build?.state || "";
  const wiki = WIKI_STAGE_LABEL[wikiState] || { text: "构建未生成", color: "default" };
  return (
    <Space size={4} wrap>
      <Tag color={PARSE_STATE_COLOR.READY}>已就绪</Tag>
      <Tag color={sum.color}>{sum.text}</Tag>
      <Tag color={wiki.color}>{wiki.text}</Tag>
    </Space>
  );
}

const IN_FLIGHT = new Set(["PENDING", "PARSING", "EMBEDDING"]);

/** Tag 颜色严重度（取最严重者作为合并标签颜色） */
const COLOR_SEVERITY: Record<string, number> = {
  error: 3,
  processing: 2,
  warning: 2,
  default: 1,
  success: 0,
};

/**
 * 卡片右上角状态标签（2026-10-20：原 footer 左侧的三段状态文本合并为单个标签，
 * 移到标题行右侧）。解析未就绪 → 单解析标签；READY → 解析/摘要/构建三段合并为一个标签，
 * 颜色取最严重者（如「摘要失败」整体呈 error）。
 */
export function docStageTag(d: DocItem): ReactNode {
  if (IN_FLIGHT.has(d.parse_state)) {
    return (
      <Tag color={PARSE_STATE_COLOR[d.parse_state] || "default"}>
        {STATE_LABEL[d.parse_state] || d.parse_state}
      </Tag>
    );
  }
  if (d.parse_state === "FAILED") {
    return <Tag color="error">失败</Tag>;
  }
  const sum = SUMMARY_STAGE_LABEL[d.summary_status || ""] || SUMMARY_STAGE_LABEL[""];
  const wiki = WIKI_STAGE_LABEL[d.wiki_build?.state || ""] || { text: "构建未生成", color: "default" };
  const color =
    (COLOR_SEVERITY[wiki.color] ?? 0) > (COLOR_SEVERITY[sum.color] ?? 0) ? wiki.color : sum.color;
  return (
    <Tag color={color}>
      已就绪 · {sum.text} · {wiki.text}
    </Tag>
  );
}

/**
 * 文件类型图标：改为 DSB 文档库（knowledge_lib_neo）逐类型 PNG
 * （实现见 @/components/kb/FileTypeIcon，图标为 DSB 原样拷贝，非自绘）。
 * 保留同名导出，供文档表格 / 详情面板 / 预览弹窗等既有调用方复用。
 * 尺寸由 size 参数决定（默认 16），容器 font-size 不再影响（返回 <img>）。
 */
import { fileTypeIcon } from "@/components/kb/FileTypeIcon";

export { fileTypeIcon };

export function formatSize(bytes?: number | null): string {
  if (bytes == null) return "--";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export function formatTime(iso?: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--";
  const pad = (n: number) => String(n).padStart(2, "0");
  // 2026-10-10: 横杠格式 YYYY-MM-DD HH:mm:ss（对齐任务监控页；放弃 toLocaleString 的斜杠输出）
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

interface Props {
  items: DocItem[];
  onOpen: (doc: DocItem) => void;
  onDownload: (doc: DocItem) => void;
  onReparse: (doc: DocItem) => void;
  onDelete: (doc: DocItem) => void;
  onTrace: (doc: DocItem) => void;
}

/** 表源判定（2026-10-10 表→wiki）：source_type === "table" */
export function isTableDoc(d: DocItem): boolean {
  return (d.source_type || "file") === "table";
}

/** 表卡片图标（区别于文件类型图标） */
function tableIcon() {
  return <TableOutlined style={{ color: "#1677ff" }} />;
}

/** 行数友好显示（1.2万 / 3,400） */
function formatRows(n?: number | null): string {
  if (n == null) return "—";
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万行`;
  return `${n} 行`;
}

export default function DocCardView({ items, onOpen, onDownload, onReparse, onDelete, onTrace }: Props) {
  if (items.length === 0) {
    return <KbsEmpty description="暂无文档，拖拽文件到右上角上传" />;
  }

  /** 卡片 meta 行（对齐知识库卡片 meta：文件数 · Wiki 数 的等位信息） */
  const metaOf = (d: DocItem) =>
    isTableDoc(d)
      ? `${formatRows(d.row_count)} · ${d.chunk_count ?? 0} 个分块`
      : `${d.chunk_count ?? 0} 个分块 · ${formatSize(d.file_size)}`;

  const menuOf = (d: DocItem) => ({
    items: [
      {
        key: "download",
        icon: <DownloadOutlined />,
        label: "下载",
        // Dropdown 菜单经 portal 渲染但仍按组件树冒泡：必须停掉冒泡，
        // 否则点菜单项会穿透到卡片的 onClick 打开详情
        onClick: ({ domEvent }: { domEvent: { stopPropagation: () => void } }) => {
          domEvent.stopPropagation();
          onDownload(d);
        },
      },
      {
        key: "trace",
        icon: <ProfileOutlined />,
        label: "构建过程",
        onClick: ({ domEvent }: { domEvent: { stopPropagation: () => void } }) => {
          domEvent.stopPropagation();
          onTrace(d);
        },
      },
      {
        key: "reparse",
        icon: <RedoOutlined />,
        label: "重新解析",
        onClick: ({ domEvent }: { domEvent: { stopPropagation: () => void } }) => {
          domEvent.stopPropagation();
          onReparse(d);
        },
      },
      {
        key: "delete",
        icon: <DeleteOutlined />,
        label: "删除",
        danger: true,
        onClick: ({ domEvent }: { domEvent: { stopPropagation: () => void } }) => {
          domEvent.stopPropagation();
          onDelete(d);
        },
      },
    ],
  });

  const hoverContentOf = (d: DocItem) => (
    <div style={{ maxWidth: 300 }}>
      <Typography.Text strong>{d.file_name}</Typography.Text>
      <div style={{ marginTop: 4, color: "#666", fontSize: 12 }}>
        {isTableDoc(d) ? (
          <>
            <div>类型：数据源表</div>
            <div>数据源：{d.ds_source_name || "—"}</div>
            <div>库：{d.ds_table_schema || "—"}</div>
            <div>数据量：{formatRows(d.row_count)}</div>
            <div>分块：{d.chunk_count != null ? `${d.chunk_count} 个` : "—"}</div>
            <div>状态：{STATE_LABEL[d.parse_state] || d.parse_state}</div>
            <div>绑定时间：{formatTime(d.created_at)}</div>
          </>
        ) : (
          <>
            <div>类型：{d.file_ext?.toUpperCase() || "—"}</div>
            <div>大小：{formatSize(d.file_size)}</div>
            <div>分块：{d.chunk_count != null ? `${d.chunk_count} 个` : "—"}</div>
            <div>状态：{STATE_LABEL[d.parse_state] || d.parse_state}</div>
            <div>上传时间：{formatTime(d.created_at)}</div>
          </>
        )}
        {d.summary ? <div>摘要：{d.summary.slice(0, 160)}</div> : null}
        {d.parse_state === "FAILED" && d.parse_error ? (
          <div style={{ color: "#cf1322" }}>错误：{d.parse_error.slice(0, 120)}</div>
        ) : null}
      </div>
      <div style={{ marginTop: 8, color: "#999", fontSize: 12 }}>点击卡片查看完整内容</div>
    </div>
  );

  /** 处理中 / 失败的文档：footer 状态文本可点，直达构建过程 */
  const traceableOf = (d: DocItem) => IN_FLIGHT.has(d.parse_state) || d.parse_state === "FAILED";

  return (
    <div className="kb-doc-cards">
      {items.map((d) => (
        <Popover
          key={d.id}
          content={hoverContentOf(d)}
          placement="right"
          overlayStyle={{ pointerEvents: "none" }}
        >
          <div className="kb-doc-card" onClick={() => onOpen(d)}>
            <div className="kb-doc-card-header">
              <div className="kb-doc-card-icon">{isTableDoc(d) ? tableIcon() : fileTypeIcon(d.file_ext, 28)}</div>
              <div className="kb-doc-card-detail">
                <div className="kb-doc-card-title-row">
                  <div className="kb-doc-card-title-name" title={d.file_name}>
                    {d.file_name}
                  </div>
                  {/* 状态标签（合并后的单一标签）置于标题行右侧 */}
                  <span
                    className="kb-doc-card-status"
                    onClick={
                      traceableOf(d)
                        ? (e) => {
                            e.stopPropagation();
                            onTrace(d);
                          }
                        : undefined
                    }
                    title={traceableOf(d) ? "查看构建过程" : undefined}
                  >
                    {docStageTag(d)}
                  </span>
                </div>
                <div className="kb-doc-card-meta">{metaOf(d)}</div>
              </div>
            </div>
            <div className="kb-doc-card-description">{d.summary || ""}</div>
            <div className="kb-doc-card-footer">
              <div className="kb-doc-card-footer-right" onClick={(e) => e.stopPropagation()}>
                <Dropdown menu={menuOf(d)} trigger={["click"]} placement="bottomRight">
                  <Button
                    size="small"
                    type="text"
                    icon={<MoreOutlined />}
                    onClick={(e) => e.stopPropagation()}
                  />
                </Dropdown>
              </div>
            </div>
          </div>
        </Popover>
      ))}
    </div>
  );
}
