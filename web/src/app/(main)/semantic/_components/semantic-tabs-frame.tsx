"use client";

/**
 * 语义层 · 动态选项卡外壳（2026-10-21）
 *
 * 参考知识库页面（kbs/page.tsx）的动态选项卡模式，语义层四个菜单页
 * （元数据/文档库/知识本体/知识图谱）统一套用：
 * - 首个 tab = 页面标题（如「元数据」），不可关闭，承载各页主内容
 *   （KbDocsPane / KbWikiPane / WikiGraphView，经 children 渲染函数注入）；
 * - 动态打开的可关闭选项卡：
 *   - 文档详情（doc-<id>）：点击文档文件打开，承载 DocDetailPanel；
 *   - wiki 关系图（wiki-graph）：wiki 页「在图谱中查看」打开，承载
 *     WikiGraphView（ego 聚焦模式）。
 *
 * 与知识库列表页的区别：这里不涉及「多知识库分段」——语义层锁定单个
 * 「电信语义层」知识库，动态选项卡只承载「详情类」内容（文档详情/
 * wiki 关系图），对齐平台「查看/详情类用动态选项卡（勿抽屉）」惯例。
 */
import { useState, type ReactNode } from "react";
import { Spin, Typography } from "antd";
import ModoTabs from "@/components/biz/modo-tabs";
import DocDetailPanel from "@/components/DocDetailPanel";
import WikiGraphView from "@/components/WikiGraphView";
import { apiGetDocument, DocItem } from "@/lib/api";

export interface SemanticTabsHandlers {
  /** 打开文档详情选项卡（已存在则仅激活） */
  openDoc: (doc: DocItem) => void;
  /** 按 id 拉取文档并打开详情选项卡（wiki 来源文档跳转用） */
  openDocById: (docId: string) => void;
  /** 打开 wiki 关系图选项卡（slug 为空 = 全库概览） */
  openGraph: (slug?: string) => void;
  /** 文档列表刷新信号（详情页签内删除/重解析后 +1） */
  refreshTick: number;
  /** 文档列表变化回调（父级用于同步已打开详情页签的 doc 数据） */
  onDocsChange: (docs: DocItem[]) => void;
  /** 单个文档被删除后回调（关闭对应详情页签并刷新列表） */
  onDocRemoved: (docId: string) => void;
}

interface SemanticTabsFrameProps {
  /** 首个 tab 标题（页面标题，不可关闭） */
  title: string;
  /** 绑定的知识库 id（电信语义层） */
  kbId: string;
  /** 绑定加载中（显示 Spin） */
  loading: boolean;
  /** 绑定失败/未配置提示文案（kbId 为空时展示） */
  error: string;
  /** 额外就绪条件（如元数据根目录未配置时 false，显示 error） */
  ready?: boolean;
  /**
   * 主内容渲染函数：绑定就绪后调用，注入动态打开 handlers，
   * 供 KbDocsPane / KbWikiPane 等主 pane 绑定 onOpenDoc / onOpenGraph。
   */
  children: (handlers: SemanticTabsHandlers) => ReactNode;
}

export default function SemanticTabsFrame({
  title,
  kbId,
  loading,
  error,
  ready = true,
  children,
}: SemanticTabsFrameProps) {
  const [docTabs, setDocTabs] = useState<DocItem[]>([]);
  // wiki 关系图选项卡：仅一个（同一知识库），focusSlug 更新即刷新 ego 聚焦
  const [graphTab, setGraphTab] = useState<{ focusSlug?: string } | null>(null);
  const [activeTab, setActiveTab] = useState("home");
  const [refreshTick, setRefreshTick] = useState(0);

  if (loading) {
    return (
      <div style={{ padding: 10, height: "100%" }}>
        <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Spin tip="加载语义层绑定…" />
        </div>
      </div>
    );
  }

  if (!kbId || !ready) {
    return (
      <div style={{ padding: 10, height: "100%" }}>
        <div
          style={{
            height: "100%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#fff",
            borderRadius: 8,
          }}
        >
          <Typography.Text type="warning">{error || "未绑定知识库"}</Typography.Text>
        </div>
      </div>
    );
  }

  /** 打开文档详情选项卡：已存在则仅激活 */
  const openDoc = (doc: DocItem) => {
    setDocTabs((prev) => {
      if (prev.some((d) => d.id === doc.id)) return prev;
      return [...prev, doc];
    });
    setActiveTab(`doc-${doc.id}`);
  };

  /** wiki 来源文档跳转：按 id 拉取后打开详情选项卡 */
  const openDocById = (docId: string) => {
    void (async () => {
      try {
        const res = await apiGetDocument(kbId, docId);
        if (res.success && res.data) openDoc(res.data);
      } catch {
        /* 文档拉取失败：忽略（wiki 页内「来源文档」跳转不阻断浏览） */
      }
    })();
  };

  /** 关闭文档详情选项卡：若当前激活则回落到首页 */
  const closeDocTab = (docId: string) => {
    setDocTabs((prev) => prev.filter((d) => d.id !== docId));
    if (activeTab === `doc-${docId}`) setActiveTab("home");
  };

  /** 文档列表变化时同步已打开详情页签的 doc 数据（如自动摘要回填） */
  const syncDocTabs = (list: DocItem[]) => {
    setDocTabs((prev) => {
      let changed = false;
      const next = prev.map((d) => {
        const found = list.find((x) => x.id === d.id);
        if (found && found !== d) {
          changed = true;
          return found;
        }
        return d;
      });
      return changed ? next : prev;
    });
  };

  /** 详情页签内删除成功：关闭页签并刷新文档列表 */
  const removeDoc = (docId: string) => {
    setDocTabs((prev) => prev.filter((d) => d.id !== docId));
    setRefreshTick((v) => v + 1);
    if (activeTab === `doc-${docId}`) setActiveTab("home");
  };

  /** 打开 wiki 关系图选项卡（wiki 页「在图谱中查看」） */
  const openGraph = (slug?: string) => {
    setGraphTab((prev) => (prev && prev.focusSlug === slug ? prev : { focusSlug: slug }));
    setActiveTab("wiki-graph");
  };

  /** 关闭 wiki 关系图选项卡 */
  const closeGraphTab = () => {
    setGraphTab(null);
    if (activeTab === "wiki-graph") setActiveTab("home");
  };

  const handlers: SemanticTabsHandlers = {
    openDoc,
    openDocById,
    openGraph,
    refreshTick,
    onDocsChange: syncDocTabs,
    onDocRemoved: removeDoc,
  };

  return (
    // 对齐知识库页面外壳：满屏白底、ModoTabs 承载、外层定高不滚动
    <div
      className="kb-kbs-page"
      style={{
        height: "calc(100vh - var(--app-header-h, 48px))",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      <ModoTabs
        type="editable-card"
        hideAdd
        activeKey={activeTab}
        onChange={setActiveTab}
        onEdit={(key, action) => {
          if (action !== "remove") return;
          const k = String(key);
          if (k.startsWith("doc-")) closeDocTab(k.replace(/^doc-/, ""));
          else if (k === "wiki-graph") closeGraphTab();
        }}
        items={[
          {
            key: "home",
            label: title,
            closable: false,
            children: children(handlers),
          },
          ...docTabs.map((d) => ({
            key: `doc-${d.id}`,
            label: d.file_name,
            closable: true,
            children: (
              <DocDetailPanel
                kbId={kbId}
                doc={d}
                onDocReparsed={() => setRefreshTick((v) => v + 1)}
              />
            ),
          })),
          ...(graphTab
            ? [
                {
                  key: "wiki-graph",
                  label: "wiki 关系图",
                  closable: true,
                  children: <WikiGraphView kbId={kbId} focusSlug={graphTab.focusSlug} />,
                },
              ]
            : []),
        ]}
      />
    </div>
  );
}
