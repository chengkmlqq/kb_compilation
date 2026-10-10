"use client";

/**
 * 数据查询（DataGrid）页 —— 像素级对齐 data-synth datagrid 工作台。
 *
 * 结构（对齐 ds page.tsx + components/client.tsx）：
 *  垂直可拖拽分栏（工作区 / console）+ 水平可拖拽分栏（Database 树 / 工作台）
 *  + 34px 图标竖条 + 底部状态栏。
 *
 * 工作台模型：左侧目录树（数据源→表/视图/函数/过程/序列）双击打开多标签，
 * SQL 编辑器（CodeMirror）执行 → 下部 console 展示结果网格（Handsontable）+ 输出日志。
 */
import DataGridClient from "./components/client";

export default function DataGridPage() {
  return (
    <div className="h-full min-h-0 w-full overflow-hidden bg-[#f5f7fa]">
      <DataGridClient />
    </div>
  );
}