"use client";

/**
 * 数据查询工作台外壳（对齐 ds datagrid/components/client.tsx）：
 *  - 垂直 PanelGroup：上部工作区（默认 65%）+ 下部 console（35%，consoleShow 时显示）
 *  - 上部水平 PanelGroup：左树（20%，可拖拽，含 34px 图标竖条 + Database 标题）+ 工作台（80%）
 *  - 底部 28px 状态栏（用户：系统管理员 / DataGrid Workspace）
 *
 * 配色：像素级对齐 ds 的 antd token（--color-bg-1=#fff / --color-fill-1=#fafafa /
 * --color-border-2=#e8eaed / --color-primary-6=#1677ff / text-1=rgba(0,0,0,.88) 等）。
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
    <div className="datagrid-shell h-full min-h-0 w-full overflow-hidden bg-[#f5f7fa] text-[rgba(0,0,0,0.88)]">
      <div
        className="flex h-full min-h-0 flex-col overflow-hidden border border-[#e8eaed] bg-[#f5f7fa]"
        style={{ margin: 8, borderRadius: 4 }}
      >
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <PanelGroup orientation="vertical" className="h-full min-h-0 w-full">
            <Panel defaultSize={consoleShow ? 65 : 100} minSize={20}>
              <div ref={hWrapRef} className="h-full w-full min-w-0">
                <PanelGroup key={`h-${key}-${leftSize}`} orientation="horizontal" className="h-full w-full min-w-0">
                  {/* 左：图标竖条 + Database 树 */}
                  <Panel defaultSize={leftSize} minSize={12}>
                    <div className="flex h-full w-full overflow-hidden bg-[#fafafa]">
                      <div
                        className="flex w-[34px] shrink-0 flex-col items-center justify-between border-r border-[#e8eaed] bg-[#fafafa] py-3"
                      >
                        <div className="flex flex-col items-center gap-3">
                          <div
                            className="flex h-7 w-7 items-center justify-center text-[#1677ff]"
                            style={{ fontSize: 14 }}
                          >
                            <DatabaseOutlined />
                          </div>
                          <span
                            className="select-none text-[13px] font-semibold tracking-[0.22em] text-[rgba(0,0,0,0.88)]"
                            style={{ writingMode: "vertical-rl", textOrientation: "mixed" }}
                          >
                            Database
                          </span>
                        </div>
                        <button
                          className="flex h-7 w-7 items-center justify-center text-[rgba(0,0,0,0.45)] transition-colors hover:text-[#1677ff]"
                          title="设置"
                        >
                          <SettingOutlined style={{ fontSize: 14 }} />
                        </button>
                      </div>
                      <div className="flex h-full min-w-0 flex-1 flex-col bg-white">
                        <div
                          className="flex h-[37.5px] shrink-0 items-center border-b border-[#e8eaed] bg-[#fafafa] px-3 text-[15px] font-semibold text-[rgba(0,0,0,0.65)]"
                        >
                          Database
                        </div>
                        <div className="min-h-0 flex-1 flex-col overflow-hidden">
                          <DbTree data={dsList} />
                        </div>
                      </div>
                    </div>
                  </Panel>

                  <PanelResizeHandle className="group flex w-[10px] shrink-0 cursor-col-resize items-center justify-center border-x border-[#e8eaed] bg-[#f0f0f0] outline-none transition-colors hover:bg-[#e6f4ff]">
                    <div className="flex h-full w-[10px] items-center justify-center outline-none">
                      <div className="h-9 w-[4px] rounded-full bg-[#d9d9d9] transition-colors group-hover:bg-[#bae0ff]" />
                    </div>
                  </PanelResizeHandle>

                  {/* 右：工作台 */}
                  <Panel defaultSize={80} minSize={30}>
                    <div className="flex h-full w-full flex-col overflow-hidden bg-white">
                      <DbTool />
                    </div>
                  </Panel>
                </PanelGroup>
              </div>
            </Panel>

            {consoleShow && (
              <>
                <PanelResizeHandle className="group flex h-[10px] shrink-0 cursor-row-resize items-center justify-center border-y border-[#e8eaed] bg-[#f0f0f0] outline-none transition-colors hover:bg-[#e6f4ff]">
                  <div className="flex h-[10px] w-full items-center justify-center outline-none">
                    <div className="h-[4px] w-9 rounded-full bg-[#d9d9d9] transition-colors group-hover:bg-[#bae0ff]" />
                  </div>
                </PanelResizeHandle>
                <Panel defaultSize={35} minSize={15}>
                  <div className="flex h-full flex-col overflow-hidden bg-white">
                    <DbConsole />
                  </div>
                </Panel>
              </>
            )}
          </PanelGroup>
        </div>

        {/* 底部状态栏 */}
        <div
          className="flex h-[28px] shrink-0 items-center justify-between border-t border-[#e8eaed] bg-[#fafafa] px-4 text-[12px] text-[rgba(0,0,0,0.45)]"
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
        .datagrid-shell .ant-tabs-content-holder {
          flex: auto;
          min-height: 0;
          height: auto;
          background: transparent !important;
        }
        .datagrid-shell .ant-tabs-content,
        .datagrid-shell .ant-tabs-tabpane {
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