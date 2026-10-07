"use client";

import React from "react";
import { Modal, Space } from "antd";
import type { ModalProps } from "antd";
import styles from "./modo-modal.module.css";
import ModoButton from "@/components/biz/modo-button";

export interface ModoModalProps extends Omit<ModalProps, "footer"> {
  onCancel?: () => void;
  onOk?: () => void;
  cancelText?: string;
  okText?: string;
  confirmLoading?: boolean;
  showFooter?: boolean;
  footer?: React.ReactNode;
}

/** ModoModal — 统一弹窗（对齐 data-synth）：header/footer 分隔线、ModoButton 底部右对齐 */
export const ModoModal: React.FC<ModoModalProps> = ({
  onCancel,
  onOk,
  cancelText = "取消",
  okText = "确定",
  confirmLoading,
  showFooter = true,
  footer,
  children,
  className,
  ...props
}) => {
  const renderFooter = () => {
    if (footer === null) return null;
    if (footer !== undefined) return footer;
    if (!showFooter) return null;
    return (
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Space size={12}>
          <ModoButton onClick={onCancel}>{cancelText}</ModoButton>
          <ModoButton type="primary" loading={confirmLoading} onClick={onOk}>
            {okText}
          </ModoButton>
        </Space>
      </div>
    );
  };

  return (
    <Modal
      className={`${styles["modo-modal"]} ${className || ""}`}
      onCancel={onCancel}
      onOk={onOk}
      confirmLoading={confirmLoading}
      footer={renderFooter()}
      centered
      destroyOnHidden
      {...props}
    >
      {children}
    </Modal>
  );
};

export default ModoModal;