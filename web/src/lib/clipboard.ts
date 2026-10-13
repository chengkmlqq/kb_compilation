/**
 * 剪贴板工具 —— navigator.clipboard 仅在安全上下文（HTTPS / localhost）可用；
 * 平台经 HTTP 内网（nginx 80 端口）访问时该 API 不存在，直接调用会抛错导致
 * 「复制失败」。统一封装：优先原生 Clipboard API，失败/不可用时降级
 * 隐藏 textarea + document.execCommand("copy")（HTTP 环境可用，须在用户手势
 * 同步链内调用，故原生分支不可用时不要 await 后再降级）。
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 原生 API 抛错（权限拒绝等），继续尝试降级
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
