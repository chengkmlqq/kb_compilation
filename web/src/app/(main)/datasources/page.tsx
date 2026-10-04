"use client";

/**
 * 数据源管理页 —— 迁移 data-synth system/datasources：
 * Tabs 布局（主页列表 + 新建/编辑动态 Tab），新建/编辑两步向导：
 *   步骤1 选择类型（分类导航 + 类型卡片，数据源 metadata 种子）
 *   步骤2 信息配置（固定字段 + 动态表单字段来自 modo_ds_form_field + 测试连接）
 * 另含「团队授权」Tab（modo_team_ds_map 编辑）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Checkbox,
  Empty,
  Form,
  Input,
  InputNumber,
  Pagination,
  Radio,
  Select,
  Space,
  Spin,
  Steps,
  Table,
  Tabs,
  Tag,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  ApiOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import {
  apiCreateDatasource,
  apiDeleteDatasource,
  apiGetDatasource,
  apiListDatasources,
  apiListDsCategories,
  apiListDsFormFields,
  apiListDsTypes,
  apiListTeamDsAuth,
  apiSaveTeamDsAuth,
  apiTestDatasource,
  apiUpdateDatasource,
  DsCategoryItem,
  DsFormFieldItem,
  DsSavePayload,
  DsTypeItem,
  DatasourceItem,
  TeamDsMapItem,
} from "@/lib/api";

type EditTab = {
  key: string;
  title: string;
  dsId?: string;
};

const FIXED_FIELDS = ["dsAcct", "dsAuth", "url", "dsVersion"];

export default function DatasourcesPage() {
  const { message, modal } = App.useApp();
  const [form] = Form.useForm();

  // ---- 列表 ----
  const [rows, setRows] = useState<DatasourceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [keyword, setKeyword] = useState("");

  // ---- 元数据 ----
  const [categories, setCategories] = useState<DsCategoryItem[]>([]);
  const [allTypes, setAllTypes] = useState<DsTypeItem[]>([]);
  const [formFields, setFormFields] = useState<DsFormFieldItem[]>([]);

  // ---- Tab ----
  const [tabs, setTabs] = useState<EditTab[]>([]);
  const [activeTab, setActiveTab] = useState("home");
  const [step, setStep] = useState(0);
  const [selectedType, setSelectedType] = useState<DsTypeItem | null>(null);
  const [category, setCategory] = useState("");
  const [typeSearch, setTypeSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  // ---- 团队授权 ----
  const [authTeam, setAuthTeam] = useState("ROOT");
  const [authItems, setAuthItems] = useState<TeamDsMapItem[]>([]);
  const [authRows, setAuthRows] = useState<DatasourceItem[]>([]);

  const loadList = useCallback(
    async (p = page, size = pageSize, kw = keyword) => {
      setLoading(true);
      try {
        const res = await apiListDatasources(p, size, kw.trim() || "");
        if (res.success) {
          setRows(res.data?.items || []);
          setTotal(res.data?.total || 0);
        }
      } finally {
        setLoading(false);
      }
    },
    [page, pageSize, keyword],
  );

  useEffect(() => {
    void loadList();
  }, [loadList]);

  useEffect(() => {
    void (async () => {
      const [c, t] = await Promise.all([apiListDsCategories(), apiListDsTypes()]);
      if (c.success && c.data) setCategories(c.data);
      if (t.success && t.data) {
        setAllTypes(t.data);
        if (c.data?.[0]?.categoryName) setCategory(c.data[0].categoryName);
      }
    })();
  }, []);

  const typeList = useMemo(() => {
    const kw = typeSearch.trim().toLowerCase();
    if (kw) {
      return allTypes.filter((t) => (t.dsTypeLabel || "").toLowerCase().includes(kw));
    }
    return allTypes.filter((t) => t.dsCategory === category);
  }, [allTypes, category, typeSearch]);

  const loadFormFields = useCallback(async (dsType: string) => {
    const res = await apiListDsFormFields(dsType);
    if (res.success) setFormFields(res.data || []);
  }, []);

  const openCreate = () => {
    const key = `new-${Date.now()}`;
    setSelectedType(null);
    setStep(0);
    setFormFields([]);
    form.resetFields();
    setTabs((prev) => [...prev, { key, title: "新建数据源" }]);
    setActiveTab(key);
  };

  const openEdit = async (row: DatasourceItem) => {
    const key = `edit-${row.id}`;
    setSelectedType(null);
    setStep(0);
    setTabs((prev) => {
      const filtered = prev.filter((t) => t.key !== key);
      return [...filtered, { key, title: `编辑: ${row.dsLabel || row.dsName}`, dsId: row.id }];
    });
    setActiveTab(key);
    // 载入已有配置（详情不含敏感口令）
    try {
      const res = await apiGetDatasource(String(row.id));
      if (res.success && res.data) {
        const d = res.data;
        form.setFieldsValue({
          name: d.name,
          label: d.label ?? "",
          dsType: row.dsType,
          dsCategory: row.dsCategory,
          state: d.state ?? "1",
          dsAcct: d.dsAcct ?? "",
          dsAuth: undefined,
          url: d.url ?? "",
          dsVersion: row.dsVersion,
        });
      }
    } catch {
      /* 详情失败不阻断编辑 */
    }
  };

  const closeTab = (key: string) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    if (activeTab === key) {
      setActiveTab("home");
      setSelectedType(null);
      setStep(0);
    }
  };

  const pickType = async (t: DsTypeItem) => {
    setSelectedType(t);
    form.setFieldsValue({ dsType: t.dsType, dsCategory: t.dsCategory });
    await loadFormFields(t.dsType);
    setStep(1);
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const values = await form.validateFields();
      const payload: Record<string, unknown> = { dsType: values.dsType };
      for (const f of FIXED_FIELDS) if (values[f]) payload[f] = values[f];
      // 动态字段：isConf=1 → 进 dsConf
      const conf: Record<string, unknown> = {};
      for (const f of formFields) {
        const v = values[f.name];
        if (v === undefined || v === null || v === "") continue;
        if (f.isConf === 1) conf[f.name] = v;
        else if (!FIXED_FIELDS.includes(f.name) && f.name !== "dsType") {
          payload[f.name] = v;
        }
      }
      if (Object.keys(conf).length) payload.dsConf = JSON.stringify(conf);
      const res = await apiTestDatasource(payload);
      const ok = res.success && (res.data as { success?: boolean } | undefined)?.success;
      if (ok) message.success("连接成功");
      else
        message.warning(
          (res.data as { message?: string } | undefined)?.message || "连接失败",
        );
    } catch {
      /* 校验失败 */
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async (dsId?: string) => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const conf: Record<string, unknown> = {};
      const payload: Record<string, unknown> = {
        name: values.name,
        label: values.label,
        dsType: values.dsType,
        dsCategory: values.dsCategory,
        state: values.state || "1",
      };
      for (const f of FIXED_FIELDS) if (values[f]) payload[f] = values[f];
      for (const f of formFields) {
        const v = values[f.name];
        if (v === undefined || v === null || v === "") continue;
        if (f.isConf === 1) conf[f.name] = v;
      }
      if (Object.keys(conf).length) payload.dsConf = JSON.stringify(conf);
      const p = payload as unknown as DsSavePayload;
      const res = dsId ? await apiUpdateDatasource(dsId, p) : await apiCreateDatasource(p);
      if (res.success) {
        message.success(dsId ? "更新成功" : "创建成功");
        closeTab(activeTab);
        void loadList();
      } else {
        message.error(res.message || "保存失败");
      }
    } catch {
      /* 校验失败 */
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = (row: DatasourceItem) => {
    modal.confirm({
      title: "确认删除",
      content: `确定要删除数据源「${row.dsLabel || row.dsName}」吗？`,
      okType: "danger",
      onOk: async () => {
        const res = await apiDeleteDatasource(String(row.id));
        if (res.success) {
          message.success("删除成功");
          void loadList();
        } else {
          message.error(res.message || "删除失败");
        }
      },
    });
  };

  // ---- 团队授权 ----
  const loadAuth = useCallback(
    async (team: string) => {
      const [maps, list] = await Promise.all([
        apiListTeamDsAuth(team),
        apiListDatasources(1, 200, ""),
      ]);
      setAuthItems(maps.success ? maps.data || [] : []);
      setAuthRows(list.success ? list.data?.items || [] : []);
    },
    [],
  );

  useEffect(() => {
    void loadAuth(authTeam);
  }, [authTeam, loadAuth]);

  const saveAuth = async () => {
    const res = await apiSaveTeamDsAuth(authTeam, authItems);
    if (res.success) {
      message.success("团队数据源授权已保存");
      void loadAuth(authTeam);
    } else {
      message.error(res.message || "保存失败");
    }
  };

  const columns: ColumnsType<DatasourceItem> = [
    { title: "英文名", dataIndex: "dsName", width: 180, ellipsis: true },
    { title: "中文名", dataIndex: "dsLabel", width: 160, ellipsis: true },
    {
      title: "类型",
      dataIndex: "dsType",
      width: 120,
      render: (v: string) => <Tag color="blue">{v || "-"}</Tag>,
    },
    { title: "版本", dataIndex: "dsVersion", width: 100, render: (v: string) => v || "-" },
    {
      title: "状态",
      dataIndex: "state",
      width: 90,
      align: "center",
      render: (v: string) => (
        <Tag color={v === "1" ? "success" : "default"}>{v === "1" ? "生效" : "停用"}</Tag>
      ),
    },
    {
      title: "操作",
      key: "action",
      width: 170,
      align: "center",
      fixed: "right",
      render: (_, row) => (
        <Space size="small">
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => void openEdit(row)}>
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(row)}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ];

  const renderWidget = (f: DsFormFieldItem) => {
    const widget = (f.widget || (f.name === "dsAuth" ? "password" : "input")).toLowerCase();
    if (f.invisible === 1) return null;
    if (widget === "password" || widget === "kerberos") {
      return <Input.Password placeholder={f.placeHold || "请输入"} />;
    }
    if (widget === "textarea" || widget === "textareawithcopy") {
      return <Input.TextArea rows={3} placeholder={f.placeHold || "请输入"} />;
    }
    if (widget === "select") {
      let options: Array<{ label: string; value: string }> = [];
      if (f.options) {
        try {
          const parsed = JSON.parse(f.options);
          if (Array.isArray(parsed)) {
            // 种子格式 [{key,label,value}] 或 [{label,value}]
            options = parsed.map((o: Record<string, string>) => ({
              label: o.label ?? o.key,
              value: o.value ?? o.key,
            }));
          }
        } catch {
          options = f.options
            .split(",")
            .map((o) => o.trim())
            .filter(Boolean)
            .map((o) => ({ label: o, value: o }));
        }
      }
      return <Select placeholder={f.placeHold || "请选择"} options={options} />;
    }
    if (widget === "inputtag") {
      // 多值输入（如 Hive 的额外 JAR 列表）
      return (
        <Select
          mode="tags"
          placeholder={f.placeHold || "可输入多个值后回车"}
          options={[]}
        />
      );
    }
    if (widget === "switch" || widget === "checkbox") {
      return <Checkbox />;
    }
    if (widget === "integer" || widget === "number") {
      return <InputNumber style={{ width: "100%" }} />;
    }
    return <Input placeholder={f.placeHold || "请输入"} />;
  };

  /** 动态字段的校验规则（挂在 Form.Item 上，控件本身不接受 rules） */
  const fieldRules = (f: DsFormFieldItem) =>
    f.regex
      ? [{ pattern: new RegExp(f.regex), message: f.placeHold || "格式不正确" }]
      : [];

  const listPane = (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Space wrap style={{ justifyContent: "space-between", width: "100%" }}>
        <Space>
          <Input
            placeholder="按名称搜索"
            allowClear
            prefix={<SearchOutlined />}
            style={{ width: 240 }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onPressEnter={() => {
              setPage(1);
              void loadList(1, pageSize, keyword);
            }}
          />
          <Button
            icon={<SearchOutlined />}
            onClick={() => {
              setPage(1);
              void loadList(1, pageSize, keyword);
            }}
          >
            查询
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              setKeyword("");
              setPage(1);
              void loadList(1, pageSize, "");
            }}
          >
            重置
          </Button>
        </Space>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新增数据源
        </Button>
      </Space>
      <Table<DatasourceItem>
        rowKey="id"
        size="middle"
        loading={loading}
        dataSource={rows}
        columns={columns}
        scroll={{ x: 900 }}
        pagination={false}
        locale={{ emptyText: <Empty description="暂无数据源" /> }}
      />
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Pagination
          current={page}
          pageSize={pageSize}
          total={total}
          showSizeChanger
          showTotal={(t) => `共 ${t} 条`}
          onChange={(p, ps) => {
            setPage(p);
            setPageSize(ps);
          }}
        />
      </div>
    </div>
  );

  const authPane = (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Space wrap>
        <span>团队：</span>
        <Select
          value={authTeam}
          style={{ width: 200 }}
          onChange={setAuthTeam}
          options={[
            { value: "ROOT", label: "ROOT" },
            { value: "test", label: "test" },
          ]}
        />
        <Button type="primary" icon={<TeamOutlined />} onClick={() => void saveAuth()}>
          保存授权
        </Button>
        <Button onClick={() => void loadAuth(authTeam)}>刷新</Button>
      </Space>
      <Table<{ key: string; dsName: string; label: string }>
        rowKey="dsName"
        size="middle"
        dataSource={authRows.map((r) => ({
          key: String(r.dsName),
          dsName: String(r.dsName),
          label: String(r.dsLabel || r.dsName),
        }))}
        columns={[
          { title: "数据源英文名", dataIndex: "dsName", width: 220 },
          { title: "数据源中文名", dataIndex: "label" },
          {
            title: "授权",
            width: 120,
            render: (_, row) => (
              <Checkbox
                checked={authItems.some((i) => i.dsName === row.dsName)}
                onChange={(e) => {
                  setAuthItems((prev) =>
                    e.target.checked
                      ? [
                          ...prev.filter((i) => i.dsName !== row.dsName),
                          {
                            id: `new-${row.dsName}`,
                            dsName: row.dsName,
                            schemaName: "",
                            teamName: authTeam,
                            isProd: "0",
                          },
                        ]
                      : prev.filter((i) => i.dsName !== row.dsName),
                  );
                }}
              />
            ),
          },
          {
            title: "生产",
            width: 90,
            render: (_, row) => (
              <Checkbox
                checked={authItems.some((i) => i.dsName === row.dsName && i.isProd === "1")}
                onChange={(e) => {
                  setAuthItems((prev) =>
                    prev.map((i) =>
                      i.dsName === row.dsName
                        ? { ...i, isProd: e.target.checked ? "1" : "0" }
                        : i,
                    ),
                  );
                }}
              />
            ),
          },
        ]}
        pagination={false}
      />
    </div>
  );

  const renderEditTab = (tab: EditTab) => (
    <div style={{ padding: "8px 4px" }}>
      <Steps
        current={step}
        style={{ marginBottom: 16 }}
        items={[{ title: "选择类型" }, { title: "信息配置" }]}
      />
      {step === 0 ? (
        <div style={{ display: "flex", gap: 16, minHeight: 420 }}>
          <div style={{ width: 180, flexShrink: 0 }}>
            <Input.Search
              placeholder="搜索类型"
              allowClear
              onChange={(e) => setTypeSearch(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            {categories.map((c) => (
              <div
                key={c.id}
                onClick={() => {
                  setCategory(c.categoryName);
                  setTypeSearch("");
                }}
                style={{
                  padding: "8px 12px",
                  cursor: "pointer",
                  borderRadius: 6,
                  background: category === c.categoryName && !typeSearch ? "#EFF4F9" : undefined,
                  color: category === c.categoryName && !typeSearch ? "#1E5EFF" : undefined,
                }}
              >
                {c.categoryLabel || c.categoryName}
              </div>
            ))}
          </div>
          <div style={{ flex: 1 }}>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
                gap: 12,
              }}
            >
              {typeList.map((t) => (
                <Card
                  key={t.id}
                  hoverable
                  size="small"
                  onClick={() => void pickType(t)}
                  style={{ textAlign: "center" }}
                >
                  <ApiOutlined style={{ fontSize: 20, color: "#1E5EFF" }} />
                  <div style={{ marginTop: 6 }}>{t.dsTypeLabel || t.dsType}</div>
                </Card>
              ))}
              {typeList.length === 0 && <Empty description="该分类下暂无类型" />}
            </div>
          </div>
        </div>
      ) : (
        <Form form={form} layout="vertical">
          <Space style={{ marginBottom: 12 }}>
            <Button onClick={() => setStep(0)}>上一步</Button>
            <Button onClick={() => void handleTest()} loading={testing}>
              测试连接
            </Button>
            <Button
              type="primary"
              loading={saving}
              onClick={() => void handleSave(tab.dsId)}
            >
              保存
            </Button>
          </Space>
          {selectedType && (
            <Alert2 text={`已选类型：${selectedType.dsTypeLabel}（${selectedType.dsType}）`} />
          )}
          <Space direction="vertical" size={12} style={{ width: "100%", marginTop: 8 }}>
            <Form.Item name="name" label="英文名" rules={[{ required: true, message: "请输入英文名" }]}>
              <Input placeholder="唯一英文标识" />
            </Form.Item>
            <Form.Item name="label" label="中文名" rules={[{ required: true, message: "请输入中文名" }]}>
              <Input placeholder="显示名称" />
            </Form.Item>
            <Form.Item name="dsType" hidden>
              <Input />
            </Form.Item>
            <Form.Item name="dsCategory" hidden>
              <Input />
            </Form.Item>
            {FIXED_FIELDS.map((name) => (
              <Form.Item
                key={name}
                name={name}
                label={
                  name === "dsAcct"
                    ? "账号"
                    : name === "dsAuth"
                      ? "口令"
                      : name === "url"
                        ? "连接地址"
                        : "版本"
                }
              >
                {name === "dsAuth" ? (
                  <Input.Password placeholder="连接口令" />
                ) : name === "dsVersion" ? (
                  <Input placeholder="如 8.0" />
                ) : (
                  <Input placeholder={name === "url" ? "jdbc:mysql://host:3306/db" : "请输入"} />
                )}
              </Form.Item>
            ))}
            {formFields
              .filter((f) => !FIXED_FIELDS.includes(f.name))
              .map((f) => (
                <Form.Item
                  key={f.id}
                  name={f.name}
                  label={`${f.label || f.name}${f.isConf === 1 ? "（高级）" : ""}`}
                  rules={fieldRules(f)}
                >
                  {renderWidget(f)}
                </Form.Item>
              ))}
            <Form.Item name="state" label="状态" initialValue="1">
              <Radio.Group>
                <Radio value="1">生效</Radio>
                <Radio value="0">停用</Radio>
              </Radio.Group>
            </Form.Item>
          </Space>
        </Form>
      )}
    </div>
  );

  return (
    <div className="modo-page" style={{ padding: 8, height: "100%", overflow: "auto" }}>
      <Tabs
        type="editable-card"
        hideAdd
        activeKey={activeTab}
        onChange={setActiveTab}
        onEdit={(key, action) => {
          if (action === "remove") closeTab(String(key));
        }}
        items={[
          { key: "home", label: "🏠 数据源管理", children: listPane },
          ...tabs.map((t) => ({
            key: t.key,
            label: t.title,
            closable: true,
            children: renderEditTab(t),
          })),
          { key: "auth", label: "团队授权", children: authPane },
        ]}
      />
    </div>
  );
}

function Alert2({ text }: { text: string }) {
  return (
    <div
      style={{
        padding: "6px 12px",
        background: "#F0F5FF",
        borderRadius: 4,
        color: "#4D5E7D",
        fontSize: 12,
      }}
    >
      {text}
    </div>
  );
}