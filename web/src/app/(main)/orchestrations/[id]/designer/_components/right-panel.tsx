'use client';

import React, { useEffect, useMemo, useRef } from 'react';
import { Drawer, Form, Button, Tag, Typography } from 'antd';
import { parseStepCfgFields } from '../_utils/json';
import { DynamicForm } from './dynamic-form';
import type { FormField } from './dynamic-form';
import type { StepDefineItem } from '@/lib/api';
import type { StepFlowNode } from './custom-node';

/**
 * 右侧配参面板 —— 从 data-synth right-panel.tsx 迁移（裁剪版，改为 antd Drawer）。
 * 裁剪内容：日志 Tab（SSE 实时日志）、参数配置模式（exec_params 面板）、版本历史、
 *          节点单独运行、复制（data-synth 专有交互 / kb 执行是同步返回无 SSE）。
 * 保留：step_cfg schema 渲染（DynamicForm）+ 步骤名称字段。
 * 受控行为：参数改动实时通过 onConfigChange 写回 node.data.config；
 *          关闭抽屉即视为已保存到画布状态（点顶栏「保存」才持久化到后端）。
 */

const { Text } = Typography;

interface RightPanelProps {
  /** 当前选中节点（null = 抽屉关闭） */
  selectedNode: StepFlowNode | null;
  /** 步骤定义列表（按 stepInst 匹配 schema） */
  defines: StepDefineItem[];
  onClose: () => void;
  /** 参数改动实时写回画布 */
  onConfigChange: (nodeId: string, values: Record<string, unknown>) => void;
}

export function RightPanel({ selectedNode, defines, onClose, onConfigChange }: RightPanelProps) {
  const [form] = Form.useForm();
  /** 仅在切换节点时重置表单，避免写回引发的重渲染冲掉用户正在输入的内容 */
  const lastNodeIdRef = useRef<string | null>(null);

  /** 按 stepInst 找步骤定义 */
  const currentDefine = useMemo<StepDefineItem | null>(() => {
    if (!selectedNode) return null;
    return defines.find((d) => d.step_inst === selectedNode.data.stepInst) ?? null;
  }, [defines, selectedNode]);

  /** 字段 = 步骤名称（基础字段）+ step_cfg 自定义字段 */
  const fields = useMemo<FormField[]>(() => {
    const baseFields: FormField[] = [
      {
        name: 'label',
        label: '步骤名称',
        type: 'input',
        required: true,
        placeholder: '请输入步骤名称',
      },
    ];
    const rawFields = parseStepCfgFields(currentDefine?.step_cfg);
    const customFields: FormField[] = [];
    for (const raw of rawFields) {
      if (!raw || typeof raw !== 'object') continue;
      const name = String(raw.name ?? '').trim();
      if (!name) continue;
      customFields.push({ ...(raw as unknown as FormField), name });
    }
    return [...baseFields, ...customFields];
  }, [currentDefine]);

  /** 切换节点：重置并回显该节点已有配置（默认值兜底） */
  useEffect(() => {
    const nodeId = selectedNode?.id ?? null;
    if (nodeId === lastNodeIdRef.current) return;
    lastNodeIdRef.current = nodeId;

    form.resetFields();
    if (!selectedNode) return;

    const defaults: Record<string, unknown> = {};
    for (const f of fields) {
      if (f.defaultValue !== undefined) defaults[f.name] = f.defaultValue;
    }
    form.setFieldsValue({
      ...defaults,
      label: selectedNode.data.label,
      ...(selectedNode.data.config || {}),
    });
  }, [selectedNode, fields, form]);

  /** 参数改动 -> 实时写回 node.data.config */
  const handleValuesChange = (_changed: unknown, allValues: Record<string, unknown>) => {
    if (!selectedNode) return;
    onConfigChange(selectedNode.id, allValues);
  };

  return (
    <Drawer
      open={!!selectedNode}
      placement="right"
      width={380}
      closable={false}
      destroyOnClose={false}
      onClose={onClose}
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          <span
            style={{
              fontSize: 14,
              fontWeight: 700,
              color: '#242e43',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {selectedNode?.data.label || '节点配置'}
          </span>
          {selectedNode?.data.stepInst && (
            <Tag color="blue" style={{ flexShrink: 0 }}>
              {selectedNode.data.stepInst}
            </Tag>
          )}
        </div>
      }
      footer={
        <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center' }}>
          <Text type="secondary" style={{ fontSize: 12, marginRight: 12 }}>
            参数改动即时生效，点顶部「保存」持久化
          </Text>
          <Button onClick={onClose}>关闭</Button>
        </div>
      }
      styles={{ body: { paddingTop: 12 } }}
    >
      {currentDefine?.step_desc && (
        <div
          style={{
            fontSize: 12,
            color: '#79879c',
            lineHeight: '20px',
            marginBottom: 12,
            whiteSpace: 'pre-wrap',
          }}
        >
          {currentDefine.step_desc}
        </div>
      )}

      {fields.length <= 1 ? (
        <div style={{ textAlign: 'center', fontSize: 12, color: '#95a6ba', padding: '20px 0' }}>
          该步骤暂无配置项
        </div>
      ) : (
        <Form form={form} layout="vertical" requiredMark={false} onValuesChange={handleValuesChange}>
          <DynamicForm config={fields} form={form} />
        </Form>
      )}
    </Drawer>
  );
}
