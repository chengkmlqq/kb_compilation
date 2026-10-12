#!/bin/bash
# 一次性重建 kb_compilation 业务容器，加载新镜像
cd /home/ctyun/kb_compilation/deploy
export REGISTRY=crpi-u265r07n4blchcqo.cn-shanghai.personal.cr.aliyuncs.com
export KB_IMAGE_TAG=latest
docker compose up -d --force-recreate \
  api-server web celery-worker celery-beat flower \
  celery-agent-worker celery-build-worker celery-orch-worker \
  celery-graph-worker mcp-server
echo "UP_EXIT=$?"