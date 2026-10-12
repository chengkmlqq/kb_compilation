"""
批量重建通知/附件/辅助文档的完整 Wiki（含实体体系）
优化版：只遍历目标家族目录，不扫全库。
用法: python3 batch_rebuild_aux.py [--dry-run] [--doc-type notice|attachment|auxiliary|all] [--limit N] [--start-from N]
"""
import json, sys, os, re, hashlib, subprocess, time, argparse
import domain
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
KB = wr.load_kb()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
wr.KB = KB
NOTICE_KIDS = [('794685f1-bc48-4052-9b60-53ecd646dde2', '关于修订《中国移动通信集团上海有限公司集中采购执行管理细则》的通知.doc', '集中采购执行管理细则', '2024版通知-29号'), ('e7b180bf-aa79-4977-8ef9-7fb444abc91b', '关于修订《中国移动通信集团上海有限公司集中采购执行管理细则》的通知.pdf', '集中采购执行管理细则', '2024版通知-29号'), ('5487ddc0-b9b4-4e27-8a3d-1e2bcb423cef', '关于修订《中国移动通信集团上海有限公司框架协议订单管理细则》的通知.doc', '中国移动通信集团上海有限公司框架协议订单管理细则', '2024版通知-47号'), ('6f67f7f1-802b-4754-9218-0060bdd9db60', '关于修订《中国移动通信集团上海有限公司框架协议订单管理细则》的通知.pdf', '中国移动通信集团上海有限公司框架协议订单管理细则', '2024版通知-47号'), ('258f9e19-c1b6-46ad-bf75-12dd5e3b875b', '沪移供管部〔2025〕55号关于印发《中国移动通信集团上海有限公司采购决策管理实施细则》的通知.pdf', '中国移动通信集团上海有限公司采购决策管理实施细则', '2025版通知-55号'), ('24f69971-7d68-4029-9efe-bce8ad65bc38', '沪移供管部〔2025〕55号关于印发《中国移动通信集团上海有限公司采购决策管理实施细则》的通知.docx', '中国移动通信集团上海有限公司采购决策管理实施细则', '2025版通知-55号'), ('394d0f4c-ac46-4624-8494-e7278527372c', '沪移供管部〔2025〕89号关于修订印发《中国移动通信集团上海有限公司采购需求管理实施细则》的通知.pdf', '采购需求管理细则', '2025版通知-89号'), ('bd80f165-a1fc-484d-8e58-db7961d732b2', '沪移供管部〔2025〕89号关于修订印发《中国移动通信集团上海有限公司采购需求管理实施细则》的通知.docx', '采购需求管理细则', '2025版通知-89号'), ('4528c4fe-5e03-4a3d-9692-5c0fb0327ff6', '沪移供管部〔2025〕45号关于印发《中国移动通信集团上海有限公司采购实施管理办法》的通知.pdf', '中国移动通信集团上海有限公司采购实施管理办法', '2025版通知-45号'), ('be568033-cc48-4d8c-bd40-484df7f66e04', '沪移供管部〔2025〕45号关于印发《中国移动通信集团上海有限公司采购实施管理办法》的通知.docx', '中国移动通信集团上海有限公司采购实施管理办法', '2025版通知-45号'), ('06d93c83-5c9c-4df7-b520-6c9988792375', '供管通〔2025〕72号关于明确部分不纳入采购管理范围的事项的通知.pdf', '关于明确部分不纳入采购管理范围的事项', '2025版通知-72号'), ('faa7db8a-f249-48bb-a269-f432fa7c589f', '供管通〔2025〕72号关于明确部分不纳入采购管理范围的事项的通知.doc', '关于明确部分不纳入采购管理范围的事项', '2025版通知-72号')]
ATTACHMENT_KIDS = [('31952ac2-5154-439b-9d3d-4ad6d72ae5ba', '附件.采购方案决策应包含的内容.docx', '中国移动通信集团上海有限公司采购决策管理实施细则', '附件-采购方案决策内容'), ('8aab1973-45d8-47c3-9d7e-fabc200ff5a3', '附件1.直接采购方式适用条件和适用场景.docx', '中国移动通信集团上海有限公司采购实施管理办法', '附件-直接采购方式适用条件'), ('7d22f121-4da7-4681-83e0-8cb38e6873e5', '附件2.领导人员违规干预采购情况记录表.docx', '中国移动通信集团上海有限公司采购决策管理实施细则', '附件-领导人员违规干预记录表')]
AUX_KIDS = [('191bf693-c57d-4950-a5ad-3b210ca36e7e', '中国移动通信集团上海有限公司采购需求管理实施细则修订说明.xlsx', '采购需求管理细则', '修订说明'), ('6abf043a-c7db-43aa-8a19-c9e2de474abc', '中国移动通信集团上海有限公司采购决策管理实施细则主要修订说明.xlsx', '中国移动通信集团上海有限公司采购决策管理实施细则', '修订说明'), ('2895f9d8-febc-4dd0-9bfa-67d0e845ba1a', '中国移动通信集团上海有限公司采购实施管理办法修订说明.xlsx', '中国移动通信集团上海有限公司采购实施管理办法', '修订说明'), ('18a8ee63-23d1-4fd8-bb87-934498fb9731', '主要修订情况一览表.xls', '采购需求管理细则', '修订说明'), ('e740aba6-3d36-45c8-8c03-a34af3c4207f', '采购决策制度修订后的分层分级授权清单（需求前置决策事项）.xlsx', '中国移动通信集团上海有限公司采购决策管理实施细则', '授权清单-需求前置'), ('8415dcfc-4abe-43c7-82b1-13d500e219cb', '采购决策制度修订后的分层分级授权清单.xlsx', '中国移动通信集团上海有限公司采购决策管理实施细则', '授权清单')]

def find_family_root(family_cn):
    """Find family root folder by name at depth 0."""
    r = wr.list_folders(kb_id=KB, parent_id='')
    for f in r.get('folders', []):
        if f['name'] == family_cn:
            return f['id']
    return None

def find_child_folder(parent_id, name):
    """Find a child folder by name under parent (depth 1 only)."""
    r = wr.list_folders(kb_id=KB, parent_id=parent_id)
    for f in r.get('folders', []):
        if f['name'] == name:
            return f['id']
    return None

def ensure_folder(name, parent_id):
    """Create folder if not exists."""
    existing = find_child_folder(parent_id, name)
    if existing:
        return existing
    r = wr.create_folder(name, parent_id=parent_id, kb_id=KB)
    return r.get('folder', {}).get('id', r.get('id', ''))

def ensure_full_structure(version_dir_id, version_name):
    """Ensure a version directory has all required subdirectories."""
    result = {}
    existing = {}
    r = wr.list_folders(kb_id=KB, parent_id=version_dir_id)
    for f in r.get('folders', []):
        existing[f['name']] = f['id']
    result['长句原文'] = existing.get('长句原文') or ensure_folder('长句原文', version_dir_id)
    result['高频关键词'] = existing.get('高频关键词') or ensure_folder('高频关键词', version_dir_id)
    result['基础实体'] = existing.get('基础实体') or ensure_folder('基础实体', version_dir_id)
    biz_cats = domain.BIZ_CATS
    rule_cats = domain.RULE_CATS
    biz_onto = existing.get('业务对象') or ensure_folder('业务对象', result['基础实体'])
    rule_onto = existing.get('关联规则') or ensure_folder('关联规则', result['基础实体'])
    result['业务对象'] = biz_onto
    result['关联规则'] = rule_onto
    result['cat_ids'] = {'biz': {}, 'rule': {}}
    for cat in biz_cats:
        result['cat_ids']['biz'][cat] = ensure_folder(cat, biz_onto)
    for cat in rule_cats:
        result['cat_ids']['rule'][cat] = ensure_folder(cat, rule_onto)
    catids_path = f'/tmp/catids_{version_name}.json'
    with open(catids_path, 'w', encoding='utf-8') as f:
        json.dump(result['cat_ids'], f, ensure_ascii=False, indent=2)
    result['catids_path'] = catids_path
    return result

def build_one(kid, source_file, family_cn, version_cn, dry_run=False):
    """Rebuild a single notification/attachment/aux document with full wiki."""
    print(f"\n{'=' * 60}")
    print(f'开始重建: {source_file}')
    print(f'  kid: {kid}')
    print(f'  家族: {family_cn} / 版本目录: {version_cn}')
    wr.load_config()
    wr.mcp_init()
    family_root_id = find_family_root(family_cn)
    if not family_root_id:
        print(f"  ERROR: 家族根目录 '{family_cn}' 不存在")
        return False
    print(f'  [1] 家族根: {family_root_id[:8]}')
    version_dir_id = find_child_folder(family_root_id, version_cn)
    if not version_dir_id:
        print(f'  [2] 创建版本目录: {version_cn}')
        version_dir_id = ensure_folder(version_cn, family_root_id)
    else:
        print(f'  [2] 版本目录已存在: {version_dir_id[:8]}')
    dirs = ensure_full_structure(version_dir_id, version_cn)
    print(f"  [3] 子目录: ls={dirs['长句原文'][:8]} kw={dirs['高频关键词'][:8]} base={dirs['基础实体'][:8]}")
    r = wr.list_chunks(kid, kb_id=KB, page_size=100)
    chunks = r.get('data', [])
    print(f'  [4] 切片: {len(chunks)} chunks')
    if len(chunks) == 0:
        print(f'  ERROR: 切片缺失（chunks=0），跳过')
        return False
    if dry_run:
        print(f'  [DRY RUN] 将执行完整构建')
        return True
    print(f'  [5] 执行 build_existing_dir.py (完整构建，含实体)...')
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, 'build_existing_dir.py'), kid, dirs['长句原文'], dirs['高频关键词'], dirs['catids_path'], source_file]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        lines = stdout.split('\n')
        show = lines[-100:] if len(lines) > 100 else lines
        print(f'  stdout (last {len(show)} lines):')
        for line in show:
            print(f'    {line}')
    if stderr:
        print(f'  stderr: {stderr[-500:]}')
    print(f'  returncode: {result.returncode}')
    if result.returncode != 0:
        print(f'  WARNING: build_existing_dir.py 返回非零')
    print(f'  [6] 跨文档合并 + 链接重建...')
    merge_result = subprocess.run([sys.executable, os.path.join(SCRIPT_DIR, 'merge_duplicate_entities.py'), '--kb', KB], capture_output=True, text=True, timeout=300)
    if merge_result.stdout:
        print(f'  merge stdout: {merge_result.stdout[-500:]}')
    wr.rebuild_links(**{'kb_id': KB})
    print(f'  ✅ 完成: {source_file}')
    return True

def main():
    parser = argparse.ArgumentParser(description='Batch rebuild notification/attachment/aux docs with full wiki')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--doc-type', choices=['notice', 'attachment', 'auxiliary', 'all'], default='all')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--start-from', type=int, default=0)
    args = parser.parse_args()
    if args.doc_type == 'notice':
        docs = NOTICE_KIDS
    elif args.doc_type == 'attachment':
        docs = ATTACHMENT_KIDS
    elif args.doc_type == 'auxiliary':
        docs = AUX_KIDS
    else:
        docs = NOTICE_KIDS + ATTACHMENT_KIDS + AUX_KIDS
    docs = docs[args.start_from:]
    if args.limit > 0:
        docs = docs[:args.limit]
    print(f'待处理文档: {len(docs)} 篇')
    for i, (kid, fname, family, version) in enumerate(docs):
        print(f'  {i + 1}. {fname} ({kid[:8]})')
    success = 0
    failed = 0
    for i, (kid, fname, family, version) in enumerate(docs):
        print(f"\n{'#' * 60}")
        print(f'# 进度: {i + 1}/{len(docs)} — {fname}')
        print(f"{'#' * 60}")
        try:
            ok = build_one(kid, fname, family, version, dry_run=args.dry_run)
            if ok:
                success += 1
            else:
                failed += 1
        except Exception as e:
            import traceback
            print(f'  ❌ 异常: {e}')
            traceback.print_exc()
            failed += 1
        if i < len(docs) - 1 and (not args.dry_run):
            time.sleep(3)
    print(f"\n{'=' * 60}")
    print(f'批次完成: 成功 {success}, 失败 {failed}, 总计 {len(docs)}')
    print(f"{'=' * 60}")
if __name__ == '__main__':
    main()