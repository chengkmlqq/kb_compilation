"use client";

/**
 * 数据源 新建/编辑 动态 Tab 内容（2026-10-14 抽出，修复多 tab 表单状态串扰）：
 *
 * 此前整个页面单例 Form + 页面级共享 selectedType/step/formFields，所有编辑
 * tab 渲染同一个 <Form form={form}> —— 打开「新建」tab 后再开「编辑」tab，
 * openEdit 的 setFieldsValue 会把编辑数据写进共享 form，新建 tab 被回显污染。
 *
 * 本组件每个 tab 一个实例：内部自持 Form.useForm() 与全部编辑状态（step /
 * selectedType / formFields / 类型筛选 / saving / testing），互不干扰。
 *
 * props：
 *  - tab   ：{key, title, dsId?}（dsId 有值 = 编辑模式，直达信息配置步）
 *  - categories / allTypes：类型选择共用只读元数据
 *  - onClose(key) / onSaved()：关闭自身 tab / 保存成功后通知父页刷新列表
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  App,
  Card,
  Checkbox,
  Empty,
  Form,
  InputNumber,
  Radio,
  Spin,
  Steps,
  Switch,
  Tag,
  Tooltip,
} from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import {
  apiCreateDatasource,
  apiGetDatasource,
  apiGetDsTypeDetail,
  apiListDsFormFields,
  apiTestDatasource,
  apiUpdateDatasource,
  DsCategoryItem,
  DsFormFieldItem,
  DsSavePayload,
  DsTypeItem,
} from "@/lib/api";
import { ModoButton } from "@/components/biz/modo-button";
import { ModoInput, ModoPassword, ModoTextArea, ModoSearch } from "@/components/biz/modo-input";
import { ModoSelect } from "@/components/biz/modo-select";
import { ModoRadio } from "@/components/biz/modo-radio";

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

function validInfoMsg(validInfo?: string | null): string {
  return validInfo || "格式不正确";
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

export type DatasourceEditTabInfo = {
  key: string;
  title: string;
  dsId?: string;
};

export function DatasourceEditTab({
  tab,
  categories,
  allTypes,
  onClose,
  onSaved,
}: {
  tab: DatasourceEditTabInfo;
  categories: DsCategoryItem[];
  allTypes: DsTypeItem[];
  onClose: (key: string) => void;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const isEditMode = Boolean(tab.dsId);

  // ---- 本 tab 独立的编辑状态（互不串扰） ----
  const [step, setStep] = useState(isEditMode ? 1 : 0);
  const [selectedType, setSelectedType] = useState<DsTypeItem | null>(null);
  const [formFields, setFormFields] = useState<DsFormFieldItem[]>([]);
  const [category, setCategory] = useState("");
  const [typeSearch, setTypeSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [typeLoaded, setTypeLoaded] = useState(false);

  const loadFormFields = useCallback(async (dsType: string) => {
    const res = await apiListDsFormFields(dsType);
    if (res.success) setFormFields(res.data || []);
  }, []);

  // 编辑模式：挂载即直达信息配置步并回显
  useEffect(() => {
    if (!isEditMode || !tab.dsId) return;
    const rowId = tab.dsId;
    let alive = true;
    (async () => {
      try {
        const row = await apiGetDatasource(rowId).catch(() => null);
        const ds = row?.success && row.data ? row.data : null;
        if (!ds || !alive) return;
        const dsType = String(ds.dsType || "");
        const typeRes = await apiGetDsTypeDetail(dsType).catch(() => null);
        if (!alive) return;
        if (typeRes?.success && typeRes.data) {
          setSelectedType(typeRes.data);
        } else {
          setSelectedType({
            id: "",
            dsType,
            dsTypeLabel: dsType,
            dsCategory: String(ds.dsCategory || ""),
            sorted: 0,
          });
        }
        await loadFormFields(dsType);
        const vals: Record<string, unknown> = {
          name: ds.name,
          label: ds.label ?? "",
          dsType,
          dsCategory: ds.dsCategory,
          state: ds.state ?? "1",
          dsAcct: ds.dsAcct ?? "",
          dsAuth: undefined,
          url: ds.url ?? "",
          dsVersion: ds.dsVersion,
        };
        if (ds.dsConf) {
          try {
            const confObj = JSON.parse(ds.dsConf) as Record<string, unknown>;
            if (Array.isArray(confObj.endpoints)) {
              confObj.endpoints = confObj.endpoints.join(",");
            }
            Object.assign(vals, confObj);
          } catch {
            /* dsConf 非 JSON 忽略 */
          }
        }
        if (alive) {
          form.setFieldsValue(vals);
        }
      } finally {
        // 详情/类型加载失败也结束 loading（不阻塞编辑表单提交）
        if (alive) setTypeLoaded(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [isEditMode, tab.dsId, form, loadFormFields]);

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
    setTypeLoaded(true);
  };

  const typeList = useMemo(() => {
    const kw = typeSearch.trim().toLowerCase();
    if (kw) {
      return allTypes.filter((t) => (t.dsTypeLabel || "").toLowerCase().includes(kw));
    }
    const cat = category || categories[0]?.categoryName || "";
    return allTypes.filter((t) => t.dsCategory === cat);
  }, [allTypes, category, categories, typeSearch]);

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
      const res = dsId
        ? await apiUpdateDatasource(dsId, payload)
        : await apiCreateDatasource(payload);
      if (res.success) {
        message.success(dsId ? "保存成功" : "创建成功");
        onClose(tab.key);
        onSaved();
      } else {
        message.error(res.message || "保存失败");
      }
    } catch {
      /* 校验失败 */
    } finally {
      setSaving(false);
    }
  };

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

  const renderInfoConfig = (isEdit: boolean, onPrev?: () => void, dsId?: string) => (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#fff" }}>
      <div style={{ flex: 1, overflow: "auto", background: "#fff" }}>
        <div style={{ maxWidth: 720, margin: "0 auto", padding: "32px 24px" }}>
          <Spin spinning={!typeLoaded && formFields.length === 0}>
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
                <ModoInput placeholder="唯一英文标识" disabled={isEdit} />
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
              {formFields
                .filter((f) => !FIXED_FIELDS.includes(f.name) && f.name !== "name" && f.name !== "label")
                .map(renderFormItem)}
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
          <ModoButton onClick={onPrev} style={{ background: "#eff4f9", color: "#242e43" }}>
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
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            flex: 1,
            minHeight: 400,
            overflow: "hidden",
            background: "#fff",
            borderRadius: 6,
          }}
        >
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
          {renderInfoConfig(isEditMode, isEditMode ? undefined : () => setStep(0), tab.dsId)}
        </div>
      )}
    </div>
  );
}