"use client";

/**
 * 文件管理页 —— 迁移自 data-synth system/files。
 *
 * 资源管理器式浏览：业务模块 → 团队 → 日期 → 文件，逐级下探；
 * 支持全局搜索、上传、单文件下载、逻辑删除、目录递归删除、目录打包下载。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Breadcrumb,
  Button,
  Dropdown,
  Empty,
  Input,
  Modal,
  Pagination,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FileOutlined,
  FolderFilled,
  FolderOpenOutlined,
  ReloadOutlined,
  SearchOutlined,
  UpOutlined,
} from "@ant-design/icons";
import {
  apiFileDelete,
  apiFileDownloadUrl,
  apiFileExplore,
  apiFileRmdir,
  apiFileUpload,
  apiFileZipUrl,
  SysFileItem,
} from "@/lib/api";

const { Text } = Typography;

function fmtSize(bytes?: number | null): string {
  if (bytes == null || Number.isNaN(bytes)) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function fmtDate(text?: string | null): string {
  return text || "-";
}

export default function FilesPage() {
  const { message, modal } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [list, setList] = useState<SysFileItem[]>([]);
  const [total, setTotal] = useState(0);
  const [currentPath, setCurrentPath] = useState("/");
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [uploading, setUploading] = useState(false);

  const parts = useMemo(
    () => currentPath.split("/").filter(Boolean),
    [currentPath],
  );

  const load = useCallback(
    async (path = currentPath, searchText = search, p = page, ps = pageSize) => {
      setLoading(true);
      try {
        const res = await apiFileExplore({
          current_path: path,
          search: searchText || undefined,
          page: p,
          page_size: ps,
        });
        if (res.success && res.data) {
          setList(res.data.list);
          setTotal(res.data.total);
        } else {
          message.error((res as { message?: string }).message || "加载文件失败");
        }
      } finally {
        setLoading(false);
      }
    },
    [currentPath, search, page, pageSize, message],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const enterFolder = (name: string) => {
    const next = [currentPath.replace(/\/+$/, ""), name].filter(Boolean).join("/");
    setCurrentPath(next.startsWith("/") ? next : `/${next}`);
    setPage(1);
  };

  const goBreadcrumb = (index: number) => {
    if (index < 0) {
      setCurrentPath("/");
    } else {
      const next = "/" + parts.slice(0, index + 1).join("/");
      setCurrentPath(next);
    }
    setPage(1);
  };

  const breadcrumbItems = [
    { title: <a onClick={() => goBreadcrumb(-1)}>全部文件</a> },
    ...parts.map((p, i) => ({
      title: (
        <a onClick={() => goBreadcrumb(i)}>
          {p.length > 12 ? `${p.slice(0, 12)}…` : p}
        </a>
      ),
    })),
  ];

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const res = await apiFileUpload(file, parts[0] || "default");
      if (res.success) {
        message.success(`上传成功: ${file.name}`);
        void load();
      } else {
        message.error(res.message || "上传失败");
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : "上传异常");
    } finally {
      setUploading(false);
    }
    return false; // 阻止 antd 默认上传
  };

  const handleDownload = (fileId: string, fileName: string) => {
    const a = document.createElement("a");
    a.href = apiFileDownloadUrl(fileId);
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const handleDeleteFile = (record: SysFileItem) => {
    modal.confirm({
      title: "确定删除该文件？",
      content: `「${record.file_name}」将被标记为删除。`,
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await apiFileDelete(record.id);
        if (res.success) {
          message.success("已删除");
          void load();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  const handleDeleteDir = (path: string, displayName: string) => {
    modal.confirm({
      title: `确定删除目录「${displayName}」下的所有文件？`,
      content: "该操作会将目录下全部文件标记为删除。",
      okButtonProps: { danger: true },
      onOk: async () => {
        const res = await apiFileRmdir(path);
        if (res.success) {
          message.success(`已标记删除 ${res.data?.total || 0} 个文件`);
          void load();
        } else {
          message.error(res.message || "目录删除失败");
        }
      },
    });
  };

  const handleZip = (path: string, displayName: string) => {
    const a = document.createElement("a");
    a.href = apiFileZipUrl(path);
    a.download = `${displayName}.zip`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const columns: ColumnsType<SysFileItem> = [
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
      ellipsis: true,
      render: (text: string, record) =>
        record.isFolder ? (
          <Space>
            <FolderFilled style={{ color: "#F5A623", fontSize: 16 }} />
            <a onClick={() => enterFolder(text)}>{text}</a>
          </Space>
        ) : (
          <Space>
            <FileOutlined style={{ color: "#3261CE" }} />
            <Text
              copyable={{ text: record.file_name }}
              style={{ wordBreak: "break-all" }}
            >
              {text}
            </Text>
          </Space>
        ),
    },
    {
      title: "大小",
      dataIndex: "file_size",
      key: "file_size",
      width: 110,
      render: (v: number | null, r) => (r.isFolder ? "-" : fmtSize(v)),
    },
    {
      title: "类型",
      dataIndex: "file_extension",
      key: "file_extension",
      width: 100,
      render: (v: string | null, r) =>
        r.isFolder ? (
          <Tag>目录</Tag>
        ) : v ? (
          <Tag color="blue">{v.replace(".", "").toUpperCase()}</Tag>
        ) : (
          "-"
        ),
    },
    {
      title: "创建人",
      dataIndex: "created_by",
      key: "created_by",
      width: 110,
      render: (v: string | null) => v || "-",
    },
    {
      title: "创建时间",
      dataIndex: "create_date",
      key: "create_date",
      width: 160,
      render: (v: string | null) => fmtDate(v),
    },
    {
      title: "操作",
      key: "action",
      width: 160,
      fixed: "right" as const,
      render: (_, record) =>
        record.isFolder ? (
          <Space size={4}>
            <Button
              type="link"
              size="small"
              icon={<DownloadOutlined />}
              onClick={() => handleZip(`${currentPath}/${record.name ?? ""}`, record.name ?? "-")}
            >
              下载
            </Button>
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() =>
                handleDeleteDir(`${currentPath}/${record.name ?? ""}`, record.name ?? "-")
              }
            >
              删除
            </Button>
          </Space>
        ) : (
          <Space size={4}>
            <Button
              type="link"
              size="small"
              icon={<DownloadOutlined />}
              onClick={() => handleDownload(record.id, record.file_name)}
            >
              下载
            </Button>
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => handleDeleteFile(record)}
            >
              删除
            </Button>
          </Space>
        ),
    },
  ];

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        minHeight: 0,
        background: "#F5F7FA",
        padding: 8,
        overflow: "hidden",
      }}
    >
      {/* 工具栏 */}
      <div
        style={{
          flexShrink: 0,
          background: "#fff",
          borderRadius: 8,
          border: "1px solid #E3E9EF",
          padding: "12px 16px",
          marginBottom: 12,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <Button
            icon={<UpOutlined />}
            disabled={currentPath === "/"}
            onClick={() => goBreadcrumb(parts.length - 2)}
          >
            上级
          </Button>
          <Breadcrumb items={breadcrumbItems} />
        </div>
        <Space size={8}>
          <Input.Search
            placeholder="搜索文件名"
            allowClear
            style={{ width: 220 }}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            onSearch={(v) => {
              setSearch(v.trim());
              setPage(1);
              void load("/", v.trim(), 1, pageSize);
            }}
          />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              setSearch("");
              setSearchInput("");
              void load();
            }}
          >
            刷新
          </Button>
          <Upload accept="*" showUploadList={false} beforeUpload={handleUpload}>
            <Button type="primary" icon={<CloudUploadOutlined />} loading={uploading}>
              上传
            </Button>
          </Upload>
        </Space>
      </div>

      {/* 文件表格 */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflow: "hidden",
          background: "#fff",
          borderRadius: 8,
          border: "1px solid #E3E9EF",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div style={{ flex: 1, overflow: "auto", padding: "0 16px" }}>
          <Table<SysFileItem>
            rowKey={(r) => (r.isFolder ? `dir:${r.name}` : r.id)}
            size="middle"
            loading={loading}
            dataSource={list}
            columns={columns}
            scroll={{ x: 900 }}
            pagination={false}
            locale={{ emptyText: <Empty description="暂无文件" /> }}
            onRow={(record) =>
              record.isFolder
                ? { onDoubleClick: () => enterFolder(record.name || "") }
                : {}
            }
          />
        </div>
        <div
          style={{
            borderTop: "1px solid #E3E9EF",
            padding: "10px 16px",
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            gap: 12,
          }}
        >
          <Text type="secondary" style={{ fontSize: 13 }}>
            共 {total} 项{search ? "（搜索结果）" : ""}
          </Text>
          <Pagination
            current={page}
            pageSize={pageSize}
            total={total}
            showSizeChanger
            showTotal={(t) => `共 ${t} 项`}
            onChange={(p, ps) => {
              setPage(p);
              setPageSize(ps);
            }}
          />
        </div>
      </div>
    </div>
  );
}