#!/bin/bash

# ===================================================================
# 脚本名称: kb-update.sh (kb-compilation 部署任务管理器)
# 功能描述: 防重复运行、后台执行、提示日志查看命令
#   实际执行脚本: deploy/kb-build-deploy.sh（本地构建 + docker-compose 更新）
# 用法: ./kb-update.sh [-s <服务名>] [-n]
#   参数原样透传给 kb-build-deploy.sh
# ===================================================================

# 1. 基础配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SCRIPT="$SCRIPT_DIR/kb-build-deploy.sh"  # 实际执行部署的脚本路径
LOG_DIR="/home/jenkins/chengkai/build-logs"
LOG_FILE="$LOG_DIR/kb-deploy-$(date '+%Y-%m-%d').log"

# 确保日志目录存在
mkdir -p "$LOG_DIR"

# 2. 防重复运行判断（使用脚本名匹配）
SCRIPT_NAME=$(basename "$DEPLOY_SCRIPT")

echo "🔍 [调试信息] 正在检查脚本名称: $SCRIPT_NAME"

# 使用 grep -x 进行整行精确匹配，防止匹配到其他包含该名称的无关进程
RUNNING_PROCESSES=$(ps -ef | grep "$SCRIPT_NAME" | grep -v grep | grep -x ".*$SCRIPT_NAME.*")

if [ -n "$RUNNING_PROCESSES" ]; then
    echo "⚠️ [调试信息] 发现以下疑似重复的进程："
    echo "$RUNNING_PROCESSES"

    RUNNING_COUNT=$(echo "$RUNNING_PROCESSES" | grep -c .)
    echo "🔢 [调试信息] 匹配到的进程总数: $RUNNING_COUNT"

    echo "❌ 错误: 检测到部署任务 ($DEPLOY_SCRIPT) 正在后台运行中，请勿重复提交！"
    echo "   当前任务日志路径: $LOG_FILE"
    exit 1
else
    echo "✅ [调试信息] 未发现重复进程，检查通过。"
fi

# 3. 启动后台部署任务
echo "✅ 检查通过，正在将部署任务转入后台运行..."
echo "📂 日志文件路径: $LOG_FILE"

# 使用 nohup 调用实际的部署脚本，并透传所有参数 ("$@")
nohup "$DEPLOY_SCRIPT" "$@" > "$LOG_FILE" 2>&1 &

echo "🚀 部署任务已提交成功！"
echo "----------------------------------------"
echo "如需实时查看部署日志，请复制并执行以下命令："
echo "----------------------------------------"
echo "tail -f \"$LOG_FILE\""
echo "----------------------------------------"
echo "等待构建完成后可用以下命令查看结果："
echo "tail -50 \"$LOG_FILE\""