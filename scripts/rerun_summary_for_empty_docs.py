"""META 清洗正则收敛后，为存量空摘要文档重跑摘要编排（2026-10-15）。

背景：Map-Reduce 落页准备的 META 正则把正文高频词行（需要/要求/需求等）全部
删除，导致 5244 字合并稿被删空（B1 文档）与 LLM 合并空输出（C9 文档）两篇
kb_document.summary 为空。apply_fix_summary_cleanup_meta.py 已收敛正则并同步
三处，本脚本按 auto 路由重新触发这两个文档的摘要编排（Map-Reduce tape
b5954467d5344db0b87664477bcd1cac）。

用法：docker cp scripts/rerun_summary_for_empty_docs.py kb-api-server:/tmp/ &&
      docker exec kb-api-server /srv/kb/.venv/bin/python /tmp/rerun_summary_for_empty_docs.py
"""
from worker.tasks.doc_process import _enqueue_summary_task

KB_ID = "087150f1cd2c47649fa2362334cfb1a9"
DOCS = [
    "520e15f3899f4f74aea554cbc221d031",  # B1 18 集中采购需求管理办法（META 删光）
    "b0f03448148d4d76b915329d8781b855",  # C9 18 简易采购实施细则（LLM 合并空输出）
]


def main() -> None:
    from worker.builtin_engine.scripts.weknora_rpc import _login

    cookie = _login()
    for doc_id in DOCS:
        run_id = _enqueue_summary_task(KB_ID, doc_id, parent_job_id=None, cookie=cookie)
        print(f"{doc_id} -> run_id={run_id}")


if __name__ == "__main__":
    main()