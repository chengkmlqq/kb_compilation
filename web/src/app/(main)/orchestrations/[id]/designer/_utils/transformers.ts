/**
 * 步骤配置转换器 —— 从 data-synth 编排设计器迁移（裁剪版）。
 *
 * data-synth 原实现里注册了 `sample_source` 等专有转换器（保存时把扁平表单值
 * 组装成运行用的嵌套结构、加载时反向展开），并依赖 sample-source-transformer.ts。
 * kb 迁移按裁剪约定去除样本源/数据集绑定等 data-synth 专有能力，
 * 因此注册表为空，读写均为恒等传递；保留此壳以便未来按 stepInst 挂载
 * 新的专用转换器（save/load 两方向）。
 */

/** 专用步骤配置转换器注册表：stepInst -> { save, load? } */
export const STEP_TRANSFORMERS: Record<
  string,
  {
    save?: (values: Record<string, unknown>) => Promise<Record<string, unknown>>;
    load?: (values: Record<string, unknown>) => Record<string, unknown>;
  }
> = {
  // 示例（未来扩展）：
  // 'sample_source': {
  //   save: async (values) => saveSampleSourceConfig(values),
  //   load: (values) => loadSampleSourceConfig(values),
  // },
};

/** 保存前转换配置（未注册转换器的步骤恒等返回） */
export async function transformForSave(
  stepInst: string,
  values: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const transformer = STEP_TRANSFORMERS[stepInst];
  if (transformer?.save) {
    return await transformer.save(values);
  }
  return values;
}

/** 加载时转换配置（未注册转换器的步骤恒等返回） */
export function transformForLoad(
  stepInst: string,
  values: Record<string, unknown>,
): Record<string, unknown> {
  if (!values) return {};
  const transformer = STEP_TRANSFORMERS[stepInst];
  if (transformer?.load) {
    return transformer.load(values);
  }
  return values;
}