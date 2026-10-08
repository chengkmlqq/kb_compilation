"use client";

/**
 * Popoto.js 知识图谱视图 —— Neo4j 可视化查询构建器。
 *
 * 与 Neo4jGraphView（自绘 SVG）并存：Popoto 提供"拖拽构建查询"的交互
 * （Graph 查询画布 + Taxonomy 分类 + Cypher 查看 + Result 结果图）。
 *
 * 数据通道：Popoto 4.x 的 runner 依赖 neo4j.js 驱动（浏览器无法直连
 * bolt），这里覆盖 popoto.runner.run/toObject 指向后端只读 Cypher 代理
 * POST /api/v1/cypher（返回 Neo4j HTTP transaction 兼容格式）。
 *
 * KB 隔离：runner 包装时对生成的语句注入 kb_id 过滤（WHERE n.kb_id=$kb），
 * 保证只看到本知识库的实体图谱。
 */
import { useEffect, useRef, useState } from "react";
import { Alert, Spin } from "antd";
import "popoto/dist/popoto.min.css";

interface PopotoGraphViewProps {
  kbId: string;
  height?: number;
}

/** 给 Popoto 生成的 Cypher 注入 kb_id 过滤（KB 隔离）。 */
function injectKbFilter(statement: string, kbId: string): { statement: string; parameters: Record<string, unknown> } {
  // 形如 "MATCH (n:Entity)" → "MATCH (n:Entity) WHERE n.kb_id = $kb_id"
  // 已带 WHERE 的查询改为追加 AND 条件（保守：仅在可安全插入时）。
  const params: Record<string, unknown> = { kb_id: kbId };
  let out = statement;
  let injected = false;

  // 逐个 MATCH 变量注入（单 label Entity 模型）
  out = out.replace(/MATCH\s+\(([a-zA-Z]\w*):Entity\)/g, (m0, varName) => {
    injected = true;
    return `${m0} WHERE ${varName}.kb_id = $kb_id`;
  });

  // 已存在 WHERE 的情况：在第一个 WHERE 后追加 AND（简单就近处理）
  if (!injected && /\bWHERE\b/i.test(out)) {
    out = out.replace(/\bWHERE\b/i, (m0) => `${m0} n.kb_id = $kb_id AND`);
  }
  return { statement: out, parameters: params };
}

export default function PopotoGraphView({ kbId, height = 640 }: PopotoGraphViewProps) {
  const graphRef = useRef<HTMLDivElement | null>(null);
  const taxonomyRef = useRef<HTMLDivElement | null>(null);
  const queryRef = useRef<HTMLDivElement | null>(null);
  const resultRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let disposed = false;
    let popoto: any = null;

    (async () => {
      try {
        const mod: any = await import("popoto");
        popoto = mod.default ?? mod;
        if (disposed) return;

        // ---- 覆盖 runner：走后端只读 Cypher 代理 ----
        popoto.runner.run = async (statements: { statements: Array<{ statement: string; parameters?: Record<string, unknown> }> }) => {
          const list = statements.statements || [];
          const out: any[] = [];
          for (const s of list) {
            const { statement, parameters } = injectKbFilter(s.statement, kbId);
            const resp = await fetch("/api/v1/cypher", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ statement, parameters: { ...parameters, ...(s.parameters || {}) } }),
            });
            const json = await resp.json();
            if (!resp.ok) {
              throw new Error(json?.detail || `cypher proxy ${resp.status}`);
            }
            // 每个 statement 一个结果槽（与 Popoto 多语句查询对应）
            if (json?.results?.length) {
              out.push(...json.results);
            } else {
              out.push({ columns: [], data: [] });
            }
          }
          return out;
        };

        popoto.runner.toObject = (results: any[]) =>
          (results || []).map((res) =>
            (res?.data || []).map((d: any) => {
              const obj: Record<string, unknown> = {};
              (res?.columns || []).forEach((c: string, i: number) => {
                obj[c] = d.row?.[i];
              });
              return obj;
            }),
          );

        // ---- 数据模型：Entity 节点 + RELATED_TO 关系 ----
        popoto.provider = popoto.provider || {};
        popoto.provider.node = {
          label: "Entity",
          display: ["name", "entity_type", "description"],
          root: { label: "Entity", display: ["name", "entity_type"], query: "MATCH (n:Entity) RETURN n LIMIT 1" },
          attributes: ["name", "entity_type", "description"],
          colors: {},
          layouts: {},
        };
        popoto.provider.relationship = {
          type: "RELATED_TO",
          source: "Entity",
          target: "Entity",
          attributes: ["type", "description", "strength"],
        };

        // ---- 挂载容器 ----
        popoto.init();
        popoto.graph(grabId(graphRef, "popoto-graph"), { rootNode: { label: "Entity" } });
        popoto.taxonomy(grabId(taxonomyRef, "popoto-taxonomy"));
        popoto.query(grabId(queryRef, "popoto-query"));
        popoto.result(grabId(resultRef, "popoto-result"));

        setReady(true);
      } catch (e) {
        setError(String(e).slice(0, 200));
      }
    })();

    return () => {
      disposed = true;
    };
  }, [kbId]);

  if (error) {
    return <Alert type="error" showIcon message="Popoto 图谱初始化失败" description={error} />;
  }

  return (
    <div style={{ minHeight: height }}>
      {!ready && <Spin style={{ display: "block", margin: "48px auto" }} />}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 260px", gap: 12, minHeight: height }}>
        <div>
          {/* 查询构建画布 + 结果图 */}
          <div ref={graphRef} id="popoto-graph" style={{ height: Math.round(height * 0.6) }} />
          <div ref={resultRef} id="popoto-result" style={{ marginTop: 12, height: Math.round(height * 0.35) }} />
          <div ref={queryRef} id="popoto-query" style={{ marginTop: 8 }} />
        </div>
        {/* 分类侧栏 */}
        <div ref={taxonomyRef} id="popoto-taxonomy" />
      </div>
    </div>
  );
}

function grabId(ref: React.RefObject<HTMLDivElement | null>, fallback: string): string {
  return ref.current?.id ?? fallback;
}