#!/bin/bash

# ===================================================================
# 脚本名称: kb-build-deploy.sh
# 功能描述: kb-compilation 本地构建更新脚本
#   - 拉取最新代码（可选跳过）
#   - 本地 docker build 构建业务镜像（不打标签到远端仓库，仅本地 KB_IMAGE_TAG）
#   - docker-compose up -d 更新服务，重启 nginx 刷新 upstream IP
#   - 健康检查与容器状态校验
# 适用环境: Linux (Docker + 独立 docker-compose 二进制，无 docker compose 插件)
# 用法: ./kb-build-deploy.sh [-s <服务名>] [-n]
#   -s|--service  指定更新服务，逗号分隔（默认全量 10 个业务服务）
#   -n|--no-pull  跳过 git pull（本地代码即最新时用）
# ===================================================================

# 开启严格模式：任何命令执行失败、使用未初始化变量或管道命令失败则立即退出
set -euo pipefail

# 1. 配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="$PROJECT_ROOT/deploy"
COMPOSE_FILE="docker-compose.yml"

# 内网 GitLab (10.19.32.157:2224) 需用 id_rsa_chengkai 才能拉代码
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/id_rsa_chengkai -o IdentitiesOnly=yes"

# 镜像仓库 host：从 compose 模板默认值提取（模板为 ${REGISTRY:-<host>}/ck-registry/kb-<svc>:${KB_IMAGE_TAG:-latest}）
# 只取 host，不带 /ck-registry 命名空间（compose 模板内硬编码，传错会拼成双命名空间）
REGISTRY="${REGISTRY:-$(grep -oP 'image: \$\{REGISTRY:-\K[^}]+' "$DEPLOY_DIR/$COMPOSE_FILE" | head -1)}"
# 本地构建 tag：默认 local，不覆盖 CI 的 latest 语义
KB_IMAGE_TAG="${KB_IMAGE_TAG:-local}"

# 服务 → 镜像 映射（10 个业务服务，4 个镜像）
declare -A SERVICE_IMAGE
SERVICE_IMAGE[web]="kb-web"
SERVICE_IMAGE[api-server]="kb-api-server"
SERVICE_IMAGE[mcp-server]="kb-api-server"
SERVICE_IMAGE[celery-worker]="kb-worker"
SERVICE_IMAGE[celery-graph-worker]="kb-worker"
SERVICE_IMAGE[celery-beat]="kb-worker"
SERVICE_IMAGE[flower]="kb-worker"
SERVICE_IMAGE[celery-agent-worker]="kb-agent-worker"
SERVICE_IMAGE[celery-build-worker]="kb-agent-worker"
SERVICE_IMAGE[celery-orch-worker]="kb-agent-worker"

# 镜像 → 构建上下文/方式（context 与 CI workflow build-images.yml 保持一致）
#   kb-web:         context=web，Dockerfile.web
#   kb-api-server:  context=仓库根，Dockerfile.api-server（api-server + mcp-server 共用）
#   kb-worker:      context=仓库根，Dockerfile.worker（worker/beat/flower/graph 共用）
#   kb-agent-worker:context=仓库根，Dockerfile.agent-worker（agent/build/orch 共用）

# 默认全量更新服务列表（顺序即 compose 服务名）
ALL_SERVICES=(web api-server mcp-server celery-worker celery-beat flower \
              celery-graph-worker celery-agent-worker celery-build-worker celery-orch-worker)

# 2. 解析命令行参数
SERVICE_LIST=""
DO_PULL=1
while [[ $# -gt 0 ]]; do
  case $1 in
    -s|--service)
      SERVICE_LIST=$(echo "$2" | tr ',' ' ')
      shift 2
      ;;
    -n|--no-pull)
      DO_PULL=0
      shift
      ;;
    *)
      echo "⚠️  未知参数: $1"
      echo "用法: $0 [-s <服务名>] [-n]"
      echo "  -s|--service  指定更新服务，逗号分隔（默认全量 10 个业务服务）"
      echo "  -n|--no-pull  跳过 git pull"
      exit 1
      ;;
  esac
done

echo "🚀 开始本地构建更新 kb-compilation 工程..."
echo "📍 当前时间: $(date '+%Y-%m-%d %H:%M:%S')"
if [ -n "$SERVICE_LIST" ]; then
    echo "🎯 指定更新服务: $SERVICE_LIST"
else
    echo "🎯 更新模式: 全量更新 (${#ALL_SERVICES[@]} 个业务服务)"
fi
echo "📦 镜像仓库: $REGISTRY  本地 tag: $KB_IMAGE_TAG"
echo "----------------------------------------"

# 3. (可选) 拉取最新代码
if [ "$DO_PULL" -eq 1 ]; then
    echo "📦 步骤 1: 拉取最新代码 (分支: $(cd "$PROJECT_ROOT" && git branch --show-current))"
    cd "$PROJECT_ROOT"
    if ! git pull 2>&1 | tail -5; then
        echo "❌ 错误: Git 拉取失败，请检查网络、SSH key（需 id_rsa_chengkai）或本地未提交改动"
        exit 1
    fi
else
    echo "📦 步骤 1: 按 -n 跳过 git pull"
fi

# 4. 构建镜像（按目标服务反查需要构建的镜像，去重）
echo "🛠️  步骤 2: 本地构建镜像"

# 计算本次要更新哪些服务
if [ -n "$SERVICE_LIST" ]; then
    TARGET_SERVICES=($SERVICE_LIST)
else
    TARGET_SERVICES=("${ALL_SERVICES[@]}")
fi

# 收集需要构建的镜像（去重）
NEED_IMAGES=""
for svc in "${TARGET_SERVICES[@]}"; do
    img="${SERVICE_IMAGE[$svc]:-}"
    if [ -z "$img" ]; then
        echo "❌ 错误: 未知服务名 '$svc'（可用: ${ALL_SERVICES[*]}）"
        exit 1
    fi
    case " $NEED_IMAGES " in
      *" $img "*) ;;
      *) NEED_IMAGES="$NEED_IMAGES $img" ;;
    esac
done

for img in $NEED_IMAGES; do
    FULL_TAG="$REGISTRY/ck-registry/$img:$KB_IMAGE_TAG"
    echo "   🔧 构建 $img → $FULL_TAG"
    case "$img" in
      kb-web)
        docker build -f "$DEPLOY_DIR/Dockerfile.web" \
                     -t "$FULL_TAG" "$PROJECT_ROOT/web"
        ;;
      kb-api-server)
        docker build -f "$DEPLOY_DIR/Dockerfile.api-server" \
                     -t "$FULL_TAG" "$PROJECT_ROOT"
        ;;
      kb-worker)
        docker build -f "$DEPLOY_DIR/Dockerfile.worker" \
                     -t "$FULL_TAG" "$PROJECT_ROOT"
        ;;
      kb-agent-worker)
        docker build -f "$DEPLOY_DIR/Dockerfile.agent-worker" \
                     -t "$FULL_TAG" "$PROJECT_ROOT"
        ;;
      *)
        echo "❌ 错误: 未配置构建方式: $img"
        exit 1
        ;;
    esac
    if [ $? -ne 0 ]; then
        echo "❌ 错误: 镜像 $img 构建失败"
        exit 1
    fi
done

# 5. 更新服务（本地已有指定 tag 镜像时 up -d 不会去远端拉取）
echo "🐳 步骤 3: docker-compose up -d 更新服务"
cd "$DEPLOY_DIR"
REGISTRY="$REGISTRY" KB_IMAGE_TAG="$KB_IMAGE_TAG" \
  docker-compose up -d "${TARGET_SERVICES[@]}"
if [ $? -ne 0 ]; then
    echo "❌ 错误: docker-compose up -d 失败"
    exit 1
fi

# 6. 重启 nginx（必须：nginx 启动时缓存 upstream IP，容器重建换 IP 后不刷新会 502）
echo "🔄 步骤 4: 重启 nginx 刷新 upstream IP"
docker-compose restart nginx

# 7. 检查容器状态
echo "----------------------------------------"
echo "🔍 步骤 5: 检查容器状态"
if docker-compose ps | grep -qE "Restarting|Exited"; then
    echo "⚠️  警告: 存在 Restarting/Exited 容器，部署可能未完全成功："
    docker-compose ps
else
    echo "✅ 全部容器状态正常"
fi

# 8. 健康检查
echo "----------------------------------------"
echo "🔍 步骤 6: 健康检查"
HEALTH=$(curl -fsS -m 10 http://127.0.0.1/api/v1/open/health 2>/dev/null || echo "FAILED")
echo "  /api/v1/open/health → $HEALTH"
if [[ "$HEALTH" != *'"success":true'* && "$HEALTH" != *"UP"* ]]; then
    echo "⚠️  警告: API 健康检查未通过，请用 docker-compose logs 排查"
fi

# 9. 收尾
echo "----------------------------------------"
echo "✅ 本地构建更新流程完成！"
echo "ℹ️  当前容器状态："
docker-compose ps
echo "----------------------------------------"
echo "🎉 kb-compilation 已更新并启动！"
echo "ℹ️  验证入口: http://127.0.0.1/ (nginx) → /api/v1/open/health (API)"