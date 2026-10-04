"use client";

/**
 * Neo4j 知识图谱视图 —— SVG 力导向图（零第三方依赖，与 WikiGraphView 同方案）。
 *
 * 数据源：Neo4j 实体/关系知识图谱（api/routers/graph.py 读取，非 wiki 链接图）。
 * 交互：拖拽固定 / 滚轮缩放 / 平移 / 悬停高亮 / 单击看详情 / 双击 ego 下钻 /
 *       类型过滤 / 搜索定位 / 统计面板。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Card, Drawer, Empty, Select, Space, Spin, Statistic, Tag, Typography } from "antd";
import { ApartmentOutlined, ExportOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  apiGraphHealth,
  apiKbGraph,
  apiKbGraphEgo,
  apiKbGraphSearch,
  apiKbGraphStats,
  GraphData,
  GraphEdge,
  GraphHealth,
  GraphNode,
  GraphStats,
} from "@/lib/api";

const { Text } = Typography;

// 实体类型颜色（有限集合外灰色）
const ENTITY_COLORS: Record<string, string> = {
  Person: "#1677ff",
  Organization: "#722ed1",
  Location: "#13c2c2",
  Product: "#fa8c16",
  Event: "#eb2f96",
  Date: "#52c41a",
  Work: "#2f54eb",
  Concept: "#a0d911",
  Resource: "#f5222d",
  Category: "#faad14",
  Operation: "#08979c",
};

const ENTITY_LABELS: Record<string, string> = {
  Person: "人",
  Organization: "组织",
  Location: "地点",
  Product: "产品",
  Event: "事件",
  Date: "日期",
  Work: "制度/工作",
  Concept: "概念",
  Resource: "资源",
  Category: "类别",
  Operation: "操作",
};

function entityColor(t: string): string {
  return ENTITY_COLORS[t] || "#bfbfbf";
}

function entityLabel(t: string): string {
  return ENTITY_LABELS[t] || t || "实体";
}

interface Pt {
  x: number;
  y: number;
  vx: number;
  vy: number;
  pinned: boolean;
}

interface Props {
  kbId: string;
  focusName?: string; // 从详情跳入时定位
}

export default function Neo4jGraphView({ kbId, focusName }: Props) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [data, setData] = useState<GraphData | null>(null);
  const [health, setHealth] = useState<GraphHealth | null>(null);
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [filterTypes, setFilterTypes] = useState<Set<string>>(new Set());
  const [center, setCenter] = useState<string>("");
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [searchOptions, setSearchOptions] = useState<{ value: string; label: string }[]>([]);

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
    async (c: string) => {
      setLoading(true);
      try {
        let res;
        if (c) {
          res = await apiKbGraphEgo(kbId, c, 1, 200);
        } else {
          res = await apiKbGraph(kbId, { limit: 300 });
        }
        if (res.success && res.data) {
          setData(res.data);
          setSearchOptions(
            res.data.nodes.slice(0, 100).map((n) => ({ value: n.name, label: n.name })),
          );
          setCenter(c);
        }
      } finally {
        setLoading(false);
      }
    },
    [kbId],
  );

  useEffect(() => {
    // 初始探测：Neo4j 是否配置/连通 + 全库统计
    void apiGraphHealth().then((h) => setHealth(h.success ? h.data ?? null : null));
    void apiKbGraphStats(kbId).then((s) => {
      if (s.success && s.data) {
        setStats(s.data);
        setFilterTypes(new Set(s.data.entity_types.map((t) => t.type)));
      }
    });
  }, [kbId]);

  useEffect(() => {
    void load(focusName || "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusName]);

  const toggleType = (t: string) => {
    setFilterTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  };

  const toOverview = () => {
    setCenter("");
    void load("");
  };

  const selectNode = (name: string) => {
    const node = data?.nodes.find((n) => n.name === name);
    if (!node) return;
    setSelected(node);
    setDetailOpen(true);
  };

  const focusEgo = (name: string) => {
    setDetailOpen(false);
    void load(name);
  };

  const visibleNodes = useCallback(
    (d: GraphData) => {
      if (filterTypes.size === 0) return d.nodes;
      return d.nodes.filter((n) => filterTypes.has(n.entity_type));
    },
    [filterTypes],
  );

  // ---- SVG 渲染（力导向模拟，同 WikiGraphView 方案）----
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || !data) return;
    const nodeList = visibleNodes(data);
    const visibleSet = new Set(nodeList.map((n) => n.name));
    const edgeList = data.edges.filter((e) => visibleSet.has(e.source) && visibleSet.has(e.target));
    svg.innerHTML = "";
    const W = svg.clientWidth || 800;
    const H = svg.clientHeight || 560;
    const ns = "http://www.w3.org/2000/svg";
    const st = stateRef.current;

    if (nodeList.length === 0) return;
    if (st.nodes.size !== nodeList.length) {
      st.nodes.clear();
      nodeList.forEach((n, i) => {
        const ang = (2 * Math.PI * i) / nodeList.length;
        st.nodes.set(n.name, {
          x: W / 2 + Math.cos(ang) * (Math.min(W, H) / 2 - 60),
          y: H / 2 + Math.sin(ang) * (Math.min(W, H) / 2 - 60),
          vx: 0,
          vy: 0,
          pinned: false,
        });
      });
    }

    const repulsion = 1100;
    const spring = 0.008;
    const restLen = 110;

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
        for (const e of edgeList) {
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

    const render = () => {
      const container = svg;
      container.innerHTML = "";
      const g = document.createElementNS(ns, "g");
      g.setAttribute("transform", `translate(${st.viewX},${st.viewY}) scale(${st.scale})`);
      container.appendChild(g);

      // edges（含关系类型标签）
      for (const e of edgeList) {
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
        line.setAttribute("opacity", hovered ? "0.95" : "0.55");
        g.appendChild(line);
        if (hovered && e.type) {
          const mx = (a.x + b.x) / 2;
          const my = (a.y + b.y) / 2 - 4;
          const lbl = document.createElementNS(ns, "text");
          lbl.setAttribute("x", String(mx));
          lbl.setAttribute("y", String(my));
          lbl.setAttribute("text-anchor", "middle");
          lbl.setAttribute("font-size", "11");
          lbl.setAttribute("fill", "#1677ff");
          lbl.setAttribute("font-weight", "bold");
          lbl.textContent = e.type.length > 12 ? e.type.slice(0, 12) + "…" : e.type;
          g.appendChild(lbl);
        }
      }

      // nodes
      for (const n of nodeList) {
        const p = st.nodes.get(n.name);
        if (!p) continue;
        const isHover = st.hover === n.name;
        const radius = 12 + Math.min(10, n.degree || 0) * 1.1;
        const group = document.createElementNS(ns, "g");
        group.setAttribute("transform", `translate(${p.x},${p.y})`);
        group.style.cursor = "pointer";
        group.addEventListener("mouseenter", () => {
          st.hover = n.name;
          render();
        });
        group.addEventListener("mouseleave", () => {
          st.hover = "";
          render();
        });
        group.addEventListener("click", (ev) => {
          ev.stopPropagation();
          selectNode(n.name);
        });
        group.addEventListener("dblclick", (ev) => {
          ev.stopPropagation();
          focusEgo(n.name);
        });

        const circle = document.createElementNS(ns, "circle");
        circle.setAttribute("r", String(radius));
        circle.setAttribute("fill", entityColor(n.entity_type));
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
        label.textContent = n.name.length > 10 ? n.name.slice(0, 10) + "…" : n.name;
        group.appendChild(label);
        g.appendChild(group);
      }
    };

    // 拖拽 / 缩放 / 平移（同 WikiGraphView）
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, filterTypes, center]);

  // Neo4j 未配置/不可用时的空态
  if (health && !health.available) {
    return (
      <Card title="Neo4j 知识图谱" extra={<Button icon={<ReloadOutlined />} onClick={() => void load(center)}>刷新</Button>}>
        <Empty
          description={
            <div>
              <Text type="secondary">
                {health.enabled
                  ? `Neo4j 不可达：${health.error || "请检查 kb-neo4j 容器"}` 
                  : "Neo4j 未配置（缺少 NEO4J_URI / NEO4J_PASSWORD，或容器未启动）"}
              </Text>
              <div style={{ marginTop: 8 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  配置与启动见 deploy/README-parsers.md / docker-compose.yml 的 neo4j 服务
                </Text>
              </div>
            </div>
          }
          style={{ paddingTop: 60 }}
        />
      </Card>
    );
  }

  return (
    <Card
      title={
        <Space>
          <ApartmentOutlined /> Neo4j 知识图谱
          {center ? <Tag color="blue">{`邻域: ${center}`}</Tag> : <Tag color="green">全库视图</Tag>}
        </Space>
      }
      extra={
        <Space wrap>
          {stats && stats.entity_types.length > 0 ? (
            <Space wrap size={4}>
              {stats.entity_types.map((t) => (
                <Tag
                  key={t.type}
                  color={filterTypes.has(t.type) ? entityColor(t.type) : "default"}
                  style={{ cursor: "pointer" }}
                  onClick={() => toggleType(t.type)}
                >
                  {entityLabel(t.type)} {t.count}
                </Tag>
              ))}
            </Space>
          ) : (
            <Text type="secondary">暂无实体类型</Text>
          )}
          <Select
            style={{ width: 180 }}
            placeholder="搜索节点"
            showSearch
            filterOption={(input, opt) => (opt?.label || "").includes(input)}
            options={searchOptions}
            value={undefined}
            onChange={(v: string) => void selectNode(v)}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load(center)}>
            刷新
          </Button>
          <Button icon={<ExportOutlined />} onClick={toOverview}>
            全库
          </Button>
        </Space>
      }
    >
      <div style={{ display: "flex", gap: 16 }}>
        <div style={{ flex: 1, position: "relative", height: 560 }}>
          {loading ? (
            <div style={{ textAlign: "center", padding: 60 }}>
              <Spin />
            </div>
          ) : !data || data.nodes.length === 0 ? (
            <Empty
              description={
                <span>
                  暂无实体图谱
                  <br />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    运行「图谱构建」任务（KbGraphBuildTask）后生成
                  </Text>
                </span>
              }
              style={{ paddingTop: 60 }}
            />
          ) : (
            <svg
              ref={svgRef}
              width="100%"
              height="100%"
              style={{ background: "#fafafa", borderRadius: 6, cursor: "grab" }}
            />
          )}
          <div style={{ position: "absolute", bottom: 8, left: 12, fontSize: 12, color: "#999" }}>
            拖拽固定 · 滚轮缩放 · 空白平移 · 单击详情 · 双击下钻邻域（悬停显示关系类型）
          </div>
        </div>
        {stats && (
          <div style={{ width: 170, flexShrink: 0 }}>
            <Card size="small" title="图规模" style={{ marginBottom: 8 }}>
              <Statistic title="节点" value={stats.nodes} />
              <Statistic title="关系" value={stats.edges} style={{ marginTop: 8 }} />
            </Card>
          </div>
        )}
      </div>

      <Drawer
        title={selected?.name || "实体详情"}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={460}
        extra={
          selected && (
            <Space>
              <Button size="small" onClick={() => focusEgo(selected.name)}>
                邻域下钻
              </Button>
              <Button size="small" type="primary" onClick={() => void load(center)}>
                刷新
              </Button>
            </Space>
          )
        }
      >
        {selected && (
          <div>
            <Tag color={entityColor(selected.entity_type)}>{entityLabel(selected.entity_type)}</Tag>
            <Tag>关联 {selected.degree}</Tag>
            {selected.description && (
              <div style={{ marginTop: 8, color: "#555", whiteSpace: "pre-wrap" }}>{selected.description}</div>
            )}
            {selected.chunks && selected.chunks.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  来源 chunk：{selected.chunks.length} 个
                </Text>
              </div>
            )}
          </div>
        )}
      </Drawer>
    </Card>
  );
}