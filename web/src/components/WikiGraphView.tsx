"use client";

/**
 * Wiki 知识图谱视图 —— SVG 力导向图（零第三方依赖，与 WeKnora 同方案）。
 *
 * 交互：
 * - 拖拽节点（固定位置）、缩放（滚轮）、平移（拖空白）
 * - 悬停节点高亮邻域、点击节点打开详情抽屉
 * - 类型过滤图例（entity/concept/summary）
 * - 搜索定位（下拉选择节点）→ ego 下钻邻域
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Card, Drawer, Empty, Select, Space, Spin, Tag, Typography } from "antd";
import { AppstoreOutlined, ExportOutlined, ReloadOutlined } from "@ant-design/icons";
import { apiWikiGraph, apiWikiPage, WikiGraphData, WikiGraphNode } from "@/lib/api";
import { useRouter } from "next/navigation";

const { Text } = Typography;

const PAGE_TYPE_COLORS: Record<string, string> = {
  entity: "#1677ff",
  concept: "#52c41a",
  summary: "#faad14",
};

const PAGE_TYPE_LABELS: Record<string, string> = {
  entity: "实体",
  concept: "概念",
  summary: "摘要",
};

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
  const [filterTypes, setFilterTypes] = useState<Set<string>>(new Set(["entity", "concept", "summary"]));
  const [mode, setMode] = useState<"overview" | "ego">("overview");
  const [center, setCenter] = useState<string>("");
  const [selected, setSelected] = useState<WikiGraphNode | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailHtml, setDetailHtml] = useState("");
  const [searchOptions, setSearchOptions] = useState<{ value: string; label: string }[]>([]);

  // 图数据内部状态（节点坐标等）
  const stateRef = useRef<{
    nodes: Map<string, Pt>;
    hover: string;
    viewX: number;
    viewY: number;
    scale: number;
    dragging: string | null;
    raf: number;
  }>({
    nodes: new Map(),
    hover: "",
    viewX: 0,
    viewY: 0,
    scale: 1,
    dragging: null,
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
          limit: 200,
          types: Array.from(filterTypes),
        });
        if (res.success && res.data) {
          setData(res.data);
          setSearchOptions(res.data.nodes.slice(0, 100).map((n) => ({ value: n.slug, label: n.title })));
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

  const selectNode = async (slug: string) => {
    const node = data?.nodes.find((n) => n.slug === slug);
    if (!node) return;
    // 打开详情
    const res = await apiWikiPage(kbId, slug);
    if (res.success && res.data) {
      setSelected(node);
      setDetailHtml(res.data.content || "");
      setDetailOpen(true);
    }
  };

  const focusEgo = (slug: string) => {
    setCenter(slug);
    setMode("ego");
    setDetailOpen(false);
  };

  const goWikiPage = (slug: string) => {
    router.push(`/kbs/${kbId}/wiki/${encodeURIComponent(slug)}`);
  };

  // ---- SVG 渲染（力导向模拟）----
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || !data) return;
    svg.innerHTML = "";
    const W = svg.clientWidth || 800;
    const H = svg.clientHeight || 600;

    const ns = "http://www.w3.org/2000/svg";
    const st = stateRef.current;

    // 初始化节点位置（环形 + 力导向迭代）
    const nodeList = data.nodes;
    if (nodeList.length === 0) return;
    if (st.nodes.size !== nodeList.length) {
      st.nodes.clear();
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
    }

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
        // 弹簧（edges）
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
        a.x = Math.max(20, Math.min(W - 20, a.x));
        a.y = Math.max(20, Math.min(H - 20, a.y));
      }
      render();
      st.raf = requestAnimationFrame(simulate);
    };

    // 渲染帧
    const render = () => {
      const container = svg;
      container.innerHTML = "";
      const g = document.createElementNS(ns, "g");
      const scale = st.scale;
      const tx = st.viewX;
      const ty = st.viewY;
      g.setAttribute("transform", `translate(${tx},${ty}) scale(${scale})`);
      container.appendChild(g);

      // edges
      for (const e of data.edges) {
        const a = st.nodes.get(e.source);
        const b = st.nodes.get(e.target);
        if (!a || !b) continue;
        const line = document.createElementNS(ns, "line");
        line.setAttribute("x1", String(a.x));
        line.setAttribute("y1", String(a.y));
        line.setAttribute("x2", String(b.x));
        line.setAttribute("y2", String(b.y));
        const hovered = st.hover && (e.source === st.hover || e.target === st.hover);
        line.setAttribute("stroke", hovered ? "#1677ff" : "#d9d9d9");
        line.setAttribute("stroke-width", hovered ? "2.5" : "1.2");
        line.setAttribute("opacity", hovered ? "0.95" : "0.5");
        g.appendChild(line);
      }

      // nodes
      for (const n of nodeList) {
        const p = st.nodes.get(n.slug);
        if (!p) continue;
        const isHover = st.hover === n.slug;
        const radius = 14 + Math.min(10, n.link_count || 0) * 1.2;
        const group = document.createElementNS(ns, "g");
        group.setAttribute("transform", `translate(${p.x},${p.y})`);
        group.style.cursor = "pointer";
        group.addEventListener("mouseenter", () => {
          st.hover = n.slug;
          render();
        });
        group.addEventListener("mouseleave", () => {
          st.hover = "";
          render();
        });
        group.addEventListener("click", (ev) => {
          ev.stopPropagation();
          void selectNode(n.slug);
        });
        group.addEventListener("dblclick", (ev) => {
          ev.stopPropagation();
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

    // 拖拽
    const svgEvt = (ev: MouseEvent) => {
      const rect = svg.getBoundingClientRect();
      const mx = (ev.clientX - rect.left - st.viewX) / st.scale;
      const my = (ev.clientY - rect.top - st.viewY) / st.scale;
      return { mx, my };
    };

    let downPos: { x: number; y: number } | null = null;
    let panning = false;
    svg.addEventListener("mousedown", (ev) => {
      downPos = { x: ev.clientX, y: ev.clientY };
      panning = true;
    });
    svg.addEventListener("mousemove", (ev) => {
      if (!downPos) return;
      const dx = ev.clientX - downPos.x;
      const dy = ev.clientY - downPos.y;
      if (panning) {
        st.viewX += dx;
        st.viewY += dy;
        downPos = { x: ev.clientX, y: ev.clientY };
      }
    });
    svg.addEventListener("mouseup", () => {
      downPos = null;
      panning = false;
    });
    svg.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      const delta = ev.deltaY > 0 ? 0.9 : 1.1;
      st.scale = Math.max(0.3, Math.min(3, st.scale * delta));
    });
    svg.addEventListener("dblclick", () => {
      st.viewX = 0;
      st.viewY = 0;
      st.scale = 1;
    });
    void svgEvt;

    // 开始
    const start = () => {
      if (st.raf) cancelAnimationFrame(st.raf);
      render();
      simulate();
    };
    start();

    return () => {
      if (st.raf) cancelAnimationFrame(st.raf);
      st.raf = 0;
    };
  }, [data, kbId, filterTypes]);

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
          {(["entity", "concept", "summary"] as const).map((t) => (
            <Tag
              key={t}
              color={filterTypes.has(t) ? PAGE_TYPE_COLORS[t] : "default"}
              style={{ cursor: "pointer" }}
              onClick={() => toggleType(t)}
            >
              {PAGE_TYPE_LABELS[t]}
            </Tag>
          ))}
          <Select
            style={{ width: 200 }}
            placeholder="搜索节点"
            showSearch
            filterOption={(input, opt) => (opt?.label || "").includes(input)}
            options={searchOptions}
            value={undefined}
            onChange={(v: string) => void selectNode(v)}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load(mode, center)}>
            刷新
          </Button>
          <Button icon={<ExportOutlined />} onClick={toOverview}>
            全库
          </Button>
        </Space>
      }
    >
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
          拖拽节点固定 · 滚轮缩放 · 拖动空白平移 · 单击看详情 · 双击下钻邻域
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
            {selected.summary && <div style={{ marginTop: 4, color: "#888" }}>{selected.summary}</div>}
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
