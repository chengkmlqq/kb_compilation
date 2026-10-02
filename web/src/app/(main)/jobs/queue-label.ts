// 队列显示名映射（对齐 data-synth src/app/(main)/job-monitor/queue-label.ts）

const JOB_MONITOR_QUEUE_LABEL_MAP: Record<string, string> = {
  default: "默认队列",
  documents: "文档处理队列",
  embedding: "向量化队列",
  wiki: "Wiki 构建队列",
  graph: "图谱队列",
};

type JobMonitorQueueSource = {
  queueLabel?: string | null;
  queueName?: string | null;
};

type JobMonitorQueueOption = {
  label: string;
  value: string;
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

export function resolveJobMonitorQueueOptionLabel(
  queueName?: string | null,
  queueLabel?: string | null,
  disambiguate = false,
): string {
  const resolvedLabel = resolveJobMonitorQueueLabel(queueName, queueLabel);
  const normalizedQueueName = normalizeQueueName(queueName);

  if (!disambiguate || !normalizedQueueName) {
    return resolvedLabel;
  }

  return `${resolvedLabel} (${normalizedQueueName})`;
}

export function buildJobMonitorQueueOptions(
  queues: JobMonitorQueueSource[],
): JobMonitorQueueOption[] {
  const uniqueQueues: Array<{ queueLabel: string; queueName: string }> = [];
  const seenQueueNames = new Set<string>();

  for (const queue of queues) {
    const queueName = normalizeQueueName(queue.queueName);

    if (seenQueueNames.has(queueName)) {
      continue;
    }

    seenQueueNames.add(queueName);
    uniqueQueues.push({
      queueLabel: normalizeQueueLabel(queue.queueLabel),
      queueName,
    });
  }

  const labelCounts = new Map<string, number>();
  for (const queue of uniqueQueues) {
    const label = resolveJobMonitorQueueLabel(queue.queueName, queue.queueLabel);
    labelCounts.set(label, (labelCounts.get(label) || 0) + 1);
  }

  return uniqueQueues.map((queue) => {
    const resolvedLabel = resolveJobMonitorQueueLabel(
      queue.queueName,
      queue.queueLabel,
    );
    const shouldDisambiguate = (labelCounts.get(resolvedLabel) || 0) > 1;

    return {
      label: resolveJobMonitorQueueOptionLabel(
        queue.queueName,
        queue.queueLabel,
        shouldDisambiguate,
      ),
      value: queue.queueName,
    };
  });
}