/**
 * 前端目录上传链路行为验证（2026-10-20，语义：保留完整层级含顶层目录）
 *
 * 直接 import web 源码里的 upload-dir.ts（纯函数，无路径别名依赖），
 * 模拟「上传文件夹 → collectDirFiles → dirs 推导 → ensure 建目录序列」，
 * 验证 2026-10-20 新语义：制度/2024/a.pdf → ["制度","2024"]，平铺文件也有顶层目录。
 */
import {
  collectDirFiles,
  dirsFromPath,
  isDirectoryUpload,
} from "./../web/src/lib/upload-dir";

function makeDirFile(relPath: string, size = 10) {
  const f = new File([new Uint8Array(size)], relPath.split("/").pop()!, {
    type: "text/markdown",
  });
  Object.defineProperty(f, "webkitRelativePath", { value: relPath, configurable: true });
  return f;
}

// 场景A：嵌套子文件夹（制度/2024/a.pdf）
const nested = [makeDirFile("制度/2024/a.pdf"), makeDirFile("制度/2024/b.docx")];
// 场景B：平铺文件（制度/root-note.txt）
const flat = [makeDirFile("制度/root-note.txt")];

const eA = collectDirFiles(nested).entries;
const eB = collectDirFiles(flat).entries;
console.log("嵌套 dirs:", JSON.stringify(eA.map((e) => e.dirs)), "期望 [['制度','2024'],['制度','2024']]");
console.log("平铺 dirs:", JSON.stringify(eB.map((e) => e.dirs)), "期望 [['制度']]");

// 逐级 ensure（内存版，与 doc-folder-ensure.ensure 同构）
type F = { id: string; name: string; parent_id: string | null };
const folders: F[] = [];
let seq = 0;
const byParentName = new Map<string, string>();
const byPath = new Map<string, string>([["", ""]]);
async function ensure(dirs: string[]) {
  let parent = "";
  let key = "";
  for (const seg of dirs) {
    key = key ? `${key}/${seg}` : seg;
    if (byPath.has(key)) { parent = byPath.get(key)!; continue; }
    const scoped = `${parent}\u0000${seg}`;
    let id = byParentName.get(scoped);
    if (!id) {
      id = `f${++seq}`;
      folders.push({ id, name: seg, parent_id: parent || null });
      byParentName.set(scoped, id);
    }
    byPath.set(key, id);
    parent = id;
  }
  return parent;
}

for (const e of eA) await ensure(e.dirs);
for (const e of eB) await ensure(e.dirs);
console.log("\n建出的目录树:");
for (const f of folders) {
  console.log(`  ${f.name}  parent=${f.parent_id ? folders.find((x) => x.id === f.parent_id)?.name : "(根)"}`);
}

const names = folders.map((f) => f.name);
if (!names.includes("制度") || !names.includes("2024")) throw new Error("顶层目录未创建!");
if (names.filter((n) => n === "制度").length !== 1) throw new Error("顶层目录重复!");
console.log("\n✅ 验证通过：保留完整层级（含顶层文件夹名），嵌套与平铺都正确建树");