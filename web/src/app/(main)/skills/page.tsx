"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import { DeleteOutlined, DownloadOutlined, PlusOutlined, ReloadOutlined, UploadOutlined } from "@ant-design/icons";
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
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export default function SkillManagePage() {
  const { message } = App.useApp();
  const [items, setItems] = useState<SkillRegistryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [scope, setScope] = useState<ModelScope>("system");
  const [detailTarget, setDetailTarget] = useState<SkillRegistryItem | null>(null);
  const [detailText, setDetailText] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(
    async (s?: ModelScope) => {
      setLoading(true);
      try {
        const res = await apiListSkillsRegistry(s ?? scope);
        if (res.success && res.data) {
          setItems(res.data.items);
          setIsAdmin(!!res.data.is_admin);
        } else {
          message.error(res.message || "加载失败");
        }
      } finally {
        setLoading(false);
      }
    },
    [scope, message],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const visibleScopes = useMemo(() => {
    if (isAdmin) return ["personal", "team", "system"] as ModelScope[];
    return ["personal", "team"] as ModelScope[];
  }, [isAdmin]);

  const filtered = useMemo(() => items.filter((it) => it.scope === scope), [items, scope]);

  const handleDelete = async (item: SkillRegistryItem) => {
    const res = await apiDeleteSkillRegistry(item.id);
    if (res.success) {
      message.success(`已删除技能 ${item.name}`);
      void load();
    } else {
      message.error(res.message || "删除失败");
    }
  };

  const handleInstall = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".zip")) {
      message.warning("仅支持 ZIP 文件");
      return false;
    }
    const res = await apiInstallSkillRegistry(file, scope);
    if (res.success) {
      message.success(`技能已安装：${res.data?.item.name ?? file.name}`);
      void load();
    } else {
      message.error(res.message || "安装失败");
    }
    return false;
  };

  const openDetail = async (item: SkillRegistryItem) => {
    setDetailTarget(item);
    setDetailText("");
    setDetailLoading(true);
    try {
      // 详情走旧代理？不——直接请求新注册表详情端点（含 package_base64）
      const res = await fetch(`/api/v1/skills/${item.id}`, {
        headers: { "Content-Type": "application/json" },
      });
      const json = await res.json();
      if (json.success && json.data) {
        const d = json.data;
        setDetailText(
          d.sk_markdown_preview ||
            d.item?.description ||
            "（无内容）",
        );
      } else {
        setDetailText("加载失败");
      }
    } catch {
      setDetailText("加载失败");
    } finally {
      setDetailLoading(false);
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
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="技能按 ZIP 包安装（含 SKILL.md，服务端校验解析 name/description/version）。分三级：我的（仅本人）/ 团队（团队成员）/ 系统（仅管理员）。安装后后台任务通过 agent-gateway 无状态执行。"
      />
      <Space style={{ marginBottom: 16 }}>
        <span>安装/查看级别：</span>
        <Radio.Group
          value={scope}
          onChange={(e) => {
            setScope(e.target.value);
            void load(e.target.value);
          }}
          optionType="button"
          buttonStyle="solid"
          options={visibleScopes.map((s) => ({ label: SCOPE_LABEL[s], value: s }))}
        />
        <Text type="secondary" style={{ fontSize: 12 }}>
          安装到当前选中级别，列表仅展示该级别
        </Text>
      </Space>
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
        width={560}
        open={!!detailTarget}
        onClose={() => setDetailTarget(null)}
        destroyOnClose
      >
        {detailTarget && (
          <Space direction="vertical" style={{ display: "flex" }} size={12}>
            <Space wrap>
              <Tag color={SCOPE_COLOR[detailTarget.scope]}>{SCOPE_LABEL[detailTarget.scope]}</Tag>
              <Tag>v{detailTarget.version || "-"}</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>{fmtSize(detailTarget.package_size)}</Text>
            </Space>
            {detailLoading ? (
              <Spin />
            ) : (
              <pre style={{ whiteSpace: "pre-wrap", margin: 0, fontSize: 12 }}>{detailText}</pre>
            )}
          </Space>
        )}
      </Drawer>
    </Card>
  );
}