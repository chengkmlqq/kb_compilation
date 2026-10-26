"use client";

import { useCallback, useEffect, useState } from "react";
import { App, Button, Descriptions, Space, Spin, Tag, Tooltip, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import {
  apiRepoDetail,
  apiRepoPull,
  RepoDetail,
} from "@/lib/api";

const { Text } = Typography;

const AUTH_TYPE_LABEL: Record<string, string> = {
  none: "无凭证（公开仓库）",
  token: "Token（访问令牌）",
  ssh_key: "SSH 私钥",
  user_pass: "用户名 + 密码",
};

const PULL_STATUS_META: Record<
  string,
  { label: string; color: string }
> = {
  idle: { label: "未同步", color: "default" },
  pulling: { label: "同步中", color: "processing" },
  success: { label: "同步成功", color: "success" },
  failed: { label: "同步失败", color: "error" },
};

interface Props {
  kbId: string;
}

/** 知识库详情「代码仓库」页签：展示拉取状态 + 手动触发同步（2026-10） */
export default function CodeRepoPane({ kbId }: Props) {
  const { message } = App.useApp();
  const [detail, setDetail] = useState<RepoDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [pulling, setPulling] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiRepoDetail(kbId);
      if (res.success) setDetail(res.data ?? null);
    } catch {
      // 非代码仓库知识库/加载失败不展示
    } finally {
      setLoading(false);
    }
  }, [kbId]);

  useEffect(() => {
    void load();
  }, [load]);

  // 同步进行中轮询状态（pulling 时每 5s 刷新一次）
  useEffect(() => {
    if (detail?.repo_pull_status !== "pulling") return;
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [detail?.repo_pull_status, load]);

  const onPull = async () => {
    setPulling(true);
    try {
      const res = await apiRepoPull(kbId);
      if (res.success) {
        message.success("已触发代码仓库同步，请稍候刷新查看状态");
        setDetail((prev) => (prev ? { ...prev, repo_pull_status: "pulling" } : prev));
      } else {
        message.error(res.message || "同步触发失败");
      }
    } catch (exc) {
      message.error(`同步触发失败: ${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setPulling(false);
    }
  };

  if (loading && !detail) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 60 }}>
        <Spin />
      </div>
    );
  }
  if (!detail) return null;

  const statusMeta = PULL_STATUS_META[detail.repo_pull_status] ?? PULL_STATUS_META.idle;
  const shortCommit = detail.repo_commit_sha ? detail.repo_commit_sha.slice(0, 8) : "-";

  return (
    <div style={{ padding: 24, maxWidth: 860 }}>
      <Spin spinning={loading}>
      <Space style={{ marginBottom: 16 }} size={12}>
        <Button
          type="primary"
          icon={<ReloadOutlined />}
          loading={pulling || detail.repo_pull_status === "pulling"}
          onClick={() => void onPull()}
          disabled={detail.repo_pull_status === "pulling"}
        >
          {detail.repo_pull_status === "pulling" ? "同步中…" : "立即同步"}
        </Button>
        <Tooltip title="同步将重新拉取仓库最新代码，目录/文件差异会自动增删改知识库文档并触发解析构建">
          <Text type="secondary" style={{ fontSize: 12 }}>
            重新拉取仓库代码并按差异更新知识库
          </Text>
        </Tooltip>
      </Space>

      <Descriptions
        bordered
        size="small"
        column={2}
        items={[
          { key: "url", label: "仓库地址", children: detail.repo_url || "-" },
          { key: "branch", label: "分支", children: detail.repo_branch || "默认分支" },
          {
            key: "auth",
            label: "拉取凭证",
            children: AUTH_TYPE_LABEL[detail.repo_auth_type] ?? detail.repo_auth_type,
          },
          {
            key: "status",
            label: "同步状态",
            children: (
              <Space size={8}>
                <Tag color={statusMeta.color}>{statusMeta.label}</Tag>
                {detail.repo_pull_status === "failed" && detail.repo_pull_error && (
                  <Tooltip title={detail.repo_pull_error}>
                    <Text type="danger" style={{ fontSize: 12 }}>
                      {(detail.repo_pull_error ?? "").slice(0, 60)}
                      {(detail.repo_pull_error ?? "").length > 60 ? "…" : ""}
                    </Text>
                  </Tooltip>
                )}
              </Space>
            ),
          },
          {
            key: "commit",
            label: "最近同步 Commit",
            children: shortCommit === "-" ? "-" : (
              <Text code>{shortCommit}</Text>
            ),
          },
          {
            key: "files",
            label: "文件数",
            children: detail.repo_file_count ?? 0,
          },
          {
            key: "last",
            label: "最近同步时间",
            children: detail.repo_last_pull_at
              ? new Date(detail.repo_last_pull_at).toLocaleString()
              : "-",
          },
        ]}
      />
      <div
        style={{
          marginTop: 16,
          padding: 12,
          background: "#f5f7fa",
          borderRadius: 6,
          color: "#4d5e7d",
          fontSize: 12,
        }}
      >
        说明：仓库代码存放在对象存储中，仓库目录已镜像为知识库「文档」页签的目录树；每个文件进入解析与
        Wiki 构建编排链路，可在「文档」与「Wiki」页签查看处理结果，任务监控页可查看编排执行进度。
      </div>
      </Spin>
    </div>
  );
}
