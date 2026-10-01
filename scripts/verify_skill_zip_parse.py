"""Quick parse_skill_zip layout verification (three ZIP layouts)."""

import io
import zipfile

from api.services.skills import parse_skill_zip


def mk_zip(layout: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for path, content in layout.items():
            z.writestr(path, content)
    return buf.getvalue()


root_zip = mk_zip(
    {
        "SKILL.md": "---\nname: my-skill\ndescription: 测试技能\nversion: 1.0\n---\n\n正文",
        "scripts/x.py": "print(1)",
    }
)
print("root layout ->", parse_skill_zip(root_zip))

nested_zip = mk_zip(
    {
        "my-skill/SKILL.md": "---\nname: nested-skill\ndescription: 嵌套\n---\n\n正文",
        "my-skill/scripts/a.py": "print(1)",
        "my-skill/sub/SKILL.md": "---\nname: sub\n---\nx",
    }
)
print("nested layout ->", parse_skill_zip(nested_zip))

try:
    parse_skill_zip(mk_zip({"a.txt": "x"}))
except ValueError as e:
    print("no SKILL.md ->", e)