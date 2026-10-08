'use client';

import React, { useMemo, useState } from 'react';
import { Input, Popover } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
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
} from '@ant-design/icons';
import type { StepDefineItem } from '@/lib/api';

/**
 * 左侧组件栏 —— 从 data-synth left-sidebar.tsx 迁移（裁剪版）。
 * - 组件列表来自 apiListStepDefines，按 group_type 分组展示
 * - HTML5 draggable：dataTransfer 携带 'application/reactflow'（=stepNode）与
 *   'application/reactflow-def'（=StepDefineItem JSON），画布 onDrop 据此生成节点
 * - kb 无 tailwind：原 className 全部替换为内联 style；无 modo-icon，全部换 @ant-design/icons
 */

interface LeftSidebarProps {
  defines: StepDefineItem[];
  disabled?: boolean;
}

/** stepInst -> 图标 兜底映射（与 custom-node 保持一致） */
const NODE_ICONS: Record<string, React.ReactNode> = {
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

/** 控制类分组显示蓝色图标底，其余青色 */
function isControlGroup(groupType: string): boolean {
  const g = String(groupType || '').trim();
  return g === 'control' || g.includes('控制');
}

export function LeftSidebar({ defines, disabled = false }: LeftSidebarProps) {
  const [searchText, setSearchText] = useState('');

  /** 分组 + 关键词过滤 */
  const groups = useMemo(() => {
    const map = new Map<string, StepDefineItem[]>();
    for (const d of defines) {
      const groupType = String(d.group_type || '').trim();
      if (!groupType) continue;
      if (searchText && !`${d.step_label}${d.step_inst}${d.step_desc || ''}`.toLowerCase().includes(searchText.toLowerCase())) {
        continue;
      }
      if (!map.has(groupType)) map.set(groupType, []);
      map.get(groupType)!.push(d);
    }
    // 按分组名排序，组内按 step_seq 排序
    return Array.from(map.entries())
      .map(([groupName, items]) => ({
        groupName,
        items: [...items].sort((a, b) => (a.step_seq ?? 0) - (b.step_seq ?? 0)),
      }))
      .sort((a, b) => a.groupName.localeCompare(b.groupName, 'zh-Hans-CN'));
  }, [defines, searchText]);

  const onDragStart = (event: React.DragEvent, defItem: StepDefineItem) => {
    if (disabled) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.setData('application/reactflow', 'stepNode');
    event.dataTransfer.setData('application/reactflow-def', JSON.stringify(defItem));
    event.dataTransfer.effectAllowed = 'move';

    // 自定义拖拽预览（隐藏到屏幕外，避免默认幽灵图）
    const dragPreview = document.createElement('div');
    dragPreview.style.width = '171px';
    dragPreview.style.height = '34px';
    dragPreview.style.padding = '0 8px';
    dragPreview.style.background = 'white';
    dragPreview.style.borderRadius = '6px';
    dragPreview.style.border = '1px solid #eff4f9';
    dragPreview.style.boxShadow = '0px 4px 10px rgba(36,46,67,0.1)';
    dragPreview.style.position = 'absolute';
    dragPreview.style.top = '-1000px';
    dragPreview.style.display = 'flex';
    dragPreview.style.alignItems = 'center';
    dragPreview.style.gap = '8px';
    dragPreview.style.pointerEvents = 'none';
    const iconBg = isControlGroup(defItem.group_type) ? '#3261ce' : '#35c9c0';
    dragPreview.innerHTML = `
      <div style="background: ${iconBg}; width: 20px; height: 20px; border-radius: 4px; display: flex; align-items: center; justify-content: center; color: white; flex-shrink: 0;">
        <svg viewBox="0 0 1024 1024" width="12" height="12" fill="currentColor"><path d="M832 64H192c-17.7 0-32 14.3-32 32v736c0 17.7 14.3 32 32 32h640c17.7 0 32-14.3 32-32V96c0-17.7-14.3-32-32-32z m-600 72h560v208H232V136z m560 648H232V424h560v360z m-448-232h336v64h-336z m0 128h336v64h-336z"></path></svg>
      </div>
      <div style="font-size: 14px; color: #242e43; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1;">
        ${defItem.step_label || defItem.step_inst || '组件'}
      </div>
    `;
    document.body.appendChild(dragPreview);
    event.dataTransfer.setDragImage(dragPreview, 85, 17);
    setTimeout(() => {
      document.body.removeChild(dragPreview);
    }, 0);
  };

  return (
    <div
      style={{
        height: '100%',
        borderRight: '1px solid #eff4f9',
        background: '#fff',
        display: 'flex',
        flexDirection: 'column',
        width: 208,
        flexShrink: 0,
        overflow: 'hidden',
        userSelect: 'none',
      }}
    >
      {/* 栏头：标题 + 搜索 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', flexShrink: 0 }}>
        <p
          style={{
            margin: 0,
            flex: 1,
            fontSize: 14,
            fontWeight: 700,
            lineHeight: '22px',
            color: '#242e43',
            fontFamily: 'PingFang SC, sans-serif',
            whiteSpace: 'nowrap',
          }}
        >
          算子列表
        </p>
        <Input
          size="small"
          allowClear
          placeholder="搜索"
          prefix={<SearchOutlined style={{ color: '#86909c', fontSize: 12 }} />}
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          style={{ width: 120, fontSize: 12 }}
        />
      </div>

      {/* 分组列表 */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {groups.length === 0 ? (
          <div style={{ textAlign: 'center', fontSize: 12, color: '#79879c', marginTop: 40 }}>暂无组件数据</div>
        ) : (
          groups.map(({ groupName, items }) => (
            <div key={groupName} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <p
                style={{
                  margin: 0,
                  height: 28,
                  display: 'flex',
                  alignItems: 'center',
                  fontSize: 12,
                  color: '#79879c',
                  lineHeight: '20px',
                  fontFamily: 'PingFang SC, sans-serif',
                  whiteSpace: 'nowrap',
                }}
              >
                {groupName}
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {items.map((item) => {
                  const isControl = isControlGroup(item.group_type);
                  const iconBg = isControl ? '#3261ce' : '#35c9c0';
                  const icon = (item.step_icon && NODE_ICONS[item.step_icon]) || NODE_ICONS[item.step_inst] || <DatabaseOutlined />;

                  const popoverContent = (
                    <div style={{ padding: '12px 16px', width: 200 }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        <div
                          style={{
                            background: iconBg,
                            boxShadow: isControl ? '0 2px 4px rgba(50,97,206,0.15)' : '0 3px 6px rgba(53,201,192,0.15)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            padding: 4,
                            borderRadius: 6,
                            width: 24,
                            height: 24,
                            color: '#fff',
                            fontSize: 14,
                          }}
                        >
                          {icon}
                        </div>
                        <div style={{ fontWeight: 600, color: '#242e43', fontSize: 14, lineHeight: '22px' }}>
                          {item.step_label}
                        </div>
                      </div>
                      <div style={{ fontSize: 12, color: '#79879c', lineHeight: '18px', whiteSpace: 'pre-wrap', marginTop: 8 }}>
                        {item.step_desc || '用于在流程中执行相关操作的逻辑单元'}
                      </div>
                    </div>
                  );

                  return (
                    <Popover
                      key={item.id}
                      placement="right"
                      mouseEnterDelay={0.4}
                      content={popoverContent}
                    >
                      <div
                        draggable={!disabled}
                        onDragStart={(event) => onDragStart(event, item)}
                        style={{
                          display: 'flex',
                          gap: 8,
                          alignItems: 'center',
                          borderRadius: 6,
                          padding: '6px 8px',
                          cursor: disabled ? 'not-allowed' : 'grab',
                          transition: 'background 0.2s',
                        }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = '#f6f8fa')}
                        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                      >
                        <div
                          style={{
                            background: iconBg,
                            boxShadow: isControl ? '0 2px 4px rgba(50,97,206,0.15)' : '0 3px 6px rgba(53,201,192,0.15)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            padding: 4,
                            borderRadius: 6,
                            width: 20,
                            height: 20,
                            color: '#fff',
                            fontSize: 12,
                            flexShrink: 0,
                          }}
                        >
                          {icon}
                        </div>
                        <p
                          style={{
                            margin: 0,
                            flex: 1,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            fontSize: 14,
                            lineHeight: '22px',
                            color: '#242e43',
                            fontFamily: 'PingFang SC, sans-serif',
                          }}
                        >
                          {item.step_label || item.step_inst}
                        </p>
                      </div>
                    </Popover>
                  );
                })}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}