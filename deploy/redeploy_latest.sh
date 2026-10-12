#!/bin/bash
set -x
cd /home/ctyun/kb_compilation/deploy
REGISTRY=crpi-u265r07n4blchcqo.cn-shanghai.personal.cr.aliyuncs.com
KB_IMAGE_TAG=latest docker compose up -d \
  celery-build-worker celery-agent-worker celery-worker \
  celery-beat api-server web 2>&1
echo "EXIT=$?"