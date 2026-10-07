"use client";

import React from "react";
import { Tabs, Tooltip } from "antd";
import type { TabsProps } from "antd";
import styles from "./modo-tabs.module.css";

export interface ModoTabsProps extends TabsProps {
  // 预留扩展位
}

export const ModoTabs: React.FC<ModoTabsProps> = ({ className, items, ...props }) => {
  // 动态标签页文字长度限制：除首个（页面标题）外超过 7 字截断 + Tooltip
  const processedItems = items?.map((item, index) => {
    const labelStr = typeof item.label === "string" ? item.label : null;
    if (labelStr && labelStr.length > 7 && index !== 0) {
      return {
        ...item,
        label: (
          <Tooltip title={labelStr}>
            <span className={styles["tab-label-truncated"]}>
              {labelStr.slice(0, 7)}...
            </span>
          </Tooltip>
        ),
      };
    }
    return item;
  });

  return (
    <Tabs
      className={`${styles["modo-tabs"]}${className ? ` ${className}` : ""}`}
      items={processedItems}
      {...props}
    />
  );
};

export default ModoTabs;
