#!/usr/bin/env python3
"""build_wiki.py — 单文档 wiki 构建标准入口（兼容别名，实际执行 run_one.py）。

agent 提示词中的 script 名即本文件：无参亦可（kid 从环境
WEKNORA_KNOWLEDGE_ID 读取）；也可显式传参透传给 run_one.py：

    python3 build_wiki.py [kid] [--kb KB_ID] [--format doc|docx|pdf|xls|xlsx] [--title T]

注意：build_full.py 是底层阶段脚本（需要 <kid> <family> <docx> <source> 四个位置
参数），**不是 agent 入口**，直接调用会报缺参。
"""
import os
import runpy
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

args = sys.argv[1:]
if not args:
    kid = os.environ.get("WEKNORA_KNOWLEDGE_ID", "").strip()
    if kid:
        args = [kid]
    else:
        sys.stderr.write(
            "build_wiki.py: 未提供 kid，且环境变量 WEKNORA_KNOWLEDGE_ID 为空；"
            "请传参 python3 build_wiki.py <kid>\n"
        )
        sys.exit(2)

sys.argv = ["run_one.py"] + args
runpy.run_path(os.path.join(_HERE, "run_one.py"), run_name="__main__")
