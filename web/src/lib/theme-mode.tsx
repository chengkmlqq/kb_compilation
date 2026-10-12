'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';

/**
 * 全局深色/亮色主题模式（2026-10-12 对齐 WeKnora `html[theme-mode="dark"]`）。
 *
 * 机制与 WeKnora 一致的部分：
 *  - 模式写入 <html data-theme="dark|light">，globals.css 以该属性挂暗色变量覆盖；
 *  - localStorage 持久化（WeKnora 是 localStorage key theme-mode）；
 *  - 根布局内联脚本在首帧前同步设置，避免刷新时白屏闪烁（WeKnora 同样做首帧同步）。
 *
 * 与 WeKnora 的差异（有意为之）：WeKnora 是全站 TDesign 变量化，kb 目前仅
 * 问答工作台的硬编码色完成了 CSS 变量化；其余页面的组件层由 antd token
 * （darkAlgorithm）自动适配，硬编码色属"逐页跟进"状态。
 */

export type ThemeMode = 'light' | 'dark';

const STORAGE_KEY = 'kb-theme-mode';

interface ThemeModeCtx {
  mode: ThemeMode;
  toggle: () => void;
}

const ThemeModeContext = createContext<ThemeModeCtx>({
  mode: 'light',
  toggle: () => {},
});

/** 首帧前同步脚本（内联进 <head>），避免刷新时暗色用户看到白闪。 */
export const themeInitScript = `(function(){try{var m=localStorage.getItem('${STORAGE_KEY}');if(m==='dark'||(!m&&window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)){document.documentElement.dataset.theme='dark';}else{document.documentElement.dataset.theme='light';}}catch(e){}})();`;

export function ThemeModeProvider({ children }: { children: ReactNode }) {
  // 客户端首帧直接读取防闪烁脚本已写入的 data-theme（服务端为 light）
  const [mode, setMode] = useState<ThemeMode>(() => {
    if (typeof window === 'undefined') return 'light';
    return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  });

  useEffect(() => {
    document.documentElement.dataset.theme = mode;
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // 隐私模式等不可用场景忽略
    }
  }, [mode]);

  const toggle = useCallback(() => {
    setMode((m) => (m === 'light' ? 'dark' : 'light'));
  }, []);

  return (
    <ThemeModeContext.Provider value={{ mode, toggle }}>
      {children}
    </ThemeModeContext.Provider>
  );
}

export function useThemeMode(): ThemeModeCtx {
  return useContext(ThemeModeContext);
}