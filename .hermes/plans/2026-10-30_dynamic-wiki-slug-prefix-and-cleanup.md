# 动态 Wiki 构建：slug 加文档前缀 + 重新触发前清理旧页

> 目标：重新触发动态构建不再残留旧 wiki 页；跨文档同名知识点不再互相覆盖。

## 背景（已核实）

- 动态构建写页节点 `wiki_upsert_page`（orchestration_engine.py:742）是纯 upsert：
  slug 存在→更新，不存在→创建，**从不删除**。
- 建目录 `wiki_ensure_folder` 只建不删。
- 知识点页 slug 只按知识点名生成（gen_dynamic_wiki_tape.py:167），不带文档前缀 →
  跨文档同名知识点 slug 冲突互相覆盖。
- 唯一清理路径是删文档（kb_admin.py:989 按 source_refs 前缀 `{document_id}|` 级联）。
- 摘要页挂 `summary/{md5}` 目录，动态页挂 `doc_root/` 子树 → 按目录可区分。

## 改动

### 1. slug 加文档前缀（gen_dynamic_wiki_tape.py n06）

知识点页 slug 改为 `{doc_root_slug}-{kp_slug}`：
- `doc_root_slug = re.sub(r'[^\w\u4e00-\u9fff-]', '-', doc_root).strip('-')`
- `kp_slug` = 现有知识点名清洗（不变）
- slug 列 String(255)，doc_root ≤80 + kp_slug 截断（≤80）够用
- 索引页 slug 已是 `{doc_root}-索引`，不动；互链 `[[slug|name]]` 用 kp_items 的 slug 字段自动跟随

### 2. 构建前清理节点（gen_dynamic_wiki_tape.py 新增 script 节点 n04a）

新增节点 `n04a`（n04 之后、n05 之前），script 代码：按双条件删本文档专属旧页：
- source_refs LIKE `%{document_id}|%`（前缀命中本文档）
- folder 在 doc_root 子树内（排除 summary/ 目录等非动态页）
- 专属页（refs 全属于本文档）整页删 + 清 WikiLink；共享页只摘引用（防御）
- 清理失败 best-effort 不阻断（warning 不 fail）
- 边：n04→n04a→n05（插在 n04 与 n05 之间）

### 3. 存量页迁移

清理节点按 source_refs 前缀命中即可在**下次重新触发构建时**自动删除存量旧 slug 页并重建为新格式（无需单独迁移脚本）。若需立即全量迁移，另跑一次脚本（按 source_refs 前缀找各文档页，删旧建新）。

## 测试

- tests/test_dynamic_wiki_build.py 加：n04a 节点存在且位于 n04 与 n05 之间；
  n06 的 slug 生成带 doc_root 前缀（断言 kp_items slug 前缀）。
- tests/test_orchestration_engine.py 或新增：清理 script 代码包含 source_refs 前缀删除逻辑。

## 风险

- slug 变更使存量页错位 → 靠"下次构建自动迁移"收敛；对不再触发的存量文档，
  旧 slug 页仍在但不再与新页冲突（除非同名 doc_root）。
- 清理误删摘要页 → 已用 folder 子树 + source_refs 双条件排除。
- 清理失败不阻断 → 旧页残留（下次构建再试），不丢数据。

## 验证

- pytest tests/test_dynamic_wiki_build.py tests/test_orchestration_engine.py
- 容器内构造：同一文档两次触发构建，第二次后旧知识点页被清、新 slug 页生成；
  跨文档同名知识点 → slug 不同不覆盖。
