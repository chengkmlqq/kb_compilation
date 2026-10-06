"use client";

import { useState } from "react";
import { Form, Input, Modal } from "antd";
import { LinkOutlined } from "@ant-design/icons";

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmit: (url: string, fileName: string) => Promise<void>;
}

export default function UrlImportModal({ open, onClose, onSubmit }: Props) {
  const [form] = Form.useForm<{ url: string; fileName?: string }>();
  const [submitting, setSubmitting] = useState(false);

  const handleOk = async () => {
    const values = await form.validateFields();
    setSubmitting(true);
    try {
      await onSubmit(values.url, values.fileName || "");
      form.resetFields();
      onClose();
    } catch {
      /* 错误已由上层 message 提示 */
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="URL 导入文档"
      open={open}
      onOk={() => void handleOk()}
      onCancel={() => {
        form.resetFields();
        onClose();
      }}
      okText="导入"
      cancelText="取消"
      confirmLoading={submitting}
      destroyOnClose
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="url"
          label="文件链接"
          rules={[
            { required: true, message: "请输入文件链接" },
            {
              pattern: /^https?:\/\//i,
              message: "仅支持 http/https 链接",
            },
          ]}
        >
          <Input prefix={<LinkOutlined />} placeholder="https://example.com/doc.pdf" />
        </Form.Item>
        <Form.Item
          name="fileName"
          label="文件名（可选）"
          extra="留空则从链接末尾识别；无法识别时需手动指定（如 doc.pdf）"
        >
          <Input placeholder="doc.pdf" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
