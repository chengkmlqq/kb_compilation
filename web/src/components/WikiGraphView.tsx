"use client";

/**
 * Wiki 知识图谱视图 —— SVG 力导向图（零第三方依赖，与 WeKnora 同方案）。
 *
 * 对齐 WeKnora（2026-10）：
 * - 12 类图例动态渲染（仅显示实际存在的类型，服务端过滤）
 * - 有向边 + 箭头显示开关
 * - 适应屏幕（fit to view）
 * - ego frontier 批量生长（并发 6，merge + LRU 淘汰）
 * - bloom 开花邻域（逐代 LRU，上限 BLOOM_MAX_NODES）
 * - 远程搜索（防抖 + 序号防乱序，空关键词回退 overview 快照）
 * - 状态卡（节点/边/全库总数/截断提示）
 * - 操作帮助弹窗 · 节点拖拽固定（修复拖拽后误开详情）
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Card,
  Drawer,
  Empty,
  Popover,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  AppstoreOutlined,
  ExpandOutlined,
  ExportOutlined,
  FullscreenOutlined,
  QuestionCircleOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import {
  apiWikiGraph,
  apiWikiPage,
  apiWikiSearch,
  WikiGraphData,
  WikiGraphEdge,
  WikiGraphNode,
  WikiSearchItem,
} from "@/lib/api";
import { useRouter } from "next/navigation";

const { Text } = Typography;

// 12 类对齐 WeKnora
const PAGE_TYPE_COLORS: Record<string, string> = {
  summary: "#0052d9",
  entity: "#2ba471",
  concept: "#e37318",
  synthesis: "#0594fa",
  comparison: "#d54941",
  business_ontology: "#7c3aed",
  rule_ontology: "#a855f7",
  original_sentence: "#f59e0b",
  frequent_keyword: "#10b981",
  topic_cluster: "#6366f1",
  knowledge_graph_summary: "#8b5cf6",
  cross_document_insight: "#ec4899",
};

const PAGE_TYPE_LABELS: Record<string, string> = {
  summary: "摘要",
  entity: "实体",
  concept: "概念",
  synthesis: "综合",
  comparison: "对比",
  business_ontology: "业务本体",
  rule_ontology: "规则本体",
  original_sentence: "原句",
  frequent_keyword: "高频关键词",
  topic_cluster: "主题簇",
  knowledge_graph_summary: "图谱摘要",
  cross_document_insight: "跨文档洞察",
};

const TYPE_ORDER = Object.keys(PAGE_TYPE_COLORS);

const GRAPH_OVERVIEW_LIMIT = 500;
const GRAPH_EGO_LIMIT = 500;
const BLOOM_MAX_NODES = 1500;
const GROW_FRONTIER_CONCURRENCY = 6;
// 全库索引类页面（index/log）：连线指向全库，参与单点扩展但不参与 frontier 批量生长
const GRAPH_SYSTEM_PAGE_TYPES = new Set(["index", "log"]);

interface Pt {
  x: number;
  y: number;
  vx: number;
  vy: number;
  pinned: boolean;
}

interface Props {
  kbId: string;
  focusSlug?: string; // 从 wiki 页跳入时定位
}

export default function WikiGraphView({ kbId, focusSlug }: Props) {
  const router = useRouter();
  const svgRef = useRef<SVGSVGElement | null>(null);

  const [data, setData] = useState<WikiGraphData | null>(null);
  const [loading, setLoading] = useState(false);
  // 空 Set = 全选（后端 types 空数组 = 全集）
  const [filterTypes, setFilterTypes] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"overview" | "ego">("overview");
  const [center, setCenter] = useState<string>("");
  const [selected, setSelected] = useState<WikiGraphNode | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailHtml, setDetailHtml] = useState("");
  const [arrowsVisible, setArrowsVisible] = useState(true);
  const [searchOptions, setSearchOptions] = useState<{ value: string; label: string }[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);

  const dataRef = useRef<WikiGraphData | null>(null);
  dataRef.current = data;
  const searchSeq = useRef(0);
  const searchTimer = useRef<number | null>(null);
  const bloomGen = useRef<Map<string, number>>(new Map());
  const bloomCurrentGen = useRef(0);

  // 图数据内部状态（节点坐标等）
  const stateRef = useRef<{
    nodes: Map<string, Pt>;
    hover: string;
    dragged: boolean;
    viewX: number;
    viewY: number;
    scale: number;
    raf: number;
  }>({
    nodes: new Map(),
    hover: "",
    dragged: false,
    viewX: 0,
    viewY: 0,
    scale: 1,
    raf: 0,
  });

  const load = useCallback(
    async (m: "overview" | "ego", c: string) => {
      setLoading(true);
      try {
        const res = await apiWikiGraph(kbId, {
          mode: m,
          center: c || undefined,
          depth: 1,
          limit: m === "overview" ? GRAPH_OVERVIEW_LIMIT : GRAPH_EGO_LIMIT,
          types: Array.from(filterTypes),
        });
        if (res.success && res.data) {
          setData(res.data);
          bloomGen.current.clear();
          bloomCurrentGen.current = 0;
          for (const n of res.data.nodes) bloomGen.current.set(n.slug, 0);
          if (m === "overview") {
            // 空关键词下拉回退：overview 快照 top-100（按链接数）
            const top = [...res.data.nodes]
              .sort((a, b) => (b.link_count || 0) - (a.link_count || 0))
              .slice(0, 100);
            setSearchOptions(top.map((n) => ({ value: n.slug, label: n.title })));
          }
        }
      } finally {
        setLoading(false);
      }
    },
    [kbId, filterTypes],
  );

  useEffect(() => {
    void load(mode, center);
  }, [load, mode, center]);

  // 初始 focus
  useEffect(() => {
    if (focusSlug) {
      setCenter(focusSlug);
      setMode("ego");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusSlug]);

  const toggleType = (t: string) => {
    setFilterTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  };

  const toOverview = () => {
    setMode("overview");
    setCenter("");
  };

  const selectNode = useCallback(
    async (slug: string) => {
      const node = dataRef.current?.nodes.find((n) => n.slug === slug);
      if (!node) return;
      const res = await apiWikiPage(kbId, slug);
      if (res.success && res.data) {
        setSelected(node);
        setDetailHtml(res.data.content || "");
        setDetailOpen(true);
      }
    },
    [kbId],
  );

  const focusEgo = useCallback((slug: string) => {
    setCenter(slug);
    setMode("ego");
    setDetailOpen(false);
    setSelected(null);
  }, []);

  const goWikiPage = useCallback(
    (slug: string) => {
      router.push(`/kbs/${kbId}/wiki/${slug}`);
    },
    [kbId, router],
  );

  // ── 远程搜索（防抖 + 序号防乱序，对齐 WeKnora）──
  const handleGraphRemoteSearch = (keyword: string) => {
    const q = (keyword || "").trim();
    if (searchTimer.current) {
      clearTimeout(searchTimer.current);
      searchTimer.current = null;
    }
    if (!q) {
      searchSeq.current += 1;
      setSearchOptions([]); // 空关键词回退到 overview 快照（computed 层）
      setSearchLoading(false);
      return;
    }
    setSearchLoading(true);
    const seq = ++searchSeq.current;
    searchTimer.current = window.setTimeout(async () => {
      try {
        const res = await apiWikiSearch(kbId, q, 20);
        if (!res.success) return;
        if (seq !== searchSeq.current) return;
        const pages: WikiSearchItem[] = res.data?.items || [];
        setSearchOptions(pages.map((p) => ({ value: p.slug, label: p.title })));
      } catch (e) {
        console.error("wiki search failed:", e);
        if (seq === searchSeq.current) setSearchOptions([]);
      } finally {
        if (seq === searchSeq.current) setSearchLoading(false);
      }
    }, 250);
  };

  const handleGraphSearchSelect = (slug: string) => {
    const cur = dataRef.current;
    if (cur && cur.nodes.some((n) => n.slug === slug)) {
      void selectNode(slug); // 已在图中 → 打开详情
    } else {
      setCenter(slug); // 不在图中 → ego 下钻定位
      setMode("ego");
      setDetailOpen(false);
      setSelected(null);
    }
  };

  // ── bloom / frontier 数据合并与 LRU 淘汰（对齐 WeKnora）──
  const mergeData = (base: WikiGraphData, incoming: WikiGraphData, gen: number): WikiGraphData => {
    const nodeBySlug = new Map<string, WikiGraphNode>();
    for (const n of base.nodes) nodeBySlug.set(n.slug, n);
    for (const n of incoming.nodes) {
      if (!nodeBySlug.has(n.slug)) {
        nodeBySlug.set(n.slug, n);
        bloomGen.current.set(n.slug, gen);
      }
    }
    const edgeKey = (e: WikiGraphEdge) => `${e.source}\u2192${e.target}`;
    const seen = new Set<string>();
    const edges: WikiGraphEdge[] = [];
    for (const e of base.edges) {
      const k = edgeKey(e);
      if (!seen.has(k)) {
        seen.add(k);
        edges.push(e);
      }
    }
    for (const e of incoming.edges) {
      const k = edgeKey(e);
      if (!seen.has(k)) {
        seen.add(k);
        edges.push(e);
      }
    }
    return {
      nodes: Array.from(nodeBySlug.values()),
      edges,
      meta: { ...incoming.meta, returned: nodeBySlug.size },
    };
  };

  const evictOverflow = (d: WikiGraphData, protect: Set<string>) => {
    if (d.nodes.length <= BLOOM_MAX_NODES) return;
    const byGen = new Map<number, string[]>();
    for (const n of d.nodes) {
      const g = bloomGen.current.get(n.slug) ?? 0;
      if (g === 0 || protect.has(n.slug)) continue;
      if (!byGen.has(g)) byGen.set(g, []);
      byGen.get(g)!.push(n.slug);
    }
    const gens = Array.from(byGen.keys()).sort((a, b) => a - b);
    const toRemove = new Set<string>();
    let remaining = d.nodes.length;
    for (const g of gens) {
      if (remaining <= BLOOM_MAX_NODES) break;
      for (const slug of byGen.get(g)!) {
        if (remaining <= BLOOM_MAX_NODES) break;
        toRemove.add(slug);
        remaining -= 1;
      }
    }
    if (toRemove.size === 0) return;
    d.nodes = d.nodes.filter((n) => !toRemove.has(n.slug));
    d.edges = d.edges.filter((e) => !toRemove.has(e.source) && !toRemove.has(e.target));
    for (const slug of toRemove) bloomGen.current.delete(slug);
  };

  const loadBloomNeighbors = useCallback(
    async (anchor: string) => {
      const cur = dataRef.current;
      if (!cur) return;
      if (mode !== "ego") {
        await focusEgo(anchor); // 非 ego → 先整体切到该节点邻域
        return;
      }
      setLoading(true);
      try {
        const res = await apiWikiGraph(kbId, {
          mode: "ego",
          center: anchor,
          depth: 1,
          limit: GRAPH_EGO_LIMIT,
          types: Array.from(filterTypes),
        });
        if (!res.success || !res.data) return;
        bloomCurrentGen.current += 1;
        const gen = bloomCurrentGen.current;
        const merged = mergeData(cur, res.data, gen);
        const protect = new Set<string>(
          [center, anchor, selected?.slug || ""].filter(Boolean),
        );
        evictOverflow(merged, protect);
        setData(merged);
      } finally {
        setLoading(false);
      }
    },
    [kbId, mode, center, filterTypes, selected?.slug, focusEgo],
  );

  const growFrontier = useCallback(async () => {
    const cur = dataRef.current;
    if (!cur || mode !== "ego") return; // frontier 生长只在 ego 布局有效
    // 收集前沿节点：可见度 < 全库链接数，且非 ego 中心、非 Index/Log 超节点
    const visibleDegree = new Map<string, number>();
    for (const e of cur.edges) {
      visibleDegree.set(e.source, (visibleDegree.get(e.source) || 0) + 1);
      visibleDegree.set(e.target, (visibleDegree.get(e.target) || 0) + 1);
    }
    const frontier: string[] = [];
    for (const n of cur.nodes) {
      if (n.slug === center) continue;
      if (GRAPH_SYSTEM_PAGE_TYPES.has(n.page_type)) continue;
      if ((n.link_count || 0) > (visibleDegree.get(n.slug) || 0)) frontier.push(n.slug);
    }
    if (frontier.length === 0) return;
    setLoading(true);
    try {
      const responses: WikiGraphData[] = [];
      let cursor = 0;
      async function worker() {
        while (cursor < frontier.length) {
          const idx = cursor++;
          try {
            const res = await apiWikiGraph(kbId, {
              mode: "ego",
              center: frontier[idx],
              depth: 1,
              limit: GRAPH_EGO_LIMIT,
              types: Array.from(filterTypes),
            });
            if (res.success && res.data) responses.push(res.data);
          } catch (e) {
            console.error("growFrontier failed:", e);
          }
        }
      }
      const workers: Promise<void>[] = [];
      const count = Math.min(GROW_FRONTIER_CONCURRENCY, frontier.length);
      for (let i = 0; i < count; i++) workers.push(worker());
      await Promise.all(workers);
      if (responses.length === 0) return;
      bloomCurrentGen.current += 1;
      const gen = bloomCurrentGen.current;
      let merged = dataRef.current!;
      for (const incoming of responses) merged = mergeData(merged, incoming, gen);
      const protect = new Set<string>([center, selected?.slug || ""].filter(Boolean));
      evictOverflow(merged, protect);
      setData(merged);
    } finally {
      setLoading(false);
    }
  }, [kbId, mode, center, filterTypes, selected?.slug]);

  // ── 适应屏幕 ──
  const fitToView = useCallback(() => {
    const svg = svgRef.current;
    const st = stateRef.current;
    if (!svg || st.nodes.size === 0) return;
    const W = svg.clientWidth || 800;
    const H = svg.clientHeight || 600;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const p of st.nodes.values()) {
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x);
      maxY = Math.max(maxY, p.y);
    }
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    const pad = 80;
    const bw = Math.max(maxX - minX, 120) + pad * 2;
    const bh = Math.max(maxY - minY, 120) + pad * 2;
    const s = Math.min(W / bw, H / bh, 1.5);
    st.scale = Math.max(0.3, s);
    st.viewX = W / 2 - cx * st.scale;
    st.viewY = H / 2 - cy * st.scale;
  }, []);

  // ── 图例动态渲染：仅显示实际存在的类型 ──
  const presentTypes = useMemo(() => {
    const set = new Set<string>();
    for (const n of data?.nodes || []) set.add(n.page_type);
    return TYPE_ORDER.filter((t) => set.has(t));
  }, [data]);

  const isTypeOn = (t: string) => (filterTypes.size === 0 ? true : filterTypes.has(t));

  // ---- SVG 渲染（力导向模拟）----
  useEffect(() => {
    const svg = svgRef.current;
    const st = stateRef.current;
    if (!svg) return;
    svg.innerHTML = "";
    const W = svg.clientWidth || 800;
    const H = svg.clientHeight || 600;
    if (!data) return;
    const nodeList = data.nodes;
    const ns = "http://www.w3.org/2000/svg";

    // 布点：数据收缩删除多余；全新 → 环形；增量（bloom/frontier merge）→ 只给新节点布点
    const inData = new Set(nodeList.map((n) => n.slug));
    for (const slug of Array.from(st.nodes.keys())) {
      if (!inData.has(slug)) st.nodes.delete(slug);
    }
    if (st.nodes.size === 0) {
      nodeList.forEach((n, i) => {
        const ang = (2 * Math.PI * i) / nodeList.length;
        st.nodes.set(n.slug, {
          x: W / 2 + Math.cos(ang) * (Math.min(W, H) / 2 - 40),
          y: H / 2 + Math.sin(ang) * (Math.min(W, H) / 2 - 40),
          vx: 0,
          vy: 0,
          pinned: false,
        });
      });
    } else if (st.nodes.size < nodeList.length) {
      const fresh = nodeList.filter((n) => !st.nodes.has(n.slug));
      const r = Math.min(W, H) / 2.4;
      fresh.forEach((n, i) => {
        const ang = (2 * Math.PI * i) / Math.max(fresh.length, 1) + 0.7;
        st.nodes.set(n.slug, {
          x: W / 2 + Math.cos(ang) * r,
          y: H / 2 + Math.sin(ang) * r,
          vx: 0,
          vy: 0,
          pinned: false,
        });
      });
    }
    const radiusOf = new Map<string, number>(
      nodeList.map((n) => [n.slug, 14 + Math.min(10, n.link_count || 0) * 1.2]),
    );

    // 力导向模拟：斥力 + 弹簧
    const repulsion = 900;
    const spring = 0.008;
    const restLen = 90;

    const simulate = () => {
      const nodes = st.nodes;
      const arr = Array.from(nodes.values());
      for (const a of arr) {
        if (a.pinned) continue;
        a.vx *= 0.85;
        a.vy *= 0.85;
        for (const b of arr) {
          if (a === b) continue;
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const d2 = dx * dx + dy * dy + 1;
          const f = repulsion / d2;
          a.vx += (dx / Math.sqrt(d2)) * f * 0.1;
          a.vy += (dy / Math.sqrt(d2)) * f * 0.1;
        }
        for (const e of data.edges) {
          const ta = nodes.get(e.source);
          const tb = nodes.get(e.target);
          if (!ta || !tb || ta === a || tb === a) continue;
          const dx = tb.x - a.x;
          const dy = tb.y - a.y;
          const d = Math.sqrt(dx * dx + dy * dy) + 1;
          const f = (d - restLen) * spring;
          a.vx += (dx / d) * f;
          a.vy += (dy / d) * f;
        }
        a.x += a.vx;
        a.y += a.vy;
        a.x = Math.max(20, Math.min(2000, a.x));
        a.y = Math.max(20, Math.min(2000, a.y));
      }
      render();
      st.raf = requestAnimationFrame(simulate);
    };

    const render = () => {
      const container = svg;
      container.innerHTML = "";
      const g = document.createElementNS(ns, "g");
      g.setAttribute("transform", `translate(${st.viewX},${st.viewY}) scale(${st.scale})`);
      container.appendChild(g);

      // edges（line + 方向箭头）
      const drawArrow = arrowsVisible && st.scale > 0.55;
      for (const e of data.edges) {
        const a = st.nodes.get(e.source);
        const b = st.nodes.get(e.target);
        if (!a || !b) continue;
        const hovered = st.hover && (e.source === st.hover || e.target === st.hover);
        const line = document.createElementNS(ns, "line");
        line.setAttribute("x1", String(a.x));
        line.setAttribute("y1", String(a.y));
        line.setAttribute("x2", String(b.x));
        line.setAttribute("y2", String(b.y));
        line.setAttribute("stroke", hovered ? "#1677ff" : "#d9d9d9");
        line.setAttribute("stroke-width", hovered ? "2.5" : "1.2");
        line.setAttribute("opacity", hovered ? "0.95" : "0.5");
        if (drawArrow) {
          line.setAttribute(
            "marker-end",
            `url(#wiki-arrow-head${hovered ? "-hot" : ""})`,
          );
        }
        g.appendChild(line);
      }

      // marker 定义（每帧重建，指向 <defs>）
      const defs = document.createElementNS(ns, "defs");
      const mk = (id: string, color: string) => {
        const m = document.createElementNS(ns, "marker");
        m.setAttribute("id", id);
        m.setAttribute("viewBox", "0 -4 8 8");
        m.setAttribute("refX", "6");
        m.setAttribute("refY", "0");
        m.setAttribute("markerWidth", "7");
        m.setAttribute("markerHeight", "7");
        m.setAttribute("orient", "auto");
        const path = document.createElementNS(ns, "path");
        path.setAttribute("d", "M0,-4L8,0L0,4Z");
        path.setAttribute("fill", color);
        m.appendChild(path);
        defs.appendChild(m);
      };
      if (drawArrow) {
        mk("wiki-arrow-head", "#bfbfbf");
        mk("wiki-arrow-head-hot", "#1677ff");
      }
      g.appendChild(defs);

      // nodes
      for (const n of nodeList) {
        const p = st.nodes.get(n.slug);
        if (!p) continue;
        const isHover = st.hover === n.slug;
        const radius = radiusOf.get(n.slug) || 14;
        const group = document.createElementNS(ns, "g");
        group.setAttribute("transform", `translate(${p.x},${p.y})`);
        group.setAttribute("data-slug", n.slug);
        group.style.cursor = "pointer";

        let clickTimer: number | null = null;
        group.addEventListener("mouseenter", () => {
          st.hover = n.slug;
          render();
        });
        group.addEventListener("mouseleave", () => {
          st.hover = "";
          render();
        });
        group.addEventListener("mousedown", (ev) => {
          ev.stopPropagation(); // 不触发画布 pan
        });
        group.addEventListener("click", (ev) => {
          if (st.dragged) {
            st.dragged = false;
            return;
          }
          ev.stopPropagation();
          if (clickTimer) clearTimeout(clickTimer);
          clickTimer = window.setTimeout(() => void selectNode(n.slug), 220);
        });
        group.addEventListener("dblclick", (ev) => {
          ev.stopPropagation();
          if (clickTimer) {
            clearTimeout(clickTimer);
            clickTimer = null;
          }
          focusEgo(n.slug);
        });

        const circle = document.createElementNS(ns, "circle");
        circle.setAttribute("r", String(radius));
        circle.setAttribute("fill", PAGE_TYPE_COLORS[n.page_type] || "#999");
        circle.setAttribute("opacity", isHover ? "0.95" : "0.85");
        circle.setAttribute("stroke", isHover ? "#333" : "#fff");
        circle.setAttribute("stroke-width", "1.5");
        group.appendChild(circle);

        const label = document.createElementNS(ns, "text");
        label.setAttribute("text-anchor", "middle");
        label.setAttribute("y", String(radius + 14));
        label.setAttribute("font-size", "12");
        label.setAttribute("fill", isHover ? "#1677ff" : "#555");
        label.setAttribute("font-weight", isHover ? "bold" : "normal");
        label.textContent = n.title.length > 10 ? n.title.slice(0, 10) + "…" : n.title;
        group.appendChild(label);
        g.appendChild(group);
      }
    };

    // 画布交互：pan / wheel / dblclick 重置 / 节点拖拽固定
    let downPos: { x: number; y: number } | null = null;
    let panning = false;
    let dragNode: string | null = null;
    let dragOfs = { x: 0, y: 0 };

    const onMouseDown = (ev: MouseEvent) => {
      const t = ev.target as Element;
      const nodeEl = t.closest("[data-slug]");
      if (nodeEl && svg.contains(nodeEl)) {
        const slug = nodeEl.getAttribute("data-slug")!;
        const p = st.nodes.get(slug);
        if (!p) return;
        dragNode = slug;
        const rect = svg.getBoundingClientRect();
        dragOfs = {
          x: (ev.clientX - rect.left - st.viewX) / st.scale - p.x,
          y: (ev.clientY - rect.top - st.viewY) / st.scale - p.y,
        };
        return;
      }
      downPos = { x: ev.clientX, y: ev.clientY };
      panning = true;
    };
    const onMouseMove = (ev: MouseEvent) => {
      if (dragNode) {
        st.dragged = true;
        const p = st.nodes.get(dragNode);
        if (p) {
          const rect = svg.getBoundingClientRect();
          p.x = (ev.clientX - rect.left - st.viewX) / st.scale - dragOfs.x;
          p.y = (ev.clientY - rect.top - st.viewY) / st.scale - dragOfs.y;
          p.pinned = true;
        }
        return;
      }
      if (!downPos) return;
      const dx = ev.clientX - downPos.x;
      const dy = ev.clientY - downPos.y;
      if (panning) {
        st.viewX += dx;
        st.viewY += dy;
        downPos = { x: ev.clientX, y: ev.clientY };
      }
    };
    const onMouseUp = () => {
      dragNode = null;
      downPos = null;
      panning = false;
    };
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const delta = ev.deltaY > 0 ? 0.9 : 1.1;
      st.scale = Math.max(0.3, Math.min(3, st.scale * delta));
    };
    const onDblClick = () => {
      st.viewX = 0;
      st.viewY = 0;
      st.scale = 1;
    };

    svg.addEventListener("mousedown", onMouseDown);
    svg.addEventListener("mousemove", onMouseMove);
    svg.addEventListener("mouseup", onMouseUp);
    svg.addEventListener("wheel", onWheel, { passive: false });
    svg.addEventListener("dblclick", onDblClick);

    const start = () => {
      if (st.raf) cancelAnimationFrame(st.raf);
      render();
      simulate();
    };
    start();

    return () => {
      if (st.raf) cancelAnimationFrame(st.raf);
      st.raf = 0;
      svg.removeEventListener("mousedown", onMouseDown);
      svg.removeEventListener("mousemove", onMouseMove);
      svg.removeEventListener("mouseup", onMouseUp);
      svg.removeEventListener("wheel", onWheel);
      svg.removeEventListener("dblclick", onDblClick);
    };
  }, [data, kbId, arrowsVisible, selectNode, focusEgo]);

  const helpContent = (
    <div style={{ fontSize: 12, lineHeight: "20px", maxWidth: 260 }}>
      <div><Text strong>拖拽节点</Text>：固定 / 取消固定位置（拖动后位置锁定）</div>
      <div><Text strong>滚轮</Text>：缩放 · <Text strong>拖动空白</Text>：平移</div>
      <div><Text strong>单击节点</Text>：查看页面详情</div>
      <div><Text strong>双击节点</Text>：下钻该节点邻域（ego）</div>
      <div><Text strong>开花邻域</Text>（详情抽屉）：以该节点为中心再展开一层</div>
      <div><Text strong>扩展前沿</Text>：批量展开所有可扩展节点（并发 6）</div>
      <div><Text strong>图例</Text>：点击切换类型过滤（服务端过滤）</div>
      <div><Text strong>适应屏幕</Text>：缩放回全部可见节点</div>
    </div>
  );

  return (
    <Card
      title={
        <Space>
          <AppstoreOutlined /> Wiki 知识图谱
          <Tag color="blue">{mode === "overview" ? "全库视图" : `邻域: ${center}`}</Tag>
        </Space>
      }
      extra={
        <Space>
          <Select
            style={{ width: 220 }}
            placeholder="搜索节点（远程）"
            showSearch
            allowClear
            filterOption={false}
            onSearch={handleGraphRemoteSearch}
            loading={searchLoading}
            options={searchOptions}
            value={undefined}
            onChange={(v: string) => handleGraphSearchSelect(v)}
          />
          <Tooltip title="操作帮助">
            <Popover content={helpContent} trigger="click" placement="bottomRight">
              <Button icon={<QuestionCircleOutlined />} />
            </Popover>
          </Tooltip>
          <Button icon={<FullscreenOutlined />} onClick={fitToView}>
            适应屏幕
          </Button>
          {mode === "ego" && (
            <Button icon={<ExpandOutlined />} loading={loading} onClick={() => void growFrontier()}>
              扩展前沿
            </Button>
          )}
          <Button icon={<ReloadOutlined />} onClick={() => void load(mode, center)}>
            刷新
          </Button>
          <Button icon={<ExportOutlined />} onClick={toOverview}>
            全库
          </Button>
        </Space>
      }
    >
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8, alignItems: "center" }}>
        {presentTypes.map((t) => (
          <Tag
            key={t}
            color={isTypeOn(t) ? PAGE_TYPE_COLORS[t] : "default"}
            style={{ cursor: "pointer" }}
            onClick={() => toggleType(t)}
          >
            {PAGE_TYPE_LABELS[t] || t}
          </Tag>
        ))}
        <span style={{ marginLeft: "auto", fontSize: 12, color: "#999" }}>
          节点 {data?.meta.returned ?? 0} · 边 {data?.edges.length ?? 0} · 全库页面{" "}
          {data?.meta.total ?? 0}
          {data?.meta.truncated ? " · 已截断（显示链接最多的节点）" : ""}
        </span>
      </div>

      <div style={{ position: "relative", height: 560 }}>
        {loading ? (
          <div style={{ textAlign: "center", padding: 60 }}>
            <Spin />
          </div>
        ) : !data || data.nodes.length === 0 ? (
          <Empty description="暂无 wiki 页面，无法生成图谱" style={{ paddingTop: 60 }} />
        ) : (
          <svg
            ref={svgRef}
            width="100%"
            height="100%"
            style={{ background: "#fafafa", borderRadius: 6, cursor: "grab" }}
          />
        )}
        <div style={{ position: "absolute", bottom: 8, left: 12, fontSize: 12, color: "#999" }}>
          拖拽节点固定 · 滚轮缩放 · 拖动空白平移 · 单击详情 · 双击下钻 · 图例/箭头/适配见上方工具栏
        </div>
      </div>

      {/* 详情抽屉 */}
      <Drawer
        title={selected?.title || "页面详情"}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={480}
        extra={
          selected && (
            <Space>
              <Button size="small" onClick={() => void loadBloomNeighbors(selected.slug)}>
                开花邻域
              </Button>
              <Button size="small" onClick={() => focusEgo(selected.slug)}>
                邻域下钻
              </Button>
              <Button size="small" type="primary" onClick={() => goWikiPage(selected.slug)}>
                打开 wiki 页
              </Button>
            </Space>
          )
        }
      >
        {selected && (
          <div style={{ marginBottom: 8 }}>
            <Tag color={PAGE_TYPE_COLORS[selected.page_type] || "default"}>
              {PAGE_TYPE_LABELS[selected.page_type] || selected.page_type}
            </Tag>
            <Tag>关联 {selected.link_count}</Tag>
            {selected.summary && (
              <div style={{ marginTop: 4, color: "#888" }}>{selected.summary}</div>
            )}
          </div>
        )}
        <div
          style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 13 }}
          dangerouslySetInnerHTML={{ __html: detailHtml }}
        />
      </Drawer>
    </Card>
  );
}