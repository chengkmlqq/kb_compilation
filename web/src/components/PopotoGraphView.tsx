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
import { useEffect, useRef, useState } from "react";
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
  // 2026-10-09 修复：技能写入的图谱节点没有 kb_id 属性（kb_id 编码在
  // label 里：ENTITY + kb_id 去横线；节点属性只有 name/attributes），
  // 旧注入 `n.kb_id = $kb_id` 永远为假 → 查询返回空 → 图谱页空白。
  // 新策略按 label 限制：无 label 的 MATCH 注入技能 label，有 label 的不注入。
  const kbLabel = "ENTITY" + kbId.replace(/-/g, "_");
  // 已精确限定本 KB 标签 → 不注入
  if (new RegExp(`MATCH\\s*\\(\\s*\\w+\\s*:\\s*${kbLabel}\\b`, "i").test(statement)) {
    return { statement, parameters: {} };
  }
  // 无 label 的 MATCH (n) → 注入技能 label：MATCH (n:ENTITY<kb>)
  if (!/MATCH\s*\(\s*\w+\s*:/.test(statement)) {
    const out = statement.replace(
      /MATCH\s*\(\s*([a-zA-Z]\w*)\s*\)/i,
      (_m, v) => `MATCH (${v}:${kbLabel})`,
    );
    return { statement: out, parameters: {} };
  }
  // 有其他 label（如平台 Entity）→ 不注入，避免破坏原生查询语义
  return { statement, parameters: {} };
}

/** Popoto 全局单例的已启动 KB（模块级，跨挂载周期存活）。
 *  重复 popoto.start() 会叠加 d3 force simulation（rAF 循环）→ CPU 打满卡死，
 *  故同一 kbId 重复挂载只复用、不再 start。 */
let popotoStartedFor: string | null = null;

/** 清空 Popoto 各容器：卸载后残留的 DOM 会让下次渲染错乱 */
function resetPopotoContainers() {
  for (const id of ["popoto-graph", "popoto-results", "popoto-query", "popoto-cypher", "popoto-taxonomy"]) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  }
}

/** 停掉 Popoto 的 d3 force simulation。
 *  dist 版未导出 popoto.stop()，但 graph 模块暴露了 `graph.force`（d3.forceSimulation），
 *  对其调用 stop() 即可停掉 rAF 循环，避免多实例/切 KB 时循环叠加打满 CPU。 */
function stopPopotoForce(popoto: any) {
  try {
    popoto?.graph?.force?.stop?.();
  } catch {
    /* 旧实例已释放，忽略 */
  }
}

function loadScript(src: string, timeoutMs = 15000): Promise<void> {
  return new Promise((resolve, reject) => {
    const g = globalThis as any;
    // 唯一就绪判据：全局对象已挂出（脚本早已执行完毕）
    if (g.popoto) {
      resolve();
      return;
    }
    // ⚠️ 已存在的 script 若执行失败/被拦截，load 事件不会重发 —— 对它 addEventListener
    // 会让 Promise 永久挂起（页面一直转圈＝「卡死」）。移除后重新加载。
    const stale = document.querySelector(`script[src="${src}"]`);
    if (stale) stale.remove();

    const el = document.createElement("script");
    el.src = src;
    const timer = setTimeout(() => {
      el.onload = null;
      el.onerror = null;
      reject(new Error(`load ${src} timeout`));
    }, timeoutMs);
    el.onload = () => {
      clearTimeout(timer);
      el.onload = null;
      el.onerror = null;
      if (g.popoto) resolve();
      else reject(new Error(`${src} 加载完成但未挂出 window.popoto`));
    };
    el.onerror = () => {
      clearTimeout(timer);
      el.onload = null;
      el.onerror = null;
      reject(new Error(`load ${src} failed`));
    };
    document.head.appendChild(el);
  });
}

export default function PopotoGraphView({ kbId, height = 640 }: PopotoGraphViewProps) {
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  // 所在页签可见性：antd Tabs 不销毁隐藏 pane，隐藏时暂停 d3 力模拟，避免后台空转。
  const rootRef = useRef<HTMLDivElement | null>(null);
  const visibleRef = useRef(true);

  // 可见性守卫：隐藏时停掉 force simulation，切回时只重启力模拟（节点/连线仍在）
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((ents) => {
      const vis = ents.some((e) => e.isIntersecting);
      visibleRef.current = vis;
      const popoto: any = (globalThis as any).popoto;
      const force = popoto?.graph?.force;
      if (!force) return; // 尚未 start()，由 init 完成后按 visibleRef 自行处理
      if (vis) {
        try {
          force.alpha(0.3).restart();
        } catch {
          /* ignore */
        }
      } else {
        stopPopotoForce(popoto);
      }
    });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    let disposed = false;
    let started = false;
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
        // 2026-10-09 修复：root label 必须是技能写入的 ENTITY<kb_id去横线>，
        // 不是平台 Entity（库里 Entity 标签节点数为 0，用 Entity 作 root 的查询全空）。
        const kbLabel = `ENTITY${kbId.replace(/-/g, "_")}`;
        const prov = popoto.provider;
        // dist 版 dist/popoto.min.js 未初始化 node.Provider（源码 src 有），兜底建对象
        prov.node.Provider = prov.node.Provider || {};
        prov.node.Provider[kbLabel] = {
          label: kbLabel,
          display: ["name", "entity_type", "description"],
          root: kbLabel,
          returnAttributes: ["name", "entity_type", "description"],
          displayAttribute: "name",
          constraintAttribute: "name",
          attributes: ["name", "entity_type", "description"],
          children: [],
          colors: {},
          layouts: {},
        };
        // 保留平台 Entity（若有）
        prov.node.Provider["Entity"] = prov.node.Provider["Entity"] || {
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
          source: kbLabel,
          target: kbLabel,
          attributes: ["type", "description", "strength"],
        };
        steps.push("provider ok: node=" + (prov.node ? "有" : "无"));

        // 节点文本显示：画布节点标签 = 实体 name（回退 entity_type/description/label）。
        // 2026-10-11 修复：旧实现 `prov.node.getTextValue = ...` 覆盖的是 popoto 内部
        // 派发函数本身（S.node.getTextValue → getProperty(label,"getTextValue")），
        // 且读取的是不存在的 node.properties → 恒为 undefined → 回退 node.id（popoto
        // 内部自增数字 id），于是画布节点全是数字和 id，长文本还把节点包围盒撑大。
        // 正确做法：把 getTextValue 注册到 label 级 provider，由 getProperty 优先命中；
        // 节点属性挂载在 node.attributes（Neo4j 属性），不是 node.properties。
        const readProps = (v: any): any => (v && typeof v === "object" ? v.attributes ?? v : v);
        const pickName = (p: any): string => {
          const flat = readProps(p);
          const v = flat?.name ?? flat?.entity_type ?? flat?.description;
          return v !== undefined && v !== null && String(v).trim() !== "" ? String(v) : "";
        };
        const readNodeText = (node: any): string => {
          // 主节点：属性值聚合在 node.value[]（每项 .attributes）
          if (Array.isArray(node?.value) && node.value.length) {
            const names = node.value.map((v: any) => pickName(v)).filter(Boolean);
            if (names.length) return names.join(" / ");
          }
          // 值节点/普通节点：属性在 node.attributes
          const own = pickName(node?.attributes) || pickName(node);
          if (own) return own;
          return String(node?.label ?? "未知实体");
        };
        prov.node.Provider[kbLabel].getTextValue = readNodeText;
        prov.node.Provider["Entity"].getTextValue = readNodeText;

        // 5) 挂载（start 自动检测 #popoto-graph/#popoto-taxonomy 等）
        // 容器先清空：卸载周期里 Popoto 渲染的 DOM 会残留，二次挂载时错乱
        resetPopotoContainers();
        if (popotoStartedFor !== kbId) {
          // 切换 KB：先停掉上一个 KB 的 d3 force simulation，避免多个 rAF 循环叠加
          // （global 单例 popoto 只支持一个实例，旧实例不停会一直空转 → CPU 打满）
          if (popotoStartedFor !== null) stopPopotoForce(popoto);
          popoto.start(kbLabel);
          popotoStartedFor = kbId;
          started = true;
          steps.push("start ok");
        } else {
          steps.push("start skipped（已启动，复用单例）");
        }
        setReady(true);
        // 若挂载时页签不可见，start() 后立即停掉力模拟，避免隐藏页签后台空转
        if (!visibleRef.current) stopPopotoForce(popoto);
      } catch (e) {
        const err = String(e instanceof Error ? e.stack || e.message : e);
        setError(`Popoto 初始化失败: ${err}\n${steps.join("\n")}`);
      }
    })();

    return () => {
      disposed = true;
      // 卸载：若本实例启动了 popoto，停掉其 force simulation 并复位单例标记，
      // 否则关闭页签后 rAF 仍在后台运行。
      if (started) {
        stopPopotoForce((globalThis as any).popoto);
        if (popotoStartedFor === kbId) popotoStartedFor = null;
      }
    };
  }, [kbId]);

  if (error) {
    return <Alert type="error" showIcon message="Popoto 图谱初始化失败" description={<pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{error}</pre>} />;
  }

  // 渲染结构对齐系统 UI：白底细边框分区面板 + 点阵画布。
  // 作用域类 kb-popoto-root 承载 globals.css 中的 popoto 亮色主题覆盖。
  return (
    <div ref={rootRef} className="kb-popoto-root">
      <link rel="stylesheet" href="/wiki/vendor/popoto.min.css" />
      {!ready && <Spin style={{ display: "block", margin: "40px auto" }} />}
      <div className="kb-popoto-grid">
        <div className="kb-popoto-main">
          <section className="kb-popoto-panel">
            <div className="kb-popoto-panel-title">关系图谱</div>
            <div id="popoto-graph" className="kb-popoto-graph" style={{ height: Math.round(height * 0.55) }} />
          </section>
          <section className="kb-popoto-panel">
            <div className="kb-popoto-panel-title">查询条件</div>
            <div id="popoto-query" className="kb-popoto-query" />
          </section>
          <section className="kb-popoto-panel">
            <div className="kb-popoto-panel-title">Cypher 语句</div>
            <div id="popoto-cypher" className="kb-popoto-cypher" />
          </section>
          <section className="kb-popoto-panel">
            <div className="kb-popoto-panel-title">查询结果</div>
            <div id="popoto-results" className="kb-popoto-results" />
          </section>
        </div>
        <aside className="kb-popoto-aside">
          <section className="kb-popoto-panel">
            <div className="kb-popoto-panel-title">实体分类</div>
            <div id="popoto-taxonomy" className="kb-popoto-taxonomy" />
          </section>
        </aside>
      </div>
    </div>
  );
}