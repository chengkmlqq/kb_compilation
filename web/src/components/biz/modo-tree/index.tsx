"use client";

import React from "react";
import { Tree, theme } from "antd";
import type { TreeProps } from "antd";
import { CaretDownOutlined, CaretRightOutlined } from "@ant-design/icons";
import styles from "./modo-tree.module.css";

export interface ModoTreeProps extends TreeProps {
  compact?: boolean;
  loading?: boolean;
}

/** ModoTree — 紧凑树（对齐 data-synth）：28px 行高、12px 开关块、小三角图标 */
export const ModoTree: React.FC<ModoTreeProps> = ({
  compact = true,
  blockNode = true,
  showLine = false,
  switcherIcon,
  className = "",
  rootClassName = "",
  loading,
  ...props
}) => {
  const { token } = theme.useToken();

  const customSwitcher = (nodeProps: { isLeaf?: boolean; expanded?: boolean }) => {
    if (nodeProps.isLeaf) return null;
    return (
      <div
        className={`ant-tree-switcher-icon ${nodeProps.expanded ? "expanded" : ""}`}
        style={{
          width: 12,
          height: 12,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: token.colorFillAlter || "#EFF4F9",
          borderRadius: 2,
          flexShrink: 0,
        }}
      >
        {nodeProps.expanded ? (
          <CaretDownOutlined style={{ fontSize: 8, color: token.colorTextSecondary }} />
        ) : (
          <CaretRightOutlined style={{ fontSize: 8, color: token.colorTextSecondary }} />
        )}
      </div>
    );
  };

  return (
    <div className={`modo-tree-wrapper ${compact ? styles.compact : ""}`}>
      <Tree
        blockNode={blockNode}
        switcherIcon={switcherIcon || customSwitcher}
        showLine={showLine}
        className={className}
        rootClassName={rootClassName}
        {...(loading !== undefined ? { loading } : {})}
        {...props}
      />
    </div>
  );
};

export default ModoTree;