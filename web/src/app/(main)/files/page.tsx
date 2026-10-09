"use client";

/**
 * 文件管理页 —— 迁移自 data-synth system/files。
 *
 * 资源管理器式浏览：业务模块 → 团队 → 日期 → 文件，逐级下探；
 * 支持全局搜索、上传、单文件下载、逻辑删除、目录递归删除、目录打包下载。
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  App,
  Breadcrumb,
  Button,
  Card,
  Dropdown,
  Empty,
  Form,
  Input,
  Modal,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  CloudUploadOutlined,
  FileOutlined,
  FolderFilled,
} from "@ant-design/icons";
import {
  apiFileDelete,
  apiFileDownloadUrl,
  apiFileExplore,
  apiFileRawDownloadUrl,
  apiFileRmdir,
  apiFileUpload,
  apiFileZipUrl,
  SysFileItem,
} from "@/lib/api";
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoTabs } from "@/components/biz/modo-tabs";

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

/** 动态选项卡条目（复用用户管理页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务内容 */
type FileTab = { key: string; title: string; children: ReactNode };

export default function FilesPage() {
  const { message, modal } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [list, setList] = useState<SysFileItem[]>([]);
  const [total, setTotal] = useState(0);
  const [currentPath, setCurrentPath] = useState("/");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [uploading, setUploading] = useState(false);
  const [searchForm] = Form.useForm<{ keyword: string }>();

  // ---- 动态选项卡（复用用户管理页 ModoTabs 模式）----
  // 首个 tab「文件管理」= 页面标题 + 列表，不可关闭；后续内容较多的详情/编辑表单可 push 进 tabs 以选项卡打开。
  // 本轮先预留能力（容器 + 开关 tab 机制就位），具体接入哪些内容后续再定。
  const [tabs, setTabs] = useState<FileTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

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

  const handleDownload = (record: SysFileItem) => {
    const a = document.createElement("a");
    // MinIO 树模式（explore 返回 storage_path 无 id）走 /files/raw，
    // 表模式走 /files/{id}/download（两者后端均支持）
    a.href = record.id
      ? apiFileDownloadUrl(record.id)
      : apiFileRawDownloadUrl(record.storage_path || "");
    a.download = record.file_name || record.name || "download";
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

  // 筛选（对齐用户管理页：查询/重置 显式触发，交由 load 的 effect 重新加载）
  const doSearch = (vals: { keyword?: string }) => {
    setSearch((vals.keyword || "").trim());
    setPage(1);
  };

  const doReset = () => {
    searchForm.resetFields();
    setSearch("");
    setPage(1);
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
      width: 140,
      fixed: "right" as const,
      render: (_: unknown, record: SysFileItem) => (
        <ModoActionGroup
          maxCount={2}
          actions={
            record.isFolder
              ? [
                  {
                    key: "download",
                    label: "下载",
                    onClick: () =>
                      handleZip(`${currentPath}/${record.name ?? ""}`, record.name ?? "-"),
                  },
                  {
                    key: "delete",
                    label: "删除",
                    danger: true,
                    onClick: () =>
                      handleDeleteDir(`${currentPath}/${record.name ?? ""}`, record.name ?? "-"),
                  },
                ]
              : [
                  {
                    key: "download",
                    label: "下载",
                    onClick: () => handleDownload(record),
                  },
                  {
                    key: "delete",
                    label: "删除",
                    danger: true,
                    onClick: () => handleDeleteFile(record),
                  },
                ]
          }
        />
      ),
    },
  ];

  // 列表区（首个选项卡内容）：路径 + 筛选 + 表格 + 钉底分页
  const listPane = (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      <Card
        style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
            padding: "12px 16px 0",
          },
        }}
      >
        {/* 路径导航（固定） */}
        <div style={{ flexShrink: 0, marginBottom: 12 }}>
          <Breadcrumb items={breadcrumbItems} />
        </div>

        {/* 筛选表单（对齐用户管理页：查询/重置 显式触发） */}
        <Form
          form={searchForm}
          layout="inline"
          style={{ marginBottom: 12, flexShrink: 0 }}
          onFinish={doSearch}
          initialValues={{ keyword: "" }}
        >
          <Form.Item name="keyword" label="文件名">
            <Input allowClear placeholder="请输入文件名" style={{ width: 220 }} />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button
                onClick={() => {
                  searchForm.resetFields();
                  doReset();
                }}
              >
                重置
              </Button>
            </Space>
          </Form.Item>
        </Form>

        <Table<SysFileItem>
          rowKey={(r) => (r.isFolder ? `dir:${r.name}` : r.id)}
          size="small"
          loading={loading}
          dataSource={list}
          columns={columns}
          pagination={false}
          scroll={{ x: 900, y: "calc(100vh - 296px)" }}
          locale={{ emptyText: <Empty description="暂无文件" /> }}
          onRow={(record) =>
            record.isFolder ? { onDoubleClick: () => enterFolder(record.name || "") } : {}
          }
        />
        <div style={{ flexShrink: 0, marginTop: "auto" }}>
          <ModoPagination
            current={page}
            pageSize={pageSize}
            total={total}
            showSizeChanger
            showTotal={(t) => `共 ${t} 项${search ? "（搜索结果）" : ""}`}
            onChange={(p, ps) => {
              setPage(p);
              setPageSize(ps);
            }}
          />
        </div>
      </Card>
    </div>
  );

  return (
    <div
      className="files-page"
      style={{
        padding: 8,
        height: "calc(100vh - 45px)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey={activeTab}
        onChange={setActiveTab}
        onEdit={(key, action) => {
          if (action === "remove") closeTab(String(key));
        }}
        tabBarExtraContent={
          <Upload accept="*" showUploadList={false} beforeUpload={handleUpload}>
            <Button type="primary" icon={<CloudUploadOutlined />} loading={uploading}>
              上传
            </Button>
          </Upload>
        }
        items={[
          { key: "home", label: "文件管理", children: listPane },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: t.children })),
        ]}
      />
    </div>
  );
}