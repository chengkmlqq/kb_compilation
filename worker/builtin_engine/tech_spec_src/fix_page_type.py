#!/usr/bin/env python3
"""批量修复 page_type：entity-b → business_ontology, entity-r → rule_ontology"""
import sys, os, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr

KB = wr.load_kb()
wr.load_config()
wr.KB = KB
wr.mcp_init()

print("MCP init OK")

# 全量拉取所有页面
print("拉取全库页面...")
all_pages = []
p = 1
while True:
    batch = wr.list_wiki_pages(KB, page=p, page_size=100)
    pages = batch.get('pages', [])
    if not pages:
        break
    all_pages.extend(pages)
    if p == 1:
        total = batch.get('total', len(pages))
        total_pages = batch.get('total_pages', 1)
        print(f"  全库总页数: {total}, 分页: {total_pages}")
    print(f"  第{p}页: {len(pages)} 条")
    if len(pages) < 100 or p >= total_pages:
        break
    p += 1
    time.sleep(0.2)

print(f"\n全库共拉取 {len(all_pages)} 页")

# 筛选需要修复的
to_fix = []
for page in all_pages:
    slug = page.get('slug', '')
    pt = page.get('page_type', '')
    if slug.startswith('entity-b-') and pt != 'business_ontology':
        to_fix.append((page, 'business_ontology'))
    elif slug.startswith('entity-r-') and pt != 'rule_ontology':
        to_fix.append((page, 'rule_ontology'))

print(f"需要修复: {len(to_fix)} 页")
b_count = sum(1 for p, t in to_fix if t == 'business_ontology')
r_count = sum(1 for p, t in to_fix if t == 'rule_ontology')
print(f"  entity-b → business_ontology: {b_count}")
print(f"  entity-r → rule_ontology: {r_count}")

if not to_fix:
    print("无需修复，退出")
    sys.exit(0)

# 批量修复
print("\n开始修复...")
fixed = 0
errors = []
for i, (page, new_type) in enumerate(to_fix):
    slug = page['slug']
    folder_id = page.get('folder_id', '')
    source_refs = page.get('source_refs')
    
    res = wr.update_page(slug, kb_id=KB, page_type=new_type, folder_id=folder_id, source_refs=source_refs)
    if 'error' not in res:
        fixed += 1
    else:
        errors.append((slug, str(res)[:200]))
        print(f"  错误: {slug}: {res['error']}")
    
    if (i + 1) % 100 == 0:
        print(f"  进度: {i+1}/{len(to_fix)}, 成功: {fixed}")
    
    time.sleep(0.2)

print(f"\n完成: 成功 {fixed}/{len(to_fix)}")
if errors:
    print(f"错误 {len(errors)} 页:")
    for slug, err in errors[:5]:
        print(f"  {slug}: {err}")

# 验证
print("\n验证...")
verify_slugs = [s for s, _ in to_fix[:3]] + [s for s, _ in to_fix[-3:]]
for slug in verify_slugs:
    page = wr.read_page(slug, kb_id=KB)
    if page:
        pt = page.get('page_type', 'MISSING')
        has_content = bool(page.get('content', '').strip())
        has_title = bool(page.get('title', '').strip())
        has_sref = bool(page.get('source_refs'))
        print(f"  {slug}: page_type={pt}, title={'OK' if has_title else 'MISSING'}, content={'OK' if has_content else 'MISSING'}, source_refs={'OK' if has_sref else 'MISSING'}")
    else:
        print(f"  {slug}: 读取失败!")