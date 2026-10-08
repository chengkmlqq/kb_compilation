"use client";

import React from "react";
import { JobStatistics } from "@/lib/api";
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  FileDoneOutlined,
  PauseCircleOutlined,
  SyncOutlined,
} from "@ant-design/icons";

interface JobStatsProps {
  statistics: JobStatistics;
}

const STAT_CARDS: Array<{
  key: keyof JobStatistics;
  label: string;
  icon: React.ReactNode;
  color: string;
  bg: string;
}> = [
  {
    key: "total",
    label: "总数",
    icon: <FileDoneOutlined style={{ fontSize: 22 }} />,
    color: "#242E43",
    bg: "#F0F2F5",
  },
  {
    key: "running",
    label: "运行中",
    icon: <SyncOutlined style={{ fontSize: 22 }} />,
    color: "#1677ff",
    bg: "#E6F4FF",
  },
  {
    key: "success",
    label: "成功",
    icon: <CheckCircleOutlined style={{ fontSize: 22 }} />,
    color: "#389e0d",
    bg: "#F6FFED",
  },
  {
    key: "failed",
    label: "失败",
    icon: <CloseCircleOutlined style={{ fontSize: 22 }} />,
    color: "#cf1322",
    bg: "#FFF1F0",
  },
  {
    key: "stopped",
    label: "已停止",
    icon: <PauseCircleOutlined style={{ fontSize: 22 }} />,
    color: "#d48806",
    bg: "#FFFBE6",
  },
  {
    key: "queued",
    label: "排队中",
    icon: <ClockCircleOutlined style={{ fontSize: 22 }} />,
    color: "#722ed1",
    bg: "#F9F0FF",
  },
];

function JobStats({ statistics }: JobStatsProps) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(6, 1fr)",
        gap: 16,
        marginBottom: 16,
      }}
    >
      {STAT_CARDS.map((stat) => {
        const running = stat.key === "running" && (statistics.running ?? 0) > 0;
        return (
          <div
            key={stat.key}
            className="modo-stat-card"
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: 18,
              background: "#fff",
              borderRadius: 8,
              border: "1px solid #E3E9EF",
              transition: "box-shadow 0.25s",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
              <span
                style={{
                  color: "#79879C",
                  fontSize: 13,
                  marginBottom: 8,
                  fontWeight: 500,
                }}
              >
                {stat.label}
              </span>
              <span
                style={{
                  fontSize: 26,
                  fontWeight: 700,
                  lineHeight: "26px",
                  color: stat.color,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {statistics[stat.key] ?? 0}
              </span>
            </div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: 48,
                height: 48,
                borderRadius: 12,
                background: stat.bg,
                color: stat.color,
                opacity: 0.75,
                flexShrink: 0,
              }}
            >
              <span className={running ? "modo-stat-icon-running" : ""}>
                {stat.icon}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default JobStats;