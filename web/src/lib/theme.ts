import type { ThemeConfig } from "antd";

/**
 * MODO design-system theme (aligned with data-synth):
 * - Primary: --color-modo-6 #3261CE
 * - Text:    --color-gray-10 #242E43
 * - Borders/rows: --color-gray-2 #EFF4F9
 * - Layout bg:    #F5F7FA
 * - Radius 4 (components), 6 (containers)
 *
 * NOTE: the authoritative MODO theme is modoThemeToken + modoAlgorithm in
 * src/theme/ (wired by antd-registry). This legacy export is kept for
 * call-sites that configure <ConfigProvider theme={modoTheme}> directly
 * (e.g. the Sider menu theme overrides); it stays in sync with the same
 * palette values.
 */
export const modoTheme: ThemeConfig = {
  token: {
    colorPrimary: "#3261CE",
    colorInfo: "#3261CE",
    colorLink: "#3261CE",
    colorText: "#242E43",
    colorTextSecondary: "#79879C",
    colorBgLayout: "#F9FBFD",
    colorBorder: "#E3E9EF",
    colorBorderSecondary: "#EFF4F9",
    borderRadius: 2,
    fontSize: 12,
    controlHeight: 28,
  },
  components: {
    Table: {
      headerBg: "#F9FBFD",
      headerColor: "#242E43",
      headerSplitColor: "transparent",
      cellPaddingBlock: 7,
      cellPaddingInline: 8,
      borderColor: "#E3E9EF",
      rowHoverBg: "#F9FBFD",
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
