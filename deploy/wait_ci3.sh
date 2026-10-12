#!/bin/bash
# 等 a4922ad 的 CI 完成 + digest 再变
R=crpi-u265r07n4blchcqo.cn-shanghai.personal.cr.aliyuncs.com/ck-registry
for i in $(seq 1 80); do
    # CI 状态
    S=$(curl -s 'https://api.github.com/repos/chengkmlqq/kb_compilation/actions/runs?per_page=6' 2>/dev/null | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    for r in d.get('workflow_runs',[]):
        if r['head_sha'].startswith('a4922ad'):
            print(r['status'], r['conclusion']); break
    else:
        print('notfound')
except: print('err')" 2>/dev/null)
    D=$(docker buildx imagetools inspect $R/kb-agent-worker:latest 2>/dev/null | grep -m1 "Digest:" | sed 's/.*sha256://' | cut -c1-16)
    echo "[$(date +%H:%M:%S)] CI=$S digest=$D"
    if echo "$S" | grep -q "completed success" && [ "$D" != "7e957492eac3cf87" ] && [ -n "$D" ]; then
        echo "READY digest=$D"
        echo "CI_DONE"
        exit 0
    fi
    sleep 30
done
echo "CI_TIMEOUT"
exit 1