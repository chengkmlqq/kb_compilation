"""
单文件实体抽取脚本（2026-09-02 B2 改造：主谓联合抽取）：

【原架构（2026-08-28，已废弃为割裂）】规则先抽（Step1 RULE_PROMPT）→ 业务后抽
（Step3 BIZ_PROMPT）两次独立 LLM 调用。病根：同一长句的「主语（业务对象）」与
「谓词约束（规则卡）」从不在一次调用里对齐——constraint/objects 名称与业务对象
名同义不同形（canonical 不匹配）→ 规则-实体关联大面积断裂（0829 实测：规则约束
对象 61% 纯文本未链、业务页关联规则表 94% 空）。

【B2 联合抽取（2026-09-02）】JOINT_PROMPT 单次调用同时输出 entities + rules：
  1. 约束对象/涉及对象必须逐字等于同批 entities[].name（同一 JSON 内引用）——
     主语-谓词在名称空间上闭包配对，不再事后文本缝合；
  2. 分批（25 句/批）抽取 → 跨批实体按 canonical 去重；
  3. 规则对象程序补建（Step 3.5）保留为双保险；
  4. 建页/合并/关联规则回填管线不变（rules_by_constraint canonical 命中率因此大幅提升）。

用法（与原版兼容）：
    python extract_entities_single.py         --ls-file /tmp/ls_all.json         --cat-ids /tmp/cat_ids.json         --kid <knowledge_id>         --kb <knowledge_base_id>         --source-file "文件名.doc"         -o /tmp/entity_slug_map.json
"""
import json, hashlib, re, time, os, sys, argparse
import domain
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weknora_rpc as wr
from domain import (BIZ_CATS, RULE_CATS, BIZ_CAT_SHORT, RULE_CAT_SHORT, RO_NUM_MAP,
                    RULE_PROMPT, BIZ_PROMPT, JOINT_PROMPT, SYSTEM_ROLE)

# 联合抽取分批大小（句/批；大批易触发 finish_reason=length，25 句 + max_tokens 20000 稳定）
# 2026-10-11 编排化适配：kb_compilation 侧 InferAI 端点对 25 句大 prompt 响应 >660s
# （llm_call 总时长守卫 660s 杀掉）→ 调小到 8 句/批，单请求更快
JOINT_BATCH = 8


def extract_joint_llm(ls_all, args):
    """B2 主谓联合抽取（2026-09-02）：实体与规则同批产出、名称空间闭包配对。

    返回 (rule_entities, biz_entities)，字段格式与旧 RULE_PROMPT/BIZ_PROMPT 产物兼容
    （下游 Step2 建规则页 / Step4 建业务页 / rules_by_constraint 逻辑不变）。
    --rule-file/--biz-file 双全存在时跳过 LLM 走旧产物（运维兼容）。
    """
    if args.skip_llm:
        return [], []
    if (getattr(args, 'rule_file', None) and getattr(args, 'biz_file', None)
            and os.path.exists(args.rule_file) and os.path.exists(args.biz_file)):
        with open(args.rule_file, encoding='utf-8') as f:
            rules = json.load(f)
        with open(args.biz_file, encoding='utf-8') as f:
            biz = json.load(f)
        print(f'  旧产物复用（--rule-file/--biz-file）: 规则 {len(rules)} / 业务 {len(biz)}')
        return rules, biz
    terms_txt = ''
    if getattr(args, 'terms_file', None) and os.path.exists(args.terms_file):
        try:
            with open(args.terms_file, encoding='utf-8') as f:
                tlist = json.load(f)
            terms_txt = '\n'.join((f"- {t.get('word', '')}" for t in tlist[:50] if t.get('word')))
        except Exception:
            terms_txt = ''
    prompt = JOINT_PROMPT.format(terms=terms_txt or '- （无）')
    biz_by_canon = {}   # canonical -> dict
    rule_entities = []
    failed = []
    total_batches = (len(ls_all) + JOINT_BATCH - 1) // JOINT_BATCH
    for bidx in range(total_batches):
        batch = ls_all[bidx * JOINT_BATCH:(bidx + 1) * JOINT_BATCH]
        ls_text = '\n'.join((f"{i + 1}. {item['text'][:400]}" for i, item in enumerate(batch)))
        got = None
        for attempt in range(2):  # 每批失败重试 1 次
            try:
                raw = wr.llm_call([{'role': 'system', 'content': f'你是{SYSTEM_ROLE}，只输出JSON，不要任何思考过程。'},
                                   {'role': 'user', 'content': prompt + '\n\n长句列表：\n' + ls_text}],
                                  model=args.model or wr.DEFAULT_MODEL, max_tokens=args.max_tokens)
                obj = json.loads(wr.ensure_full_json(raw))
                if not isinstance(obj, dict):
                    raise ValueError('联合抽取返回非 JSON 对象')
                ents, ruls = [], []
                # 结构归一化（坑 83：LLM 偶发嵌套结构）
                def _c(x, out):
                    if isinstance(x, dict):
                        out.append(x)
                    elif isinstance(x, list):
                        for y in x:
                            _c(y, out)
                _c(obj.get('entities'), ents)
                _c(obj.get('rules'), ruls)
                got = (ents, ruls)
                break
            except Exception as e:
                print(f'  联合批 {bidx + 1}/{total_batches} 第 {attempt + 1} 次失败: {e}')
        if got is None:
            failed.append(bidx + 1)
            continue
        ents, ruls = got
        for e in ents:
            nm = (e.get('name') or '').strip()
            if not nm:
                continue
            cc = canonicalize(nm)
            if cc in biz_by_canon:
                continue  # 同概念跨句/跨批重复：保留首现
            biz_by_canon[cc] = {'name': nm,
                                'category': (e.get('category') or '').strip(),
                                'definition': (e.get('definition') or '').strip(),
                                'description': (e.get('description') or '').strip()}
        for r in ruls:
            t = (r.get('title') or '').strip()
            cat = (r.get('category') or '').strip()
            if not t or cat not in RULE_CAT_SHORT:
                continue  # 类别非法直接丢（宁缺毋滥）
            rule_entities.append({'title': t, 'category': cat,
                                  'structure': r.get('structure') or {},
                                  'description': (r.get('description') or '').strip(),
                                  'evidence': (r.get('evidence') or '').strip(),
                                  'constraint': (r.get('constraint') or '').strip(),
                                  'objects': r.get('objects') or []})
    biz_entities = [{'name': v['name'], 'category': v['category'],
                     'definition': v['definition'], 'description': v['description']}
                    for v in biz_by_canon.values()]
    if failed:
        print(f'  ⚠️ 联合抽取 {len(failed)}/{total_batches} 批最终失败: {failed}（该批实体/规则缺失，由补建/补抽兜底）')
    print(f'  联合抽取完成: 业务对象 {len(biz_entities)} 个 / 规则 {len(rule_entities)} 个（{total_batches} 批）')
    return rule_entities, biz_entities


def canonicalize(name):
    """简单归一化：去括号注释、去空格、词尾中文数字转阿拉伯。"""
    name = re.sub('[（(][^）)]*[）)]', '', name or '').strip()
    name = re.sub('\\s+', '', name)
    cn2ar = {'一': '1', '二': '2', '三': '3', '四': '4', '五': '5', '六': '6', '七': '7', '八': '8', '九': '9', '十': '10'}
    name = re.sub('([一二三四五六七八九十])$', lambda m: cn2ar.get(m.group(1), m.group(0)), name)
    return name

def entity_slug(cat, name):
    h = hashlib.md5(f'{cat}|{canonicalize(name)}'.encode()).hexdigest()[:10]
    return f'entity-b-{h}'

def rule_slug(ro_num, title, evidence=''):
    """规则 slug：优先用 evidence 原文片段做确定性锚点；无 evidence 回退 title。"""
    raw = evidence.strip() if evidence.strip() else title.strip()
    h = hashlib.md5(canonicalize(raw).encode()).hexdigest()[:12]
    return f'entity-r-ro{ro_num:03d}-{h}'

def find_ls_slugs(ls_all, entity_name, limit=3, text_only=False):
    """在长句列表中查找包含实体名的长句（策略降级：原文→标题→核心词）。

    text_only=True 时只匹配长句原文 text（业务对象硬约束：title 是 LLM 生成的
    摘要标题，不算原文依据，2026-08-28 用户要求），跳过标题/核心词降级。
    """
    _SUFFIXES = ['规则', '原则', '要求', '说明', '定义', '流程', '规定', '办法', '制度', '条件', '约束', '审批', '决策', '职责', '引用', '例外', '时间', '金钱', '版本', '模式', '情形', '事项', '工作']

    def _core(name):
        for suf in _SUFFIXES:
            if name.endswith(suf) and len(name) > len(suf) + 1:
                return name[:-len(suf)]
        return name
    hits, used = ([], set())
    for item in ls_all:
        ok = entity_name in item.get('text', '')
        if text_only and not ok:
            # 剥括号注释/空白后的规范名仍是原文子串才算命中（如「采购项目（集团）」原文只写「采购项目」）
            ok = canonicalize(entity_name) in item.get('text', '')
        if ok:
            key = item['slug']
            if key not in used:
                hits.append((item['slug'], item['title']))
                used.add(key)
                if len(hits) >= limit:
                    return hits
    if text_only:
        return hits
    for item in ls_all:
        if entity_name in item.get('title', ''):
            key = item['slug']
            if key not in used:
                hits.append((item['slug'], item['title']))
                used.add(key)
                if len(hits) >= limit:
                    return hits
    core = _core(entity_name)
    if core != entity_name and len(core) >= 3:
        for item in ls_all:
            if core in item.get('title', '') or core in item.get('text', ''):
                key = item['slug']
                if key not in used:
                    hits.append((item['slug'], item['title']))
                    used.add(key)
                    if len(hits) >= limit:
                        return hits
    return hits

def render_ls_grouped(by_file):
    """渲染原文关联：按文件名分组，每组 **《文件名》** 粗体标题。"""
    if not by_file:
        return ''
    parts = []
    for fname, links in by_file.items():
        if not links:
            continue
        rows = '\n'.join((f'- [[{s}|{t}]]' for s, t in links))
        parts.append(f'**{wr.bookname(fname)}**\n{rows}')
    return '\n\n'.join(parts)

def append_ls_grouped(content, by_file):
    """在已有 content 的 ## 原文关联 节中按文件分组追加长句（幂等：slug 已存在跳过）。"""
    if not by_file:
        return content
    marker = '## 原文关联'
    if marker not in content:
        return content
    seg = content.split(marker, 1)[1]
    next_sec = seg.find('\n## ')
    body = seg[:next_sec] if next_sec >= 0 else seg
    tail = seg[next_sec:] if next_sec >= 0 else ''
    for fname, links in by_file.items():
        if not links:
            continue
        gt = f'**{wr.bookname(fname)}**'
        gi = body.find(gt)
        if gi >= 0:
            ge = body.find('\n**', gi + len(gt))
            if ge < 0:
                ge = len(body)
            group = body[gi:ge]
            existing = set(re.findall('\\[\\[([^\\]|]+)', group))
            add = [f'- [[{s}|{t}]]' for s, t in links if s not in existing]
            if add:
                body = body[:ge] + '\n' + '\n'.join(add) + body[ge:]
        else:
            body = body.rstrip() + f'\n\n{gt}\n' + '\n'.join((f'- [[{s}|{t}]]' for s, t in links))
    return content.split(marker, 1)[0] + marker + body + tail

def append_key_rules(content, key_rules):
    """在 entity-b 页 ## 关联规则 表中追加行（去重：按规则 slug）。"""
    if not key_rules:
        return content
    marker = '## 关联规则'
    if marker not in content:
        return content
    seg = content.split(marker, 1)[1]
    next_sec = seg.find('\n## ')
    body = seg[:next_sec] if next_sec >= 0 else seg
    tail = seg[next_sec:] if next_sec >= 0 else ''
    lines = body.split('\n')
    insert_at = None
    for i, ln in enumerate(lines):
        if '---' in ln and i + 1 < len(lines):
            insert_at = i + 1
    if insert_at is None:
        return content
    existing = set(re.findall('\\[\\[([^\\]|]+)', body))
    new_rows = [r for r in key_rules if r[0] not in existing]
    if not new_rows:
        return content
    rows = '\n'.join((f'| [[{s}|{t}]] | {desc} | {wr.bookname(src)} |' for s, t, desc, src in new_rows))
    lines.insert(insert_at, rows)
    new_body = '\n'.join(lines)
    return content.split(marker, 1)[0] + marker + new_body + tail

def build_biz_content(name, category, definition, description, by_file, key_rules, source_file, rels=None, name_to_slug=None):
    ls_section = render_ls_grouped(by_file)
    ls_section = f'\n\n## 原文关联\n\n{ls_section}' if ls_section else ''
    if key_rules:
        rules_rows = '\n'.join((f'| [[{s}|{t}]] | {desc} | {wr.bookname(src)} |' for s, t, desc, src in key_rules))
    else:
        rules_rows = f'| （待关联关联规则） | （待补充） | {wr.bookname(source_file)} |'
    # 关联对象表：规则驱动（2026-08-28 用户要求：业务对象间关联通过关联规则建立）
    if rels:
        rows = []
        for rname, rtype, rdesc, rsrc in rels:
            rc = canonicalize(rname)
            slug = (name_to_slug or {}).get(rc, '')
            cell = f'[[{slug}|{rname}]]' if slug else rname
            rows.append(f'| {cell} | {rtype} | {rdesc} | {wr.bookname(rsrc)} |')
        rel_rows = '\n'.join(rows)
    else:
        rel_rows = f'| （待补充） | （待补充） | （待补充） | {wr.bookname(source_file)} |'
    return f'# {name}\n\n{definition or description}\n\n## 基本信息\n\n| 字段 | 内容 |\n|---|---|\n| 所属本体 | 业务对象 > {category} > {name} |\n| 来源文档 | {wr.bookname(source_file)} |\n\n## 关联规则\n\n| 规则类型 | 内容 | 来源 |\n|---|---|---|\n{rules_rows}\n\n## 关联对象\n\n| 关联对象 | 关系类型 | 关系说明 | 来源 |\n|---|---|---|---|\n{rel_rows}{ls_section}'


def build_rels_from_rules(rule_entities, source_file):
    """规则驱动关联对象 v2（2026-08-28 优化：角色×类别联合映射关系类型 + 具体说明 + 去重）。

    每条规则卡：约束对象 X + 涉及对象 Y（带角色）→ 双向关系：
      Y 角色 = 主体 → 按类别：职责规则=管理职责 / 审批规则=审核职责 / 流程规则=执行职责；反向 被管理/被审核/被执行
      Y 角色 = 动作 → 产出关系（规则动作产生 Y）；反向 被产出
      Y 角色 = 条件 → 触发条件；反向 被触发
      Y 角色 = 客体 → 作用对象；反向 被作用
      其他/缺省 → 规则绑定；反向 被约束
    说明 = 规则《title》规定：<description 前 40 字>（具体可追溯）。
    输出前按 (关联名, 关系类型) 去重，同对实体多规则合并说明。
    返回 {实体canonical名: [(关联名, 关系类型, 说明, 来源)]}"""
    FWD_BY_ROLE = {
        '主体': {'职责规则': '管理职责', '审批规则': '审核职责', '流程规则': '执行职责'},
        '动作': {'*': '产出'},
        '条件': {'*': '触发条件'},
        '客体': {'*': '作用对象'},
    }
    REV_BY_ROLE = {
        '主体': {'职责规则': '被管理', '审批规则': '被审核', '流程规则': '被执行'},
        '动作': {'*': '被产出'},
        '条件': {'*': '被触发'},
        '客体': {'*': '被作用'},
    }
    DEFAULT_FWD, DEFAULT_REV = '规则绑定', '被约束'
    rels = {}
    for ent in rule_entities:
        X = (ent.get('constraint') or '').strip()
        title = (ent.get('title') or '').strip()
        cat = (ent.get('category') or '').strip()
        desc = (ent.get('description') or '').strip()[:40]
        note = f'规则《{title}》规定：{desc}' if desc else f'规则《{title}》'
        if not X:
            continue
        for o in (ent.get('objects') or []):
            Y = (o.get('name') or '').strip() if isinstance(o, dict) else str(o or '').strip()
            role = (o.get('role') or '').strip() if isinstance(o, dict) else ''
            if not Y or canonicalize(Y) == canonicalize(X):
                continue
            fwd = FWD_BY_ROLE.get(role, {}).get(cat) or FWD_BY_ROLE.get(role, {}).get('*') or DEFAULT_FWD
            rev = REV_BY_ROLE.get(role, {}).get(cat) or REV_BY_ROLE.get(role, {}).get('*') or DEFAULT_REV
            rels.setdefault(canonicalize(X), []).append((Y, fwd, note, source_file))
            rels.setdefault(canonicalize(Y), []).append((X, rev, note, source_file))
    # 去重：(关联名, 关系类型) 相同 → 合并说明（顿号连接多条规则）
    out = {}
    for k, lst in rels.items():
        seen = {}
        for item in lst:
            key = (item[0], item[1])
            if key in seen:
                old = seen[key]
                merged_note = old[2] + '；' + item[2] if item[2] not in old[2] else old[2]
                seen[key] = (old[0], old[1], merged_note, old[3])
            else:
                seen[key] = item
        out[k] = list(seen.values())
    return out

def build_rule_content(title, category, description, structure, constraint_link, by_file, source_file, objects=None, obj_links=None):
    struct_rows = []
    for k, v in structure.items():
        if v and str(v).strip():
            struct_rows.append(f'- {k}：{v}')
    struct_section = '\n'.join(struct_rows) if struct_rows else '- （待补充）'
    # 关联对象展示（2026-08-28 用户要求：规则涉及对象带角色标注；已建业务对象 → 链接）
    if objects:
        cells = []
        for o in objects:
            name = (o.get('name') or '').strip() if isinstance(o, dict) else str(o or '').strip()
            role = (o.get('role') or '').strip() if isinstance(o, dict) else ''
            if not name:
                continue
            slug = (obj_links or {}).get(canonicalize(name), '')
            cell = f'[[{slug}|{name}]]' if slug else name
            cells.append(f'{cell}（{role}）' if role else cell)
        obj_row = '、'.join(cells) if cells else '-'
    else:
        obj_row = '-'
    ls_section = render_ls_grouped(by_file)
    ls_section = f'\n## 原文关联\n\n{ls_section}' if ls_section else '\n## 原文关联\n\n- （待补充）'
    return f'# {title}\n\n**{category}**\u3000{description}\n\n## 基本信息\n\n| 字段 | 内容 |\n|---|---|\n| 规则类别 | {category} |\n| 约束对象 | {constraint_link} |\n| 关联对象 | {obj_row} |\n| 来源文档 | {wr.bookname(source_file)} |\n\n## 规则结构\n\n{struct_section}\n{ls_section}'

def load_registry(KB, page_type):
    """拉全库某类型页面，按 canonical 名索引：canonical -> [page, ...]。"""
    pages, page = ([], 1)
    while True:
        r = wr.list_wiki_pages(**{'kb_id': KB, 'page': page, 'page_size': 200})
        ps = r.get('pages') or []
        pages.extend((p for p in ps if not p.get('deleted_at') and p.get('page_type') == page_type))
        if len(ps) < 200:
            break
        page += 1
    reg = {}
    for p in pages:
        reg.setdefault(canonicalize(p.get('title', '')), []).append(p)
    return reg

def merge_page(KB, p, new_folder_id, kid, source_file, by_file=None, key_rules=None):
    """创建即合并：同名实体已存在 → update 现有页（folder_ids += 本目录、source_refs += 本 kid、
    原文关联按文件分组追加、关联规则合并）。幂等。

    2026-08-29 修复：同文档跨分类重复不追加目录。merge_page 原本为跨文档同名合并设计
    （不同文档的同一实体保留各文档目录），但 LLM 会把**同一文档内**同一实体归到两个不同
    分类（如「采购决策管理」→ 2-采购业务对象 和 9-制度概念），第二次命中时 new_folder_id
    被追加 → 实体双目录（0829 库实测 2 页中招）。判断：new_ref 已存在于 refs = 同文档重复
    → 只合并 content/refs，不追加目录（实体唯一分类，以首次为准）；跨文档才追加目录。
    """
    fids = list(p.get('folder_ids') or [p.get('folder_id')] or [])
    new_ref = f'{kid}|{source_file}'
    same_doc = new_ref in (p.get('source_refs') or [])
    if new_folder_id and new_folder_id not in fids and not same_doc:
        fids.append(new_folder_id)
    refs = list(p.get('source_refs') or [])
    if new_ref not in refs:
        refs.append(new_ref)
    content = p.get('content', '')
    if by_file:
        content = append_ls_grouped(content, by_file)
    if key_rules:
        content = append_key_rules(content, key_rules)
    # 2026-09-02 修复（坑 105）：合并后基本信息表「来源文档」行按 refs 并集重写——
    # 原只追加 source_refs/原文关联/关联规则，漏维护该行（跨文档合并后停留首创建者）。
    # refs 此时已含 new_ref（跨文档）或不变（同文档重复），重写幂等。
    content = wr.rewrite_source_line(content, refs)
    payload = {'kb_id': KB, 'slug': p['slug'], 'title': p.get('title', ''), 'content': content, 'folder_id': fids[0] if fids else '', 'folder_ids': fids, 'page_type': p.get('page_type', ''), 'status': p.get('status', 'published'), 'source_refs': refs}
    wr.update_page(**payload)

def create_page(KB, slug, title, content, folder_id, page_type, kid, source_file):
    """幂等建页 + 补写 source_refs（坑 2/3）。返回 'created' | 'exists' | 'error'。"""
    r = wr.read_page(**{'kb_id': KB, 'slug': slug})
    if 'error' not in r:
        return 'exists'
    r = wr.create_page(**{'kb_id': KB, 'slug': slug, 'title': title, 'content': content, 'folder_id': folder_id, 'page_type': page_type, 'source_refs': [f'{kid}|{source_file}']})
    if 'error' in r:
        err = str(r['error'])
        if '500' in err:
            r2 = wr.read_page(**{'kb_id': KB, 'slug': slug})
            return 'exists' if 'error' not in r2 else 'error'
        return 'error'
    wr.update_page(**{'kb_id': KB, 'slug': slug, 'title': title, 'content': content, 'folder_id': folder_id, 'page_type': page_type, 'status': 'published', 'source_refs': [f'{kid}|{source_file}']})
    return 'created'

def extract_rules_llm(ls_all, args):
    # 2026-08-29：--rule-file 单独存在即跳过规则 LLM（大文档规则抽取超时时，用
    # extract_rules_batch.py 分批补抽的产物直接建页；--skip-llm 仍整体跳过）
    if args.rule_file and os.path.exists(args.rule_file):
        with open(args.rule_file) as f:
            return json.load(f)
    if args.skip_llm:
        return []
    ls_text = '\n'.join((f"{i + 1}. {item['title']}: {item['text'][:400]}" for i, item in enumerate(ls_all)))
    result = wr.llm_call([{'role': 'system', 'content': f'你是{SYSTEM_ROLE}，只输出JSON。'}, {'role': 'user', 'content': RULE_PROMPT + '\n\n长句列表：\n' + ls_text}], model=args.model or wr.DEFAULT_MODEL, max_tokens=args.max_tokens)
    result = wr.ensure_full_json(result)
    try:
        ents = json.loads(result)
        # 2026-08-29 结构归一化（与 extract_biz_llm 同款防御）：LLM 偶发嵌套结构会致 e.get() 崩溃
        normalized = []
        def _collect(x):
            if isinstance(x, dict):
                normalized.append(x)
            elif isinstance(x, list):
                for y in x:
                    _collect(y)
        _collect(ents)
        print(f'  解析到 {len(normalized)} 个关联规则（原始 {len(ents)}）')
        return normalized
    except json.JSONDecodeError as e:
        print(f'  关联规则 JSON 解析失败: {e}')
        print(f'  响应前300字: {result[:300]}')
        return []

def extract_biz_llm(ls_all, constraints, args):
    if args.skip_llm and args.biz_file:
        with open(args.biz_file) as f:
            return json.load(f)
    if args.skip_llm:
        return []
    constraint_text = '\n'.join((f'- {c}' for c in constraints if c)) or '- （无）'
    terms_txt = ''
    if getattr(args, 'terms_file', None) and os.path.exists(args.terms_file):
        try:
            with open(args.terms_file, encoding='utf-8') as f:
                tlist = json.load(f)
            terms_txt = '\n'.join((f"- {t.get('word', '')}" for t in tlist[:50] if t.get('word')))
        except Exception:
            terms_txt = ''
    prompt = BIZ_PROMPT.format(constraints=constraint_text, terms=terms_txt or '- （无）')
    ls_text = '\n'.join((f"{i + 1}. {item['text'][:400]}" for i, item in enumerate(ls_all)))
    result = wr.llm_call([{'role': 'system', 'content': f'你是{SYSTEM_ROLE}，只输出JSON。'}, {'role': 'user', 'content': prompt + '\n\n长句列表：\n' + ls_text}], model=args.model or wr.DEFAULT_MODEL, max_tokens=args.max_tokens)
    result = wr.ensure_full_json(result)
    try:
        ents = json.loads(result)
        # 2026-08-29 结构归一化：LLM 偶发返回嵌套/混合结构（如 [[{...}], {...}] 或 [["x",...], {...}]），
        # 不归一化会导致 main() 第 460 行 e.get('name') 崩溃（'list' object has no attribute 'get'），
        # 业务对象全量丢失（0829 库 55号通知实测：解析到 35 个实体但全部未建页）。
        normalized = []
        def _collect(x):
            if isinstance(x, dict):
                normalized.append(x)
            elif isinstance(x, list):
                for y in x:
                    _collect(y)
        _collect(ents)
        print(f'  解析到 {len(normalized)} 个业务对象（原始 {len(ents)}）')
        return normalized
    except json.JSONDecodeError as e:
        print(f'  业务对象 JSON 解析失败: {e}')
        print(f'  响应前300字: {result[:300]}')
        return []

def main():
    parser = argparse.ArgumentParser(description='单文件实体抽取 + 建页（规则优先 + 创建即合并）')
    parser.add_argument('--ls-file', required=True, help='长句列表 JSON (ls_all.json)')
    parser.add_argument('--cat-ids', required=True, help='类别目录 ID 映射 JSON')
    parser.add_argument('--kid', required=True, help='目标文档 knowledge_id')
    parser.add_argument('--kb', required=True, help='知识库 ID')
    parser.add_argument('--source-file', default='', help='来源文档原始文件名')
    parser.add_argument('-o', '--output', default='/tmp/entity_slug_map.json', help='entity_slug_map 输出路径')
    parser.add_argument('--model', default=None, help='LLM 模型名（默认 wr.DEFAULT_MODEL）')
    parser.add_argument('--max-tokens', type=int, default=20000, help='LLM max_tokens')
    parser.add_argument('--terms-file', default=None, help='高频候选术语 JSON（discover_terms.py 输出，注入业务抽取 prompt）')
    parser.add_argument('--skip-llm', action='store_true', help='跳过 LLM 调用（仅建页，需预存 JSON）')
    parser.add_argument('--biz-file', help='预存的业务对象 JSON 文件路径')
    parser.add_argument('--rule-file', help='预存的关联规则 JSON 文件路径')
    args = parser.parse_args()
    with open(args.ls_file) as f:
        ls_all = json.load(f)
    with open(args.cat_ids) as f:
        cat_data = json.load(f)
    BIZ_CAT_IDS = cat_data.get('biz', {})
    RULE_CAT_IDS = cat_data.get('rule', {})
    KID = args.kid
    KB = args.kb
    SOURCE_FILE = args.source_file or '（来源文件）'
    wr.load_config()
    wr.mcp_init()
    entity_slug_map = {}
    print('加载全库业务对象注册表...')
    biz_reg = load_registry(KB, 'business_ontology')
    # 关联规则 2026-08-28 起不合并（每篇文档独立建页），无需规则注册表
    print(f'  业务对象注册表: {sum((len(v) for v in biz_reg.values()))} 页 / {len(biz_reg)} 名')
    print('=' * 60)
    print('Step 1: 主谓联合抽取 (B2: 业务对象+关联规则同批产出，约束对象名称空间闭包)')
    print('=' * 60)
    rule_entities, biz_entities = extract_joint_llm(ls_all, args)
    print(f'关联规则: {len(rule_entities)} 个 | 业务对象: {len(biz_entities)} 个（联合产物）')
    print('=' * 60)
    print('Step 2: 建关联规则页（创建即合并）')
    print('=' * 60)
    created = skipped = merged = errors = 0
    for ent in rule_entities:
        title = (ent.get('title') or '').strip()
        category = (ent.get('category') or '').strip()
        structure = ent.get('structure', {}) or {}
        description = (ent.get('description') or '').strip()
        constraint = (ent.get('constraint') or '').strip()
        if not title or not category:
            continue
        cat_dir = RULE_CAT_SHORT.get(category)
        if not cat_dir:
            print(f'  [SKIP] {title}: 类别 {category} 不匹配')
            continue
        cat_id = RULE_CAT_IDS.get(cat_dir)
        if not cat_id:
            print(f'  [SKIP] {title}: 找不到目录ID {cat_dir}')
            continue
        ro_num = RO_NUM_MAP.get(category, 0)
        evidence = (ent.get('evidence') or '').strip()
        slug = rule_slug(ro_num, title, evidence=evidence)
        constraint_link = constraint or '-'
        if constraint:
            for cand in biz_reg.get(canonicalize(constraint), []):
                constraint_link = f"[[{cand['slug']}|{constraint}]]"
                break
        # 关联规则必须有原文关联（2026-09-02 修改：title 改为 LLM 概括，不再 text_only 匹配 title；
        # 改为匹配 evidence 原文片段；无长句链接仍不允许创建无依据规则页）
        ls_links = find_ls_slugs(ls_all, evidence, text_only=True) if evidence else []
        if not ls_links:
            print(f'  [SKIP] {title}: 未匹配到任何长句原文（evidence 未命中），不允许创建无原文关联的规则页')
            entity_slug_map[title] = ''
            continue
        by_file = {SOURCE_FILE: ls_links}
        # 关联对象链接：规则涉及对象中已存在的业务对象 → 链接（2026-08-28 新增展示）
        obj_links = {}
        for o in (ent.get('objects') or []):
            nm = (o.get('name') or '').strip() if isinstance(o, dict) else str(o or '').strip()
            if nm:
                for cand in biz_reg.get(canonicalize(nm), []):
                    obj_links[canonicalize(nm)] = cand['slug']
                    break
        content = build_rule_content(title, category, description, structure, constraint_link, by_file, SOURCE_FILE,
                                     objects=ent.get('objects') or [], obj_links=obj_links)
        st = create_page(KB, slug, title, content, cat_id, 'rule_ontology', KID, SOURCE_FILE)
        if st == 'created':
            created += 1
            print(f'  [OK] {title} -> {slug} ({category})')
        elif st == 'exists':
            skipped += 1
            print(f'  [SKIP] {title} -> {slug}（已存在）')
        else:
            errors += 1
            print(f'  [ERR] {title}: 创建失败')
        entity_slug_map[title] = slug
    print('=' * 60)
    print('Step 3: 业务对象（Step 1 联合产物；此处做规则对象程序兜底补建）')
    print('=' * 60)
    constraints = [e.get('constraint', '').strip() for e in rule_entities if e.get('constraint', '').strip()]
    # 规则涉及对象（objects）展平：约束对象之外，规则主体/条件/动作中的对象也应成为业务对象
    rule_objects = []
    for e in rule_entities:
        for o in (e.get('objects') or []):
            name = (o.get('name') or '').strip() if isinstance(o, dict) else str(o or '').strip()
            if name:
                rule_objects.append(name)
    all_rule_objects = constraints + rule_objects
    # Step 3.5: 规则对象程序兜底补建（2026-08-28 用户要求：规则约束对象/涉及对象本身就是业务对象）
    # LLM 可能漏提取——对每个有原文依据的规则对象，自动补建业务对象（category 制度对象，
    # 原文关联逐字匹配；关联规则在 Step 4 由 rules_by_constraint 自动回填）
    biz_names = {canonicalize(e.get('name') or '') for e in biz_entities}
    for c in all_rule_objects:
        cc = canonicalize(c)
        if not cc or cc in biz_names:
            continue
        if not find_ls_slugs(ls_all, c, text_only=True):
            print(f'  [SKIP] 规则对象补建: {c}（原文无逐字依据）')
            continue
        biz_entities.append({'name': c, 'category': '制度对象',
                             'definition': '规则对象（规则约束或涉及的业务对象，来自关联规则）',
                             'description': ''})
        biz_names.add(cc)
        print(f'  [AUTO] 规则对象补建业务对象: {c}')
    print(f'业务对象: {len(biz_entities)} 个（含规则对象补建）')
    # 规则驱动关联对象（2026-08-28 用户要求：业务对象间关联通过关联规则建立）
    rels_map = build_rels_from_rules(rule_entities, SOURCE_FILE)
    # 实体名 → category 映射（关联链接 slug 计算用）
    biz_cats = {canonicalize(e.get('name') or ''): (e.get('category') or '').strip() for e in biz_entities}
    print('=' * 60)
    print('Step 4: 建业务对象页（创建即合并 + 关联规则填充）')
    print('=' * 60)
    rules_by_constraint = {}
    for ent in rule_entities:
        c = canonicalize((ent.get('constraint') or '').strip())
        if not c:
            continue
        t = (ent.get('title') or '').strip()
        cat = (ent.get('category') or '').strip()
        evidence = (ent.get('evidence') or '').strip()
        if not t:
            continue
        rules_by_constraint.setdefault(c, []).append((rule_slug(RO_NUM_MAP.get(cat, 0), t, evidence=evidence), t, (ent.get('description') or '')[:80]))
    for ent in biz_entities:
        name = (ent.get('name') or '').strip()
        category = (ent.get('category') or '').strip()
        definition = (ent.get('definition') or '').strip()
        description = (ent.get('description') or '').strip()
        if not name or not category:
            continue
        cat_dir = BIZ_CAT_SHORT.get(category)
        if not cat_dir:
            print(f'  [SKIP] {name}: 找不到类别 {category}')
            continue
        cat_id = BIZ_CAT_IDS.get(cat_dir)
        if not cat_id:
            print(f'  [SKIP] {name}: 找不到目录ID {cat_dir}')
            continue
        slug = entity_slug(category, name)
        ls_links = find_ls_slugs(ls_all, name, text_only=True)
        if not ls_links:
            print(f'  [SKIP] {name}: 未匹配到任何长句，不允许创建无原文关联的实体页')
            entity_slug_map[name] = ''
            continue
        by_file = {SOURCE_FILE: ls_links}
        key_rules = [(s, t, desc, SOURCE_FILE) for s, t, desc in rules_by_constraint.get(canonicalize(name), [])]
        my_rels = rels_map.get(canonicalize(name), [])
        name_to_slug = {}
        for e in biz_entities:
            nm = (e.get('name') or '').strip()
            cat = (e.get('category') or '').strip()
            if nm and cat:
                name_to_slug[canonicalize(nm)] = entity_slug(cat, nm)
        # 跨文档已存在的业务对象也参与链接（2026-08-28 优化：关联对象行不因本文件未提取而丢链接）
        for cname, pages in biz_reg.items():
            if pages and cname not in name_to_slug:
                name_to_slug[cname] = pages[0]['slug']
        matches = biz_reg.get(canonicalize(name), [])
        if matches:
            p = matches[0]
            merge_page(KB, p, cat_id, KID, SOURCE_FILE, by_file=by_file, key_rules=key_rules)
            merged += 1
            entity_slug_map[name] = p['slug']
            print(f"  [MERGE] {name} -> {p['slug']}（已有 {len(p.get('source_refs') or [])} 来源, 关联规则 {len(key_rules)}）")
            continue
        content = build_biz_content(name, category, definition, description, by_file, key_rules, SOURCE_FILE, rels=my_rels, name_to_slug=name_to_slug)
        st = create_page(KB, slug, name, content, cat_id, 'business_ontology', KID, SOURCE_FILE)
        if st == 'created':
            created += 1
            print(f'  [OK] {name} -> {slug} ({category}, 关联规则 {len(key_rules)})')
            biz_reg.setdefault(canonicalize(name), []).append({'slug': slug, 'title': name, 'folder_id': cat_id, 'folder_ids': [cat_id], 'source_refs': [f'{KID}|{SOURCE_FILE}'], 'page_type': 'business_ontology', 'content': content})
        elif st == 'exists':
            skipped += 1
            print(f'  [SKIP] {name} -> {slug}（已存在）')
        else:
            errors += 1
            print(f'  [ERR] {name}: 创建失败')
        entity_slug_map[name] = slug
    print('=' * 60)
    print('Step 5: 规则页约束对象链接回填')
    print('=' * 60)
    backfilled = 0
    for ent in rule_entities:
        constraint = (ent.get('constraint') or '').strip()
        t = (ent.get('title') or '').strip()
        cat = (ent.get('category') or '').strip()
        if not constraint or not t or (not cat):
            continue
        biz_slug = entity_slug_map.get(constraint, '')
        if not biz_slug:
            continue
        ro_num = RO_NUM_MAP.get(cat, 0)
        evidence = (ent.get('evidence') or '').strip()
        slug = rule_slug(ro_num, t, evidence=evidence)
        r = wr.read_page(**{'kb_id': KB, 'slug': slug})
        if 'error' in r:
            continue
        content = r.get('content', '')
        plain = f'| 约束对象 | {constraint} |'
        linked = f'| 约束对象 | [[{biz_slug}|{constraint}]] |'
        if plain in content and linked not in content:
            nc = content.replace(plain, linked)
            wr.update_page(**{'kb_id': KB, 'slug': slug, 'title': r.get('title', t), 'content': nc, 'folder_id': r.get('folder_id', ''), 'folder_ids': r.get('folder_ids') or [r.get('folder_id', '')], 'page_type': 'rule_ontology', 'status': r.get('status', 'published'), 'source_refs': r.get('source_refs') or []})
            backfilled += 1
            print(f'  [LINK] {t} -> [[{biz_slug}|{constraint}]]')
    with open(args.output, 'w') as f:
        json.dump(entity_slug_map, f, ensure_ascii=False, indent=2)
    b_count = len([v for v in entity_slug_map.values() if v.startswith('entity-b')])
    r_count = len([v for v in entity_slug_map.values() if v.startswith('entity-r')])
    print(f"\n{'=' * 60}")
    print(f'完成！创建: {created}, 合并: {merged}, 跳过: {skipped}, 错误: {errors}, 约束回填: {backfilled}')
    print(f'业务对象: {b_count}, 关联规则: {r_count}, 合计: {len(entity_slug_map)}')
    print(f'entity_slug_map 已保存到 {args.output}')
    print(f"{'=' * 60}")
if __name__ == '__main__':
    main()