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
import weknora_rpc as wr
# 泛化高频实体（市场监督域，2026-09-04）：跨法规几乎必出现的通用概念/机关名——
# 共享它们不具备隐性关联信息量（如任何两部法规都可能提到 经营者/处罚/监督管理），
# 全库挖掘前按此表剔除；领域化共享实体（药品/专利/电梯等）不在此列，保留挖掘。
BROAD_ENTITIES = {'经营者', '消费者', '企业', '公司', '单位', '部门', '机构', '机关', '人员',
    '法人', '个人', '组织', '行业协会', '市场', '产品', '商品', '服务', '货物', '工程',
    '行为', '活动', '事项', '情形', '条件', '程序', '要求', '义务', '责任', '权利',
    '规定', '办法', '条例', '细则', '规章', '法规', '法律', '行政法规', '部门规章',
    '规范性文件', '标准', '行政许可', '行政处罚', '行政强制', '行政复议', '行政诉讼',
    '监督管理', '监督检查', '监管', '执法', '处罚', '罚款', '没收', '责令改正',
    '登记', '备案', '公告', '施行', '修订', '废止', '之日起', '期限', '有效期',
    '国务院', '县级以上', '人民政府', '市场监督管理部门', '药品监督管理部门',
    '国家市场监督管理总局', '总局', '部门规章', '法律、法规', '规定期限'}

# 噪音实体判定（2026-08-31 新增，0828 库挖掘实测：手机号/人名/制度名/公司全称/文号前缀等
# 被抽成业务实体，导致假隐性关联——如 89号通知 ↔ 45号通知 因共享落款"张隶华"被判关联）
_NOISE_PATTERNS = [
    r'^\d{5,}$',                    # 纯数字（编号）
    r'^\d{11}$',                    # 11 位数字
    r'^1[3-9]\d{9}$',              # 大陆手机号
    r'^《.+》$',                    # 法规名（书名号包裹，应链 summary 而非实体）
    r'^[^《]*《.+》',               # 法规名+文号
    r'中华人民共和国|国家市场监督管理总局|国家知识产权局|国家药品监督管理局',  # 法律全称前缀/机关全称
    r'主席令|国务院令|总局令|部令|局令',   # 令号前缀
    r'〔20\d{2}〕[^《》]*号|（20\d{2}年[^《》]*修正）',  # 文号/修正标注
    r'^通知$|^关于印发|^关于修订|^关于修改|^关于公布',   # 文件类型碎片
    r'^第[一二三四五六七八九十百千0-9]+[条款]',   # 条款引用
    r'@|\.com$|\.cn$',              # 邮箱/网址
    r'^[（(][^）)]*[）)]',           # 括号开头
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
    fn = fn.replace('中华人民共和国', '')
    fn = fn.replace('暂行', '')      # 办法/暂行办法 视作同族版本（正式办法通常废止暂行办法）
    fn = fn.replace('修正文本', '')
    return fn.strip()

def gen_summary(n1, n2, biz):
    """根据共享业务实体数生成一句话总体关系（列表头用，不细讲实体）"""
    n1s = re.sub('\\.(docx|doc|pdf|xlsx|xls)$', '', n1)
    n2s = re.sub('\\.(docx|doc|pdf|xlsx|xls)$', '', n2)

    def short(fn):
        fn = re.sub('\\.(docx|doc|pdf|xlsx|xls)$', '', fn)
        fn = fn.replace('中华人民共和国', '').replace('实施细则', '细则').replace('暂行', '')
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