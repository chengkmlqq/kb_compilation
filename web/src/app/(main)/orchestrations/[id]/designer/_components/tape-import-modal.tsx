"use client";

/**
 * 编排草稿导入弹窗（迁移自 data-synth tape-import-modal.tsx 裁剪版）：
 * 上传 JSON -> 交给 onImport 调后端导入接口（覆盖当前草稿，不自动发布）。
 * kb 无「绑定数据集/合成模型」概念，故去掉 overwriteBindings 选项。
 */
import { useState } from "react";
import { Alert, App, Modal, Upload } from "antd";
import { InboxOutlined } from "@ant-design/icons";

const { Dragger } = Upload;

interface TapeImportModalProps {
  open: boolean;
  onClose: () => void;
  onImport: (content: string) => Promise<void>;
}

export function TapeImportModal({ open, onClose, onImport }: TapeImportModalProps) {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);

  const handleBeforeUpload = async (file: File) => {
    setLoading(true);
    try {
      const content = await file.text();
      await onImport(content);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "读取导入文件失败");
    } finally {
      setLoading(false);
    }
    return false;
  };

  return (
    <Modal
      title="导入编排草稿"
      open={open}
      onCancel={() => {
        if (!loading) onClose();
      }}
      footer={null}
      destroyOnClose
      width={560}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <Alert
          type="warning"
          showIcon
          message="导入说明"
          description="导入会覆盖当前未发布的草稿，但不会自动发布或自动运行。建议先确认当前草稿已通过「导出」备份。"
        />
        <Dragger
          accept=".json,application/json"
          beforeUpload={handleBeforeUpload}
          showUploadList={false}
          disabled={loading}
          style={{ borderRadius: 8 }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined style={{ color: "#1677ff" }} />
          </p>
          <p className="ant-upload-text">点击或拖拽 JSON 文件到此区域上传</p>
          <p className="ant-upload-hint">仅支持由编排设计器导出的 JSON 草稿文件</p>
        </Dragger>
        {loading && (
          <div style={{ textAlign: "center", color: "#86909c" }}>正在导入，请稍候...</div>
        )}
      </div>
    </Modal>
  );
}