// 队列显示名映射（对齐 data-synth src/app/(main)/job-monitor/queue-label.ts）

const JOB_MONITOR_QUEUE_LABEL_MAP: Record<string, string> = {
  default: "默认队列",
  documents: "文档处理队列",
  embedding: "向量化队列",
  wiki: "Wiki 构建队列",
  graph: "图谱队列",
};

function normalizeQueueName(queueName?: string | null): string {
  return String(queueName || "").trim();
}

function normalizeQueueLabel(queueLabel?: string | null): string {
  return String(queueLabel || "").trim();
}

export function resolveJobMonitorQueueLabel(
  queueName?: string | null,
  queueLabel?: string | null,
): string {
  const normalizedQueueName = normalizeQueueName(queueName);
  const normalizedQueueLabel = normalizeQueueLabel(queueLabel);
  const mappedLabel = JOB_MONITOR_QUEUE_LABEL_MAP[normalizedQueueName.toLowerCase()];

  if (mappedLabel) {
    return mappedLabel;
  }

  return normalizedQueueLabel || normalizedQueueName || "-";
}
