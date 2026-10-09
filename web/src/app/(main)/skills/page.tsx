"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  App,
  Button,
  Card,
  Drawer,
  Empty,
  Modal,
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
import { ReloadOutlined, UploadOutlined } from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import CodeViewer from "@/components/CodeViewer";
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoActionGroup } from "@/components/biz/modo-action-group";
import { ModoTabs } from "@/components/biz/modo-tabs";
import {
  apiDeleteSkillRegistry,
  apiInstallSkillRegistryWithProgress,
  apiListSkillsRegistry,
  SkillRegistryItem,
  ModelScope,
} from "@/lib/api";
import { emitUploadTask, makeUploadTaskId, onFileDrop, setFileDropTarget } from "@/lib/upload-bus";

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

/** 动态选项卡条目（复用用户管理页模式）：首个 tab 固定为「页面标题 + 列表」，其余为可关闭的业务内容 */
type SkillTab = { key: string; title: string; children: ReactNode };

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
  const { message, modal } = App.useApp();
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
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // ---- 动态选项卡（复用用户管理页 ModoTabs 模式）----
  // 首个 tab「技能管理」= 页面标题 + 列表，不可关闭；后续内容较多的详情/编辑表单可 push 进 tabs 以选项卡打开。
  // 本轮先预留能力（容器 + 开关 tab 机制就位），具体接入哪些内容后续再定。
  const [tabs, setTabs] = useState<SkillTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) setActiveTab("home");
  };

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
  // 客户端分页（对齐 data-synth 常驻底栏分页）
  const totalRows = filtered.length;
  const safePage = Math.min(page, Math.max(1, Math.ceil(totalRows / pageSize)));
  const pagedItems = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);

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

  // 带进度上报的技能安装（经 uploadTask 事件驱动任务浮层，支持重试）
  const installSkill = (file: File, scope: ModelScope, taskId: string) => {
    emitUploadTask({
      id: taskId,
      name: file.name,
      size: file.size,
      status: "uploading",
      progress: 0,
      retry: () => installSkill(file, scope, taskId),
    });
    void (async () => {
      try {
        const res = await apiInstallSkillRegistryWithProgress(file, scope, (pct) => {
          emitUploadTask({ id: taskId, name: file.name, size: file.size, status: "uploading", progress: pct });
        });
        if (res.success) {
          emitUploadTask({ id: taskId, name: file.name, size: file.size, status: "success", progress: 100 });
          message.success(`技能已安装：${res.data?.item.name ?? file.name}`);
          setInstallOpen(false);
          setPendingFile(null);
          void load();
        } else {
          emitUploadTask({
            id: taskId,
            name: file.name,
            size: file.size,
            status: "error",
            progress: 100,
            error: res.message || "安装失败",
          });
          message.error(res.message || "安装失败");
        }
      } catch (e) {
        emitUploadTask({
          id: taskId,
          name: file.name,
          size: file.size,
          status: "error",
          progress: 100,
          error: e instanceof Error ? e.message : "安装失败",
        });
      }
    })();
  };

  const confirmInstall = () => {
    if (!pendingFile) return;
    installSkill(pendingFile, installScope, makeUploadTaskId("skill-install"));
  };

  // 全局拖放落点：仅接受单个 .zip 技能包（目录拖入会被递归展开并统计文件数）
  const handleGlobalSkillFiles = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;
      const zips = files.filter((f) => f.name.toLowerCase().endsWith(".zip"));
      if (files.length === 1 && zips.length === 1) {
        handleInstall(zips[0]);
        return;
      }
      if (zips.length === 1) {
        message.info(`拖入了 ${files.length} 个文件（含目录展开），将安装其中的 ${zips[0].name}`);
        handleInstall(zips[0]);
        return;
      }
      message.warning(
        `拖入了 ${files.length} 个文件（含目录展开）；技能安装仅支持单个 .zip 包${zips.length > 1 ? `（收到 ${zips.length} 个 .zip）` : ""}`,
      );
    },
    [message],
  );

  // 全局拖放：注册目标（遮罩文案「技能管理」）+ 监听 kbFileDrop 兜底事件
  useEffect(() => {
    setFileDropTarget({ label: "技能管理" });
    const off = onFileDrop(handleGlobalSkillFiles);
    return () => {
      off();
      setFileDropTarget(null);
    };
  }, [handleGlobalSkillFiles]);

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

  // 列表区（首个选项卡内容）：表格 + 钉底分页 + 详情抽屉 + 安装弹窗
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
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={pagedItems}
        pagination={false}
        scroll={{ x: 1000, y: "calc(100vh - 208px)" }}
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
            width: 140,
            render: (_: unknown, r: SkillRegistryItem) => (
              <ModoActionGroup
                maxCount={2}
                actions={[
                  { key: "detail", label: "详情", onClick: () => void openDetail(r) },
                  { key: "export", label: "导出", onClick: () => handleExport(r) },
                  {
                    key: "delete",
                    label: "删除",
                    danger: true,
                    onClick: () =>
                      modal.confirm({
                        title: `确定删除技能 ${r.name}？`,
                        onOk: () => handleDelete(r),
                      }),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      <div style={{ flexShrink: 0, marginTop: "auto" }}>
        <ModoPagination
          current={safePage}
          pageSize={pageSize}
          total={totalRows}
          showTotal={(t) => `共 ${t} 个技能`}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      </div>
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
    </div>
  );

  return (
    <div
      className="skills-page"
      style={{ padding: 8, height: "calc(100vh - 45px)", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
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
        items={[
          { key: "home", label: "技能管理", children: listPane },
          ...tabs.map((t) => ({ key: t.key, label: t.title, closable: true, children: t.children })),
        ]}
      />
    </div>
  );
}