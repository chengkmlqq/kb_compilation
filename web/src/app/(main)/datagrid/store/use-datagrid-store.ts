"use client";

/**
 * 数据查询工作台 store（迁移 data-synth datagrid/store/use-datagrid-store.ts）。
 *
 * 三层 tab 状态机：
 *  - DbToolTabs  ：上方工作区多标签（schema=SQL 编辑器 / table=表结构，右键重命名、可关）
 *  - DbConsoleTabs：下方 console（每数据源一个 tab，内含 结果子标签 + 输出日志子标签）
 *  - consoleShow ：console 是否展开（首次执行 SQL 自动展开，对齐 ds）
 */
import { create } from "zustand";

export type TabType = "schema" | "table";

export interface BaseTab {
  id: string;
  type: TabType;
  label: string;
  index: number;
  dsName: string;
  dsSchema: string;
  dsType: string;
}

export interface SchemaTab extends BaseTab {
  type: "schema";
  runSql: string;
  limit: number;
}

export interface TableTab extends BaseTab {
  type: "table";
  tableName: string;
  /** table-info 结果（键值对数组），table-structure 概览卡渲染用 */
  general: Array<Record<string, unknown>>;
  /** 字段列表（columns 端点 rows） */
  columns: Array<Record<string, unknown>>;
  DDL: string;
}

export type DbToolTab = SchemaTab | TableTab;

export interface ResultTab {
  id: string;
  type: "result";
  label: string;
  tableName: string;
  runSql?: string;
  dsSchema: string;
  dsName: string;
  dsType: string;
  data: Array<Record<string, unknown>>;
  columns: string[];
  filter: { pageNum: number; pageSize: number; total: number };
}

export interface OutputTab {
  id: string;
  type: "output";
  label: string;
  logs: Array<{ dsType: string; code: string; execTime: string }>;
}

export interface DbConsoleTab {
  id: string;
  type: "db";
  label: string;
  dsName: string;
  dsSchema: string;
  tabActiveIndex: string | null;
  runNum: number;
  tabs: Array<ResultTab | OutputTab>;
}

interface DataGridStore {
  dbToolTabs: DbToolTab[];
  dbToolActiveTab: string | null;
  dbConsoleTabs: DbConsoleTab[];
  dbConsoleActiveTab: string | null;
  consoleShow: boolean;

  addDbToolTab: (tab: DbToolTab) => void;
  removeDbToolTab: (id: string) => void;
  setDbToolActiveTab: (id: string) => void;
  updateDbToolTab: (id: string, updates: Partial<DbToolTab>) => void;

  addDbConsoleTab: (tab: DbConsoleTab) => void;
  removeDbConsoleTab: (id: string) => void;
  setDbConsoleActiveTab: (id: string) => void;
  setConsoleShow: (show: boolean) => void;
  addResultTab: (dbTabId: string, result: ResultTab) => void;
  removeResultTab: (dbTabId: string, resultId: string) => void;
  setDbConsoleInnerActiveTab: (dbTabId: string, resultId: string) => void;
  addLog: (dbTabId: string, log: { dsType: string; code: string; execTime: string }) => void;
  clearLogs: (dbTabId: string) => void;
}

export const useDataGridStore = create<DataGridStore>((set) => ({
  dbToolTabs: [],
  dbToolActiveTab: null,
  dbConsoleTabs: [],
  dbConsoleActiveTab: null,
  consoleShow: false,

  addDbToolTab: (tab) =>
    set((s) => {
      const exists = s.dbToolTabs.find((t) => t.id === tab.id);
      if (exists) return { dbToolActiveTab: tab.id };
      return { dbToolTabs: [...s.dbToolTabs, tab], dbToolActiveTab: tab.id };
    }),

  removeDbToolTab: (id) =>
    set((s) => {
      const newTabs = s.dbToolTabs.filter((t) => t.id !== id);
      let active = s.dbToolActiveTab;
      if (s.dbToolActiveTab === id) {
        const idx = s.dbToolTabs.findIndex((t) => t.id === id);
        active = idx > 0 ? newTabs[idx - 1]?.id ?? null : newTabs[0]?.id ?? null;
      }
      return { dbToolTabs: newTabs, dbToolActiveTab: active };
    }),

  setDbToolActiveTab: (id) => set({ dbToolActiveTab: id }),
  updateDbToolTab: (id, updates) =>
    set((s) => ({
      dbToolTabs: s.dbToolTabs.map((t) => (t.id === id ? ({ ...t, ...updates } as DbToolTab) : t)),
    })),

  addDbConsoleTab: (tab) =>
    set((s) => {
      const exists = s.dbConsoleTabs.find((t) => t.id === tab.id);
      if (exists) return { dbConsoleActiveTab: tab.id, consoleShow: true };
      return {
        dbConsoleTabs: [...s.dbConsoleTabs, tab],
        dbConsoleActiveTab: tab.id,
        consoleShow: true,
      };
    }),

  removeDbConsoleTab: (id) =>
    set((s) => {
      const newTabs = s.dbConsoleTabs.filter((t) => t.id !== id);
      let active = s.dbConsoleActiveTab;
      if (s.dbConsoleActiveTab === id) {
        const idx = s.dbConsoleTabs.findIndex((t) => t.id === id);
        active = idx > 0 ? newTabs[idx - 1]?.id ?? null : newTabs[0]?.id ?? null;
      }
      return { dbConsoleTabs: newTabs, dbConsoleActiveTab: active };
    }),

  setDbConsoleActiveTab: (id) => set({ dbConsoleActiveTab: id }),
  setConsoleShow: (show) => set({ consoleShow: show }),

  addResultTab: (dbTabId, result) =>
    set((s) => ({
      dbConsoleTabs: s.dbConsoleTabs.map((tab) =>
        tab.id === dbTabId && tab.type === "db"
          ? { ...tab, tabs: [...tab.tabs, result], tabActiveIndex: result.id, runNum: tab.runNum + 1 }
          : tab,
      ),
    })),

  removeResultTab: (dbTabId, resultId) =>
    set((s) => ({
      dbConsoleTabs: s.dbConsoleTabs.map((tab) => {
        if (tab.id !== dbTabId || tab.type !== "db") return tab;
        const newTabs = tab.tabs.filter((t) => t.id !== resultId);
        let active = tab.tabActiveIndex;
        if (tab.tabActiveIndex === resultId) {
          const idx = tab.tabs.findIndex((t) => t.id === resultId);
          active = idx > 0 ? newTabs[idx - 1]?.id ?? null : newTabs[0]?.id ?? null;
        }
        return { ...tab, tabs: newTabs, tabActiveIndex: active };
      }),
    })),

  setDbConsoleInnerActiveTab: (dbTabId, resultId) =>
    set((s) => ({
      dbConsoleTabs: s.dbConsoleTabs.map((tab) =>
        tab.id === dbTabId && tab.type === "db" ? { ...tab, tabActiveIndex: resultId } : tab,
      ),
    })),

  addLog: (dbTabId, log) =>
    set((s) => ({
      dbConsoleTabs: s.dbConsoleTabs.map((tab) => {
        if (tab.id !== dbTabId || tab.type !== "db") return tab;
        return {
          ...tab,
          tabs: tab.tabs.map((t) =>
            t.type === "output" ? { ...t, logs: [...t.logs, log] } : t,
          ),
        };
      }),
    })),

  clearLogs: (dbTabId) =>
    set((s) => ({
      dbConsoleTabs: s.dbConsoleTabs.map((tab) => {
        if (tab.id !== dbTabId || tab.type !== "db") return tab;
        return {
          ...tab,
          tabs: tab.tabs.map((t) => (t.type === "output" ? { ...t, logs: [] } : t)),
        };
      }),
    })),
}));