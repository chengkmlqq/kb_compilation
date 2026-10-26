/**
 * 知识库详情（文档面板）共用图标
 *
 * 与「知识库管理页」左树保持同一套图标语言（DSB 文档库 knowledge_lib_neo 左树）：
 * 目录节点为官方 folderIcon.svg 原样内联（14x14），展开箭头为 arco IconCaretDown/IconCaretRight。
 * 分页箭头与空态插画沿用知识库管理页（arco 官方 path），保证两页形态一致。
 * 说明：本文件为详情侧的同一套内联图标（管理页的同名图标在 kbs/page.tsx 内），
 * 不引第三方图标库、不自绘。
 */

/** arco Empty 默认插画（viewBox 48 / 描边 4）；容器 font-size 48px → 插画 48px（对齐 arco Empty 原生规格） */
export const EmptyIllustration = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={4} aria-hidden>
    <path d="M24 5v6m7 1 4-4m-18 4-4-4m28.5 22H28s-1 3-4 3-4-3-4-3H6.5M40 41H8a2 2 0 0 1-2-2v-8.46a2 2 0 0 1 .272-1.007l6.15-10.54A2 2 0 0 1 14.148 18H33.85a2 2 0 0 1 1.728.992l6.149 10.541A2 2 0 0 1 42 30.541V39a2 2 0 0 1-2 2Z" />
  </svg>
);

/** arco IconLeft / IconRight（分页上一页 / 下一页：viewBox 48 / 描边 4） */
export const ChevronLeftIcon = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={4} aria-hidden>
    <path d="M32 8.4 16.444 23.956 32 39.513" />
  </svg>
);

export const ChevronRightIcon = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={4} aria-hidden>
    <path d="m16 39.513 15.556-15.557L16 8.4" />
  </svg>
);

/** DSB 文档库（knowledge_lib_neo）左树目录节点图标——原样内联官方
    `KnowledgeLibNeo/images/folderIcon.svg`（14x14 蓝色文件夹：#517ED8 外壳 + 白色内页 +
    #97B9EB→#739CE2 渐变扉页），非自绘。 */
export const FolderIcon = () => (
  <svg width={14} height={14} viewBox="0 0 14 14" fill="none" aria-hidden>
    <path
      d="M5.22522 1.53125C5.4198 1.53125 5.60889 1.59615 5.76248 1.71561L6.30936 2.14093C6.46294 2.26037 6.65205 2.32529 6.84662 2.32529H12.25C12.7332 2.32529 13.125 2.71704 13.125 3.20029V11.545C13.125 12.0283 12.7332 12.42 12.25 12.42H1.75C1.26675 12.42 0.875 12.0283 0.875 11.545V2.40625C0.875 1.923 1.26675 1.53125 1.75 1.53125H5.22522Z"
      fill="#517ED8"
    />
    <path
      d="M11.5615 3.34619C11.9239 3.34624 12.2177 3.64003 12.2177 4.00244V9.49554C12.2177 9.85793 11.9239 10.1517 11.5615 10.1518H2.43872C2.0763 10.1518 1.7825 9.85796 1.78247 9.49554V4.00244C1.78247 3.64 2.07628 3.34619 2.43872 3.34619H11.5615Z"
      fill="#fff"
    />
    <path
      d="M12.4688 4.47998C12.8312 4.47998 13.125 4.77379 13.125 5.13623V11.5447C13.125 12.028 12.7332 12.4197 12.25 12.4197H1.75C1.26675 12.4197 0.875 12.028 0.875 11.5447V5.13623C0.875 4.77379 1.16881 4.47998 1.53125 4.47998H12.4688Z"
      fill="url(#kbDocFolderGradient)"
    />
    <path
      d="M11.9229 5.68994C12.0438 5.68994 12.1417 5.78788 12.1417 5.90869V5.92493C12.1417 6.04572 12.0437 6.14368 11.9229 6.14368H10.5457C10.4249 6.14368 10.327 6.04572 10.327 5.92493V5.90869C10.327 5.78788 10.4249 5.68994 10.5457 5.68994H11.9229Z"
      fill="#517ED8"
    />
    <defs>
      <linearGradient
        id="kbDocFolderGradient"
        x1="7"
        y1="4.47998"
        x2="7"
        y2="12.4197"
        gradientUnits="userSpaceOnUse"
      >
        <stop stopColor="#97B9EB" />
        <stop offset="1" stopColor="#739CE2" />
      </linearGradient>
    </defs>
  </svg>
);

/** arco IconCaretDown / IconCaretRight（DSB 文档库左树 hover 时替换文件夹图标的展开箭头） */
export const CaretDownIcon = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={4} aria-hidden>
    <path
      fill="currentColor"
      stroke="none"
      d="M24.938 34.829a1.2 1.2 0 0 1-1.875 0L9.56 17.949c-.628-.785-.069-1.949.937-1.949h27.007c1.006 0 1.565 1.164.937 1.95L24.937 34.829Z"
    />
  </svg>
);

export const CaretRightIcon = () => (
  <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth={4} aria-hidden>
    <path
      fill="currentColor"
      stroke="none"
      d="M34.829 23.063c.6.48.6 1.394 0 1.874L17.949 38.44c-.785.629-1.949.07-1.949-.937V10.497c0-1.007 1.164-1.566 1.95-.937l16.879 13.503Z"
    />
  </svg>
);

/** 彩色放大镜（对齐 modo-design IconSearchColor：viewBox 12x12，圆圈 opacity .25 + 实心头） */
export const SearchColorIcon = ({ size = 14 }: { size?: number }) => (
  <svg
    viewBox="0 0 12 12"
    fill="none"
    stroke="none"
    width={size}
    height={size}
    style={{ verticalAlign: "-2px" }}
    aria-hidden
  >
    <path
      fillRule="evenodd"
      d="M5 8a3 3 0 1 0-3-3 3 3 0 0 0 3 3Zm0 1a4 4 0 1 0-4-4 4 4 0 0 0 4 4Z"
      opacity=".25"
      fill="currentColor"
    />
    <path
      d="M7.64 8.005a4.018 4.018 0 0 0 .365-.364l.35.097a.5.5 0 0 1 .219.129L11 10.293l-.707.707-2.426-2.426a.5.5 0 0 1-.129-.22Z"
      fill="currentColor"
    />
  </svg>
);
