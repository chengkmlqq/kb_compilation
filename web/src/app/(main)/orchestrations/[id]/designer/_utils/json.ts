/**
 * JSON 解析工具 —— 从 data-synth 编排设计器迁移。
 * 后端存储的 config / exec_params / step_cfg 可能是对象、数组或 JSON 字符串，
 * 统一从这里安全解析，坏值回退到默认值。
 */

/** 解析任意值为 JSON（字符串则尝试 parse，失败回退 fallback） */
export function parseJsonValue<T>(value: unknown, fallback: T): T {
  if (value === null || value === undefined) {
    return fallback;
  }

  if (typeof value !== 'string') {
    return value as T;
  }

  const normalized = value.trim();
  if (!normalized) {
    return fallback;
  }

  try {
    return JSON.parse(normalized) as T;
  } catch {
    return fallback;
  }
}

/** 解析为数组（字符串/数组均可，失败返回 []） */
export function parseJsonArray<T = unknown>(value: unknown): T[] {
  if (Array.isArray(value)) {
    return value;
  }

  const parsed = parseJsonValue<unknown>(value, []);
  return Array.isArray(parsed) ? (parsed as T[]) : [];
}

/** 解析为普通对象（对象/字符串均可，失败返回 {}） */
export function parseJsonRecord<T extends Record<string, unknown> = Record<string, unknown>>(value: unknown): T {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as T;
  }

  const parsed = parseJsonValue<unknown>(value, {});
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    return parsed as T;
  }

  return {} as T;
}

/** step_cfg 字段数组解析（dynamic-form FormField[] schema） */
export function parseStepCfgFields(stepCfg: unknown): Array<Record<string, unknown>> {
  return parseJsonArray(stepCfg);
}

/** 执行参数解析：支持 [{paramName,paramValue}] 或 {params:[...]} 两种形态 */
export function parseExecParams(value: unknown): { params: Array<Record<string, unknown>> } {
  const parsed = parseJsonValue<unknown>(value, []);

  if (Array.isArray(parsed)) {
    return { params: parsed as Array<Record<string, unknown>> };
  }

  if (parsed && typeof parsed === 'object' && Array.isArray((parsed as { params?: unknown[] }).params)) {
    return { params: (parsed as { params: Array<Record<string, unknown>> }).params };
  }

  return { params: [] };
}