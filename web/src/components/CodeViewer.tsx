"use client";

// 只读代码查看器：CodeMirror 6 高亮，按需动态加载（不进主包），主题跟随系统。

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import type { Extension } from "@codemirror/state";

const CodeMirror = dynamic(() => import("@uiw/react-codemirror"), { ssr: false });

// 语言包按扩展名动态加载（不进主 bundle）
const LANG_LOADERS: Record<string, () => Promise<Extension>> = {
  py: async () => (await import("@codemirror/lang-python")).python(),
  json: async () => (await import("@codemirror/lang-json")).json(),
  jsonc: async () => (await import("@codemirror/lang-json")).json(),
  yaml: async () => (await import("@codemirror/lang-yaml")).yaml(),
  yml: async () => (await import("@codemirror/lang-yaml")).yaml(),
  md: async () => (await import("@codemirror/lang-markdown")).markdown(),
  markdown: async () => (await import("@codemirror/lang-markdown")).markdown(),
  js: async () => (await import("@codemirror/lang-javascript")).javascript(),
  jsx: async () => (await import("@codemirror/lang-javascript")).javascript({ jsx: true }),
  ts: async () => (await import("@codemirror/lang-javascript")).javascript({ typescript: true }),
  tsx: async () => (await import("@codemirror/lang-javascript")).javascript({ jsx: true, typescript: true }),
  sh: async () => {
    const [lang, shell] = await Promise.all([
      import("@codemirror/language"),
      import("@codemirror/legacy-modes/mode/shell"),
    ]);
    return lang.StreamLanguage.define(shell.shell);
  },
  bash: async () => {
    const [lang, shell] = await Promise.all([
      import("@codemirror/language"),
      import("@codemirror/legacy-modes/mode/shell"),
    ]);
    return lang.StreamLanguage.define(shell.shell);
  },
  zsh: async () => {
    const [lang, shell] = await Promise.all([
      import("@codemirror/language"),
      import("@codemirror/legacy-modes/mode/shell"),
    ]);
    return lang.StreamLanguage.define(shell.shell);
  },
};

export function usePrefersDark(): boolean {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    setDark(mq.matches);
    const on = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return dark;
}

function extKeyOf(fileName: string): string {
  const i = fileName.lastIndexOf(".");
  return i >= 0 ? fileName.slice(i + 1).toLowerCase() : "";
}

export default function CodeViewer({
  value,
  fileName,
  height = 560,
}: {
  value: string;
  fileName: string;
  height?: number;
}) {
  const dark = usePrefersDark();
  const [langs, setLangs] = useState<Extension[]>([]);
  const [theme, setTheme] = useState<Extension | undefined>(undefined);
  const key = extKeyOf(fileName);

  useEffect(() => {
    let alive = true;
    const loader = LANG_LOADERS[key];
    if (loader) {
      loader()
        .then((e) => {
          if (alive) setLangs([e]);
        })
        .catch(() => {
          if (alive) setLangs([]);
        });
    } else {
      setLangs([]);
    }
    return () => {
      alive = false;
    };
  }, [key]);

  useEffect(() => {
    let alive = true;
    if (dark) {
      import("@codemirror/theme-one-dark")
        .then((m) => {
          if (alive) setTheme(m.oneDark);
        })
        .catch(() => {
          if (alive) setTheme(undefined);
        });
    } else {
      setTheme(undefined);
    }
    return () => {
      alive = false;
    };
  }, [dark]);

  return (
    <div
      style={{
        border: "1px solid rgba(0,0,0,0.08)",
        borderRadius: 6,
        overflow: "auto",
        background: dark ? "#1e1e1e" : "#fff",
      }}
    >
      <CodeMirror
        value={value}
        readOnly
        theme={theme}
        extensions={langs}
        height={`${height}px`}
        basicSetup={{
          lineNumbers: true,
          foldGutter: false,
          highlightActiveLine: false,
          highlightActiveLineGutter: false,
        }}
      />
    </div>
  );
}