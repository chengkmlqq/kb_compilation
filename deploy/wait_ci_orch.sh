#!/bin/bash
# 等 c9e8cf2 CI: kb-agent-worker digest 变(当前运行 0dfe799b4)即 READY
R=crpi-u265r07n4blchcqo.cn-shanghai.personal.cr.aliyuncs.com/ck-registry
for i in $(seq 1 80); do
    S=$(curl -s 'https://api.github.com/repos/chengkmlqq/kb_compilation/actions/runs?per_page=6' 2>/dev/null | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    for r in d.get('workflow_runs',[]):
        if r['head_sha'].startswith('c9e8cf2'):
            print(r['status'], r['conclusion']); break
    else:
        print('notfound')
except: print('err')" 2>/dev/null)
    D=$(docker buildx imagetools inspect $R/kb-agent-worker:latest 2>/dev/null | grep -m1 "Digest:" | sed 's/.*sha256://' | cut -c1-16)
    echo "[$(date +%H:%M:%S)] CI=$S digest=$D"
    if echo "$S" | grep -q "completed success" && [ -n "$D" ] && [ "$D" != "0dfe799b47df2de4" ]; then
        echo "READY digest=$D"
        echo "CI_DONE"
        exit 0
    fi
    sleep 30
done
echo "CI_TIMEOUT"
exit 1