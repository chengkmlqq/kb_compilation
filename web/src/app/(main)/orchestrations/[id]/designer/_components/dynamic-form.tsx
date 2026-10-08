'use client';

import React from 'react';
import { Form, Input, Select, InputNumber, Radio, Slider, Switch } from 'antd';
import type { FormInstance } from 'antd';

/** 动态表单字段契约 —— 与 data-synth dynamic-form.tsx 一致（step_cfg schema 元素） */
export interface FormField {
  name: string;
  label: string;
  type: 'input' | 'select' | 'number' | 'radio' | 'slider' | 'switch' | 'textarea';
  options?: Array<{ label: string; value: string | number | boolean }>;
  required?: boolean;
  defaultValue?: unknown;
  placeholder?: string;
  /** 透传给控件的额外属性 */
  props?: Record<string, unknown>;
}

interface DynamicFormProps {
  /** 字段数组，或可被 JSON.parse 的 schema 字符串 */
  config: string | FormField[];
  form: FormInstance;
}

/**
 * 按 schema 渲染动态表单（input/select/number/radio/slider/switch/textarea）。
 * kb 无 tailwind：原 tailwind className 全部替换为内联 style。
 */
export function DynamicForm({ config, form }: DynamicFormProps) {
  const fields: FormField[] = React.useMemo(() => {
    if (!config) return [];
    if (typeof config === 'string') {
      try {
        return JSON.parse(config) as FormField[];
      } catch (e) {
        console.error('Failed to parse dynamic form config:', e);
        return [];
      }
    }
    return config;
  }, [config]);

  if (!fields || !Array.isArray(fields)) return null;

  return (
    <React.Fragment>
      {fields.map((field) => (
        <Form.Item
          key={field.name}
          name={field.name}
          label={
            <span style={{ fontSize: 12, color: '#4d5e7d', fontFamily: 'PingFang SC, sans-serif' }}>
              {field.label}
            </span>
          }
          rules={field.required ? [{ required: true, message: `请输入${field.label}` }] : undefined}
          style={{ marginBottom: 0 }}
        >
          {renderWidget(field, form)}
        </Form.Item>
      ))}
    </React.Fragment>
  );
}

/** Slider 带百分比读数（原 SliderWithLabel） */
function SliderWithLabel({ field, form }: { field: FormField; form: FormInstance }) {
  const value = Form.useWatch(field.name, form);
  const commonProps = {
    placeholder: field.placeholder || '请输入',
    ...(field.props || {}),
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
      <Slider
        {...commonProps}
        style={{ flex: 1 }}
        tooltip={{ open: false }}
      />
      <div
        style={{
          background: '#eff4f9',
          padding: '4px 12px',
          borderRadius: 4,
          minWidth: 60,
          textAlign: 'center',
          fontSize: 12,
          color: '#242e43',
          fontFamily: 'PingFang SC, sans-serif',
        }}
      >
        {String(value ?? field.defaultValue ?? 0)}%
      </div>
    </div>
  );
}

function renderWidget(field: FormField, form: FormInstance) {
  const commonProps = {
    placeholder: field.placeholder || '请输入',
    ...(field.props || {}),
  };

  switch (field.type) {
    case 'select':
      return (
        <Select
          {...commonProps}
          options={field.options}
          style={{ width: '100%', height: 32 }}
          variant="borderless"
        />
      );
    case 'number':
      return <InputNumber {...commonProps} style={{ width: '100%', height: 32 }} />;
    case 'radio':
      return (
        <Radio.Group {...commonProps} style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
          {field.options?.map((opt) => (
            <Radio key={String(opt.value)} value={opt.value}>
              {opt.label}
            </Radio>
          ))}
        </Radio.Group>
      );
    case 'slider':
      return <SliderWithLabel field={field} form={form} />;
    case 'switch':
      return <Switch {...commonProps} size="small" />;
    case 'textarea':
      return <Input.TextArea {...commonProps} rows={4} />;
    case 'input':
    default:
      return <Input {...commonProps} style={{ height: 32 }} />;
  }
}