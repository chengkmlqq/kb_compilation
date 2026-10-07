"use client";

import React from "react";
import { Space, Dropdown } from "antd";
import type { MenuProps } from "antd";
import { MoreOutlined } from "@ant-design/icons";
import ModoButton from "@/components/biz/modo-button";

export interface ActionItem {
  key: string;
  label: React.ReactNode;
  icon?: React.ReactNode;
  danger?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  visible?: boolean;
}

export interface ModoActionGroupProps {
  actions: ActionItem[];
  maxCount?: number;
}

/** ModoActionGroup — 操作列按钮组（对齐 data-synth）：超出 maxCount 的收进「更多」下拉 */
export const ModoActionGroup: React.FC<ModoActionGroupProps> = ({ actions = [], maxCount = 2 }) => {
  const visibleActions = actions.filter((action) => action.visible !== false);
  if (visibleActions.length === 0) return null;

  let primaryActions: ActionItem[] = [];
  let secondaryActions: ActionItem[] = [];
  if (visibleActions.length <= maxCount) {
    primaryActions = visibleActions;
  } else {
    primaryActions = visibleActions.slice(0, maxCount);
    secondaryActions = visibleActions.slice(maxCount);
  }

  const renderButton = (action: ActionItem) => (
    <ModoButton
      key={action.key}
      type="link"
      size="small"
      danger={action.danger}
      disabled={action.disabled}
      onClick={action.onClick}
      style={{ padding: "0 4px" }}
    >
      {action.label}
    </ModoButton>
  );

  const menuItems: MenuProps["items"] = secondaryActions.map((action) => ({
    key: action.key,
    label: action.label,
    danger: action.danger,
    disabled: action.disabled,
    icon: action.icon,
    onClick: action.onClick,
  }));

  return (
    <Space size={4} role="group">
      {primaryActions.map(renderButton)}
      {secondaryActions.length > 0 && (
        <Dropdown menu={{ items: menuItems }} trigger={["click"]}>
          <ModoButton
            type="link"
            size="small"
            style={{ padding: "0 4px", display: "inline-flex", alignItems: "center" }}
          >
            <MoreOutlined style={{ fontSize: 14 }} />
          </ModoButton>
        </Dropdown>
      )}
    </Space>
  );
};

export default ModoActionGroup;