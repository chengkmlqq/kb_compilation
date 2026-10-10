"use client";

/**
 * 添加表弹窗（表→wiki，2026-10-10）：从数据源选一张维度表绑定到知识库。
 *
 * 流程（对齐元数据采集选表语义）：
 *  1. 选择数据源（可采集的 rdb 数据源列表）
 *  2. 加载该数据源下已采集的表（kb_metadata_table），可搜索表名
 *  3. 确认绑定 → POST /kbs/{kbId}/tables → 建表文档 + 入队 KbTableIngestTask
 *
 * 表可绑定到多个知识库（用户拍板 D7：允许重复绑定），每次绑定各建一行。
 */
import { useEffect, useState } from "react";
import { App, Button, Empty, Input, List, Modal, Select, Space, Spin, Tag, Typography } from "antd";
import { DatabaseOutlined, TableOutlined } from "@ant-design/icons";
import {
  apiAddKbTable,
  apiDatagridDatasources,
  apiListTableCandidates,
  DatagridDsItem,
  TableCandidateItem,
} from "@/lib/api";

const { Text } = Typography;

interface Props {
  open: boolean;
  kbId: string;
  onCancel: () => void;
  onBound: () => void;
}

function formatRows(n?: number | null): string {
  if (n == null) return "—";
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万行`;
  return `${n} 行`;
}

export default function SelectTableDialog({ open, kbId, onCancel, onBound }: Props) {
  const { message } = App.useApp();
  const [dsList, setDsList] = useState<DatagridDsItem[]>([]);
  const [dsName, setDsName] = useState<string>("");
  const [tables, setTables] = useState<TableCandidateItem[]>([]);
  const [loadingTables, setLoadingTables] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [selected, setSelected] = useState<TableCandidateItem | null>(null);
  const [binding, setBinding] = useState(false);

  // 打开时加载数据源列表
  useEffect(() => {
    if (!open) return;
    setDsName("");
    setSelected(null);
    setKeyword("");
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
    setSelected(null);
    void apiListTableCandidates(kbId, dsName, keyword || undefined)
      .then((res) => {
        if (res.success && Array.isArray(res.data)) setTables(res.data);
        else setTables([]);
      })
      .catch(() => setTables([]))
      .finally(() => setLoadingTables(false));
  }, [open, dsName, keyword, kbId]);

  const confirmBind = async () => {
    if (!selected) return;
    setBinding(true);
    try {
      const res = await apiAddKbTable(kbId, {
        ds_name: selected.ds_name,
        schema: selected.schema || undefined,
        table_name: selected.table_name,
        sample_size: 500,
      });
      if (res.success && res.data) {
        message.success(`已绑定表「${selected.table_name}」，后台处理中`);
        onBound();
      } else {
        message.error(res.message || "绑定失败");
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : "绑定失败");
    } finally {
      setBinding(false);
    }
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
          <Button onClick={onCancel}>取消</Button>
          <Button type="primary" disabled={!selected} loading={binding} onClick={() => void confirmBind()}>
            绑定表
          </Button>
        </Space>
      }
      destroyOnClose
    >
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
            <Text strong style={{ fontSize: 13 }}>表</Text>
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
                renderItem={(t) => (
                  <List.Item
                    style={{ cursor: "pointer", padding: "8px 12px" }}
                    onClick={() => setSelected(t)}
                    className={selected?.id === t.id ? "kb-select-table-row-active" : ""}
                  >
                    <Space direction="vertical" size={0} style={{ width: "100%" }}>
                      <Space>
                        <TableOutlined style={{ color: "#1677ff" }} />
                        <Text style={{ fontSize: 13 }}>{t.table_name}</Text>
                        <Tag style={{ fontSize: 11 }}>{t.table_type || "TABLE"}</Tag>
                        <span style={{ fontSize: 12, color: "rgba(0,0,0,.45)" }}>{formatRows(t.row_count)}</span>
                      </Space>
                      {t.table_comment ? (
                        <Text type="secondary" style={{ fontSize: 12, marginLeft: 22 }} ellipsis>
                          {t.table_comment}
                        </Text>
                      ) : null}
                    </Space>
                  </List.Item>
                )}
              />
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
