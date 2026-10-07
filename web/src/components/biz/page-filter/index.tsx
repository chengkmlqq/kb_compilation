"use client";

import React, { useState, useRef, useEffect } from "react";
import { Form, Row, Col, Tooltip, Tag } from "antd";
import type { FormInstance } from "antd";
import { ClearOutlined, FilterOutlined } from "@ant-design/icons";
import ModoButton from "@/components/biz/modo-button";

type FilterParams = Record<string, unknown>;
type FilterValueRenderer = Record<string, React.ReactNode> | ((value: unknown) => React.ReactNode);
type FilterValueMap = Record<string, FilterValueRenderer>;
type SetSearchParams<T> = ((params: T) => void) | React.Dispatch<React.SetStateAction<T>>;

interface PageFilterProps<TFormValues extends object = object, TSearchParams extends object = TFormValues> {
  form: FormInstance<TFormValues>;
  onSearch: (values: TSearchParams) => void;
  onReset: () => void;
  searchParams: TSearchParams;
  setSearchParams: SetSearchParams<TSearchParams>;
  labelMap: Record<string, string>;
  valueMap?: FilterValueMap;
  children: React.ReactNode;
}

function toFilterRecord(value: unknown): FilterParams {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as FilterParams;
  }
  return {};
}

/** PageFilter — 折叠筛选条（对齐 data-synth page-filter）：前3项常显，更多项展开；
 *  已筛选条件标签回显可单删；右侧 重置/展开/查询 按钮恒定。 */
export function PageFilter<TFormValues extends object = object, TSearchParams extends object = TFormValues>({
  form,
  onSearch,
  onReset,
  searchParams,
  setSearchParams,
  labelMap,
  valueMap = {},
  children,
}: PageFilterProps<TFormValues, TSearchParams>) {
  const [expand, setExpand] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const [placeholderHeight, setPlaceholderHeight] = useState<number | undefined>(undefined);

  const getCleanValues = (values: TFormValues): TSearchParams => {
    const record = toFilterRecord(values);
    return Object.fromEntries(
      Object.entries(record).filter(([, v]) => v !== undefined && v !== null && v !== "")
    ) as TSearchParams;
  };

  const handleSearch = (values: TFormValues) => {
    const cleanValues = getCleanValues(values);
    setSearchParams(cleanValues);
    onSearch(cleanValues);
  };

  const handleReset = () => {
    form.resetFields();
    setSearchParams({} as TSearchParams);
    onReset();
  };

  const onCloseFilterTag = (key: string) => {
    const newParams: FilterParams = { ...toFilterRecord(searchParams) };
    delete newParams[key];
    form.setFieldValue(key as never, undefined);
    setSearchParams(newParams as TSearchParams);
    onSearch(newParams as TSearchParams);
  };

  const wrapLabel = (label: React.ReactNode): React.ReactNode => {
    if (!label) return label;
    const labelText = typeof label === "string" ? label : "";
    return (
      <Tooltip title={labelText}>
        <span
          style={{
            display: "inline-block",
            maxWidth: 96,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            verticalAlign: "middle",
            height: 28,
            lineHeight: "28px",
          }}
        >
          {label}
        </span>
      </Tooltip>
    );
  };

  const processChildren = (nodes: React.ReactNode): React.ReactNode[] => {
    return (
      React.Children.map(nodes, (child) => {
        if (React.isValidElement<{ label?: React.ReactNode }>(child) && child.props.label !== undefined) {
          return React.cloneElement(child, { label: wrapLabel(child.props.label) });
        }
        return child;
      }) || []
    );
  };

  const renderFilterTags = () => {
    const tags: React.ReactNode[] = [];
    const searchParamRecord = toFilterRecord(searchParams);
    Object.keys(searchParamRecord).forEach((key) => {
      const val = searchParamRecord[key];
      if (val !== undefined && val !== null && val !== "") {
        const valueKey = String(val);
        let displayVal: React.ReactNode = valueKey;
        const valueRenderer = valueMap[key];
        if (typeof valueRenderer === "function") {
          displayVal = valueRenderer(val);
        } else if (valueRenderer && valueRenderer[valueKey] !== undefined) {
          displayVal = valueRenderer[valueKey];
        }
        tags.push(
          <Tag
            closable
            onClose={() => onCloseFilterTag(key)}
            key={key}
            style={{
              margin: 0,
              fontSize: 12,
              background: "#E8F0FD",
              borderColor: "transparent",
              color: "#3261CE",
              borderRadius: 2,
              padding: "0 6px",
              height: 20,
              lineHeight: "20px",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            {labelMap[key] || key} : {displayVal}
          </Tag>
        );
      }
    });
    return tags;
  };

  const items = processChildren(children);
  const initialItems = items.slice(0, 3);
  const extraItems = items.slice(3);

  const isOverlay = (expand || isClosing) && extraItems.length > 0;

  useEffect(() => {
    if (!expand && !isClosing && containerRef.current) {
      setPlaceholderHeight(containerRef.current.offsetHeight);
    }
  }, [expand, isClosing, searchParams]);

  const handleToggleExpand = () => {
    if (expand) {
      setExpand(false);
      setIsClosing(true);
      setTimeout(() => setIsClosing(false), 300);
    } else {
      setExpand(true);
    }
  };

  return (
    <div style={{ position: "relative", width: "100%", zIndex: 10, height: isOverlay ? placeholderHeight : "auto" }}>
      <div
        ref={containerRef}
        style={{
          display: "flex",
          flexDirection: "column",
          width: "100%",
          position: isOverlay ? "absolute" : "relative",
          top: 0,
          left: 0,
          zIndex: isOverlay ? 50 : "auto",
          background: expand ? "#fff" : "#F9FBFD",
          padding: expand ? "16px" : "10px 16px",
          boxShadow: expand ? "0 4px 10px 0 rgba(36,46,67,0.1)" : "none",
          borderRadius: expand ? 4 : 2,
          gap: expand ? "10px" : "6px",
          transition: "background 0.3s ease, box-shadow 0.3s ease, padding 0.3s ease, border-radius 0.3s ease",
        }}
      >
        <Form form={form} onFinish={handleSearch} colon={false} style={{ width: "100%" }}>
          <div style={{ display: "flex", gap: 16, alignItems: "flex-start", width: "100%" }}>
            <div style={{ flex: 1, minWidth: 0, width: "100%" }}>
              <Row gutter={[20, 10]}>
                {initialItems.map((item, index) => (
                  <Col span={8} key={`initial-${index}`} style={{ height: 28 }}>
                    <div style={{ height: 28, overflow: "hidden" }}>{item}</div>
                  </Col>
                ))}
              </Row>
              {extraItems.length > 0 && (
                <div
                  style={{
                    maxHeight: expand ? `${Math.ceil(extraItems.length / 3) * 38 + 10}px` : "0",
                    opacity: expand ? 1 : 0,
                    overflow: "hidden",
                    transition: "max-height 0.3s ease, opacity 0.25s ease",
                  }}
                >
                  <Row gutter={[20, 10]} style={{ marginTop: 10 }}>
                    {extraItems.map((item, index) => (
                      <Col span={8} key={`extra-${index}`} style={{ height: 28 }}>
                        <div style={{ height: 28, overflow: "hidden" }}>{item}</div>
                      </Col>
                    ))}
                  </Row>
                </div>
              )}
            </div>

            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "flex-end", gap: 10, flexShrink: 0 }}>
              <Tooltip title="重置">
                <ModoButton
                  type="text"
                  shape="circle"
                  icon={<ClearOutlined style={{ fontSize: 12, color: "#79879C" }} />}
                  onClick={handleReset}
                  style={{ width: 28, height: 28 }}
                />
              </Tooltip>
              {items.length > 3 && (
                <Tooltip title={expand ? "收起" : "展开筛选"}>
                  <ModoButton
                    type="text"
                    shape="circle"
                    icon={<FilterOutlined style={{ fontSize: 12, color: "#79879C" }} />}
                    onClick={handleToggleExpand}
                    style={{ width: 28, height: 28 }}
                  />
                </Tooltip>
              )}
              <ModoButton type="primary" htmlType="submit" style={{ minWidth: 72, height: 28 }}>
                查询
              </ModoButton>
            </div>
          </div>
        </Form>

        <div style={{ display: "flex", fontSize: 12, width: "100%", alignItems: "flex-start", minHeight: 20, lineHeight: "20px", marginTop: 6 }}>
          <div style={{ display: "flex", alignItems: "flex-start", flex: 1 }}>
            <span style={{ flexShrink: 0, color: "#79879C", whiteSpace: "pre-wrap", display: "flex", alignItems: "center", minHeight: 20 }}>
              已筛选条件：
            </span>
            <div style={{ display: "flex", alignItems: "flex-start", flexWrap: "wrap", gap: 4, minHeight: 20 }}>
              {Object.keys(searchParams).length > 0 ? (
                <>{renderFilterTags()}</>
              ) : (
                <span style={{ color: "#242E43", display: "flex", alignItems: "center", minHeight: 20 }}>全部</span>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default PageFilter;