import type { ThemeConfig } from 'antd';

// MODO Design System Mapping - Reference Specification
// Based on reference/COLOR.md and reference/FONT.md

export const modoThemeToken: ThemeConfig = {
    token: {
        // 1. 品牌色 (Brand Colors)
        colorPrimary: '#3261CE', // --color-primary-6
        colorPrimaryHover: '#517ED8', // --color-primary-5
        colorPrimaryActive: '#2045AE', // --color-primary-7

        // 2. 功能色 (Functional Colors)
        colorSuccess: '#55BC8A', // --color-success-6
        colorWarning: '#F5A623', // --color-warning-6
        colorError: '#C3342F',   // --color-danger-6
        colorInfo: '#3261CE',    // --color-primary-6
        colorLink: '#3261CE',    // --color-link-6
        colorLinkHover: '#517ED8', // --color-link-5

        // 3. 字体与圆角 (Typography & Radius)
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', sans-serif",
        fontSize: 12, // --font-size-base
        borderRadius: 2, // --border-radius-medium (Updated to 2px)

        // Control Heights (Button Sizes)
        controlHeight: 28, // Default (Small - 28px)
        controlHeightSM: 24, // Small (Mini - 24px)
        controlHeightLG: 32, // Large (Medium - 32px)

        // 4. 背景与边框 (Background & Border)
        colorBgBase: '#FFFFFF', // --color-bg-1
        colorBorder: '#E3E9EF', // --color-border-2
        colorText: '#242E43',   // --color-text-1
        colorTextSecondary: '#79879C', // --color-text-3
        colorTextPlaceholder: '#B3C0CC', // --color-text-4
        colorTextDisabled: '#B3C0CC',

        // 5. 填充色对齐 (Fill Colors)
        colorFillAlter: '#EFF4F9', // Neutral-2
        colorFillTertiary: '#EFF4F9', // Override default for Filled variants (Select, Input, etc.)
        controlInteractiveSize: 12, // Global Interactive Size (Checkbox, Radio)

        // 6. 阴影系统 (Shadow System)
        boxShadow: '0 2px 5px 0 rgba(36, 46, 67, 0.1)', // Tier 1 - Low
        boxShadowSecondary: '0 4px 10px 0 rgba(36, 46, 67, 0.1)', // Tier 2 - Middle
        boxShadowTertiary: '0 8px 20px 0 rgba(36, 46, 67, 0.1)', // Tier 3 - High
    },
    components: {
        Button: {
            paddingContentHorizontal: 16,
            defaultShadow: 'none',
            primaryShadow: 'none',
            dangerShadow: 'none',
            marginXS: 4
        },
        Input: {
            colorBgContainer: '#EFF4F9',
            activeBorderColor: '#3261CE',
            hoverBorderColor: 'transparent',
            colorBorder: 'transparent',
            colorBgContainerDisabled: '#EFF4F9', // fill-2
        },
        Select: {
            colorBgContainer: '#EFF4F9',
            colorBorder: 'transparent',
            hoverBorderColor: 'transparent',
            colorBgContainerDisabled: '#EFF4F9', // fill-2
        },
        Form: {
            labelColor: '#4D5E7D', // --color-text-2
        },
        Table: {
            headerBg: '#F9FBFD',
            rowHoverBg: '#F9FBFD',
            borderColor: '#E3E9EF',
        },
        Card: {
            actionsBg: '#F9FBFD',
            headerBg: '#FFFFFF',
        },
        Tree: {
            nodeSelectedBg: '#E8F3FF',
            nodeHoverBg: '#F9FBFD',
            controlHeight: 28,
            borderRadius: 2,
            fontSize: 12,
        },
        Checkbox: {
            controlInteractiveSize: 12,
            borderRadiusSM: 2,
        },
        Radio: {
            controlInteractiveSize: 12,
        },
        Steps: {
            iconSize: 24,
        }
    }
};

export default modoThemeToken;
