/**
 * 自研轻量代码高亮（零外部依赖）—— 配色对齐 WeKnora hljs 亮色主题
 * （AgentStreamDisplay / chat-markdown.less 使用的 #282c34 深底 + One Dark 亮色系）。
 *
 * 设计约束（来自本仓库现状）：
 *  - 项目无 hljs / prism / marked 依赖（bun install 有历史 EACCES 问题），
 *    问答渲染器 MarkdownViewer 一贯「自研轻量、避免额外依赖」，此处保持一致。
 *  - 返回 flat token 流（text 内保留换行），由 MarkdownViewer 映射为
 *    <span className="kb-hl-*">；<pre> 默认 white-space: pre，换行天然保留。
 *  - 宁可漏着色，不可错着色：字符串/注释一旦误判会破坏代码可读性。
 */

/** token 类别 → CSS 类 kb-hl-<type>。 */
export type TokenType =
  | "plain"
  | "comment"
  | "string"
  | "keyword"
  | "number"
  | "function";

export interface CodeToken {
  type: TokenType;
  text: string;
}

/** 各语言关键字表（缺失语言回落 COMMON_KEYWORDS）。 */
const KEYWORDS: Record<string, string[]> = {
  js: [
    "const", "let", "var", "function", "return", "if", "else", "for", "while",
    "class", "extends", "implements", "new", "import", "export", "default",
    "from", "as", "try", "catch", "finally", "throw", "async", "await", "yield",
    "typeof", "instanceof", "in", "of", "this", "super", "delete", "void",
    "switch", "case", "break", "continue", "do", "static", "get", "set",
    "interface", "type", "enum", "public", "private", "protected", "readonly",
    "null", "undefined", "true", "false", "require", "module",
  ],
  python: [
    "def", "class", "if", "elif", "else", "for", "while", "return", "import",
    "from", "as", "with", "try", "except", "finally", "raise", "lambda", "pass",
    "break", "continue", "yield", "global", "nonlocal", "assert", "async",
    "await", "and", "or", "not", "in", "is", "None", "True", "False", "self",
    "print", "len", "range", "str", "int", "float", "dict", "list", "set",
  ],
  shell: [
    "if", "then", "else", "elif", "fi", "for", "while", "do", "done", "case",
    "esac", "function", "return", "echo", "export", "local", "set", "unset",
    "source", "cd", "pwd", "ls", "cat", "grep", "sed", "awk", "read", "shift",
    "exit", "sudo", "docker", "git", "curl", "mkdir", "rm", "cp", "mv", "chmod",
  ],
  sql: [
    "SELECT", "INSERT", "UPDATE", "DELETE", "FROM", "WHERE", "JOIN", "LEFT",
    "RIGHT", "INNER", "OUTER", "ON", "GROUP", "BY", "ORDER", "HAVING", "LIMIT",
    "OFFSET", "CREATE", "TABLE", "INDEX", "ALTER", "DROP", "ADD", "PRIMARY",
    "KEY", "FOREIGN", "REFERENCES", "VALUES", "INTO", "SET", "AS", "DISTINCT",
    "AND", "OR", "NOT", "NULL", "IS", "IN", "BETWEEN", "LIKE", "CASE", "WHEN",
    "THEN", "ELSE", "END", "UNION", "ALL", "EXISTS", "WITH", "COUNT", "SUM",
    "AVG", "DESC", "ASC",
  ],
  java: [
    "public", "private", "protected", "class", "interface", "extends",
    "implements", "static", "final", "void", "new", "return", "if", "else",
    "for", "while", "do", "switch", "case", "default", "break", "continue",
    "try", "catch", "finally", "throw", "throws", "import", "package", "this",
    "super", "abstract", "synchronized", "volatile", "transient", "instanceof",
    "null", "true", "false", "int", "long", "double", "float", "boolean",
    "String", "List", "Map",
  ],
  go: [
    "package", "import", "func", "return", "if", "else", "for", "range",
    "switch", "case", "default", "break", "continue", "go", "defer", "chan",
    "map", "struct", "interface", "type", "var", "const", "select", "fallthrough",
    "nil", "true", "false", "string", "int", "int64", "bool", "error",
  ],
  rust: [
    "fn", "let", "mut", "const", "static", "struct", "enum", "impl", "trait",
    "pub", "use", "mod", "match", "if", "else", "loop", "for", "while", "return",
    "self", "Self", "super", "crate", "where", "async", "await", "move", "ref",
    "dyn", "true", "false", "Some", "None", "Ok", "Err", "String", "Vec",
  ],
  c: [
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if", "inline",
    "int", "long", "register", "return", "short", "signed", "sizeof", "static",
    "struct", "switch", "typedef", "union", "unsigned", "void", "volatile",
    "while", "class", "namespace", "template", "public", "private", "protected",
    "virtual", "new", "delete", "nullptr", "true", "false", "string", "bool",
  ],
  json: ["true", "false", "null"],
  yaml: ["true", "false", "null", "yes", "no"],
  html: [],
  css: [],
  sql_like: [],
};

/** 未知语言共用的兜底词表（取最高频语法词）。 */
const COMMON_KEYWORDS = [
  "if", "else", "for", "while", "return", "function", "class", "const", "let",
  "var", "import", "from", "export", "default", "new", "try", "catch", "throw",
  "null", "true", "false", "None", "True", "False", "async", "await", "def",
  "select", "from", "where", "join", "group", "order", "by", "limit", "case",
  "when", "then", "else", "end", "public", "private", "static", "void", "int",
  "string", "bool",
];

/** 语言别名归一化（markdown ``` 围栏里模型写的写法很杂）。 */
const ALIASES: Record<string, string> = {
  javascript: "js", jsx: "js", mjs: "js", cjs: "js",
  typescript: "js", tsx: "js",
  python: "python", py: "python", python3: "python",
  bash: "shell", sh: "shell", zsh: "shell", shell: "shell", console: "shell",
  sql: "sql", mysql: "sql", pgsql: "sql", postgresql: "sql",
  golang: "go",
  rs: "rust",
  cpp: "c", "c++": "c", cxx: "c", cs: "c", csharp: "c",
  yml: "yaml",
  xml: "html", vue: "html", svelte: "html",
  scss: "css", less: "css",
  jsonc: "json",
};

/** 用 # / -- / ; 作注释的语言（行首或行内 #）。 */
const HASH_COMMENT = new Set(["python", "shell", "yaml"]);
const SQL_COMMENT = new Set(["sql"]);

/** 明确无语法高亮的语言。 */
const PLAIN_LANGS = new Set(["text", "txt", "plain", "plaintext", "output", "log"]);

function normalizeLang(lang: string): string {
  const raw = (lang || "").trim().toLowerCase();
  if (!raw) return "";
  if (PLAIN_LANGS.has(raw)) return "";
  return ALIASES[raw] || raw;
}

/** 该语言是否走高亮（无语言标识 / text → 不高亮）。 */
export function hasHighlight(lang: string): boolean {
  return normalizeLang(lang) !== "";
}

function keywordsFor(lang: string): string[] {
  const set = new Set<string>([...COMMON_KEYWORDS, ...(KEYWORDS[lang] || [])]);
  return Array.from(set);
}

/** 单行扫描：字符串 / 行注释 / 关键字 / 数字 / 函数调用。 */
function scanLine(line: string, kwRe: RegExp, out: CodeToken[]): void {
  const re = new RegExp(
    [
      // 单引号 / 双引号 / 反引号字符串（转义感知）
      `('(?:[^'\\\\\\n]|\\\\.)*'|"(?:[^"\\\\\\n]|\\\\.)*")`,
      `(\`(?:[^\`\\\\]|\\\\.)*\`)`,
      // 行注释
      `(//[^\\n]*)`,
      // 关键字（\\b 包裹，避免命中标识符片段）
      `(\\b(?:${kwRe.source})\\b)`,
      // 数字
      `(\\b\\d+(?:\\.\\d+)?\\b)`,
      // 函数调用（标识符后紧跟左括号）
      `([A-Za-z_$][\\w$]*(?=\\s*\\())`,
    ].join("|"),
    "g",
  );
  let last = 0;
  let m: RegExpExecArray | null;
  const pushPlain = (text: string) => {
    if (!text) return;
    const prev = out[out.length - 1];
    if (prev && prev.type === "plain") prev.text += text;
    else out.push({ type: "plain", text });
  };
  while ((m = re.exec(line)) !== null) {
    pushPlain(line.slice(last, m.index));
    const types: TokenType[] = ["string", "string", "comment", "keyword", "number", "function"];
    const gi = m.findIndex((g, idx) => idx > 0 && g !== undefined);
    out.push({ type: types[gi === -1 ? 0 : gi - 1], text: m[0] });
    last = m.index + m[0].length;
  }
  pushPlain(line.slice(last));
}

/** hash 系语言的行内 # 注释切分（避开含引号片段，保守处理）。 */
function splitHashComment(line: string): [string, string] | null {
  let inSingle = false;
  let inDouble = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === "'" && !inDouble) inSingle = !inSingle;
    else if (ch === '"' && !inSingle) inDouble = !inDouble;
    else if (ch === "#" && !inSingle && !inDouble) {
      const atLineStart = line.slice(0, i).trim() === "";
      const prev = line[i - 1];
      if (atLineStart || prev === " " || prev === "\t") {
        return [line.slice(0, i), line.slice(i)];
      }
    }
  }
  return null;
}

/**
 * 把源码切成 token 流。text 内保留换行符，交给 <pre> 的 white-space: pre 渲染。
 * 支持行注释、块注释、字符串、关键字、数字、函数名；hash 系语言额外识别 # 行尾注释。
 */
export function tokenizeCode(code: string, lang: string): CodeToken[] {
  const norm = normalizeLang(lang);
  if (!norm) return [{ type: "plain", text: code }];
  const kwRe = new RegExp(
    keywordsFor(norm)
      .map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .sort((a, b) => b.length - a.length)
      .join("|"),
  );
  const out: CodeToken[] = [];
  const lines = code.split("\n");
  let inBlock = false;
  for (let li = 0; li < lines.length; li++) {
    let line = lines[li];
    const isLast = li === lines.length - 1;
    // 块注释 /* ... */ 跨行
    if (inBlock) {
      const end = line.indexOf("*/");
      if (end === -1) {
        out.push({ type: "comment", text: line + (isLast ? "" : "\n") });
        continue;
      }
      out.push({ type: "comment", text: line.slice(0, end + 2) });
      line = line.slice(end + 2);
      inBlock = false;
    }
    const blockStart = line.indexOf("/*");
    if (blockStart !== -1) {
      const blockEnd = line.indexOf("*/", blockStart + 2);
      if (blockStart > 0) scanLine(line.slice(0, blockStart), kwRe, out);
      if (blockEnd === -1) {
        out.push({ type: "comment", text: line.slice(blockStart) + (isLast ? "" : "\n") });
        inBlock = true;
        continue;
      }
      out.push({ type: "comment", text: line.slice(blockStart, blockEnd + 2) });
      scanLine(line.slice(blockEnd + 2), kwRe, out);
    } else if (HASH_COMMENT.has(norm)) {
      const split = splitHashComment(line);
      if (split) {
        scanLine(split[0], kwRe, out);
        out.push({ type: "comment", text: split[1] });
      } else {
        scanLine(line, kwRe, out);
      }
    } else if (SQL_COMMENT.has(norm)) {
      const idx = line.indexOf("--");
      if (idx !== -1) {
        scanLine(line.slice(0, idx), kwRe, out);
        out.push({ type: "comment", text: line.slice(idx) });
      } else {
        scanLine(line, kwRe, out);
      }
    } else {
      scanLine(line, kwRe, out);
    }
    if (!isLast) {
      const prev = out[out.length - 1];
      if (prev && !prev.text.endsWith("\n")) prev.text += "\n";
    }
  }
  return out;
}