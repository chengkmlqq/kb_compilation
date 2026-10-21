"""为 seed_tape_data/*.json 中被压成 (0,0) / 无坐标的编排画布节点生成 DAG 分层坐标。

背景（2026-10-21）：20 个 wiki构建-* 编排的 nodes.position 全为 {x:0,y:0}（另有
3 个编排 position 为 null），打开设计器所有节点叠在左上角。坐标在种子 JSON 里
就是 0——导出脚本原样携带，非入库丢失。本脚本按 edges 拓扑分层重排：

  1. 拓扑分层：入度为 0 的节点为第 0 层，逐层推进（同层 = 不互为依赖）；
  2. 坐标：x = 层内序号 * (节点宽 240 + 水平间距 80)，y = 层号 * (节点高 ~72 + 垂直间距 100)；
  3. 仅处理「全部节点坐标为零/空」的磁带——已排好的（摘要/质量评估）不动；
  4. 幂等：重跑按当前 edges 重新计算，结果稳定。

节点高取设计器 custom-node 常见高度约 72（含标题+描述行），层内宽度按最大
节点数自适应：x 以「最宽层」为基准居中，避免布局偏出画布一侧。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "seed_tape_data"

NODE_W = 240  # custom-node.tsx width
NODE_H = 72
H_GAP = 80  # 水平间距
V_GAP = 100  # 垂直间距
TOP_PAD = 120  # 顶层留白（头部工具栏）
LEFT_PAD = 60
CHAIN_COLS = 4  # 链状折行每行节点数（画布宽 ~1300px 内放下 4 个 240px 节点）


def _dump(doc: dict) -> str:
    """序列化对齐 scripts/export_orchestrations_to_seed.py：indent=1 且无末尾换行。

    ⚠️ 必须与导出脚本完全一致，否则重写种子文件会引入全文件缩进漂移，
    污染 diff、且与 HEAD 版本（同为 indent=1）产生海量格式噪音。
    """
    return json.dumps(doc, ensure_ascii=False, indent=1)


def _is_flat(nodes: list[dict]) -> bool:
    """全部节点无有效坐标（零/空/缺失）→ 需要重排。"""
    if not nodes:
        return False
    for n in nodes:
        p = n.get("position") or {}
        if p.get("x") not in (0, None) or p.get("y") not in (0, None):
            # 任一节点有非零坐标 → 视为已排好，不动
            if p.get("x") not in (None,) or p.get("y") not in (None,):
                if p.get("x") or p.get("y"):
                    return False
    return True


def _layout(nodes: list[dict], edges: list[dict]) -> dict[str, dict]:
    """返回 {node_id: {x, y}}——按 edges 拓扑分层，层内从左到右排。"""
    ids = [str(n["id"]) for n in nodes]
    node_set = set(ids)
    adj: dict[str, list[str]] = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for e in edges:
        s, t = str(e.get("source") or ""), str(e.get("target") or "")
        if s in node_set and t in node_set and t not in adj[s]:
            adj[s].append(t)
            indeg[t] += 1
    q = deque([i for i in ids if indeg[i] == 0])
    layer = {i: 0 for i in ids}
    # 记录前驱的最大层号（防 DAG 中非链式分支）
    pred_layer = {i: -1 for i in ids}
    order: list[str] = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in adj[u]:
            pred_layer[v] = max(pred_layer[v], layer[u])
            indeg[v] -= 1
            if indeg[v] == 0:
                layer[v] = pred_layer[v] + 1
                q.append(v)
    # 环保护：若仍有未入序节点（环），按剩余顺序补层
    rest = [i for i in ids if i not in order]
    if rest:
        nxt = max(layer.values(), default=0) + 1
        for i in rest:
            layer[i] = nxt
    # 按层分组
    by_layer: dict[int, list[str]] = defaultdict(list)
    for i in ids:
        by_layer[layer[i]].append(i)
    # 每层内：保持节点原始相对顺序（而非拓扑序）→ 视觉稳定
    orig_order = {i: idx for idx, i in enumerate(ids)}
    for lv in by_layer:
        by_layer[lv].sort(key=lambda i: orig_order[i])
    max_per_layer = max(len(v) for v in by_layer.values())

    # 2026-10-21 链状折行：纯流水线（分支宽 ≤2）按每行 CHAIN_COLS 个节点蛇形
    # 排布，避免 18 节点单列 3000px 高（画布不可用）；分支图（如摘要/质量评估
    # 那种并行 fan-out）保持分层横排。
    if max_per_layer <= 2:
        flat: list[str] = []
        for lv in sorted(by_layer):
            # 层内若两个节点（并行分支），纵向排开更可读
            flat.extend(by_layer[lv])
        out = {}
        for idx, nid in enumerate(flat):
            row, col = divmod(idx, CHAIN_COLS)
            x = LEFT_PAD + col * (NODE_W + H_GAP)
            y = TOP_PAD + row * (NODE_H + V_GAP)
            out[nid] = {"x": round(x, 2), "y": round(y, 2)}
        return out

    width_total = max_per_layer * (NODE_W + H_GAP) - H_GAP
    out: dict[str, dict] = {}
    for lv, items in sorted(by_layer.items()):
        y = TOP_PAD + lv * (NODE_H + V_GAP)
        row_w = len(items) * (NODE_W + H_GAP) - H_GAP
        start_x = LEFT_PAD + (width_total - row_w) / 2
        for i, nid in enumerate(items):
            out[nid] = {"x": round(start_x + i * (NODE_W + H_GAP), 2), "y": round(y, 2)}
    return out


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="修复种子编排画布节点坐标（DAG 分层/链状折行）")
    parser.add_argument(
        "--force", action="store_true",
        help="强制重排所有文件（含已带坐标的）；默认仅重排全零/空坐标的",
    )
    args = parser.parse_args()

    fixed_files: list[str] = []
    skipped = []
    for fp in sorted(DATA_DIR.glob("*.json")):
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"  跳过(读取失败) {fp.name}: {e}")
            continue
        d = doc.get("draft") or doc
        nodes = d.get("nodes") or []
        edges = d.get("edges") or []
        if not nodes:
            continue
        if not args.force and not _is_flat(nodes):
            skipped.append(fp.name)
            continue
        pos = _layout(nodes, edges)
        for n in nodes:
            n["position"] = pos[n["id"]]
        fp.write_text(_dump(doc), encoding="utf-8")
        fixed_files.append(f"{fp.name}({len(nodes)}节点)")
    print(f"已修复 {len(fixed_files)} 个: {', '.join(fixed_files)}")
    if skipped:
        print(f"跳过(已有坐标): {', '.join(skipped)}")


if __name__ == "__main__":
    sys.exit(main())
