"use client";

/**
 * Wiki 知识图谱视图 —— Canvas 2D 力导向图（零第三方依赖）。
 *
 * 架构（2026-10-11 由 SVG DOM 改为 Canvas 2D）：
 * SVG 方案在节点多时（数百~上千）每帧要对每个 `<circle>/<line>/<text>` 写属性，
 * 是上万次 DOM 写 + 字符串分配，主线程必堵。改为 Canvas：一次 `fill/stroke` 画完，
 * 无 DOM 节点数量上限，命中检测用坐标反算 + 线性扫描（≤1500 节点足够）。
 * 交互（拖拽固定 / 空白平移 / 滚轮缩放 / hover 高亮 / 单击详情 / 双击下钻 /
 * 双击空白适应屏幕 / 箭头开关 / 图例过滤 / 开花邻域 / 扩展前沿）与样式类名
 * （`.kb-wikigraph-*`）全部保持不变。
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
  Popover,
  Select,
  Space,
  Spin,
  Tag,
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
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const { Text } = Typography;

// [[slug]] / [[slug|别名]] 双链 → markdown 链接（与 WikiBrowseView 一致，href 规整为 wiki 路由）
function renderWikiLinks(content: string, kbId: string): string {
  if (!content) return content;
  const wikiLinkRe = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;
  return content.replace(wikiLinkRe, (_m, slug: string, alias?: string) => {
    const clean = (slug || "").trim();
    if (!clean) return _m;
    const label = (alias || "").trim() || clean;
    const href = `/kbs/${encodeURIComponent(kbId)}/wiki/${encodeURIComponent(clean)}`;
    return `[${label}](${href})`;
  });
}

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
// 安全上限：即便未达速度阈值，最多模拟这么多帧后强制收停（防抖动不收敛长期占 CPU）
const SIM_MAX_FRAMES = 1200;
// 缩放到该比例以下不再绘制标签（文字绘制最贵，且极小尺寸下本就不可读）
const LABEL_MIN_SCALE = 0.4;
// 全库索引类页面（index/log）：连线指向全库，参与单点扩展但不参与 frontier 批量生长
const GRAPH_SYSTEM_PAGE_TYPES = new Set(["index", "log"]);

interface Pt {
  x: number;
  y: number;
  vx: number;
  vy: number;
  pinned: boolean;
}

// ── Barnes-Hut 四叉树 ──
// 斥力原为全量两两遍历（O(n²)/帧）：overview 上限 500 → 25 万次/帧，
// bloom 上限 1500 → 225 万次/帧（含 sqrt），16ms 内算不完直接堵死主线程。
// 改用四叉树按空间聚簇、远处子树以其质心近似整体斥力，复杂度降到 O(n log n)。
interface BhCell {
  cx: number;
  cy: number;
  h: number; // 单元中心与半边长
  mass: number;
  mx: number; // 子树坐标和（质心 = mx/mass）
  my: number;
  slug: string | null; // 叶子占据点（未细分时）
  px: number;
  py: number;
  kids: BhCell[] | null;
}

// theta 判据（越小越精确越慢）；0.85² ≈ 0.72
const BH_THETA2 = 0.72;
// 最大细分深度：重合/极近点（如被夹到画布角）会一直落在同一象限，
// 需要深度上限兜底，否则无限细分导致栈溢出。
const BH_MAX_DEPTH = 24;

function bhCell(cx: number, cy: number, h: number): BhCell {
  return { cx, cy, h, mass: 0, mx: 0, my: 0, slug: null, px: 0, py: 0, kids: null };
}

function bhQuadrant(c: BhCell, x: number, y: number): number {
  return (x < c.cx ? 0 : 1) + (y < c.cy ? 0 : 2);
}

function bhInsert(c: BhCell, slug: string, x: number, y: number, depth = 0): void {
  if (!c.kids && (c.slug === null || depth >= BH_MAX_DEPTH)) {
    // 空叶子，或已达深度上限：把点并入本单元作为团聚点
    if (c.slug === null) {
      c.slug = slug;
      c.px = x;
      c.py = y;
    }
    c.mass += 1;
    c.mx += x;
    c.my += y;
    return;
  }
  if (!c.kids) {
    // 单点叶子且仍有细分空间：细分后把旧点与新点一起下放
    const oldSlug = c.slug as string;
    const ox = c.px;
    const oy = c.py;
    const h = c.h / 2;
    c.slug = null;
    c.kids = [
      bhCell(c.cx - h, c.cy - h, h),
      bhCell(c.cx + h, c.cy - h, h),
      bhCell(c.cx - h, c.cy + h, h),
      bhCell(c.cx + h, c.cy + h, h),
    ];
    c.mass = 0;
    c.mx = 0;
    c.my = 0;
    bhInsert(c.kids[bhQuadrant(c, ox, oy)], oldSlug, ox, oy, depth + 1);
    c.mass = 1;
    c.mx = ox;
    c.my = oy;
  }
  bhInsert(c.kids![bhQuadrant(c, x, y)], slug, x, y, depth + 1);
  c.mass += 1;
  c.mx += x;
  c.my += y;
}

function bhAddRepulsion(
  out: { fx: number; fy: number },
  x: number,
  y: number,
  px: number,
  py: number,
  m: number,
  repulsion: number,
): void {
  const ax = x - px;
  const ay = y - py;
  const dd = ax * ax + ay * ay + 1;
  const f = (repulsion * m) / dd;
  const inv = 0.1 / Math.sqrt(dd);
  out.fx += ax * f * inv;
  out.fy += ay * f * inv;
}

function bhRepulse(
  c: BhCell,
  slug: string,
  x: number,
  y: number,
  repulsion: number,
  out: { fx: number; fy: number },
): void {
  if (c.mass === 0) return;
  if (!c.kids) {
    if (c.slug === slug) return; // 跳过自身
    bhAddRepulsion(out, x, y, c.mx / c.mass, c.my / c.mass, c.mass, repulsion);
    return;
  }
  const bx = c.mx / c.mass;
  const by = c.my / c.mass;
  const dx = x - bx;
  const dy = y - by;
  const d2 = dx * dx + dy * dy + 1;
  const s = c.h * 2;
  if ((s * s) / d2 < BH_THETA2) {
    // 单元足够远：整簇用质心近似
    bhAddRepulsion(out, x, y, bx, by, c.mass, repulsion);
    return;
  }
  for (const k of c.kids) bhRepulse(k, slug, x, y, repulsion, out);
}

interface Props {
  kbId: string;
  focusSlug?: string; // 从 wiki 页跳入时定位
}

export default function WikiGraphView({ kbId, focusSlug }: Props) {
  const router = useRouter();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const [data, setData] = useState<WikiGraphData | null>(null);
  const [loading, setLoading] = useState(false);
  // 空 Set = 全选（后端 types 空数组 = 全集）
  const [filterTypes, setFilterTypes] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"overview" | "ego">("overview");
  const [center, setCenter] = useState<string>("");
  const [selected, setSelected] = useState<WikiGraphNode | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailHtml, setDetailHtml] = useState("");
  const [detailLinks, setDetailLinks] = useState<{ slug: string; title: string; page_type: string }[]>([]);
  const [detailInLinks, setDetailInLinks] = useState<{ slug: string; title: string; page_type: string }[]>([]);
  const [arrowsVisible, setArrowsVisible] = useState(true);
  const [searchOptions, setSearchOptions] = useState<{ value: string; label: string }[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);

  const dataRef = useRef<WikiGraphData | null>(null);
  dataRef.current = data;
  const searchSeq = useRef(0);
  const searchTimer = useRef<number | null>(null);
  const bloomGen = useRef<Map<string, number>>(new Map());
  const bloomCurrentGen = useRef(0);
  // 本次布点是否已自动「适应屏幕」（重新布点时置 false，收敛后做一次）
  const autoFitRef = useRef(false);

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
        setDetailLinks(res.data.links || []);
        setDetailInLinks(res.data.in_links || []);
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
    const canvas = canvasRef.current;
    const st = stateRef.current;
    if (!canvas || st.nodes.size === 0) return;
    const W = canvas.clientWidth || 800;
    const H = canvas.clientHeight || 600;
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

  // ---- Canvas 渲染（力导向模拟）----
  useEffect(() => {
    const canvas = canvasRef.current;
    const st = stateRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (!data) return;

    // 画布尺寸 / DPR：CSS 尺寸决定逻辑坐标，物理像素 ×dpr 保证清晰
    let W = canvas.clientWidth || 800;
    let H = canvas.clientHeight || 600;
    let dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;

    const resize = () => {
      const w = canvas.clientWidth || 800;
      const h = canvas.clientHeight || 600;
      dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
      W = w;
      H = h;
      const pw = Math.max(1, Math.round(w * dpr));
      const ph = Math.max(1, Math.round(h * dpr));
      if (canvas.width !== pw) canvas.width = pw;
      if (canvas.height !== ph) canvas.height = ph;
    };
    resize();

    const nodeList = data.nodes;

    // 布点：数据收缩删除多余；全新 → 黄金角螺旋（按面积均匀铺满画布；原单环布点在节点多时
    // 会挤成一个密集的圈 → 视觉上「全部重叠」）；增量（bloom/frontier merge）→ 新节点贴着当前质心补充
    const inData = new Set(nodeList.map((n) => n.slug));
    for (const slug of Array.from(st.nodes.keys())) {
      if (!inData.has(slug)) st.nodes.delete(slug);
    }
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    if (st.nodes.size === 0) {
      // 世界坐标与视口解耦：布点半径只由节点数决定（世界原点即图中心）。
      // 原实现用 min(W,H)/2 作半径并以画布中心为原点，导致节点永远被限制在
      // 首次打开时视口大小的方块内。
      const R = 40 + 26 * Math.sqrt(Math.max(nodeList.length, 1));
      nodeList.forEach((n, i) => {
        const r = R * Math.sqrt((i + 0.5) / Math.max(nodeList.length, 1));
        const ang = i * goldenAngle;
        st.nodes.set(n.slug, {
          x: Math.cos(ang) * r,
          y: Math.sin(ang) * r,
          vx: 0,
          vy: 0,
          pinned: false,
        });
      });
      // 首次布点把世界原点摆到视口中心（保持初始可见）；收敛后会自动 fitToView
      st.viewX = W / 2;
      st.viewY = H / 2;
      st.scale = 1;
      autoFitRef.current = false;
    } else if (st.nodes.size < nodeList.length) {
      const fresh = nodeList.filter((n) => !st.nodes.has(n.slug));
      let cx = 0;
      let cy = 0;
      for (const p of st.nodes.values()) {
        cx += p.x;
        cy += p.y;
      }
      cx /= st.nodes.size;
      cy /= st.nodes.size;
      fresh.forEach((n, i) => {
        const ang = i * goldenAngle + 0.7;
        const r = 50 * Math.sqrt((i + 0.5) / Math.max(fresh.length, 1)) + 60;
        st.nodes.set(n.slug, {
          x: cx + Math.cos(ang) * r,
          y: cy + Math.sin(ang) * r,
          vx: 0,
          vy: 0,
          pinned: false,
        });
      });
      autoFitRef.current = false;
    }
    const radiusOf = new Map<string, number>(
      nodeList.map((n) => [n.slug, 14 + Math.min(10, n.link_count || 0) * 1.2]),
    );

    // 力计算邻接表：每节点只遍历相连边（O(Σdeg)，而非原实现的每帧 O(n×E)）
    const adj = new Map<string, string[]>();
    for (const e of data.edges) {
      if (!adj.has(e.source)) adj.set(e.source, []);
      if (!adj.has(e.target)) adj.set(e.target, []);
      adj.get(e.source)!.push(e.target);
      adj.get(e.target)!.push(e.source);
    }

    // 力导向模拟：斥力 + 弹簧（常量与 SVG 版一致，保证布局行为不变）
    const repulsion = 900;
    const spring = 0.008;
    const restLen = 90;
    // 向心力（gravity）：把整体向世界原点收束，使布局收敛到与视口无关的有限尺度。
    // 原实现改用「夹到画布 20..W-20」代替，导致图被锁死在画布方块内（节点还会被挤到边界）。
    const gravity = 0.0001;

    // 可见性守卫：切到隐藏页签（display:none）时停帧，避免后台空转。
    // antd Tabs 默认不销毁隐藏 pane，若不守卫则多开图页签会多实例满帧跑。
    let visible = true;
    let simFrames = 0;

    // 画布坐标变换（世界坐标 → 屏幕）：ctx.setTransform(dpr*s, 0, 0, dpr*s, dpr*viewX, dpr*viewY)
    const applyTransform = () => {
      ctx.setTransform(dpr * st.scale, 0, 0, dpr * st.scale, dpr * st.viewX, dpr * st.viewY);
    };

    // 箭头（近似 SVG marker：tip 在端点前 ~1.75、底边在其后 ~5.25、半高 3.5）
    const drawArrowHead = (x: number, y: number, ux: number, uy: number, color: string) => {
      const tipX = x + ux * 1.75;
      const tipY = y + uy * 1.75;
      const baseX = x - ux * 5.25;
      const baseY = y - uy * 5.25;
      const px = -uy * 3.5;
      const py = ux * 3.5;
      ctx.beginPath();
      ctx.moveTo(tipX, tipY);
      ctx.lineTo(baseX + px, baseY + py);
      ctx.lineTo(baseX - px, baseY - py);
      ctx.closePath();
      ctx.fillStyle = color;
      ctx.fill();
    };

    // 渲染：单次清屏 + 变换后整图重画（无 DOM 节点，节点/边数量不产生属性写开销）
    const render = () => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      applyTransform();

      const drawArrow = arrowsVisible && st.scale > 0.55;

      // edges（先画，节点覆盖其上）
      for (const e of data.edges) {
        const a = st.nodes.get(e.source);
        const b = st.nodes.get(e.target);
        if (!a || !b) continue;
        const hovered = !!st.hover && (e.source === st.hover || e.target === st.hover);
        // 终点缩到目标节点边缘（半径 + 箭头余量），避免箭头与节点重叠
        const rA = radiusOf.get(e.source) || 14;
        const rB = radiusOf.get(e.target) || 14;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.hypot(dx, dy) || 1;
        const ux = dx / dist;
        const uy = dy / dist;
        const x2 = b.x - ux * (rB + 6);
        const y2 = b.y - uy * (rB + 6);
        const x1 = a.x + ux * (rA + 2);
        const y1 = a.y + uy * (rA + 2);
        ctx.globalAlpha = hovered ? 0.95 : 0.5;
        ctx.strokeStyle = hovered ? "#1677ff" : "#d9d9d9";
        ctx.lineWidth = hovered ? 2.5 : 1.2;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        if (drawArrow) drawArrowHead(x2, y2, ux, uy, hovered ? "#1677ff" : "#bfbfbf");
      }

      // nodes
      const showLabel = st.scale >= LABEL_MIN_SCALE;
      if (showLabel) {
        ctx.textAlign = "center";
        ctx.textBaseline = "alphabetic";
      }
      for (const n of nodeList) {
        const p = st.nodes.get(n.slug);
        if (!p) continue;
        const isHover = st.hover === n.slug;
        const radius = radiusOf.get(n.slug) || 14;
        ctx.globalAlpha = isHover ? 0.95 : 0.85;
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
        ctx.fillStyle = PAGE_TYPE_COLORS[n.page_type] || "#999";
        ctx.fill();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = isHover ? "#333" : "#fff";
        ctx.stroke();
        if (showLabel) {
          ctx.globalAlpha = 1;
          ctx.fillStyle = isHover ? "#1677ff" : "#555";
          ctx.font = `${isHover ? "bold " : ""}12px sans-serif`;
          const title = n.title.length > 10 ? n.title.slice(0, 10) + "…" : n.title;
          ctx.fillText(title, p.x, p.y + radius + 14);
        }
      }
      ctx.globalAlpha = 1;
    };

    const simulate = () => {
      if (!visible) {
        st.raf = 0;
        return;
      }
      const nodes = st.nodes;
      const entries = Array.from(nodes.entries());
      let totalV = 0;

      // Barnes-Hut：每帧按当前位置建树，斥力由 O(n²) 降到 O(n log n)
      let minX = Infinity;
      let minY = Infinity;
      let maxX = -Infinity;
      let maxY = -Infinity;
      for (const [, p] of entries) {
        if (p.x < minX) minX = p.x;
        if (p.y < minY) minY = p.y;
        if (p.x > maxX) maxX = p.x;
        if (p.y > maxY) maxY = p.y;
      }
      const root = bhCell(
        (minX + maxX) / 2,
        (minY + maxY) / 2,
        Math.max(maxX - minX, maxY - minY, 1) / 2 + 1,
      );
      for (const [slug, p] of entries) bhInsert(root, slug, p.x, p.y);

      const acc = { fx: 0, fy: 0 };
      for (const [slug, a] of entries) {
        if (a.pinned) {
          totalV += Math.abs(a.vx) + Math.abs(a.vy);
          continue;
        }
        a.vx *= 0.85;
        a.vy *= 0.85;
        // 斥力：Barnes-Hut 近似（远处子树以质心代替全量两两遍历）
        acc.fx = 0;
        acc.fy = 0;
        bhRepulse(root, slug, a.x, a.y, repulsion, acc);
        // 斥力 + 向心力（向心力替代原先的画布边界硬夹取，让图自然收敛且与视口无关）
        a.vx += acc.fx - a.x * gravity;
        a.vy += acc.fy - a.y * gravity;
        for (const other of adj.get(slug) || []) {
          const tb = nodes.get(other);
          if (!tb || tb === a) continue;
          const dx = tb.x - a.x;
          const dy = tb.y - a.y;
          const d = Math.sqrt(dx * dx + dy * dy) + 1;
          const f = (d - restLen) * spring;
          a.vx += (dx / d) * f;
          a.vy += (dy / d) * f;
        }
        a.x += a.vx;
        a.y += a.vy;
        totalV += Math.abs(a.vx) + Math.abs(a.vy);
      }
      render();
      simFrames += 1;
      // 稳定即停帧（省 CPU）；hover / 拖拽 / 平移缩放后按需唤醒。
      // 收敛后自动「适应屏幕」一次，保证整体居中且完整可见。
      const converged = entries.length > 5 && totalV < 0.5;
      if (converged || simFrames >= SIM_MAX_FRAMES) {
        st.raf = 0;
        if (!autoFitRef.current && converged) {
          autoFitRef.current = true;
          fitToView();
          render();
        }
        return;
      }
      st.raf = requestAnimationFrame(simulate);
    };

    const wake = () => {
      if (!st.raf) st.raf = requestAnimationFrame(simulate);
    };

    // 命中检测：屏幕坐标 → 世界坐标 → 反序线性扫描（后画者在上，优先命中）
    const hitNode = (clientX: number, clientY: number): string | null => {
      const rect = canvas.getBoundingClientRect();
      const wx = (clientX - rect.left - st.viewX) / st.scale;
      const wy = (clientY - rect.top - st.viewY) / st.scale;
      for (let i = nodeList.length - 1; i >= 0; i--) {
        const n = nodeList[i];
        const p = st.nodes.get(n.slug);
        if (!p) continue;
        const r = radiusOf.get(n.slug) || 14;
        const dx = wx - p.x;
        const dy = wy - p.y;
        if (dx * dx + dy * dy <= r * r) return n.slug;
      }
      return null;
    };

    // 画布交互：pan / wheel / dblclick 重置 / 节点拖拽固定
    let downPos: { x: number; y: number } | null = null;
    let panning = false;
    let dragNode: string | null = null;
    let dragOfs = { x: 0, y: 0 };
    let clickTimer: number | null = null;

    const onMouseDown = (ev: MouseEvent) => {
      const hit = hitNode(ev.clientX, ev.clientY);
      if (hit) {
        const p = st.nodes.get(hit);
        if (!p) return;
        dragNode = hit;
        const rect = canvas.getBoundingClientRect();
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
          const rect = canvas.getBoundingClientRect();
          p.x = (ev.clientX - rect.left - st.viewX) / st.scale - dragOfs.x;
          p.y = (ev.clientY - rect.top - st.viewY) / st.scale - dragOfs.y;
          p.pinned = true;
        }
        // 停帧后拖拽仍需刷新：若模拟已停则唤醒，否则手动重绘
        render();
        wake();
        return;
      }
      if (downPos) {
        const dx = ev.clientX - downPos.x;
        const dy = ev.clientY - downPos.y;
        if (panning) {
          st.viewX += dx;
          st.viewY += dy;
          downPos = { x: ev.clientX, y: ev.clientY };
          render();
        }
        return;
      }
      // hover 命中（不唤醒物理，避免悬停时节点漂移；只重绘高亮态）
      const hit = hitNode(ev.clientX, ev.clientY) || "";
      if (hit !== st.hover) {
        st.hover = hit;
        canvas.style.cursor = hit ? "pointer" : "";
        render();
      }
    };

    const onMouseUp = () => {
      dragNode = null;
      downPos = null;
      panning = false;
    };

    const onMouseLeave = () => {
      dragNode = null;
      downPos = null;
      panning = false;
      if (st.hover) {
        st.hover = "";
        canvas.style.cursor = "";
        render();
      }
    };

    const onClick = (ev: MouseEvent) => {
      if (st.dragged) {
        st.dragged = false;
        return;
      }
      const hit = hitNode(ev.clientX, ev.clientY);
      if (!hit) return;
      if (clickTimer) clearTimeout(clickTimer);
      clickTimer = window.setTimeout(() => void selectNode(hit), 220);
    };

    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left;
      const my = ev.clientY - rect.top;
      // 缩放前光标处的世界坐标（缩放后保持不动，实现「以光标为锚点」缩放）
      const wx = (mx - st.viewX) / st.scale;
      const wy = (my - st.viewY) / st.scale;
      const delta = ev.deltaY > 0 ? 0.9 : 1.1;
      st.scale = Math.max(0.3, Math.min(3, st.scale * delta));
      st.viewX = mx - wx * st.scale;
      st.viewY = my - wy * st.scale;
      render();
    };

    const onDblClick = (ev: MouseEvent) => {
      const hit = hitNode(ev.clientX, ev.clientY);
      if (hit) {
        // 双击节点：取消待触发的单击详情，转为下钻该节点邻域（ego）
        if (clickTimer) {
          clearTimeout(clickTimer);
          clickTimer = null;
        }
        focusEgo(hit);
        return;
      }
      // 双击空白：重新适应屏幕（居中 + 完整可见）
      autoFitRef.current = true;
      fitToView();
      render();
    };

    canvas.addEventListener("mousedown", onMouseDown);
    canvas.addEventListener("mousemove", onMouseMove);
    canvas.addEventListener("mouseup", onMouseUp);
    canvas.addEventListener("mouseleave", onMouseLeave);
    canvas.addEventListener("click", onClick);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("dblclick", onDblClick);

    // 容器尺寸变化（页签切换 / 面板拖拽）时同步画布尺寸并重绘
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => {
            resize();
            render();
          })
        : null;
    ro?.observe(canvas);

    const start = () => {
      if (st.raf) cancelAnimationFrame(st.raf);
      resize();
      simFrames = 0;
      render();
      simulate();
    };
    start();

    // 可见性守卫：观察 canvas 是否在视口内（隐藏页签 display:none → 不交叉）。
    // 切到隐藏页签即停帧，切回自动恢复（并重新测量尺寸），避免后台持续占用主线程。
    const io =
      typeof IntersectionObserver !== "undefined"
        ? new IntersectionObserver((ents) => {
            const nowVisible = ents.some((e) => e.isIntersecting);
            if (nowVisible === visible) return;
            visible = nowVisible;
            if (nowVisible) {
              resize();
              render();
              wake();
            } else if (st.raf) {
              cancelAnimationFrame(st.raf);
              st.raf = 0;
            }
          })
        : null;
    io?.observe(canvas);

    return () => {
      if (st.raf) cancelAnimationFrame(st.raf);
      st.raf = 0;
      if (clickTimer) clearTimeout(clickTimer);
      io?.disconnect();
      ro?.disconnect();
      canvas.removeEventListener("mousedown", onMouseDown);
      canvas.removeEventListener("mousemove", onMouseMove);
      canvas.removeEventListener("mouseup", onMouseUp);
      canvas.removeEventListener("mouseleave", onMouseLeave);
      canvas.removeEventListener("click", onClick);
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("dblclick", onDblClick);
    };
  }, [data, kbId, arrowsVisible, selectNode, focusEgo, fitToView]);

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
    // 白底直角内容面板（对齐系统列表页）：标题由页签承担，画布铺满面板
    <Card
      style={{ height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", minHeight: 0 }}
      styles={{ body: { flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden", padding: 0 } }}
    >
    <div className="kb-wikigraph">
      {/* 画布（全屏，对齐 .wiki-graph-canvas） */}
      <div className="kb-wikigraph-canvas">
        {loading ? (
          <div className="kb-wikigraph-loading">
            <Spin />
          </div>
        ) : !data || data.nodes.length === 0 ? (
          <div className="kb-wikigraph-empty">
            <div className="kb-wikigraph-empty-icon">
              <AppstoreOutlined style={{ fontSize: 48 }} />
            </div>
            <p className="kb-wikigraph-empty-desc">暂无 wiki 页面，无法生成图谱</p>
          </div>
        ) : (
          <canvas ref={canvasRef} className="kb-wikigraph-svg" />
        )}
      </div>

      {/* 搜索覆盖层（对齐 .wiki-graph-search-container） */}
      <div className="kb-wikigraph-search-container">
        <div className="kb-wikigraph-search-row">
          <Select
            className="kb-wikigraph-search"
            placeholder="搜索 wiki 页面"
            showSearch
            allowClear
            filterOption={false}
            onSearch={handleGraphRemoteSearch}
            loading={searchLoading}
            options={searchOptions}
            value={undefined}
            onChange={(v: string) => handleGraphSearchSelect(v)}
          />
          <Popover content={helpContent} trigger="click" placement="bottomRight">
            <Button type="text" icon={<QuestionCircleOutlined />} className="kb-wikigraph-help" />
          </Popover>
        </div>
      </div>

      {/* 图例覆盖层（对齐 .wiki-graph-legend） */}
      <div className="kb-wikigraph-legend">
        <div className="kb-wikigraph-legend-items">
          {presentTypes.map((t) => (
            <div
              key={t}
              className={`kb-wikigraph-legend-item${isTypeOn(t) ? "" : " disabled"}`}
              onClick={() => toggleType(t)}
            >
              <span className="kb-wikigraph-legend-dot" style={{ background: PAGE_TYPE_COLORS[t] }} />
              {PAGE_TYPE_LABELS[t] || t}
            </div>
          ))}
        </div>
        <div className="kb-wikigraph-legend-divider" />
        <div className="kb-wikigraph-legend-actions">
          <div className="kb-wikigraph-legend-action" onClick={fitToView} title="适应屏幕">
            <FullscreenOutlined className="kb-wikigraph-legend-action-icon" />
            <span>适应屏幕</span>
          </div>
          <div
            className="kb-wikigraph-legend-action"
            onClick={() => setArrowsVisible((v) => !v)}
            title="显示/隐藏箭头"
          >
            <AppstoreOutlined className="kb-wikigraph-legend-action-icon" />
            <span>{arrowsVisible ? "隐藏箭头" : "显示箭头"}</span>
          </div>
          {mode === "ego" && (
            <div
              className="kb-wikigraph-legend-action"
              onClick={() => void growFrontier()}
              title="批量扩展当前邻域前沿"
            >
              <ExpandOutlined className="kb-wikigraph-legend-action-icon" />
              <span>扩展前沿</span>
            </div>
          )}
          {mode === "ego" && (
            <div className="kb-wikigraph-legend-action" onClick={toOverview} title="返回全库视图">
              <ReloadOutlined className="kb-wikigraph-legend-action-icon" />
              <span>返回全库</span>
            </div>
          )}
          <div
            className="kb-wikigraph-legend-action"
            onClick={() => void load(mode, center)}
            title="刷新"
          >
            <ExportOutlined className="kb-wikigraph-legend-action-icon" />
            <span>刷新</span>
          </div>
        </div>
        {/* 状态卡（对齐 .wiki-graph-status-card） */}
        {data && (
          <div className="kb-wikigraph-status-card">
            <div className="kb-wikigraph-status-primary">
              节点 {data.meta.returned ?? 0} · 边 {data.edges.length ?? 0} · 全库页面 {data.meta.total ?? 0}
            </div>
            {data.meta.truncated && (
              <div className="kb-wikigraph-status-secondary">已截断（显示链接最多的节点）</div>
            )}
          </div>
        )}
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
        {/* 对齐 WeKnora .wiki-reader-meta + .wiki-reader-body */}
        <div className="kb-wiki-reader-meta" style={{ marginBottom: 8 }}>
          {selected && (
            <>
              <Tag color={PAGE_TYPE_COLORS[selected.page_type] || "default"}>
                {PAGE_TYPE_LABELS[selected.page_type] || selected.page_type}
              </Tag>
              <span className="kb-wiki-reader-meta-text">关联 {selected.link_count}</span>
            </>
          )}
        </div>
        {selected?.summary && (
                  <div className="kb-wiki-reader-aliases" style={{ marginBottom: 10 }}>
                    <span className="kb-wiki-alias-label">{selected.summary}</span>
                  </div>
                )}
                {/* 反向链接（谁引用本页） */}
                {detailInLinks.length > 0 && (
                  <div className="kb-wiki-reader-backlinks" style={{ marginBottom: 12 }}>
                    <span className="kb-wiki-link-label">反向链接</span>
                    <div className="kb-wiki-backlink-list">
                      {detailInLinks.map((link) => (
                        <a
                          key={link.slug}
                          className="kb-wiki-backlink-tag"
                          href={`/kbs/${kbId}/wiki/${encodeURIComponent(link.slug)}`}
                          onClick={(e) => {
                            e.preventDefault();
                            void selectNode(link.slug);
                          }}
                        >
                          {link.title}
                        </a>
                      ))}
                    </div>
                  </div>
                )}
                {/* 正文：Markdown 渲染 + [[slug]] 双链 */}
                <div className="kb-wiki-reader-body">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: (props) => (
                        <a
                          {...props}
                          onClick={(e) => {
                            const href = (props.href || "").trim();
                            if (!href.startsWith("/kbs/")) return;
                            e.preventDefault();
                            const m = href.match(/\/wiki\/([^/]+)$/);
                            if (m) void selectNode(decodeURIComponent(m[1]));
                          }}
                        />
                      ),
                    }}
                  >
                    {renderWikiLinks(detailHtml, kbId)}
                  </ReactMarkdown>
                </div>
                {/* 出链（本页引用的其他页面） */}
                {detailLinks.length > 0 && (
                  <div className="kb-wiki-reader-sources">
                    <span className="kb-wiki-link-label">引用</span>
                    <div className="kb-wiki-source-list">
                      {detailLinks.map((link) => (
                        <a
                          key={link.slug}
                          className="kb-wiki-source-ref"
                          href={`/kbs/${kbId}/wiki/${encodeURIComponent(link.slug)}`}
                          onClick={(e) => {
                            e.preventDefault();
                            void selectNode(link.slug);
                          }}
                        >
                          {link.title}
                        </a>
                      ))}
                    </div>
                  </div>
                )}
      </Drawer>
    </div>
    </Card>
  );
}
