#!/bin/bash
cd /home/ctyun/kb_compilation/deploy
REGISTRY=crpi-u265r07n4blchcqo.cn-shanghai.personal.cr.aliyuncs.com \
KB_IMAGE_TAG=latest \
docker compose up -d celery-build-worker celery-agent-worker celery-worker celery-orch-worker celery-beat api-server web
echo "UP_EXIT=$?"