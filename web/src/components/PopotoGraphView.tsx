"use client";

/**
 * Popoto.js 知识图谱视图 —— Neo4j 可视化查询构建器。
 *
 * 与 Neo4jGraphView（自绘 SVG）并存：Popoto 提供"拖拽构建查询"的交互
 * （Graph 查询画布 + Taxonomy 分类 + Query/Cypher 查看 + Result 结果图）。
 *
 * 加载方式：popoto dist 是 UMD 且依赖**全局 d3**，ESM 源码 import 与 Next
 * 打包存在互操作问题（provider.node 丢失）。因此这里：
 *   1) 由 d3 npm 包挂 window.d3（bundler 打包）
 *   2) 动态插入 <script src="/vendor/popoto.min.js">（UMD 复用 window.d3）
 *   3) 使用全局 window.popoto
 *
 * 数据通道：覆盖 popoto.runner.run/toObject 指向后端只读 Cypher 代理
 * POST /api/v1/cypher（Popoto 4.x runner 默认依赖 neo4j.js 驱动直连 bolt，
 * 浏览器不可达）。
 *
 * KB 隔离：runner 包装时对生成的语句注入 kb_id 过滤（WHERE n.kb_id=$kb）。
 */
import { useEffect, useState } from "react";
import { Alert, Spin } from "antd";

interface PopotoGraphViewProps {
  kbId: string;
  height?: number;
}

/** 给 Popoto 生成的 Cypher 注入 kb_id 过滤（KB 隔离）。

 * 2026-10-08 bug fix: 旧实现把 WHERE 插在 MATCH 之后 —— Popoto 的查询形如
 * MATCH (entity:`Entity`)-[r]->(x) RETURN ...（单条 MATCH 带完整 pattern），
 * 插入后变成 MATCH (...) WHERE ..., (..)-[r]->(x) —— Cypher 语法错误 → 500。
 * 正确做法: 提取首个 MATCH 变量, 有 WHERE 则前插, 否则在 RETURN 前插。
 */
function injectKbFilter(statement: string, kbId: string): { statement: string; parameters: Record<string, unknown> } {
  const params: Record<string, unknown> = { kb_id: kbId };
  const m = statement.match(/MATCH\s*\(([a-zA-Z]\w*):/);
  const varName = m ? m[1] : "n";
  let out = statement;
  if (/\bWHERE\b/i.test(out)) {
    out = out.replace(/\bWHERE\b/i, `WHERE ${varName}.kb_id = $kb_id AND `);
  } else if (/\bRETURN\b/i.test(out)) {
    out = out.replace(/\bRETURN\b/i, `WHERE ${varName}.kb_id = $kb_id RETURN `);
  }
  return { statement: out, parameters: params };
}

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      if ((window as any).popoto) resolve();
      else existing.addEventListener("load", () => resolve());
      return;
    }
    const el = document.createElement("script");
    el.src = src;
    el.onload = () => resolve();
    el.onerror = () => reject(new Error(`load ${src} failed`));
    document.head.appendChild(el);
  });
}

export default function PopotoGraphView({ kbId, height = 640 }: PopotoGraphViewProps) {
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let disposed = false;
    const steps: string[] = [];

    (async () => {
      try {
        // 1) 全局 d3（popoto.min.js UMD 依赖 window.d3）
        const d3mod: any = await import("d3");
        const g: any = globalThis as any;
        if (!g.d3) g.d3 = d3mod.default ?? d3mod;
        steps.push("d3 ok");

        // 2) 加载 popoto UMD → window.popoto（basePath=/wiki 前缀，public/ 静态资源）
        await loadScript("/wiki/vendor/popoto.min.js");
        const popoto: any = (globalThis as any).popoto;
        if (!popoto) throw new Error("window.popoto 未就绪");
        steps.push("popoto ok: " + typeof popoto.start);

        if (disposed) return;

        // 3) runner 覆盖 → 后端只读 Cypher 代理
        popoto.runner.run = async (statements: { statements: Array<{ statement: string; parameters?: Record<string, unknown> }> }) => {
          const out: any[] = [];
          for (const s of statements.statements || []) {
            const { statement, parameters } = injectKbFilter(s.statement, kbId);
            const resp = await fetch("/api/v1/cypher", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ statement, parameters: { ...parameters, ...(s.parameters || {}) } }),
            });
            const json = await resp.json();
            if (!resp.ok) throw new Error(json?.detail || `cypher proxy ${resp.status}`);
            const resultsArr = json?.results?.length ? json.results : [{ columns: [], data: [] }];
            // 2026-10-08 fix(Taxonomy 计数): Popoto updateCount 用驱动风格
            // records[0].get("count")——给每个结果附加 records 兼容对象
            for (const res of resultsArr) {
              (res as any).records = ((res.data || []) as any[]).map((d: any) => ({
                get: (col: string) => {
                  const i = (res.columns || []).indexOf(col);
                  return i >= 0 ? d.row?.[i] : undefined;
                },
                toObject: () => {
                  const obj: Record<string, unknown> = {};
                  (res.columns || []).forEach((c: string, i: number) => {
                    obj[c] = d.row?.[i];
                  });
                  return obj;
                },
              }));
            }
            out.push(...resultsArr);
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
        steps.push("runner ok");

        // 4) 数据模型：label provider
        const prov = popoto.provider;
        // dist 版 dist/popoto.min.js 未初始化 node.Provider（源码 src 有），兜底建对象
        prov.node.Provider = prov.node.Provider || {};
        prov.node.Provider["Entity"] = {
          label: "Entity",
          display: ["name", "entity_type", "description"],
          root: "Entity",
          returnAttributes: ["name", "entity_type", "description"],
          displayAttribute: "name",
          constraintAttribute: "name",
          attributes: ["name", "entity_type", "description"],
          children: [],
          colors: {},
          layouts: {},
        };
        prov.relationship = prov.relationship || {};
        prov.relationship.Provider = prov.relationship.Provider || {};
        prov.relationship.Provider["RELATED_TO"] = {
          type: "RELATED_TO",
          source: "Entity",
          target: "Entity",
          attributes: ["type", "description", "strength"],
        };
        steps.push("provider ok: node=" + (prov.node ? "有" : "无"));

        // 节点文本显示：默认显示 Neo4j internal ID，改为实体 name（回退 entity_type）
        prov.node.getTextValue = (node: any) => {
          const p = node?.properties || node || {};
          return String(p.name ?? p.entity_type ?? p.id ?? "未知实体");
        };
        prov.node.getTooltipText = (node: any) => {
          const p = node?.properties || node || {};
          const bits = [p.name, p.entity_type ? `类型: ${p.entity_type}` : null, p.description].filter(Boolean);
          return bits.join(" · ");
        };

        // 5) 挂载（start 自动检测 #popoto-graph/#popoto-taxonomy 等）
        popoto.start("Entity");
        steps.push("start ok");
        setReady(true);
      } catch (e) {
        const err = String(e instanceof Error ? e.stack || e.message : e);
        setError(`Popoto 初始化失败: ${err}\n${steps.join("\n")}`);
      }
    })();

    return () => {
      disposed = true;
    };
  }, [kbId]);

  if (error) {
    return <Alert type="error" showIcon message="Popoto 图谱初始化失败" description={<pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{error}</pre>} />;
  }

  return (
    <div style={{ minHeight: height }}>
      {!ready && <Spin style={{ display: "block", margin: "48px auto" }} />}
      <link rel="stylesheet" href="/wiki/vendor/popoto.min.css" />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 260px", gap: 12, minHeight: height }}>
        <div>
          <div id="popoto-graph" style={{ height: Math.round(height * 0.55) }} />
          <div id="popoto-results" style={{ marginTop: 12, minHeight: 120 }} />
          <div id="popoto-query" style={{ marginTop: 8 }} />
          <div id="popoto-cypher" style={{ marginTop: 8 }} />
        </div>
        <div id="popoto-taxonomy" />
      </div>
    </div>
  );
}