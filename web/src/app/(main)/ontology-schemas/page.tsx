"use client";

import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
} from "antd";
import {
  ApiOutlined,
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import ModoTable from "@/components/biz/modo-table";
import ModoPagination from "@/components/biz/modo-pagination";

import {
  apiBindKbOntologySchema,
  apiCopyOntologySchema,
  apiCreateOntologyCategory,
  apiDeleteOntologyCategory,
  apiListKbs,
  apiListOntologySchemas,
  apiUpdateOntologyCategory,
  OntologyCategory,
  OntologyCategoryPayload,
  OntologySchemaGroup,
} from "@/lib/api";

const DIM_LABEL: Record<string, string> = { business: "业务本体", rule: "规则本体" };

export default function OntologySchemasPage() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [groups, setGroups] = useState<OntologySchemaGroup[]>([]);
  const [current, setCurrent] = useState<OntologySchemaGroup | null>(null);
  const [dimTab, setDimTab] = useState<"business" | "rule">("business");
  const [editing, setEditing] = useState<OntologyCategory | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);
  const [bindOpen, setBindOpen] = useState(false);
  const [kbList, setKbList] = useState<{ id: string; name: string }[]>([]);
  const [form] = Form.useForm();
  const [copyForm] = Form.useForm();
  const [bindForm] = Form.useForm();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiListOntologySchemas();
      setGroups(res.data?.schemas ?? []);
      const cur = (res.data?.schemas ?? []).find((s) => s.schema_name === current?.schema_name) ?? (res.data?.schemas ?? [])[0] ?? null;
      setCurrent(cur);
    } catch (e) {
      message.error(`加载失败: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }, [current?.schema_name, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    form.setFieldsValue({
      schema_name: current?.schema_name,
      dimension: dimTab,
      cat_no: (current ? current[dimTab].length : 0) + 1,
      cat_name: "",
      prompt_hint: "",
      neo4j_edge: "",
      cat_label: "",
    });
    setModalOpen(true);
  };

  const openEdit = (item: OntologyCategory) => {
    setEditing(item);
    form.setFieldsValue({
      schema_name: item.schema_name,
      dimension: item.dimension,
      cat_no: item.cat_no,
      cat_name: item.cat_name,
      prompt_hint: item.prompt_hint ?? "",
      neo4j_edge: item.neo4j_edge ?? "",
      cat_label: item.cat_label,
    });
    setModalOpen(true);
  };

  const submit = async () => {
    const v = await form.validateFields();
    const payload: OntologyCategoryPayload = {
      ...v,
      dimension: editing?.dimension ?? dimTab,
      schema_name: current?.schema_name ?? v.schema_name,
    };
    try {
      if (editing) {
        await apiUpdateOntologyCategory(editing.id, payload);
        message.success("分类已更新");
      } else {
        await apiCreateOntologyCategory(payload);
        message.success("分类已新增");
      }
      setModalOpen(false);
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const remove = async (item: OntologyCategory) => {
    try {
      await apiDeleteOntologyCategory(item.id);
      message.success("分类已删除");
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const doCopy = async () => {
    const v = await copyForm.validateFields();
    try {
      const res = await apiCopyOntologySchema(v.source_schema, v.new_schema_name, v.new_schema_label ?? "");
      message.success(`已复制 ${res.data?.copied} 个分类到《${res.data?.schema_name}》`);
      setCopyOpen(false);
      copyForm.resetFields();
      void load();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  const loadKbs = async () => {
    try {
      const res = await apiListKbs(1, 500);
      setKbList(
        ((res as unknown as { items?: Array<{ id: string; name: string }> }).items ?? []).map((k) => ({
          id: k.id,
          name: k.name,
        })),
      );
    } catch {
      /* 选用时再提示 */
    }
  };

  const doBind = async () => {
    const v = await bindForm.validateFields();
    try {
      await apiBindKbOntologySchema(v.kb_id, v.schema_name ?? null);
      message.success("绑定已更新");
      setBindOpen(false);
      bindForm.resetFields();
    } catch (e) {
      message.error((e as Error).message);
    }
  };

  if (loading && groups.length === 0) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 80 }}>
        <Spin />
      </div>
    );
  }

  const cats = current ? current[dimTab] : [];
  const isRule = dimTab === "rule";
  // 客户端分页（对齐 data-synth 常驻底栏分页；切换维度/Schema 时安全回退到第 1 页）
  const totalRows = cats.length;
  const safePage = Math.min(page, Math.max(1, Math.ceil(totalRows / pageSize)));
  const pagedCats = cats.slice((safePage - 1) * pageSize, safePage * pageSize);

  const columns = [
    { title: "序号", dataIndex: "cat_no", width: 60 },
    { title: "类别名", dataIndex: "cat_name", width: 160 },
    {
      title: "目录名",
      dataIndex: "cat_label",
      width: 150,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: "LLM 提示词段（识别要点）",
      dataIndex: "prompt_hint",
      ellipsis: true,
      render: (v: string | null) => v || <span style={{ color: "#999" }}>（未设置）</span>,
    },
    {
      title: "Neo4j 边类型",
      dataIndex: "neo4j_edge",
      width: 130,
      render: (v: string | null) => (v ? <Tag color="blue">{v}</Tag> : "-"),
    },
    {
      title: "操作",
      width: 110,
      render: (_: unknown, item: OntologyCategory) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(item)} />
          <Popconfirm title={`删除「${item.cat_name}」？`} onConfirm={() => void remove(item)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
      // 一屏自适应（对齐 data-synth）：外层不滚动，卡片内表格占满剩余高度，分页常驻底栏
      <div style={{ padding: 8, height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
        <Card
          title="本体 Schema"
          style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
          styles={{ body: { flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" } }}
          extra={
        <Space>
          <Button icon={<ApiOutlined />} onClick={() => { void loadKbs(); bindForm.setFieldsValue({ schema_name: current?.schema_name }); setBindOpen(true); }}>
            绑定知识库
          </Button>
          <Button icon={<CopyOutlined />} onClick={() => { copyForm.setFieldsValue({ source_schema: current?.schema_name }); setCopyOpen(true); }}>
            复制为新的领域
          </Button>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增分类
          </Button>
        </Space>
      }
    >
            {current && (
              <Descriptions style={{ marginBottom: 16, flexShrink: 0 }} size="small" column={4} bordered>
            <Descriptions.Item label="Schema 名称">{current.schema_name}</Descriptions.Item>
            <Descriptions.Item label="标签">{current.schema_label}</Descriptions.Item>
            <Descriptions.Item label="业务本体">{current.business.length} 类</Descriptions.Item>
            <Descriptions.Item label="规则本体">{current.rule.length} 类</Descriptions.Item>
            <Descriptions.Item label="描述" span={4}>
              {current.schema_desc || "-"}
            </Descriptions.Item>
          </Descriptions>
        )}
        <Select
          style={{ width: 320, marginBottom: 16, flexShrink: 0 }}
          placeholder="切换 Schema（领域）"
          value={current?.schema_name}
          onChange={(v: string) => {
            setCurrent(groups.find((g) => g.schema_name === v) ?? null);
            setPage(1);
          }}
          options={groups.map((g) => ({
            value: g.schema_name,
            label: `${g.schema_label}（${g.schema_name}）`,
          }))}
        />
        <Tabs
          activeKey={dimTab}
          onChange={(k) => {
            setDimTab(k as "business" | "rule");
            setPage(1);
          }}
          style={{ marginBottom: 16, flexShrink: 0 }}
          items={[
            { key: "business", label: `业务本体 (${current?.business.length ?? 0})` },
            { key: "rule", label: `规则本体 (${current?.rule.length ?? 0})` },
          ]}
        />
        <ModoTable
                  rowKey="id"
                  size="small"
                  dataSource={pagedCats}
                  columns={columns}
                />
                <ModoPagination
                  current={safePage}
                  pageSize={pageSize}
                  total={totalRows}
                  showTotal={(t) => `共 ${t} 条`}
                  onChange={(p, ps) => {
                    setPage(p);
                    setPageSize(ps);
                  }}
                />
                <div style={{ color: "#999", fontSize: 12, flexShrink: 0, marginTop: 4 }}>
                  提示：每个领域一套 Schema（业务本体 + 规则本体双维度）。分类的 LLM 提示词段决定抽取识别要点；
                  规则类的 Neo4j 边类型决定图谱关系动词。构建技能运行时按知识库绑定动态读取。
                  {isRule ? "" : ""}
                </div>

      <Modal
        title={editing ? `编辑分类（${DIM_LABEL[editing.dimension]}）` : `新增分类（${DIM_LABEL[dimTab]}）`}
        open={modalOpen}
        onOk={() => void submit()}
        onCancel={() => setModalOpen(false)}
        width={560}
      >
        <Form form={form} layout="vertical" initialValues={{ cat_no: 1 }}>
          <Form.Item name="schema_name" label="Schema" rules={[{ required: true }]}>
            <Input disabled />
          </Form.Item>
          <Form.Item name="dimension" label="维度" hidden>
            <Input />
          </Form.Item>

        </Form>
      </Modal>
      <Modal
        title="绑定知识库 → Schema"
        open={bindOpen}
        onOk={() => void doBind()}
        onCancel={() => setBindOpen(false)}
      >
        <Form form={bindForm} layout="vertical">
          <Form.Item name="kb_id" label="知识库" rules={[{ required: true }]}>
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择知识库"
              options={kbList.map((k) => ({ value: k.id, label: k.name }))}
            />
          </Form.Item>
          <Form.Item name="schema_name" label="Schema">
            <Select
              allowClear
              placeholder="选择本体 Schema（留空 = 解绑）"
              options={groups.map((g) => ({ value: g.schema_name, label: g.schema_name }))}
            />
          </Form.Item>
        </Form>
      </Modal>
            </Card>
          </div>
        );
      }
