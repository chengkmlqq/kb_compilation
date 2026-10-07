"use client";

import React from "react";
import { Drawer, Space } from "antd";
import type { DrawerProps } from "antd";
import { CloseOutlined } from "@ant-design/icons";
import styles from "./modo-drawer.module.css";
import ModoButton from "@/components/biz/modo-button";

export interface ModoDrawerProps extends Omit<DrawerProps, "extra" | "footer" | "closable"> {
  onCancel?: () => void;
  onOk?: () => void;
  cancelText?: string;
  okText?: string;
  confirmLoading?: boolean;
  showFooter?: boolean;
  footer?: React.ReactNode;
}

/** ModoDrawer — 统一抽屉（对齐 data-synth）：右上角关闭图标 + 底部右对齐 取消/确定 */
export const ModoDrawer: React.FC<ModoDrawerProps> = ({
  onCancel,
  onOk,
  onClose,
  cancelText = "取消",
  okText = "确定",
  confirmLoading,
  showFooter = true,
  footer,
  children,
  className,
  styles: drawerStyles,
  ...props
}) => {
  const handleClose: DrawerProps["onClose"] = (e) => {
    onCancel?.();
    onClose?.(e);
  };

  const handleCancelClick = (e?: React.MouseEvent<HTMLElement>) => {
    onCancel?.();
    if (e) onClose?.(e);
  };

  const getFooter = () => {
    if (footer !== undefined) return footer;
    if (!showFooter) return false;
    return (
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Space>
          <ModoButton onClick={handleCancelClick}>{cancelText}</ModoButton>
          <ModoButton type="primary" loading={confirmLoading} onClick={onOk}>
            {okText}
          </ModoButton>
        </Space>
      </div>
    );
  };

  const { width, size, ...drawerProps } = props;
  const combinedDrawerStyles = {
    ...drawerStyles,
    wrapper: {
      ...(drawerStyles as { wrapper?: React.CSSProperties } | undefined)?.wrapper,
      ...(width ? { width } : {}),
    },
  };

  return (
    <Drawer
      className={`${styles["modo-drawer"]} ${className || ""}`}
      styles={combinedDrawerStyles as DrawerProps["styles"]}
      size={size}
      autoFocus={false}
      destroyOnClose={true}
      closable={false}
      onClose={handleClose}
      extra={<CloseOutlined onClick={handleCancelClick} className={styles["close-icon"]} />}
      footer={getFooter()}
      {...drawerProps}
    >
      {children}
    </Drawer>
  );
};

export default ModoDrawer;