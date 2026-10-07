"use client";

/**
 * 系统 → 引擎与存储：向量库类型 / 存储类型 / 解析引擎管理开关 + 系统信息。
 * 对齐 WeKnora 的引擎配置（PARSER_ENGINES 管理员开关）与 SystemInfo 展示。
 * 端点：/system/vector-store-types、/system/storage-types、
 *       /parsers/engines（GET 现有）、/parsers/engines/{name}/enabled（PUT）、
 *       /system/info
 */
import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Col, Descriptions, Row, Space, Spin, Switch, Tag, Typography } from "antd";
import {
  apiParserEngines,
  apiSetParserEngineEnabled,
  apiStorageTypes,
  apiSystemInfo,
  apiVectorStoreTypes,
  EngineInfoItem,
  StorageTypeItem,
  SystemInfoItem,
  VectorStoreTypeItem,
} from "@/lib/api";

const { Text, Title } = Typography;

function AvailTag({ available }: { available: boolean }) {
  return available ? (
    <Tag color="green">可用</Tag>
  ) : (
    <Tag color="default">不可用</Tag>
  );
}

export default function EnginesPage() {
  const { message } = App.useApp();
  const [info, setInfo] = useState<SystemInfoItem | null>(null);
  const [vectorTypes, setVectorTypes] = useState<VectorStoreTypeItem[]>([]);
  const [storageTypes, setStorageTypes] = useState<StorageTypeItem[]>([]);
  const [engines, setEngines] = useState<EngineInfoItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [infoRes, vtRes, stRes, engRes] = await Promise.all([
        apiSystemInfo(),
        apiVectorStoreTypes(),
        apiStorageTypes(),
        apiParserEngines(),
      ]);
      if (infoRes.success) setInfo(infoRes.data ?? null);
      if (vtRes.success) setVectorTypes(vtRes.data || []);
      if (stRes.success) setStorageTypes(stRes.data || []);
      if (engRes.success) setEngines(engRes.data || []);
    } catch {
      message.error("加载引擎信息失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleEngine = async (name: string, enabled: boolean) => {
    setToggling(name);
    try {
      const res = await apiSetParserEngineEnabled(name, enabled);
      if (res.success) {
        message.success(enabled ? `已启用 ${name}` : `已禁用 ${name}`);
        void load();
      } else {
        message.error(res.message || "操作失败");
      }
    } catch {
      message.error("操作失败");
    } finally {
      setToggling(null);
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Spin spinning={loading}>
        <Row gutter={[16, 16]}>
          {/* 系统信息 */}
          <Col span={24}>
            <Card size="small" title="系统信息">
              {info ? (
                <Descriptions size="small" column={3}>
                  <Descriptions.Item label="版本">
                    <Text>{info.version}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="向量库后端">
                    <Tag color={info.vector_store_type === "pg" ? "blue" : "orange"}>
                      {info.vector_store_type === "pg" ? "pgvector" : info.vector_store_type}
                    </Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="对象存储">
                    <AvailTag available={info.minio_enabled} />
                  </Descriptions.Item>
                  <Descriptions.Item label="解析引擎">
                    {info.engines_available}/{info.engines_total} 可用
                  </Descriptions.Item>
                  <Descriptions.Item label="服务器时间">
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {info.server_time}
                    </Text>
                  </Descriptions.Item>
                </Descriptions>
              ) : (
                <Text type="secondary">加载失败</Text>
              )}
            </Card>
          </Col>

          {/* 向量库类型 */}
          <Col span={12}>
            <Card size="small" title="向量库类型">
              {vectorTypes.length === 0 ? (
                <Text type="secondary">无</Text>
              ) : (
                vectorTypes.map((v) => (
                  <div key={v.code} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: "1px solid #f5f5f5" }}>
                    <div>
                      <Text strong>{v.label}</Text>
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {v.description}
                        </Text>
                      </div>
                    </div>
                    <AvailTag available={v.available} />
                  </div>
                ))
              )}
            </Card>
          </Col>

          {/* 存储类型 */}
          <Col span={12}>
            <Card size="small" title="存储类型">
              {storageTypes.length === 0 ? (
                <Text type="secondary">无</Text>
              ) : (
                storageTypes.map((s) => (
                  <div key={s.code} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: "1px solid #f5f5f5" }}>
                    <div>
                      <Text strong>{s.label}</Text>
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {s.description}
                        </Text>
                      </div>
                    </div>
                    <AvailTag available={s.available} />
                  </div>
                ))
              )}
            </Card>
          </Col>

          {/* 解析引擎开关 */}
          <Col span={24}>
            <Card
              size="small"
              title="解析引擎"
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  禁用后文档解析将按兜底引擎路由（worker 15 秒内生效）
                </Text>
              }
            >
              {engines.length === 0 ? (
                <Text type="secondary">无</Text>
              ) : (
                engines.map((e) => (
                  <div key={e.name} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 0", borderBottom: "1px solid #f5f5f5" }}>
                    <div>
                      <Text strong>{e.display_name}</Text>
                      <Tag style={{ marginLeft: 8 }}>{e.name}</Tag>
                      <Tag>{e.file_types?.join(" / ")}</Tag>
                      <div>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {e.description}
                        </Text>
                      </div>
                      {!e.available && (
                        <div>
                          <Text type="danger" style={{ fontSize: 12 }}>
                            {e.reason}
                          </Text>
                        </div>
                      )}
                    </div>
                    <Space>
                      <AvailTag available={e.available} />
                      <Switch
                        checked={e.enabled !== false}
                        disabled={toggling === e.name}
                        onChange={(v) => void toggleEngine(e.name, v)}
                      />
                    </Space>
                  </div>
                ))
              )}
            </Card>
          </Col>
        </Row>
      </Spin>
    </Space>
  );
}
