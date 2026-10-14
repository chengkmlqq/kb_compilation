#!/bin/bash
# 等 kb_compilation CI 构建完成（kb-api-server / kb-web digest 变化即退出）
R=crpi-u265r07n4blchcqo.cn-shanghai.personal.cr.aliyuncs.com/ck-registry
get_d() { docker buildx imagetools inspect "$1" 2>/dev/null | grep -m1 "Digest:" | sed 's/.*sha256://' | cut -c1-20; }
APIO=$(get_d $R/kb-api-server:latest)
WEBO=$(get_d $R/kb-web:latest)
echo "[$(date +%H:%M:%S)] 旧 digest: api=$APIO web=$WEBO"
for i in $(seq 1 100); do
    API=$(get_d $R/kb-api-server:latest)
    WEB=$(get_d $R/kb-web:latest)
    API_CHG=""; WEB_CHG=""
    [ -n "$API" ] && [ "$API" != "$APIO" ] && API_CHG=" NEW_API"
    [ -n "$WEB" ] && [ "$WEB" != "$WEBO" ] && WEB_CHG=" NEW_WEB"
    echo "[$(date +%H:%M:%S)] 第${i}轮 api=${API}$API_CHG web=${WEB}$WEB_CHG"
    if [ -n "$API_CHG" ] && [ -n "$WEB_CHG" ]; then
        echo "CI_DONE 两个镜像均已更新"
        exit 0
    fi
    sleep 40
done
echo "CI_TIMEOUT"
exit 1