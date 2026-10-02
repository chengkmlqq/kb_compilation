import type { ThemeConfig } from "antd";

/**
 * MODO design-system theme (aligned with data-synth):
 * - Primary: --color-modo-6 #3261CE
 * - Text:    --color-gray-10 #242E43
 * - Borders/rows: --color-gray-2 #EFF4F9
 * - Layout bg:    #F5F7FA
 * - Radius 4 (components), 6 (containers)
 */
export const modoTheme: ThemeConfig = {
  token: {
    colorPrimary: "#3261CE",
    colorInfo: "#3261CE",
    colorLink: "#3261CE",
    colorText: "#242E43",
    colorTextSecondary: "#5E708A",
    colorBgLayout: "#F5F7FA",
    colorBorder: "#E3E9EF",
    colorBorderSecondary: "#EFF4F9",
    borderRadius: 4,
    fontSize: 14,
    controlHeight: 32,
  },
  components: {
    Table: {
      headerBg: "#ffffff",
      headerColor: "#242E43",
      headerSplitColor: "transparent",
      cellPaddingBlock: 7,
      cellPaddingInline: 8,
      borderColor: "#EFF4F9",
      rowHoverBg: "#F5F7FA",
    },
    Card: {
      borderRadiusLG: 6,
      paddingLG: 16,
      headerBg: "#ffffff",
    },
    Modal: {
      borderRadiusLG: 6,
    },
    Tabs: {
      inkBarColor: "#3261CE",
      itemSelectedColor: "#3261CE",
    },
    Menu: {
      itemSelectedBg: "#EFF4F9",
      itemSelectedColor: "#242E43",
      itemHoverBg: "#EFF4F9",
      itemHoverColor: "#242E43",
    },
  },
};