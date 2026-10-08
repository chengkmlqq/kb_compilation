'use client';

import React, { memo, useMemo } from 'react';
import { Handle, Position } from '@xyflow/react';
import type { Node, NodeProps } from '@xyflow/react';
import { Dropdown } from 'antd';
import {
  DatabaseOutlined,
  HomeOutlined,
  CheckCircleOutlined,
  SyncOutlined,
  PrinterOutlined,
  CodeOutlined,
  SecurityScanOutlined,
  ExperimentOutlined,
  RobotOutlined,
  CloudUploadOutlined,
  FilterOutlined,
  DeleteOutlined,
  MoreOutlined,
} from '@ant-design/icons';

/**
 * 编排画布自定义节点 —— 从 data-synth custom-node.tsx 迁移（裁剪版）。
 * 节点数据契约：{ stepInst, label, icon, group, config }
 * - 图标：优先 node.data.icon 命中的内置映射，其次按 stepInst 映射，兜底 DatabaseOutlined
 * - 操作：仅保留「删除」（拖拽删除/连线删除由 ReactFlow 原生键盘删除负责）
 * - kb 无 tailwind：原 className 全部替换为内联 style；无 modo-icon，全部换 @ant-design/icons
 */

/** 节点数据契约（与后端同步：保存时仅持久化 stepInst/label/icon/group/config） */
export interface StepNodeData extends Record<string, unknown> {
  /** 步骤实例标识（step_inst），后端据此匹配 kb_tape_step */
  stepInst: string;
  /** 步骤显示名 */
  label: string;
  /** 图标名（step_icon，可为空） */
  icon?: string | null;
  /** 分组（group_type） */
  group?: string | null;
  /** 步骤配置（DynamicForm 渲染 step_cfg 后的值） */
  config: Record<string, unknown>;
  /** 删除回调（运行期注入，保存时剔除） */
  onDelete?: (nodeId: string) => void;
}

/** React Flow 节点类型：type 固定 'stepNode' */
export type StepFlowNode = Node<StepNodeData, 'stepNode'>;

/** stepInst -> 图标 兜底映射（kb 通用编排的常见步骤） */
const STEP_ICON_MAP: Record<string, React.ReactNode> = {
  var: <HomeOutlined />,
  condition: <CheckCircleOutlined />,
  loop: <SyncOutlined />,
  print: <PrinterOutlined />,
  code: <CodeOutlined />,
  sample: <DatabaseOutlined />,
  sensitive: <SecurityScanOutlined />,
  train: <ExperimentOutlined />,
  synth: <RobotOutlined />,
  push: <CloudUploadOutlined />,
  filter: <FilterOutlined />,
};

/** 图标名 -> 图标 映射（node.data.icon） */
const ICON_MAP: Record<string, React.ReactNode> = {
  ...STEP_ICON_MAP,
  database: <DatabaseOutlined />,
  home: <HomeOutlined />,
  check: <CheckCircleOutlined />,
  sync: <SyncOutlined />,
  printer: <PrinterOutlined />,
  code: <CodeOutlined />,
  security: <SecurityScanOutlined />,
  experiment: <ExperimentOutlined />,
  robot: <RobotOutlined />,
  upload: <CloudUploadOutlined />,
  filter: <FilterOutlined />,
};

/** 控制类分组显示蓝色图标底，其余青色 */
function isControlGroup(group?: string | null): boolean {
  const g = String(group || '').trim();
  return g === 'control' || g.includes('控制');
}

/** 节点卡片上的配置摘要：取 config 前 3 个非空字段 */
function buildConfigPreview(config: Record<string, unknown>): Array<{ label: string; value: string }> {
  const preview: Array<{ label: string; value: string }> = [];
  for (const [key, value] of Object.entries(config || {})) {
    if (key === 'label') continue;
    if (value === null || value === undefined || value === '') continue;
    const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
    if (!text) continue;
    preview.push({ label: key, value: text });
    if (preview.length >= 3) break;
  }
  return preview;
}

export const CustomNode = memo(function CustomNode({ id, data, selected }: NodeProps<StepFlowNode>) {
  const isControl = isControlGroup(data.group);
  const iconBg = isControl ? '#3261ce' : '#35c9c0';

  const icon = useMemo(() => {
    if (data.icon && ICON_MAP[data.icon]) return ICON_MAP[data.icon];
    if (data.stepInst && STEP_ICON_MAP[data.stepInst]) return STEP_ICON_MAP[data.stepInst];
    return <DatabaseOutlined />;
  }, [data.icon, data.stepInst]);

  const previewItems = useMemo(() => buildConfigPreview(data.config), [data.config]);

  const cardStyle: React.CSSProperties = {
    background: '#fff',
    border: `1px solid ${selected ? '#3261ce' : '#e3e9ef'}`,
    borderRadius: 6,
    boxShadow: selected
      ? '0 0 0 1px #3261ce, 0 8px 20px rgba(36,46,67,0.1)'
      : '0 2px 5px rgba(36,46,67,0.1)',
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    padding: 12,
    width: 240,
    position: 'relative',
    transition: 'all 0.2s ease-in-out',
  };

  return (
    <div style={{ display: 'flex', alignItems: 'stretch', minHeight: 48, position: 'relative' }}>
      {/* 目标连接点（左侧，悬停/已连接时显示） */}
      <Handle
        type="target"
        position={Position.Left}
        style={{
          width: 3,
          minWidth: 0,
          borderRadius: 0,
          left: -3,
          top: 23,
          transform: 'translateY(-50%)',
          background: '#3261ce',
          border: 'none',
          padding: 0,
        }}
      />

      <div style={cardStyle}>
        {/* 头部：图标 + 标题 + 操作菜单 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', minWidth: 0 }}>
            <div
              style={{
                background: iconBg,
                width: 24,
                height: 24,
                borderRadius: 6,
                boxShadow: '0 2px 4px rgba(50,97,206,0.15)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: '#fff',
                fontSize: 14,
                flexShrink: 0,
              }}
            >
              {icon}
            </div>
            <p
              title={data.label}
              style={{
                margin: 0,
                maxWidth: 150,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontSize: 14,
                fontWeight: 600,
                lineHeight: '22px',
                color: '#242e43',
                fontFamily: 'PingFang SC, sans-serif',
              }}
            >
              {data.label}
            </p>
          </div>

          <Dropdown
            menu={{
              items: [
                {
                  key: 'delete',
                  label: '删除',
                  danger: true,
                  icon: <DeleteOutlined />,
                  onClick: (info: { domEvent: { stopPropagation: () => void } }) => {
                    info.domEvent.stopPropagation();
                    data.onDelete?.(id);
                  },
                },
              ],
            }}
            trigger={['click']}
            placement="bottomRight"
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                padding: 2,
                borderRadius: '50%',
                color: '#79879c',
                cursor: 'pointer',
              }}
              onClick={(e) => e.stopPropagation()}
            >
              <MoreOutlined style={{ fontSize: 16 }} />
            </div>
          </Dropdown>
        </div>

        {/* 配置摘要 */}
        {previewItems.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, width: '100%' }}>
            {previewItems.map((item) => (
              <div
                key={item.label}
                style={{
                  background: '#eff4f9',
                  display: 'flex',
                  gap: 10,
                  height: 28,
                  alignItems: 'center',
                  overflow: 'hidden',
                  padding: '0 10px',
                  borderRadius: 6,
                  width: '100%',
                }}
              >
                <span
                  style={{
                    flexShrink: 0,
                    minWidth: 48,
                    fontSize: 12,
                    color: '#79879c',
                    lineHeight: '20px',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {item.label}
                </span>
                <span
                  title={item.value}
                  style={{
                    flex: 1,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    fontSize: 12,
                    color: '#242e43',
                    lineHeight: '20px',
                  }}
                >
                  {item.value}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 源连接点（右侧，悬停/已连接时显示） */}
      <Handle
        type="source"
        position={Position.Right}
        id="next"
        style={{
          width: 3,
          minWidth: 0,
          borderRadius: 0,
          right: -3,
          top: 23,
          transform: 'translateY(-50%)',
          background: '#3261ce',
          border: 'none',
          padding: 0,
        }}
      />
    </div>
  );
});

/** 画布节点类型注册表 */
export const nodeTypes = {
  stepNode: CustomNode,
};
