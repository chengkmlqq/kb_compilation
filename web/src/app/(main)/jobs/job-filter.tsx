"use client";

import React, { useEffect, useState } from "react";
import { Button, Form, Input, Select, Space } from "antd";
import { SearchOutlined, ReloadOutlined } from "@ant-design/icons";
import { apiJobQueues, JobQueueOption } from "@/lib/api";
import { buildJobMonitorQueueOptions } from "./queue-label";

interface JobFilterValues {
  keyWord?: string;
  queueName?: string;
  state?: string;
}

interface JobFilterProps {
  onSearch: (values: JobFilterValues) => void;
  onReset: () => void;
}

const STATE_OPTIONS = [
  { label: "待执行", value: "PENDING" },
  { label: "运行中", value: "RUNNING" },
  { label: "成功", value: "SUCCESS" },
  { label: "失败", value: "FAILED" },
  { label: "已停止", value: "STOPPED" },
];

function JobFilter({ onSearch, onReset }: JobFilterProps) {
  const [form] = Form.useForm<JobFilterValues>();
  const [queueList, setQueueList] = useState<{ label: string; value: string }[]>([]);

  useEffect(() => {
    apiJobQueues()
      .then((res) => {
        if (res.success && res.data) {
          setQueueList(buildJobMonitorQueueOptions(res.data as JobQueueOption[]));
        }
      })
      .catch(() => {
        /* 队列列表加载失败不阻塞页面 */
      });
  }, []);

  return (
    <div
      style={{
        background: "#fff",
        borderRadius: 8,
        border: "1px solid #E3E9EF",
        padding: "14px 16px",
        marginBottom: 16,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 16,
        flexWrap: "wrap",
      }}
    >
      <Form
        form={form}
        layout="inline"
        onFinish={(values: JobFilterValues) => {
          const filters: JobFilterValues = {};
          if (values.keyWord?.trim()) filters.keyWord = values.keyWord.trim();
          if (values.queueName) filters.queueName = values.queueName;
          if (values.state) filters.state = values.state;
          onSearch(filters);
        }}
        style={{ gap: 12, rowGap: 8 }}
      >
        <Form.Item name="keyWord" label="关键词" style={{ marginBottom: 0 }}>
          <Input placeholder="任务 ID / Task 类名" allowClear style={{ width: 200 }} />
        </Form.Item>
        <Form.Item name="queueName" label="队列" style={{ marginBottom: 0 }}>
          <Select
            placeholder="选择队列"
            allowClear
            style={{ width: 170 }}
            options={queueList}
          />
        </Form.Item>
        <Form.Item name="state" label="状态" style={{ marginBottom: 0 }}>
          <Select
            placeholder="选择状态"
            allowClear
            style={{ width: 130 }}
            options={STATE_OPTIONS}
          />
        </Form.Item>
      </Form>
      <Space>
        <Button
          type="primary"
          icon={<SearchOutlined />}
          onClick={() => form.submit()}
        >
          查询
        </Button>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => {
            form.resetFields();
            onReset();
          }}
        >
          重置
        </Button>
      </Space>
    </div>
  );
}

export default JobFilter;