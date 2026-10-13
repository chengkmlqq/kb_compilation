"""
隐性文档关系挖掘脚本 v2（2026-08-20 重构）
基于跨文档合并的【业务实体】source_refs，挖掘文档间隐性关联。
输出按"共享实体"展开，供 describe_hidden_relations.py 生成 LLM 对比说明。

用法：
  python3 mine_hidden_relations.py --kb <kb_id> -o <output.json> [--threshold 2]
  python3 mine_hidden_relations.py --kb <kb_id> --include-same-family  # 含同族版本对

变更（2026-08-26）：
  - 默认排除同族文档对（same_family=true），因为同族版本间的实体共享属于版本继承，
    不是跨文档隐性关联。使用 --include-same-family 可恢复旧行为。

变更（2026-08-20）：
  - 只依赖业务实体（entity-b-），排除规则实体（entity-r-）
  - 输出按每个共享实体展开：{doc: { 关联文档: [ {entity,  ...} ] } }

输出 JSON: {
  "<doc_kid>": {
    "<关联文档kid>": {
      "doc_name": "...", "same_family": true/false, "level": "★★",
      "entities": ["实体1", "实体2", ...]   # 共享业务实体名列表
    }
  }
}
"""
import json, sys, os, re, argparse
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kb_wiki_rpc as wr
BROAD_ENTITIES = {'采购', '公司', '部门', '单位', '采购需求', '供应商', '采购部门', '需求部门', '采购人', '采购项目', '采购结果', '采购方案', '采购文件', '采购方式', '采购执行', '框架协议', '采购订单', '集中采购', '供应链管理部', '采购预算', '采购资金', '合同', '货物', '工程', '服务', '招标', '评审', '采购决策', '需求', '订单', '采购活动', '采购过程', '采购业务', '采购工作', '公司分管领导', '采购计划', '采购公告', '评审报告', '成交', '采购结果确认', '采购方案决策', '非集中采购', '集中采购需求', '采购决策形式', '采购请示', '采购类别', '采购实施', '采购组织', '采购纪律', '采购建议', '采购人员', '采购委员会', '采购办理', '采购沟通', '采购分工', '采购任务', '采购状态', '采购信息', '供应商管理', '采购成本', '采购清单', '集团', '中国移动', '上海公司', '上海', '审批', '决策', '采购内容', '采购数量', '集中采购目录', '单一来源', '招标方式', '采购委员会成员', '采购方案审批', '采购报告', '采购过程管理', '采购工作小组', '采购决策事项', '采购决策参与人员', '采购决策监督', '采购决策审批', '采购决策会签', '采购决策监督部门'}

# 噪音实体判定（2026-08-31 新增，0828 库挖掘实测：手机号/人名/制度名/公司全称/文号前缀等
# 被抽成业务实体，导致假隐性关联——如 89号通知 ↔ 45号通知 因共享落款"张隶华"被判关联）
_NOISE_PATTERNS = [
    r'^\d{5,}$',                    # 纯数字（手机号/编号）
    r'^\d{11}$',                    # 11 位手机号
    r'^1[3-9]\d{9}$',              # 大陆手机号
    r'^《.+》$',                    # 制度名（书名号包裹，应链 summary 而非实体）
    r'^[^《]*《.+》',               # 制度名+文号（如《关于…的通知》（沪移人资〔2024〕50号））
    r'中国移动通信集团上海有限公司',  # 公司全称
    r'中国移动通信集团有限公司',     # 集团全称
    r'沪移供管部|供管通|移动采购发|中移办供管|沪移人资',  # 文号前缀/落款
    r'^通知$|^关于印发|^关于修订|^沪移',       # 通知类文件名碎片
    r'^第[一二三四五六七八九十百千0-9]+[条款]',   # 条款引用
    r'@|\.com$|\.cn$',              # 邮箱/网址
    r'^[（(][^）)]*[）)]',           # 括号开头（如（党委组织部））
]

def is_noise_entity(title):
    """判断实体名是否为噪音（2026-08-31）：纯数字/手机号/制度名/公司全称/文号前缀/条款引用。"""
    t = (title or '').strip()
    if not t:
        return True
    if len(t) < 2:
        return True
    # 人名特征：3 字中文且无业务语义后缀（难精确判定，用已知落款名单+长度启发）
    #   已知 0828 库实测落款人名：张隶华（审批记录签署人）
    if t in ('张隶华',):
        return True
    for pat in _NOISE_PATTERNS:
        if re.search(pat, t):
            return True
    return False

def core_name(fn):
    """提取文件名核心名（去版本号/编号），用于判断同族版本"""
    fn = re.sub('\\.(docx|doc|pdf|xlsx|xls)$', '', fn)
    fn = re.sub('^[A-Z]+\\d+[\\d.]*', '', fn)
    fn = re.sub('（V[0-9.]+）', '', fn)
    fn = re.sub('^[，,\\s]*\\d+[年版，,]?', '', fn)
    fn = re.sub('^[A-Z]*\\d+[\\d.]*', '', fn)
    fn = re.sub('\\d+[年版]', '', fn)
    fn = re.sub('[，,、\\s]+', '', fn)
    fn = re.sub('附件\\d?[：:]?', '', fn)
    fn = re.sub('（[^）]*）', '', fn)
    fn = re.sub('采购决策管理(实施)?细则', '采购决策管理细则', fn)
    fn = re.sub('采购需求管理实施细?则', '采购需求管理细则', fn)
    fn = re.sub('集中采购需求管理办法', '集中采购需求管理办法', fn)
    return fn.strip()

def gen_summary(n1, n2, biz):
    """根据共享业务实体数生成一句话总体关系（列表头用，不细讲实体）"""
    n1s = re.sub('\\.(docx|doc|pdf|xlsx|xls)$', '', n1)
    n2s = re.sub('\\.(docx|doc|pdf|xlsx|xls)$', '', n2)

    def short(fn):
        fn = re.sub('\\.(docx|doc|pdf|xlsx|xls)$', '', fn)
        fn = fn.replace('中国移动通信集团上海有限公司', '').replace('实施细则', '细则')
        fn = re.sub('^[A-Z]+[\\d.]+', '', fn)
        fn = re.sub('（V[0-9.]+）', '', fn)
        return fn[:14]
    s1, s2 = (short(n1), short(n2))
    if core_name(n1) == core_name(n2):
        return f'{s1}与{s2}属同一制度家族的不同版本/附件，概念与规则体系高度一致，共有{len(biz)}个重叠业务概念。'
    return f'{s1}与{s2}存在跨制度隐性关联，共有{len(biz)}个重叠业务概念。'

def load_docs_and_pages(kb):
    doc_map = {}
    pw = 1
    while True:
        r = wr.list_docs(page=pw, page_size=20)
        data = r.get('data', [])
        if not data:
            break
        for d in data:
            doc_map[d['id']] = d.get('file_name', '')
        if len(data) < 20:
            break
        pw += 1
    wiki_pages = []
    p2 = 1
    while True:
        r = wr.list_wiki_pages(**{'kb_id': kb, 'page': p2, 'page_size': 100})
        data = r.get('pages') or r.get('data') or []
        if not data:
            break
        wiki_pages.extend(data)
        if len(data) < 100:
            break
        p2 += 1
    return (doc_map, wiki_pages)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default=None)
    ap.add_argument('-o', '--output', default='/tmp/hidden_relations.json')
    ap.add_argument('--threshold', type=int, default=2)
    ap.add_argument('--include-same-family', action='store_true', help='包含同族文档对的隐性关联（默认排除，因为同族实体共享属于版本继承）')
    args = ap.parse_args()
    wr.load_config()
    wr.mcp_init()
    KB = args.kb
    wr.KB = KB
    TH = args.threshold
    doc_map, wiki_pages = load_docs_and_pages(KB)
    entity_docs = defaultdict(set)
    for p in wiki_pages:
        slug = p.get('slug', '')
        if not slug.startswith('entity-b-'):
            continue
        refs = p.get('source_refs', []) or []
        if len(refs) < 2:
            continue
        title = p.get('title', '').strip()
        if not title or title in BROAD_ENTITIES:
            continue
        if is_noise_entity(title):
            continue  # 2026-08-31：噪音实体（手机号/人名/制度名/公司全称/文号前缀）不参与隐性关联
        entity_docs[title] |= set(refs)
    usable = {t: ds for t, ds in entity_docs.items() if 2 <= len(ds) <= 12}
    pair_ents = defaultdict(list)
    for title, docset in usable.items():
        ds = list(docset)
        for i in range(len(ds)):
            for j in range(i + 1, len(ds)):
                pair = tuple(sorted([ds[i], ds[j]]))
                pair_ents[pair].append(title)
    results = {}
    same_family_skipped = 0
    for (d1, d2), ents in pair_ents.items():
        n1, n2 = (doc_map.get(d1, d1[:8]), doc_map.get(d2, d2[:8]))
        count = len(ents)
        if count < TH:
            continue
        same = core_name(n1) == core_name(n2)
        if same and (not args.include_same_family):
            same_family_skipped += 1
            continue
        level = '★★★' if count >= 5 else '★★' if count >= 3 else '★'
        entry = {'doc_name': n2, 'same_family': same, 'level': level, 'entities': sorted(ents), 'count': count, 'summary': gen_summary(n1, n2, sorted(ents))}
        results.setdefault(d1, {})[d2] = entry
        results.setdefault(d2, {})[d1] = {'doc_name': n1, 'same_family': same, 'level': level, 'entities': sorted(ents), 'count': count, 'summary': entry['summary']}
    if same_family_skipped:
        print(f'已排除同族文档对 {same_family_skipped} 对（使用 --include-same-family 可包含）')
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    pairs = sum((len(v) for v in results.values())) // 2
    print(f'=== 隐性关联挖掘完成 (仅业务实体) ===')
    print(f'跨文档独特业务实体: {len(usable)} 个')
    print(f'有隐性关联的文档: {len(results)} 个')
    print(f'关联对: {pairs} 对')
    print(f'结果已保存: {args.output}')
if __name__ == '__main__':
    main()