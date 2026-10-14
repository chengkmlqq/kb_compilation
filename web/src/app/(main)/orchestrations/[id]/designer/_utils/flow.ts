/**
 * 编排骨架（nodes / edges）与 ReactFlow 画布结构之间的转换工具。
 *
 * 抽出自 designer-client.tsx：可编辑设计器与「模板设计」只读预览共用同一套
 * 归一化逻辑，避免两处重复实现导致模板预览与设计器渲染不一致。
 */
import type { Edge } from '@xyflow/react';

import type { StepFlowNode } from '../_components/custom-node';
import { parseJsonRecord } from './json';

/** 画布节点 ID 生成（无 nanoid 依赖） */
export function uid(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/** 后端 nodes 原始结构（TapeDetail.nodes 为宽松结构，此处限定读取字段） */
export interface RawFlowNode {
  id?: unknown;
  type?: unknown;
  position?: { x?: unknown; y?: unknown };
  data?: { stepInst?: unknown; label?: unknown; icon?: unknown; group?: unknown; config?: unknown };
}

/** 后端 edges 原始结构 */
export interface RawFlowEdge {
  id?: unknown;
  source?: unknown;
  target?: unknown;
  sourceHandle?: unknown;
  targetHandle?: unknown;
  type?: unknown;
}

/** 后端节点 → 画布节点 */
export function normalizeNode(raw: RawFlowNode): StepFlowNode {
  return {
    id: String(raw.id ?? uid('node')),
    type: 'stepNode',
    position: {
      x: Number(raw.position?.x ?? 0),
      y: Number(raw.position?.y ?? 0),
    },
    data: {
      stepInst: String(raw.data?.stepInst ?? ''),
      label: String(raw.data?.label ?? ''),
      icon: typeof raw.data?.icon === 'string' ? raw.data.icon : null,
      group: typeof raw.data?.group === 'string' ? raw.data.group : null,
      config: parseJsonRecord(raw.data?.config ?? {}),
    },
  };
}

/** 后端连线 → 画布连线 */
export function normalizeEdge(raw: RawFlowEdge): Edge {
  return {
    id: String(raw.id ?? `${raw.source}-${raw.target}`),
    source: String(raw.source ?? ''),
    target: String(raw.target ?? ''),
    sourceHandle: typeof raw.sourceHandle === 'string' ? raw.sourceHandle : undefined,
    targetHandle: typeof raw.targetHandle === 'string' ? raw.targetHandle : undefined,
    type: typeof raw.type === 'string' ? raw.type : 'default',
  };
}
