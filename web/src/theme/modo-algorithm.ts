import { theme } from 'antd';
import type { MapToken, SeedToken } from 'antd/es/theme/interface';

// MODO Design Palette System
// Based on provided Figma Tokens
// Arrays are 0-indexed, mapping to Ant Design Levels 1-10
export const modoPalette = {
    // color-modo-1 to 10 (Brand Blue / Primary)
    primary: [
        '#E8F3FF', '#BED6F5', '#97B9EB', '#739CE2', '#517ED8',
        '#3261CE', '#2045AE', '#112E8D', '#071B6D', '#000D4D'
    ],
    // color-green-1 to 10 (Success)
    success: [
        '#E8FFF0', '#C6F2D7', '#A6E4C0', '#88D7AC', '#6EC99A',
        '#55BC8A', '#36A071', '#1E845A', '#0C6845', '#004D32'
    ],
    // color-orange-1 to 10 (Warning)
    warning: [
        '#FFFAE8', '#FDEEBF', '#FBDF97', '#F9CE70', '#F7BA49',
        '#F5A623', '#CB7F16', '#A15E0B', '#773F04', '#4D2500'
    ],
    // color-red-1 to 10 (Error)
    error: [
        '#FFEDE8', '#F3C6BD', '#E79F94', '#DB7A6F', '#CF564E',
        '#C3342F', '#A51E1E', '#881014', '#6A060D', '#4D0008'
    ],
    // color-gray-1 to 10 (Neutral)
    neutral: [
        '#F9FBFD', '#EFF4F9', '#E3E9EF', '#B3C0CC', '#95A6BA',
        '#79879C', '#5E708A', '#4D5E7D', '#35435E', '#242E43'
    ],
    // Auxiliary Colors

    // color-purple-1 to 10
    purple: [
        '#F3E8FF', '#D9C4F3', '#BFA2E8', '#A582DC', '#8B65D1',
        '#724BC5', '#5130A7', '#361A89', '#1F0A6B', '#0E004D'
    ],
    // color-cyan-1 to 10
    cyan: [
        '#E8FFFA', '#BFF4E9', '#98E9DC', '#74DFD0', '#53D4C7',
        '#35C9C0', '#22AAA5', '#128B8B', '#07686C', '#00424D'
    ],
    // color-blue-1 to 10 (Auxiliary Blue)
    blue: [
        '#E8FCFF', '#BEEBF5', '#97D9EB', '#73C6E2', '#51B2D8',
        '#329DCE', '#207CAE', '#115E8D', '#07426D', '#002A4D'
    ],
    // color-gold-1 to 10
    gold: [
        '#FFFEE8', '#FDF8BF', '#FBEF97', '#F9E470', '#F7D749',
        '#F5C723', '#CB9E16', '#A1760B', '#775204', '#4D2D00'
    ],
    // color-yellow-1 to 10
    yellow: [
        '#FCFFE8', '#F7FCCD', '#F4F9B3', '#F3F69A', '#F3F380',
        '#F0EB68', '#C7BE41', '#9E9222', '#75670D', '#4D3800'
    ],
    // color-magenta-1 to 10
    magenta: [
        '#FFE8F4', '#F6C6E0', '#EEA6CF', '#E588C0', '#DD6CB3',
        '#D451A8', '#B2338C', '#901C71', '#6E0B57', '#4D0034'
    ],
    // color-pinkpurple-1 to 10 (Auxiliary Pink)
    pink: [
        '#FDE8FF', '#EFC2F6', '#DF9DED', '#CF7BE3', '#BD5BDA',
        '#AB3ED1', '#8727B0', '#66158F', '#48086E', '#42004D'
    ],
    // color-orangered-1 to 10 (Volcano equivalent?)
    volcano: [ // Mapping OrangeRed to Volcano
        '#FFF3E8', '#F9DAC0', '#F3BF99', '#EEA374', '#E88651',
        '#E2682F', '#BD4B1D', '#973210', '#721D06', '#4D0E00'
    ],
    // color-lime-1 to 10
    lime: [
        '#F9FFE8', '#E4F2C6', '#CFE4A6', '#BAD788', '#A5C96E',
        '#8FBC55', '#6FA036', '#51841E', '#37680C', '#2A4D00'
    ],
    // color-orange-1 to 10 (Explicit generic orange, same as warning)
    orange: [
        '#FFFAE8', '#FDEEBF', '#FBDF97', '#F9CE70', '#F7BA49',
        '#F5A623', '#CB7F16', '#A15E0B', '#773F04', '#4D2500'
    ],
    // geekblue (Mapped to MODO Primary)
    geekblue: [
        '#E8F3FF', '#BED6F5', '#97B9EB', '#739CE2', '#517ED8',
        '#3261CE', '#2045AE', '#112E8D', '#071B6D', '#000D4D'
    ],
    // green (Copy of success)
    green: [
        '#E8FFF0', '#C6F2D7', '#A6E4C0', '#88D7AC', '#6EC99A',
        '#55BC8A', '#36A071', '#1E845A', '#0C6845', '#004D32'
    ],
    // red (Copy of error)
    red: [
        '#FFEDE8', '#F3C6BD', '#E79F94', '#DB7A6F', '#CF564E',
        '#C3342F', '#A51E1E', '#881014', '#6A060D', '#4D0008'
    ]
};

// Helper to get color by level (1-10)
const getPaletteColor = (type: keyof typeof modoPalette, level: number) => {
    return modoPalette[type][level - 1];
};

/**
 * MODO Custom Algorithm
 * Overrides Ant Design's default generation with MODO's exact palette values.
 */
export const modoAlgorithm = (seed: SeedToken, map?: MapToken): MapToken => {
    // 1. Generate default tokens as baseline
    const mergeMapToken = theme.defaultAlgorithm(seed);

    // 2. Override Functional Colors with MODO Palette

    // --- Primary (Modo Blue) ---
    mergeMapToken.colorPrimaryBg = getPaletteColor('primary', 1);
    mergeMapToken.colorPrimaryBgHover = getPaletteColor('primary', 2);
    mergeMapToken.colorPrimaryBorder = getPaletteColor('primary', 3);
    mergeMapToken.colorPrimaryBorderHover = getPaletteColor('primary', 4);
    mergeMapToken.colorPrimaryHover = getPaletteColor('primary', 5);
    mergeMapToken.colorPrimary = getPaletteColor('primary', 6);
    mergeMapToken.colorPrimaryActive = getPaletteColor('primary', 7);
    mergeMapToken.colorPrimaryTextHover = getPaletteColor('primary', 8);
    mergeMapToken.colorPrimaryText = getPaletteColor('primary', 9);
    mergeMapToken.colorPrimaryTextActive = getPaletteColor('primary', 10);

    // --- Success (Green) ---
    mergeMapToken.colorSuccessBg = getPaletteColor('success', 1);
    mergeMapToken.colorSuccessBgHover = getPaletteColor('success', 2);
    mergeMapToken.colorSuccessBorder = getPaletteColor('success', 3);
    mergeMapToken.colorSuccessBorderHover = getPaletteColor('success', 4);
    mergeMapToken.colorSuccessHover = getPaletteColor('success', 5);
    mergeMapToken.colorSuccess = getPaletteColor('success', 6);
    mergeMapToken.colorSuccessActive = getPaletteColor('success', 7);
    mergeMapToken.colorSuccessTextHover = getPaletteColor('success', 8);
    mergeMapToken.colorSuccessText = getPaletteColor('success', 9);
    mergeMapToken.colorSuccessTextActive = getPaletteColor('success', 10);

    // Ant Design v5 uses colorBgContainer for content background
    mergeMapToken.colorBgContainer = '#FFFFFF';
    (mergeMapToken as any).colorFillAlter = getPaletteColor('neutral', 2); // #EFF4F9 (Used for Tree Switcher Bg)

    // --- Warning (Orange) ---
    mergeMapToken.colorWarningBg = getPaletteColor('warning', 1);
    mergeMapToken.colorWarningBgHover = getPaletteColor('warning', 2);
    mergeMapToken.colorWarningBorder = getPaletteColor('warning', 3);
    mergeMapToken.colorWarningBorderHover = getPaletteColor('warning', 4);
    mergeMapToken.colorWarningHover = getPaletteColor('warning', 5);
    mergeMapToken.colorWarning = getPaletteColor('warning', 6);
    mergeMapToken.colorWarningActive = getPaletteColor('warning', 7);
    mergeMapToken.colorWarningTextHover = getPaletteColor('warning', 8);
    mergeMapToken.colorWarningText = getPaletteColor('warning', 9);
    mergeMapToken.colorWarningTextActive = getPaletteColor('warning', 10);

    // --- Error (Red) ---
    mergeMapToken.colorErrorBg = getPaletteColor('error', 1);
    mergeMapToken.colorErrorBgHover = getPaletteColor('error', 2);
    mergeMapToken.colorErrorBorder = getPaletteColor('error', 3);
    mergeMapToken.colorErrorBorderHover = getPaletteColor('error', 4);
    mergeMapToken.colorErrorHover = getPaletteColor('error', 5);
    mergeMapToken.colorError = getPaletteColor('error', 6);
    mergeMapToken.colorErrorActive = getPaletteColor('error', 7);
    mergeMapToken.colorErrorTextHover = getPaletteColor('error', 8);
    mergeMapToken.colorErrorText = getPaletteColor('error', 9);
    mergeMapToken.colorErrorTextActive = getPaletteColor('error', 10);

    // --- Neutral / Gray ---
    mergeMapToken.colorBgLayout = getPaletteColor('neutral', 1);

    // --- 3. Override Preset Palettes (Green, Orange, Red, etc.) ---
    // This allows AntD components using 'color="green"' to pick up MODO's Green automatically.
    const presetMap: Record<string, string[]> = {
        green: modoPalette.green,
        orange: modoPalette.orange,
        red: modoPalette.red,
        blue: modoPalette.blue,
        purple: modoPalette.purple,
        cyan: modoPalette.cyan,
        gold: modoPalette.gold,
        yellow: modoPalette.yellow,
        lime: modoPalette.lime,
        magenta: modoPalette.magenta,
        geekblue: modoPalette.geekblue,
        volcano: modoPalette.volcano,
        pink: modoPalette.pink, // pink is not a standard AntD preset, but useful
    };

    Object.entries(presetMap).forEach(([colorName, colorScale]) => {
        // Set Primary Token (e.g. token.green)
        (mergeMapToken as any)[colorName] = colorScale[5]; // Level 6

        // Set Palette Tokens (e.g. token['green-1'] ... token['green-10'])
        // Also token['green1'] ... token['green10'] for robustness
        colorScale.forEach((val, idx) => {
            const level = idx + 1;
            (mergeMapToken as any)[`${colorName}-${level}`] = val;
            (mergeMapToken as any)[`${colorName}${level}`] = val;
        });
    });

    return mergeMapToken;
};
