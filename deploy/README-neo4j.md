# kb_compilation Neo4j 知识图谱

实体/关系知识图谱存储。**独立实例 `kb-neo4j`**，与 WeKnora-neo4j 完全隔离
（不同 compose 项目、不同数据卷、不同密码、宿主端口 17474/17687 避开 WeKnora 的
7474/7687）。

## 架构与调用链

```
写入：worker KbGraphBuildTask
        → api/services/graph.py :: Neo4jGraphStore.write_graph (MERGE)
        → Neo4j  (:Entity{name,kb_id,entity_type,description,chunks}
                    -[:RELATED_TO{kb_id,type,description,strength}]-> :Entity)
读取：api/routers/graph.py（HTTP）
        → api/services/graph_query.py :: Neo4jGraphReader
        → 前端 web/src/components/Neo4jGraphView.tsx
```

关系语义类型（"由…制定"/"负责"等）存在**关系属性** `type`；Cypher 关系类型统一为
`RELATED_TO`（Neo4j 关系类型不可参数化），与 WeKnora 约定一致。

> 注：KB 页还有一套 **wiki 链接图**（`/kbs/{kb_id}/wiki/graph`，读 pg 的
> wiki_page+wiki_link，`WikiGraphView`）——那是页面链接图，与本 Neo4j 实体图谱
> 不同源、并存互补。

## 配置（deploy/.env，compose 已注入 api-server + celery-worker）

```dotenv
NEO4J_URI=bolt://neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=kb_neo4j_pass
NEO4J_DATABASE=neo4j
```

`KbGraphBuildTask` 读 env 兜底（task_params 里的 `neo4jUri/neo4jUser/...` 仍可覆盖，
用于测试/临时改指向）。Neo4j 未配置时建图任务照常抽取实体，只是不落图并记日志。

## 启动/验证

```bash
cd deploy
docker compose up -d neo4j
docker compose ps neo4j                       # 期望 healthy
docker compose exec neo4j cypher-shell -u neo4j -p kb_neo4j_pass 'RETURN 1'
# Browser: http://<host>:17474  (neo4j / kb_neo4j_pass)
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | /api/v1/graph/health | Neo4j 连通性（enabled/available/ok） |
| GET  | /api/v1/graph/engines | 图谱引擎信息 |
| GET  | /api/v1/kbs/{kb_id}/graph | 全库实体图（?limit=，默认 200） |
| GET  | /api/v1/kbs/{kb_id}/graph/stats | 规模统计（节点/关系/实体类型分布） |
| GET  | /api/v1/kbs/{kb_id}/graph/ego | 节点邻域下钻（?center=&depth=&limit=） |
| POST | /api/v1/kbs/{kb_id}/graph/search | 节点名搜索（body {q,limit}，返回诱导子图） |

均需登录（x-next-identity cookie）；数据按 kb_id 隔离。

## 建图触发

通过 Celery 任务 `KbGraphBuildTask`（调度框架的 cron 任务，表 `modo_cron_task`），
task_params 至少含 `kbId`（可选 `language`）。前端 KB 页的「Neo4j 知识图谱」卡片
在图为空时提示先运行该任务。

## 内存

本机与 WeKnora-neo4j 共存且内存紧张，compose 默认 heap 1G / pagecache 512M
（实测占用 ~1.45GiB）。调整：`.env` 设 `NEO4J_HEAP` / `NEO4J_PAGECACHE`。

## 已知修复（本次接入时）

`api/services/graph.py::Neo4jGraphStore._write_relationships` 原先把
description/strength 打包成 Map 写 `rel.attributes`，但 Neo4j 属性只接受标量/标量
数组，导致写入必报 `Neo.ClientError.Statement.TypeError`（建图 100% 失败）。已改为
摊平成独立属性 `rel.description` / `rel.strength`。