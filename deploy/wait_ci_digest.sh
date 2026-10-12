#!/bin/bash
# 等 CI 构建 kb-agent-worker 新镜像：digest 变了即 exit
R=crpi-u265r07n4blchcqo.cn-shanghai.personal.cr.aliyuncs.com/ck-registry
OLD=5c9918e64812c1a4229a
for i in $(seq 1 60); do
    D=$(docker buildx imagetools inspect $R/kb-agent-worker:latest 2>/dev/null | grep -m1 "Digest:" | sed 's/.*sha256://' | cut -c1-20)
    echo "[$(date +%H:%M:%S)] 第$i轮 digest=$D (旧=$OLD)"
    if [ -n "$D" ] && [ "$D" != "$OLD" ]; then
        echo "NEW_DIGEST=$D"
        echo "CI_DONE"
        exit 0
    fi
    sleep 30
done
echo "CI_TIMEOUT"
exit 1