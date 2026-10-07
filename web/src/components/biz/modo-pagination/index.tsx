"use client";

import React from "react";
import { Pagination, PaginationProps } from "antd";
import styles from "./modo-pagination.module.css";

export interface ModoPaginationProps extends PaginationProps {
  containerClassName?: string;
}

/**
 * ModoPagination — 常驻底栏分页（对齐 data-synth 的 modo-pagination 实现）。
 * 与 <ModoTable /> 配套使用：表格 flex:1 占满剩余高度，分页 flex-shrink:0 固定在底部。
 */
export const ModoPagination: React.FC<ModoPaginationProps> = ({
  showSizeChanger = true,
  showQuickJumper = true,
  showTotal = (total) => `共 ${total} 条`,
  containerClassName,
  ...props
}) => {
  return (
    <div
      className={
        containerClassName
          ? `${styles["pagination-container"]} ${containerClassName}`
          : styles["pagination-container"]
      }
    >
      <Pagination
        showSizeChanger={showSizeChanger}
        showQuickJumper={showQuickJumper}
        showTotal={showTotal}
        {...props}
      />
    </div>
  );
};

export default ModoPagination;