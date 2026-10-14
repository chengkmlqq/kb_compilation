"use client";

/**
 * 数据查询工作台外壳（对齐 ds datagrid/components/client.tsx）：
 *  - 垂直 PanelGroup：上部工作区（默认 65%）+ 下部 console（35%，consoleShow 时显示）
 *  - 上部水平 PanelGroup：左树（20%，可拖拽，含 34px 图标竖条 + Database 标题）+ 工作台（80%）
 *  - 底部 28px 状态栏（用户：系统管理员 / DataGrid Workspace）
 *
 * 配色：像素级对齐 ds 的 antd token（--color-bg-1=#fff / --color-fill-1=#fafafa /
 * --color-border-2=#e8eaed / --color-primary-6=#1677ff / text-1=rgba(0,0,0,.88) 等）。
 *
 * 注（2026-10-11）：原实现用的是 tailwind 工具类（h-full / flex / bg-[#...]），但本项目
 * 未接入 tailwind，这些类全部无效 → 高度链断裂、布局塌陷。已全部改为内联 style；
 * hover/transition 等内联表达不了的改用 globals.css 中的 .datagrid-icon-btn /
 * .datagrid-resize-handle 类。
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { App } from "antd";
import { DatabaseOutlined, SettingOutlined } from "@ant-design/icons";
import { Group as PanelGroup, Panel, Separator as PanelResizeHandle } from "react-resizable-panels";
import DbTree from "./db-tree";
import DbTool from "./db-tool";
import DbConsole from "./db-console";
import { useDataGridStore } from "../store/use-datagrid-store";
import { apiDatagridDatasources, DatagridDsItem } from "@/lib/api";

export default function DataGridClient() {
  const { message } = App.useApp();
  const { consoleShow } = useDataGridStore();
  const [dsList, setDsList] = useState<DatagridDsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const hWrapRef = React.useRef<HTMLDivElement | null>(null);
  const [leftSize, setLeftSize] = useState(20);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const res = await apiDatagridDatasources();
        if (res.success && res.data) setDsList(res.data);
        else message.error(res.message || "加载数据源失败");
      } finally {
        setLoading(false);
      }
    })();
  }, [message]);

  // 左树默认宽度 ~320px（百分比随容器宽度自适应，12%~45% 夹逼，对齐 ds）
  useEffect(() => {
    const update = () => {
      const w = hWrapRef.current?.clientWidth;
      if (!w || w <= 0) return;
      const pct = (320 / w) * 100;
      setLeftSize(Math.min(45, Math.max(12, Number(pct.toFixed(3)))));
    };
    update();
    const ro = new ResizeObserver(update);
    if (hWrapRef.current) ro.observe(hWrapRef.current);
    return () => ro.disconnect();
  }, []);

  const [key, setKey] = React.useState(0);
  useEffect(() => {
    // 容器尺寸变化时重算左树默认宽度（Panel key 变化触发重新布局）
    setKey((k) => k + 1);
  }, [leftSize]);

  return (
    <div
      className="datagrid-shell datagrid-page"
      style={{
        height: "100%",
        minHeight: 0,
        width: "100%",
        overflow: "hidden",
        background: "#f5f7fa",
        color: "rgba(0,0,0,0.88)",
        display: "flex",
        flexDirection: "column",
        padding: 8,
      }}
    >
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          border: "1px solid #e8eaed",
          borderRadius: 4,
          background: "#f5f7fa",
        }}
      >
        <div style={{ display: "flex", minHeight: 0, flex: 1, overflow: "hidden" }}>
          <PanelGroup orientation="vertical" style={{ height: "100%", minHeight: 0, width: "100%" }}>
            <Panel defaultSize={consoleShow ? "65%" : "100%"} minSize="20%">
              <div ref={hWrapRef} style={{ height: "100%", width: "100%", minWidth: 0 }}>
                <PanelGroup
                  key={`h-${key}-${leftSize}`}
                  orientation="horizontal"
                  style={{ height: "100%", width: "100%", minWidth: 0 }}
                >
                  {/* 左：图标竖条 + Database 树 */}
                  <Panel defaultSize={`${leftSize}%`} minSize="12%">
                    <div style={{ display: "flex", height: "100%", width: "100%", overflow: "hidden", background: "#fafafa" }}>
                      <div
                        style={{
                          display: "flex",
                          width: 34,
                          flexShrink: 0,
                          flexDirection: "column",
                          alignItems: "center",
                          justifyContent: "space-between",
                          borderRight: "1px solid #e8eaed",
                          background: "#fafafa",
                          paddingTop: 12,
                          paddingBottom: 12,
                        }}
                      >
                        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
                          <div
                            style={{
                              display: "flex",
                              height: 28,
                              width: 28,
                              alignItems: "center",
                              justifyContent: "center",
                              color: "#1677ff",
                              fontSize: 14,
                            }}
                          >
                            <DatabaseOutlined />
                          </div>
                          <span
                            style={{
                              userSelect: "none",
                              fontSize: 13,
                              fontWeight: 600,
                              letterSpacing: "0.22em",
                              color: "rgba(0,0,0,0.88)",
                              writingMode: "vertical-rl",
                              textOrientation: "mixed",
                            }}
                          >
                            Database
                          </span>
                        </div>
                        <button className="datagrid-icon-btn datagrid-icon-btn--muted" title="设置">
                          <SettingOutlined style={{ fontSize: 14 }} />
                        </button>
                      </div>
                      <div style={{ display: "flex", height: "100%", minWidth: 0, flex: 1, flexDirection: "column", background: "#fff" }}>
                        <div
                          style={{
                            display: "flex",
                            height: 37.5,
                            flexShrink: 0,
                            alignItems: "center",
                            borderBottom: "1px solid #e8eaed",
                            background: "#fafafa",
                            paddingLeft: 12,
                            paddingRight: 12,
                            fontSize: 15,
                            fontWeight: 600,
                            color: "rgba(0,0,0,0.65)",
                          }}
                        >
                          Database
                        </div>
                        <div style={{ minHeight: 0, flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
                          <DbTree data={dsList} />
                        </div>
                      </div>
                    </div>
                  </Panel>

                  <PanelResizeHandle className="datagrid-resize-handle datagrid-resize-handle--h">
                    <div className="datagrid-resize-handle__bar" />
                  </PanelResizeHandle>

                  {/* 右：工作台 */}
                  <Panel defaultSize="80%" minSize="30%">
                    <div style={{ display: "flex", height: "100%", width: "100%", flexDirection: "column", overflow: "hidden", background: "#fff" }}>
                      <DbTool />
                    </div>
                  </Panel>
                </PanelGroup>
              </div>
            </Panel>

            {consoleShow && (
              <>
                <PanelResizeHandle className="datagrid-resize-handle datagrid-resize-handle--v">
                  <div className="datagrid-resize-handle__bar" />
                </PanelResizeHandle>
                <Panel defaultSize="35%" minSize="15%">
                  <div style={{ display: "flex", height: "100%", flexDirection: "column", overflow: "hidden", background: "#fff" }}>
                    <DbConsole />
                  </div>
                </Panel>
              </>
            )}
          </PanelGroup>
        </div>

        {/* 底部状态栏 */}
        <div
          style={{
            display: "flex",
            height: 28,
            flexShrink: 0,
            alignItems: "center",
            justifyContent: "space-between",
            borderTop: "1px solid #e8eaed",
            background: "#fafafa",
            paddingLeft: 16,
            paddingRight: 16,
            fontSize: 12,
            color: "rgba(0,0,0,0.45)",
          }}
        >
          <span>用户：系统管理员</span>
          <span>DataGrid Workspace</span>
        </div>
      </div>

      <style jsx global>{`
        .datagrid-shell .ant-tabs {
          display: flex;
          flex-direction: column;
        }
        .datagrid-shell .ant-tabs-nav {
          margin: 0 !important;
          flex-shrink: 0;
        }
        /* antd v6（@rc-component/tabs 15+）DOM 链：body-holder → body → tabpane(.ant-tabs-content)。
           旧 v5 选择器（.ant-tabs-content-holder /.ant-tabs-tabpane）在 v6 中已不存在 → 全部失效，
           pane 高度塌为内容高（SQL 编辑器不占满）。补齐 body 高度链即可让 pane 内 flex:1 生效。 */
        .datagrid-shell .ant-tabs-body-holder {
          flex: auto;
          min-height: 0;
        }
        .datagrid-shell .ant-tabs-body {
          height: 100%;
          min-height: 0;
        }
        .datagrid-shell .ant-tabs-content {
          height: 100%;
          min-height: 0;
          overflow: hidden;
        }
        .datagrid-shell .ant-tabs-card.ant-tabs-top > .ant-tabs-nav .ant-tabs-tab {
          border-color: #e8eaed !important;
          background: #fafafa !important;
          border-radius: 0 !important;
          color: rgba(0, 0, 0, 0.65) !important;
          padding: 7px 14px !important;
        }
        .datagrid-shell .ant-tabs-card.ant-tabs-top > .ant-tabs-nav .ant-tabs-tab-active {
          background: #fff !important;
          color: #1677ff !important;
          box-shadow: inset 0 2px 0 #1677ff !important;
        }
        .datagrid-shell .datagrid-tool-tabs > .ant-tabs-nav,
        .datagrid-shell .datagrid-console-tabs > .ant-tabs-nav,
        .datagrid-shell .datagrid-console-inner-tabs > .ant-tabs-nav {
          background: #fafafa !important;
          padding: 0 8px !important;
        }
        .datagrid-shell .datagrid-console-inner-tabs .ant-tabs-tab {
          padding: 6px 12px !important;
          border-radius: 0 !important;
        }
      `}</style>
    </div>
  );
}
