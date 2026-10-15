"use client";

/**
 * 添加表弹窗（表→wiki，2026-10-10；2026-10-12 改多选）：从数据源选一张或多张维度表绑定到知识库。
 *
 * 流程（对齐元数据采集选表语义）：
 *  1. 选择数据源（可采集的 rdb 数据源列表）
 *  2. 加载该数据源下已采集的表（kb_metadata_table），可搜索表名，Checkbox 勾选多张
 *  3. 确认绑定 → POST /kbs/{kbId}/tables 逐张 → 建表文档 + 入队 KbTableIngestTask
 *
 * 表可绑定到多个知识库（用户拍板 D7：允许重复绑定），每次绑定各建一行。
 */
import { useEffect, useState } from "react";
import { App, Button, Checkbox, Empty, Input, List, Modal, Select, Space, Spin, Tag, Typography } from "antd";
import { DatabaseOutlined, TableOutlined } from "@ant-design/icons";
import {
  apiAddKbTable,
  apiDatagridDatasources,
  apiListTableCandidates,
  BuildEngineItem,
  DatagridDsItem,
  TableCandidateItem,
} from "@/lib/api";
import { groupBuildEngineOptions } from "@/lib/tape-func";

const { Text } = Typography;

interface Props {
  open: boolean;
  kbId: string;
  /** 可选的构建服务（服务编排中已发布的编排） */
  buildEngines?: BuildEngineItem[];
  /** 知识库默认构建引擎（kb_datasource.build_tape_id），用于展示「跟随默认」状态 */
  defaultBuildTapeId?: string;
  onCancel: () => void;
  onBound: () => void;
}

function formatRows(n?: number | null): string {
  if (n == null) return "—";
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万行`;
  return `${n} 行`;
}

export default function SelectTableDialog({
  open,
  kbId,
  buildEngines = [],
  defaultBuildTapeId,
  onCancel,
  onBound,
}: Props) {
  const { message } = App.useApp();
  const [dsList, setDsList] = useState<DatagridDsItem[]>([]);
  const [dsName, setDsName] = useState<string>("");
  const [tables, setTables] = useState<TableCandidateItem[]>([]);
  const [loadingTables, setLoadingTables] = useState(false);
  const [keyword, setKeyword] = useState("");
  // 多选：勾选行的 id 集合（2026-10-12 由单选对象改为多选，支持批量绑表）
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [binding, setBinding] = useState(false);
  const [buildEngine, setBuildEngine] = useState<string>("");

  // 打开时加载数据源列表
  useEffect(() => {
    if (!open) return;
    setDsName("");
    setSelectedIds(new Set());
    setKeyword("");
    setBuildEngine(""); // 每次打开重置为「跟随知识库默认」
    void apiDatagridDatasources()
      .then((res) => {
        if (res.success && res.data) setDsList(res.data);
        else setDsList([]);
      })
      .catch(() => setDsList([]));
  }, [open]);

  // 数据源变化 → 加载其已采集的表
  useEffect(() => {
    if (!open || !dsName) {
      setTables([]);
      return;
    }
    setLoadingTables(true);
    setSelectedIds(new Set());
    void apiListTableCandidates(kbId, dsName, keyword || undefined)
      .then((res) => {
        if (res.success && Array.isArray(res.data)) setTables(res.data);
        else setTables([]);
      })
      .catch(() => setTables([]))
      .finally(() => setLoadingTables(false));
  }, [open, dsName, keyword, kbId]);

  // 勾选/取消勾选某张表
  const toggleTable = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // 全选/取消全选当前列表
  const toggleSelectAll = () => {
    const allChecked = tables.length > 0 && tables.every((t) => selectedIds.has(t.id));
    setSelectedIds(allChecked ? new Set() : new Set(tables.map((t) => t.id)));
  };

  const confirmBind = async () => {
    const picked = tables.filter((t) => selectedIds.has(t.id));
    if (picked.length === 0) return;
    setBinding(true);
    let okCount = 0;
    const failed: string[] = [];
    for (const t of picked) {
      try {
        const res = await apiAddKbTable(kbId, {
          ds_name: t.ds_name,
          schema: t.schema || undefined,
          table_name: t.table_name,
          sample_size: 500,
          build_tape_id: buildEngine || undefined,
        });
        if (res.success && res.data) okCount += 1;
        else failed.push(t.table_name);
      } catch {
        failed.push(t.table_name);
      }
    }
    if (failed.length === 0) {
      message.success(`已绑定 ${okCount} 张表，后台处理中`);
      onBound();
    } else {
      message.warning(`成功绑定 ${okCount} 张，失败 ${failed.length} 张（${failed.join("、")}）`);
      if (okCount > 0) onBound();
    }
    setBinding(false);
  };

  // 可用的 rdb 数据源（对齐元数据采集：dsCategory=rdb 的可采集源）
  const rdbDs = dsList.filter((d) => (d.dsCategory || "rdb").toLowerCase() === "rdb" || !d.dsCategory);

  return (
    <Modal
      open={open}
      onCancel={onCancel}
      title={
        <Space>
          <DatabaseOutlined style={{ color: "#1677ff" }} />
          <span>添加表到知识库</span>
        </Space>
      }
      width={760}
      footer={
        <Space>
          <span style={{ fontSize: 12, color: "rgba(0,0,0,.45)" }}>
            {selectedIds.size > 0 ? `已选 ${selectedIds.size} 张表` : "请勾选要绑定的表"}
          </span>
          <Button onClick={onCancel}>取消</Button>
          <Button
            type="primary"
            disabled={selectedIds.size === 0}
            loading={binding}
            onClick={() => void confirmBind()}
          >
            绑定{selectedIds.size > 0 ? ` ${selectedIds.size} 张表` : ""}
          </Button>
        </Space>
      }
      destroyOnClose
    >
      {/* 构建服务（文档级重选，覆盖 KB 默认） */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "2px 0 12px",
          borderBottom: "1px solid #f0f0f0",
          marginBottom: 12,
        }}
      >
        <span style={{ fontSize: 13, color: "rgba(0,0,0,.85)", fontWeight: 600, flexShrink: 0 }}>
          构建服务
        </span>
        <Select
          style={{ width: 320 }}
          allowClear
          placeholder={defaultBuildTapeId ? "跟随知识库默认（已选）" : "跟随知识库默认（未设置）"}
          value={buildEngine || undefined}
          onChange={setBuildEngine}
          options={groupBuildEngineOptions(buildEngines)}
        />
        <span style={{ fontSize: 12, color: "rgba(0,0,0,.45)" }}>
          选择后绑定表构建 Wiki 走该编排，留空跟随知识库默认
        </span>
      </div>
      <div style={{ display: "flex", gap: 16, minHeight: 320 }}>
        {/* 左：数据源选择 */}
        <div style={{ width: 240, flexShrink: 0 }}>
          <Text strong style={{ fontSize: 13 }}>数据源</Text>
          <Select
            style={{ width: "100%", marginTop: 8 }}
            placeholder="选择数据源"
            value={dsName || undefined}
            onChange={setDsName}
            showSearch
            optionFilterProp="label"
            options={rdbDs.map((d) => ({ value: d.dsName, label: `${d.dsLabel || d.dsName}（${d.dsType || "?"}）` }))}
          />
          <div style={{ marginTop: 8, fontSize: 12, color: "rgba(0,0,0,.45)" }}>
            仅显示已配置的数据库数据源；表清单来自元数据采集结果（未采集的数据源请先在「系统 → 元数据采集」执行采集）。
          </div>
        </div>

        {/* 右：表选择 */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <Space>
              <Text strong style={{ fontSize: 13 }}>表</Text>
              <Checkbox
                checked={tables.length > 0 && tables.every((t) => selectedIds.has(t.id))}
                indeterminate={tables.some((t) => selectedIds.has(t.id)) && !tables.every((t) => selectedIds.has(t.id))}
                onChange={toggleSelectAll}
                disabled={!tables.length}
                style={{ fontSize: 12 }}
              >
                全选
              </Checkbox>
            </Space>
            <Input
              size="small"
              allowClear
              style={{ width: 180 }}
              placeholder="搜索表名"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              disabled={!dsName}
            />
          </div>
          <div style={{ height: 260, overflow: "auto", border: "1px solid #f0f0f0", borderRadius: 6 }}>
            {loadingTables ? (
              <div style={{ textAlign: "center", padding: 24 }}>
                <Spin size="small" />
              </div>
            ) : tables.length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={dsName ? "暂无已采集的表" : "请先选择数据源"}
              />
            ) : (
              <List
                size="small"
                dataSource={tables}
                renderItem={(t) => {
                  const checked = selectedIds.has(t.id);
                  return (
                    <List.Item
                      style={{ cursor: "pointer", padding: "8px 12px" }}
                      onClick={() => toggleTable(t.id)}
                      className={checked ? "kb-select-table-row-active" : ""}
                    >
                      <Space direction="vertical" size={0} style={{ width: "100%" }}>
                        <Space>
                          <Checkbox
                            checked={checked}
                            onClick={(e) => e.stopPropagation()}
                            onChange={() => toggleTable(t.id)}
                          />
                          <TableOutlined style={{ color: "#1677ff" }} />
                          <Text style={{ fontSize: 13 }}>{t.table_name}</Text>
                          <Tag style={{ fontSize: 11 }}>{t.table_type || "TABLE"}</Tag>
                          <span style={{ fontSize: 12, color: "rgba(0,0,0,.45)" }}>{formatRows(t.row_count)}</span>
                        </Space>
                        {t.table_comment ? (
                          <Text type="secondary" style={{ fontSize: 12, marginLeft: 26 }} ellipsis>
                            {t.table_comment}
                          </Text>
                        ) : null}
                      </Space>
                    </List.Item>
                  );
                }}
              />
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
