"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tree,
  Typography,
  Upload,
} from "antd";
import { DeleteOutlined, DownloadOutlined, PlusOutlined, ReloadOutlined, UploadOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import CodeViewer from "@/components/CodeViewer";
import {
  apiDeleteSkillRegistry,
  apiInstallSkillRegistry,
  apiListSkillsRegistry,
  SkillRegistryItem,
  ModelScope,
} from "@/lib/api";

const { Text } = Typography;

const SCOPE_LABEL: Record<ModelScope, string> = {
  personal: "我的",
  team: "团队",
  system: "系统",
};
const SCOPE_COLOR: Record<ModelScope, string> = {
  personal: "blue",
  team: "green",
  system: "purple",
};

function fmtSize(n: number): string {
  if (!n) return "-";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function isMarkdown(p: string): boolean {
  const l = p.toLowerCase();
  return l.endsWith(".md") || l.endsWith(".markdown");
}

type TreeNode = {
  title: string;
  key: string;
  children?: TreeNode[];
};

// 由扁平文件清单构建目录树（目录在前、按名排序）
function buildFileTree(files: { path: string; size: number }[]): TreeNode[] {
  const root: TreeNode[] = [];
  for (const f of files) {
    const parts = f.path.split("/").filter(Boolean);
    if (parts.length === 0) continue;
    let level = root;
    parts.forEach((seg, i) => {
      const isLeaf = i === parts.length - 1;
      const key = parts.slice(0, i + 1).join("/");
      let node = level.find((n) => n.key === key);
      if (!node) {
        node = { title: seg, key, children: isLeaf ? undefined : [] };
        level.push(node);
      }
      if (!isLeaf && !node.children) node.children = [];
      level = node.children ?? [];
    });
  }
  const sortFn = (list: TreeNode[]) => {
    list.sort((a, b) => {
      const ad = !!a.children && a.children.length > 0;
      const bd = !!b.children && b.children.length > 0;
      if (ad !== bd) return ad ? -1 : 1;
      return a.title.localeCompare(b.title);
    });
    list.forEach((n) => n.children && sortFn(n.children));
  };
  sortFn(root);
  return root;
}

export default function SkillManagePage() {
  const { message } = App.useApp();
  const [items, setItems] = useState<SkillRegistryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [installScope, setInstallScope] = useState<ModelScope>("personal");
  const [installOpen, setInstallOpen] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [detailTarget, setDetailTarget] = useState<SkillRegistryItem | null>(null);
  const [detailFiles, setDetailFiles] = useState<{ path: string; size: number }[]>([]);
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [fileData, setFileData] = useState<{
    path: string;
    content: string;
    truncated: boolean;
    binary: boolean;
    size: number;
  } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [fileLoading, setFileLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListSkillsRegistry();
      if (res.success && res.data) {
        setItems(res.data.items);
        setIsAdmin(!!res.data.is_admin);
      } else {
        message.error(res.message || "加载失败");
      }
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => items, [items]);

  const handleDelete = async (item: SkillRegistryItem) => {
    const res = await apiDeleteSkillRegistry(item.id);
    if (res.success) {
      message.success(`已删除技能 ${item.name}`);
      void load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const handleInstall = (file: File) => {
    if (!file.name.toLowerCase().endsWith(".zip")) {
      message.warning("仅支持 ZIP 文件");
      return false;
    }
    setPendingFile(file);
    setInstallScope("personal");
    setInstallOpen(true);
    return false;
  };

  const confirmInstall = async () => {
    if (!pendingFile) return;
    const res = await apiInstallSkillRegistry(pendingFile, installScope);
    if (res.success) {
      message.success(`技能已安装：${res.data?.item.name ?? pendingFile.name}`);
      setInstallOpen(false);
      setPendingFile(null);
      void load();
    } else {
      message.error(res.message || "安装失败");
    }
  };

  const openDetail = async (item: SkillRegistryItem) => {
      setDetailTarget(item);
      setDetailFiles([]);
      setSelectedPath(null);
      setFileData(null);
      setDetailLoading(true);
      try {
        const res = await fetch(`/api/v1/skills/${item.id}`, {
          headers: { "Content-Type": "application/json" },
        });
        const json = await res.json();
        if (json.success && json.data) {
          const d = json.data;
          const files: { path: string; size: number }[] = d.files ?? [];
          setDetailFiles(files);
          // 默认选中第一个 Markdown 文档（通常 SKILL.md）
          const md = files.find((f) => f.path.toLowerCase().endsWith(".md"));
          const first = md ?? files[0];
          if (first) void loadFile(item.id, first.path);
        } else {
          setFileData({ path: "", content: "加载失败", truncated: false, binary: false, size: 0 });
        }
      } catch {
        setFileData({ path: "", content: "加载失败", truncated: false, binary: false, size: 0 });
      } finally {
        setDetailLoading(false);
      }
    };

    const loadFile = async (skillId: string, path: string) => {
      setSelectedPath(path);
      setFileLoading(true);
      setFileData(null);
      try {
        const res = await fetch(
          `/api/v1/skills/${skillId}/files/${encodeURIComponent(path.split("/").map(encodeURIComponent).join("/"))}`,
          { headers: { "Content-Type": "application/json" } },
        );
        const json = await res.json();
        if (json.success && json.data) {
          setFileData(json.data);
        } else {
          setFileData({ path, content: "加载失败", truncated: false, binary: false, size: 0 });
        }
      } catch {
        setFileData({ path, content: "加载失败", truncated: false, binary: false, size: 0 });
      } finally {
        setFileLoading(false);
      }
    };

  const handleExport = (item: SkillRegistryItem) => {
    // 导出走后端 package 下载端点：GET /api/v1/skills/{id}/package → base64
    void (async () => {
      try {
        const res = await fetch(`/api/v1/skills/${item.id}/package`, {
          headers: { "Content-Type": "application/json" },
        });
        const json = await res.json();
        const b64 = json?.data?.package_base64;
        if (!b64) {
          message.error("导出失败：未获取到包内容");
          return;
        }
        // base64 → blob 下载
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        const blob = new Blob([bytes], { type: "application/zip" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${item.name}.zip`;
        a.click();
        URL.revokeObjectURL(url);
        message.success("已导出");
      } catch (e) {
        message.error("导出失败");
      }
    })();
  };

  return (
    <Card
      title="技能管理"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
          <Upload accept=".zip" showUploadList={false} beforeUpload={handleInstall}>
            <Button type="primary" icon={<UploadOutlined />}>
              安装技能 (ZIP)
            </Button>
          </Upload>
        </Space>
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={filtered}
        pagination={false}
        locale={{ emptyText: <Text type="secondary">暂无技能，点击右上角「安装技能 (ZIP)」上传</Text> }}
        columns={[
          {
            title: "名称",
            dataIndex: "name",
            render: (v: string, r: SkillRegistryItem) => (
              <Space size={4}>
                <Text strong>{v}</Text>
                <Tag color={SCOPE_COLOR[r.scope]}>{SCOPE_LABEL[r.scope]}</Tag>
              </Space>
            ),
          },
          { title: "描述", dataIndex: "description", ellipsis: true },
          { title: "版本", dataIndex: "version", width: 90, render: (v: string) => v || "-" },
          {
            title: "大小",
            dataIndex: "package_size",
            width: 100,
            render: (v: number) => fmtSize(v),
          },
          {
            title: "操作",
            width: 200,
            render: (_: unknown, r: SkillRegistryItem) => (
              <Space size={4}>
                <Button size="small" onClick={() => void openDetail(r)}>
                  详情
                </Button>
                <Button size="small" icon={<DownloadOutlined />} onClick={() => handleExport(r)}>
                  导出
                </Button>
                <Popconfirm title={`删除技能 ${r.name}？`} onConfirm={() => void handleDelete(r)}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <Drawer
        title={detailTarget ? `技能详情：${detailTarget.name}` : "技能详情"}
        width={840}
        open={!!detailTarget}
        onClose={() => setDetailTarget(null)}
        destroyOnClose
      >
        {detailTarget && (
          <div style={{ display: "flex", gap: 16, minHeight: 480 }}>
            {/* 左侧：元信息 + 文件树 */}
            <div
              style={{
                width: 250,
                flex: "0 0 250px",
                borderRight: "1px solid rgba(0,0,0,0.06)",
                paddingRight: 12,
                overflow: "auto",
                maxHeight: 620,
              }}
            >
              <Space wrap size={4} style={{ marginBottom: 10 }}>
                <Tag color={SCOPE_COLOR[detailTarget.scope]}>{SCOPE_LABEL[detailTarget.scope]}</Tag>
                <Tag>v{detailTarget.version || "-"}</Tag>
                <Text type="secondary" style={{ fontSize: 12 }}>{fmtSize(detailTarget.package_size)}</Text>
              </Space>
              {detailLoading ? (
                <Spin />
              ) : detailFiles.length === 0 ? (
                <Empty description="包内无文件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                <Tree
                  treeData={buildFileTree(detailFiles)}
                  selectedKeys={selectedPath ? [selectedPath] : []}
                  defaultExpandAll
                  onSelect={(keys) => {
                    const k = keys[0] as string | undefined;
                    if (k && detailTarget) void loadFile(detailTarget.id, k);
                  }}
                  showIcon={false}
                  blockNode
                />
              )}
            </div>
            {/* 右侧：文件预览 */}
            <div style={{ flex: 1, minWidth: 0 }}>
              {fileLoading ? (
                <div style={{ textAlign: "center", paddingTop: 120 }}>
                  <Spin />
                </div>
              ) : !fileData ? (
                <Empty description="从左侧选择文件预览" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : fileData.binary ? (
                <Empty description={`${fileData.path} 为二进制文件（${fmtSize(fileData.size)}），不支持内联预览，可「导出」整包`} />
              ) : fileData.truncated ? (
                <Empty description={`${fileData.path} 超过 512KB（${fmtSize(fileData.size)}），仅展示元信息`} />
              ) : isMarkdown(fileData.path) ? (
                <div
                  style={{
                    maxHeight: 620,
                    overflow: "auto",
                    padding: "0 10px",
                    fontSize: 13,
                    lineHeight: 1.7,
                  }}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{fileData.content}</ReactMarkdown>
                </div>
              ) : (
                <CodeViewer value={fileData.content} fileName={fileData.path} height={620} />
              )}
            </div>
          </div>
        )}
      </Drawer>
      <Modal
        title="安装技能"
        open={installOpen}
        onCancel={() => {
          setInstallOpen(false);
          setPendingFile(null);
        }}
        onOk={() => void confirmInstall()}
        okText="安装"
      >
        <Space direction="vertical" style={{ display: "flex" }} size={12}>
          <Text type="secondary">文件：{pendingFile?.name}</Text>
          <div>
            <Text>安装到</Text>
            <Radio.Group
              value={installScope}
              onChange={(e) => setInstallScope(e.target.value)}
              optionType="button"
              buttonStyle="solid"
              style={{ marginLeft: 8 }}
              options={[
                { label: "我的（仅自己）", value: "personal" },
                { label: "团队（团队共用）", value: "team" },
                ...(isAdmin ? [{ label: "系统（仅管理员）", value: "system" }] : []),
              ]}
            />
          </div>
        </Space>
      </Modal>
    </Card>
  );
}