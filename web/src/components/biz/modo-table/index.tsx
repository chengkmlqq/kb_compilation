"use client";

import React from "react";
import { Table } from "antd";
import type { TableProps } from "antd";
import styles from "./modo-table.module.css";

export interface ModoTableProps<T> extends Omit<TableProps<T>, "pagination"> {
  /** 追加到外层容器的 className */
  containerClassName?: string;
  /** 外层容器行内样式（默认占满剩余高度 flex:1/minHeight:0） */
  containerStyle?: React.CSSProperties;
}

/**
 * ModoTable — 一屏自适应表格（对齐 data-synth 的 modo-table 实现）。
 *
 * 特性：
 * - flex 布局占满父容器剩余高度，数据少时表体撑满（底部无空白），数据多时表体内滚动（页面不滚动）
 * - 高度完全由 modo-table.module.css 的 flex 链撑满/收缩（表体 overflow 滚动），**scroll.y 必须不传**：
 *   antd v6 下传 y（哪怕 '100.1%'）会激活 Table 内置滚动测量（ResizeObserver + inline max-height），
 *   实测导致数据加载后表体高度坍缩（底部大片空白）；CSS 已用 max-height:none !important 覆盖 inline，
 *   但 v6 的测量逻辑仍会干扰 flex 链 —— 一律只传 x
 * - 分页交由配套的 <ModoPagination /> 常驻底栏（table 内置分页会随内容浮动）
 */
export function ModoTable<T extends object>({
  scroll,
  containerClassName,
  containerStyle,
  ...tableProps
}: ModoTableProps<T>) {
  const defaultScroll = { x: 1000 };
  const mergedScroll = scroll ? { ...defaultScroll, ...scroll } : defaultScroll;

  return (
    <div
      className={containerClassName ? `${styles["modo-table"]} ${containerClassName}` : styles["modo-table"]}
      style={containerStyle}
    >
      <Table<T> scroll={mergedScroll} pagination={false} {...tableProps} />
    </div>
  );
}

export default ModoTable;