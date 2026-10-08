"use client";

/**
 * 数据源管理页 —— 对齐 data-synth system/datasources（2026-10-06 ds-align）：
 * - Tabs 布局（列表 + 新建/编辑动态 Tab）；团队数据源授权已迁至「系统管理 / 团队管理」页
 * - 列表：三条件筛选（英文名/中文名/类型下拉，数据驱动）
 * - 新建两步向导：选择类型（分类侧栏 + 类型卡片 img/字母头像）→ 信息配置
 * - 编辑直达「信息配置」步（类型只读，无需重选）；dsConf 解析回填动态字段，endpoints 数组转逗号串
 * - 信息配置：水平表单居中(max 720) + 底部固定操作栏（上一步/测试连通性/保存）
 * - 动态字段：required + regex + tooltip + boolean(checked) + radio/checkbox-group + defaultValue 回填
 * - vector_es 类型前端禁用删除按钮（与后端保护一致）
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  App,
  Button,
  Card,
  Checkbox,
  Empty,
  Form,
  Input,
  InputNumber,
  Radio,
  Select,
  Space,
  Spin,
  Steps,
  Switch,
  Table,
  Tag,
  Tooltip,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  DatabaseOutlined,
  QuestionCircleOutlined,
} from "@ant-design/icons";
import {
  apiCreateDatasource,
  apiDeleteDatasource,
  apiGetDatasource,
  apiGetDsTypeDetail,
  apiListDatasources,
  apiListDsCategories,
  apiListDsFormFields,
  apiListDsTypes,
  apiTestDatasource,
  apiUpdateDatasource,
  DsCategoryItem,
  DsFormFieldItem,
  DsSavePayload,
  DsTypeItem,
  DatasourceItem,
} from "@/lib/api";
import ModoPagination from "@/components/biz/modo-pagination";
import { ModoTabs } from "@/components/biz/modo-tabs";
import { ModoButton } from "@/components/biz/modo-button";
import { ModoInput, ModoPassword, ModoTextArea, ModoSearch } from "@/components/biz/modo-input";
import { ModoSelect } from "@/components/biz/modo-select";
import { ModoRadio } from "@/components/biz/modo-radio";
import { ModoActionGroup } from "@/components/biz/modo-action-group";

type EditTab = {
  key: string;
  title: string;
  dsId?: string;
};

const FIXED_FIELDS = ["dsAcct", "dsAuth", "url", "dsVersion"];

/** 与 ds 原版一致的动态字段正则解析（支持 /pattern/flags 与裸字符串） */
function safeRegex(str?: string | null): RegExp | undefined {
  if (!str) return undefined;
  try {
    if (str.startsWith("/") && str.lastIndexOf("/") > 0) {
      const lastSlash = str.lastIndexOf("/");
      const flags = str.substring(lastSlash + 1);
      if (/^[gimsuy]*$/.test(flags)) {
        const pattern = str.substring(1, lastSlash);
        try {
          return new RegExp(pattern, flags);
        } catch {
          /* fallthrough */
        }
      }
    }
    return new RegExp(str);
  } catch {
    return undefined;
  }
}

/** ds 原版 validInfo 可能包一层 {regex:{message}} */
function validInfoMsg(validInfo?: string | null): string {
  if (!validInfo) return "格式不正确";
  try {
    const obj = JSON.parse(validInfo);
    return obj?.regex?.message || validInfo;
  } catch {
    return validInfo;
  }
}

function parseFieldOptions(options?: string | null): Array<{ label: string; value: string }> {
  if (!options) return [];
  try {
    const parsed = JSON.parse(options);
    if (Array.isArray(parsed)) {
      return parsed.map((o: Record<string, string>) => ({
        label: o.label ?? o.key,
        value: o.value ?? o.key,
      }));
    }
  } catch {
    /* fallthrough */
  }
  return options
    .split(",")
    .map((o) => o.trim())
    .filter(Boolean)
    .map((o) => ({ label: o, value: o }));
}

export default function DatasourcesPage() {
  const { message, modal } = App.useApp();
    const [form] = Form.useForm();
    const [filterForm] = Form.useForm();

  // ---- 列表 ----
  const [rows, setRows] = useState<DatasourceItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [filters, setFilters] = useState<{ name?: string; label?: string; dsType?: string }>({});

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

  const loadList = useCallback(
    async (p = page, size = pageSize, flt = filters) => {
      setLoading(true);
      try {
        const res = await apiListDatasources(p, size, flt);
        if (res.success) {
          setRows(res.data?.items || []);
          setTotal(res.data?.total || 0);
        }
      } finally {
        setLoading(false);
      }
    },
    [page, pageSize, filters],
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

  const loadFormFields = useCallback(
    async (dsType: string) => {
      const res = await apiListDsFormFields(dsType);
      if (res.success) setFormFields(res.data || []);
    },
    [],
  );

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
    const label = row.dsLabel || row.dsName || row.id || "";
    setTabs((prev) => {
      const filtered = prev.filter((t) => t.key !== key);
      return [...filtered, { key, title: `编辑: ${label}`, dsId: row.id }];
    });
    setActiveTab(key);
    setStep(1); // 对齐 ds：编辑直达「信息配置」步
    setSelectedType(null);
    setFormFields([]);
    form.resetFields();
    // 载入类型详情（只读展示）+ 动态字段 + 已有配置回显
    const typeRes = await apiGetDsTypeDetail(String(row.dsType || "")).catch(() => null);
    if (typeRes?.success && typeRes.data) {
      setSelectedType(typeRes.data);
    } else {
      setSelectedType({ id: "", dsType: row.dsType || "", dsTypeLabel: row.dsType || "", dsCategory: row.dsCategory || "", sorted: 0 });
    }
    await loadFormFields(String(row.dsType || ""));
    try {
      const res = await apiGetDatasource(String(row.id));
      if (res.success && res.data) {
        const d = res.data;
        const vals: Record<string, unknown> = {
          name: d.name,
          label: d.label ?? "",
          dsType: row.dsType,
          dsCategory: row.dsCategory,
          state: d.state ?? "1",
          dsAcct: d.dsAcct ?? "",
          dsAuth: undefined,
          url: d.url ?? "",
          dsVersion: row.dsVersion,
        };
        // dsConf 解析回填（对齐 ds InfoConfig：endpoints 数组转逗号串）
        if (d.dsConf) {
          try {
            const confObj = JSON.parse(d.dsConf) as Record<string, unknown>;
            if (Array.isArray(confObj.endpoints)) {
              confObj.endpoints = confObj.endpoints.join(",");
            }
            Object.assign(vals, confObj);
          } catch {
            /* dsConf 非 JSON 忽略 */
          }
        }
        form.setFieldsValue(vals);
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
      setFormFields([]);
      form.resetFields();
    }
  };

  const pickType = async (t: DsTypeItem) => {
    setSelectedType(t);
    form.setFieldsValue({ dsType: t.dsType, dsCategory: t.dsCategory });
    await loadFormFields(t.dsType);
    // 新建模式：defaultValue 回填（对齐 ds InfoConfig）
    const res = await apiListDsFormFields(t.dsType);
    if (res.success) {
      const fields = res.data || [];
      setFormFields(fields);
      const defaults: Record<string, unknown> = {};
      fields.forEach((f) => {
        if (f.defaultValue) defaults[f.name] = f.defaultValue;
      });
      form.setFieldsValue(defaults);
    }
    setStep(1);
  };

  const buildPayload = (values: Record<string, unknown>) => {
    const payload: Record<string, unknown> = {
      name: values.name,
      label: values.label,
      dsType: values.dsType as string,
      dsCategory: (values.dsCategory as string) || undefined,
      state: values.state || "1",
    };
    for (const f of FIXED_FIELDS) if (values[f]) payload[f] = values[f];
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
    return payload;
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const values = await form.validateFields();
      const payload = buildPayload(values) as Record<string, unknown>;
      const res = await apiTestDatasource(payload);
      const ok = res.success && (res.data as { success?: boolean } | undefined)?.success;
      if (ok) message.success("连接成功");
      else message.warning((res.data as { message?: string } | undefined)?.message || "连接失败");
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
      const payload = buildPayload(values) as unknown as DsSavePayload;
      const res = dsId ? await apiUpdateDatasource(dsId, payload) : await apiCreateDatasource(payload);
      if (res.success) {
        message.success(dsId ? "保存成功" : "创建成功");
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
      width: 140,
      fixed: "right",
      render: (_, row) => (
        <ModoActionGroup
          actions={[
            { key: "edit", label: "编辑", onClick: () => void openEdit(row) },
            {
              key: "delete",
              label: "删除",
              danger: true,
              disabled: row.dsType === "vector_es", // 对齐 ds：向量库数据源前端禁用删除
              onClick: () => handleDelete(row),
            },
          ]}
        />
      ),
    },
  ];

  /** 动态字段渲染（对齐 ds InfoConfig 的 widget 全集） */
  const renderFormItem = (f: DsFormFieldItem) => {
    if (f.invisible === 1) return null;
    const widget = (f.widget || (f.name === "dsAuth" ? "password" : "input")).toLowerCase();
    const options = parseFieldOptions(f.options);
    const isBoolean =
      widget === "switch" || widget === "kerberos" || (widget === "checkbox" && options.length === 0);

    let node: ReactNode;
        if (widget === "password") {
          node = <ModoPassword autoComplete="new-password" placeholder={f.placeHold || "请输入"} />;
        } else if (widget === "kerberos" || widget === "switch") {
          node = <Switch />;
        } else if (widget === "textarea" || widget === "textareawithcopy") {
          node = <ModoTextArea rows={4} placeholder={f.placeHold || "请输入"} />;
        } else if (widget === "select") {
          node = (
            <ModoSelect
              placeholder={f.placeHold || "请选择"}
              options={options}
              showSearch
              optionFilterProp="label"
            />
          );
        } else if (widget === "radio") {
          node = <ModoRadio.Group options={options} />;
        } else if (widget === "checkbox") {
          node = options.length > 0 ? <Checkbox.Group options={options} /> : <Checkbox />;
        } else if (widget === "inputtag") {
          node = <ModoSelect mode="tags" placeholder={f.placeHold || "可输入多个值后回车"} options={[]} />;
        } else if (widget === "integer" || widget === "number" || widget === "inputnumber") {
          node = <InputNumber style={{ width: "100%" }} placeholder={f.placeHold || "请输入"} />;
        } else {
          node = <ModoInput placeholder={f.placeHold || "请输入"} />;
        }

    const labelNode = (
      <span>
        {f.label || f.name}
        {f.tooltip ? (
          <Tooltip title={f.tooltip}>
            <QuestionCircleOutlined className="ml-1" style={{ color: "#999" }} />
          </Tooltip>
        ) : null}
        {f.isConf === 1 ? <Tag style={{ marginLeft: 6 }}>高级</Tag> : null}
      </span>
    );

    const rules: Array<Record<string, unknown>> = [];
    if (f.required === 1) rules.push({ required: true, message: `${f.label || f.name}不能为空` });
    const regexObj = safeRegex(f.regex);
    if (regexObj) rules.push({ pattern: regexObj, message: validInfoMsg(f.validInfo) });

    return (
      <Form.Item
        key={f.id || f.name}
        name={f.name}
        label={labelNode}
        rules={rules}
        valuePropName={isBoolean ? "checked" : "value"}
      >
        {node}
      </Form.Item>
    );
  };

  const renderInfoConfig = (isEditMode: boolean, onPrev?: () => void, dsId?: string) => (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#fff" }}>
      <div style={{ flex: 1, overflow: "auto", background: "#fff" }}>
        <div style={{ maxWidth: 720, margin: "0 auto", padding: "32px 24px" }}>
          <Spin spinning={formFields.length === 0 && !selectedType}>
            <Form form={form} layout="horizontal" labelCol={{ span: 5 }} wrapperCol={{ span: 19 }}>
              <Form.Item label="数据源类型" style={{ marginBottom: 16 }}>
                <ModoInput value={selectedType?.dsTypeLabel || selectedType?.dsType || ""} disabled />
              </Form.Item>
              <Form.Item name="dsType" hidden>
                <ModoInput />
              </Form.Item>
              <Form.Item name="dsCategory" hidden>
                <ModoInput />
              </Form.Item>
              <Form.Item
                name="name"
                label="数据源名称"
                rules={[{ required: true, message: "请输入数据源名称" }]}
                style={{ marginBottom: 16 }}
              >
                <ModoInput placeholder="唯一英文标识" disabled={isEditMode} />
              </Form.Item>
              <Form.Item
                name="label"
                label="数据源中文名"
                rules={[{ required: true, message: "请输入数据源中文名" }]}
                style={{ marginBottom: 16 }}
              >
                <ModoInput placeholder="显示名称" />
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
                  style={{ marginBottom: 16 }}
                >
                  {name === "dsAuth" ? (
                    <ModoPassword placeholder="连接口令" autoComplete="new-password" />
                  ) : name === "dsVersion" ? (
                    <ModoInput placeholder="如 8.0" />
                  ) : (
                    <ModoInput placeholder={name === "url" ? "jdbc:mysql://host:3306/db" : "请输入"} />
                  )}
                </Form.Item>
              ))}
              {formFields.filter((f) => !FIXED_FIELDS.includes(f.name) && f.name !== "name" && f.name !== "label").map(renderFormItem)}
              <Form.Item name="state" label="状态" initialValue="1" style={{ marginBottom: 16 }}>
                <ModoRadio.Group>
                  <ModoRadio value="1">生效</ModoRadio>
                  <ModoRadio value="0">停用</ModoRadio>
                </ModoRadio.Group>
              </Form.Item>
            </Form>
          </Spin>
        </div>
      </div>
      {/* 底部固定操作栏（对齐 ds InfoConfig footer） */}
      <div
        style={{
          flexShrink: 0,
          padding: "12px 16px",
          borderTop: "1px solid #eff4f9",
          display: "flex",
          justifyContent: "flex-end",
          gap: 12,
          background: "#fff",
        }}
      >
        {onPrev ? (
          <ModoButton
            onClick={onPrev}
            style={{ background: "#eff4f9", color: "#242e43" }}
          >
            上一步
          </ModoButton>
        ) : null}
        <ModoButton loading={testing} onClick={() => void handleTest()}>
          测试连通性
        </ModoButton>
        <ModoButton type="primary" loading={saving} onClick={() => void handleSave(dsId)}>
          保存
        </ModoButton>
      </div>
    </div>
  );

  const renderEditTab = (tab: EditTab) => {
    const isCreate = !tab.dsId;
    return (
      <div style={{ padding: "8px 4px", display: "flex", flexDirection: "column", height: "100%" }}>
        <Steps
                  current={step}
                  style={{ marginBottom: 16 }}
                  items={[
                    {
                      title: (
                        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.2 }}>
                          <span>选择类型</span>
                          <span style={{ fontSize: 12, color: "#999", fontWeight: 400, marginTop: 4 }}>
                            选择数据源类型
                          </span>
                        </div>
                      ),
                    },
                    {
                      title: (
                        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.2 }}>
                          <span>信息配置</span>
                          <span style={{ fontSize: 12, color: "#999", fontWeight: 400, marginTop: 4 }}>
                            配置连接信息
                          </span>
                        </div>
                      ),
                    },
                  ]}
                />
                {step === 0 ? (
                  <div style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 400, overflow: "hidden", background: "#fff", borderRadius: 6 }}>
                    {/* 顶栏全局搜索（对齐 ds SelectSource） */}
                    <div style={{ padding: 12, borderBottom: "1px solid #eff4f9" }}>
                      <ModoSearch
                        placeholder="搜索全部数据源类型"
                        allowClear
                        value={typeSearch}
                        onChange={(e) => setTypeSearch(e.target.value)}
                        style={{ width: 320 }}
                      />
                    </div>
                    <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
                      <div style={{ width: 192, flexShrink: 0, borderRight: "1px solid #eff4f9", overflowY: "auto" }}>
                        {categories.map((c) => (
                          <div
                            key={c.id}
                            onClick={() => {
                              setCategory(c.categoryName);
                              setTypeSearch("");
                            }}
                            style={{
                              padding: "12px 16px",
                              cursor: "pointer",
                              transition: "background 0.2s",
                              background: category === c.categoryName && !typeSearch ? "#EFF4F9" : undefined,
                              color: category === c.categoryName && !typeSearch ? "#3261CE" : undefined,
                              fontWeight: category === c.categoryName && !typeSearch ? 500 : undefined,
                            }}
                          >
                            {c.categoryLabel || c.categoryName}
                          </div>
                        ))}
                      </div>
                      <div style={{ flex: 1, padding: 24, overflowY: "auto" }}>
                        {typeList.length > 0 ? (
                          <div
                            style={{
                              display: "grid",
                              gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
                              gap: 16,
                            }}
                          >
                            {typeList.map((t) => (
                              <Card
                                key={t.id || t.dsType}
                                hoverable
                                onClick={() => void pickType(t)}
                                style={{ textAlign: "center", borderRadius: 8 }}
                                styles={{ body: { padding: 16 } }}
                              >
                                <div
                                  style={{
                                    margin: "0 auto 12px",
                                    width: 96,
                                    height: 96,
                                    display: "flex",
                                    alignItems: "center",
                                    justifyContent: "center",
                                    borderRadius: "50%",
                                    overflow: "hidden",
                                    background: "#F0F2F5",
                                  }}
                                >
                                  {t.img ? (
                                    <img
                                      src={t.img}
                                      alt={t.dsTypeLabel || t.dsType}
                                      style={{ width: "100%", height: "100%", objectFit: "contain" }}
                                    />
                                  ) : (
                                    <span style={{ fontSize: 20, fontWeight: 600, color: "#999" }}>
                                      {(t.dsType || "").substring(0, 2).toUpperCase()}
                                    </span>
                                  )}
                                </div>
                                <div style={{ fontWeight: 500, color: "#4d5e7d" }}>
                                  {t.dsTypeLabel || t.dsType}
                                </div>
                              </Card>
                            ))}
                          </div>
                        ) : (
                          <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
                            <Empty description="暂无数据" />
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ) : (
          <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <div style={{ padding: "0 12px 8px" }}>
              <div style={{ padding: "6px 12px", background: "#F0F5FF", borderRadius: 4, color: "#4D5E7D", fontSize: 12 }}>
                已选类型：{selectedType?.dsTypeLabel || selectedType?.dsType || "-"}（{selectedType?.dsType || "-"}）
              </div>
            </div>
            {renderInfoConfig(!isCreate, isCreate ? () => setStep(0) : undefined, tab.dsId)}
          </div>
        )}
      </div>
    );
  };

  const doSearch = (vals: { name?: string; label?: string; dsType?: string }) => {
    setFilters(vals);
    setPage(1);
    void loadList(1, pageSize, vals);
  };

  const doReset = () => {
    filterForm.resetFields();
    setFilters({});
    setPage(1);
    void loadList(1, pageSize, {});
  };

  const listPane = (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}>
      <Card
        title="数据源管理"
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
        extra={
          <Button type="primary" onClick={openCreate}>
            新增数据源
          </Button>
        }
      >
        <Form
          form={filterForm}
          layout="inline"
          style={{ marginBottom: 12, flexShrink: 0 }}
          onFinish={doSearch}
          initialValues={{ name: "", label: "", dsType: undefined }}
        >
          <Form.Item name="name" label="英文名">
            <Input allowClear placeholder="请输入英文名" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="label" label="中文名">
            <Input allowClear placeholder="请输入中文名" style={{ width: 180 }} />
          </Form.Item>
          <Form.Item name="dsType" label="类型">
            <Select
              allowClear
              showSearch
              placeholder="请选择类型"
              style={{ width: 200 }}
              optionFilterProp="label"
              options={allTypes.map((t) => ({ label: t.dsTypeLabel || t.dsType, value: t.dsType }))}
            />
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                查询
              </Button>
              <Button onClick={doReset}>重置</Button>
            </Space>
          </Form.Item>
        </Form>
        <Table<DatasourceItem>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={rows}
          columns={columns}
          pagination={false}
          scroll={{ x: 900, y: "calc(100vh - 308px)" }}
          locale={{ emptyText: <Empty description="暂无数据源" /> }}
        />
        <div style={{ flexShrink: 0, marginTop: "auto" }}>
          <ModoPagination
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
      </Card>
    </div>
  );

  return (
    <div
      className="datasources-page"
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
        items={[
          { key: "home", label: "数据源管理", icon: <DatabaseOutlined />, children: listPane },
          ...tabs.map((t) => ({
            key: t.key,
            label: t.title,
            closable: true,
            children: renderEditTab(t),
          })),
        ]}
      />
    </div>
  );
}