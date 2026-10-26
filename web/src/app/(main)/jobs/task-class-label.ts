// 任务类编码 -> 中文展示（任务监控页：作业列表/父任务表/作业日志抽屉共用）

const TASK_CLASS_LABEL: Record<string, string> = {
  KbDocumentProcessTask: "文档处理",
  KbDocumentEmbedTask: "文档向量化",
  KbSkillDirectBuildTask: "技能直跑构建",
  KbAgentWikiBuildTask: "智能体 Wiki 构建",
  KbSkillWikiBuildTask: "技能 Wiki 构建",
  KbWikiBuildTask: "Wiki 构建",
  KbGraphBuildTask: "图谱构建",
  KbOrphanRecoveryTask: "孤儿任务恢复",
  KbAgentGatewayTask: "智能体网关",
  KbChunkVerifyTask: "切片校验",
  KbLogCleanupTask: "日志清理",
  KbTableIngestTask: "表摄取",
  KbMetadataCollectionTask: "元数据采集",
  KbOrchestrationRunTask: "编排执行",
  OrchestrationRun: "编排执行",
  KbBuildRetriggerTask: "构建失败补位自愈",
};

export function taskClassLabel(v?: string | null): string {
  if (!v) return "-";
  return TASK_CLASS_LABEL[v] ?? v.replace("Kb", "").replace("Task", "");
}