"""清理存量摘要标题行（幂等可重放）：把 kb_document.summary 开头的
'# 《文档名》文档摘要' / markdown 标题行剥掉，只留摘要正文。

对齐三个摘要编排 2026-10-14 的整理 script 语义（META 正则的标题行分支：
^#{1,6}\s 与 《[^》]{1,80}》\s*文档摘要）。只剥**首行**标题（若存在），
正文逐行保留（含正文中可能出现的 # 引用标题，不误伤）。

用法：docker cp scripts/clean_summary_titles.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/clean_summary_titles.py
"""
import re

from sqlalchemy import select, update

from api.db import get_sessionmaker
from api.models.knowledge import KbDocument

# 标题行判定：markdown 标题（# 开头）且含《》文档摘要 字样；或纯《文档名》文档摘要行
_TITLE_RE = re.compile(r"^\s*#{1,6}\s*《[^》]{1,80}》\s*文档摘要\s*$|^\s*《[^》]{1,80}》\s*文档摘要\s*$")
# 兜底：任意 markdown 标题行（# 开头），仅当它是摘要文本首行
_MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")


def strip_title(summary: str) -> str:
    """剥掉摘要文本开头的标题行（仅首行；正文其余行不动）。"""
    if not summary:
        return summary
    lines = summary.split("\n")
    first = lines[0]
    if _TITLE_RE.match(first):
        lines = lines[1:]
    elif _MD_HEADING_RE.match(first) and len(lines) > 1:
        # 裸 markdown 标题（无《》文档摘要 字样）只剥首行，保留正文
        lines = lines[1:]
    out = "\n".join(lines).strip()
    return out


def main():
    s = get_sessionmaker()()
    try:
        rows = s.execute(
            select(KbDocument.id, KbDocument.summary).where(
                KbDocument.summary.isnot(None), KbDocument.summary != ""
            )
        ).all()
        changed = 0
        skipped = 0
        for doc_id, summary in rows:
            if summary is None:
                continue
            stripped = strip_title(summary)
            if stripped != summary:
                s.execute(
                    update(KbDocument)
                    .where(KbDocument.id == doc_id)
                    .values(summary=stripped)
                )
                changed += 1
            else:
                skipped += 1
        s.commit()
        print(f"DONE: 扫描 {len(rows)} 条摘要，清理标题行 {changed} 条，无需处理 {skipped} 条")
    finally:
        s.close()


if __name__ == "__main__":
    main()