# -*- coding: utf-8 -*-
r"""从 AGE3 源码 Lists.cpp 抽取“效果命令类型”全表，并配上中文（取自既有译文表）。

产出 工作/datsearch/cmd_table.py：
    CMD_TYPES = {0: {"en": "Attribute Modifier (Set)", "zh": "属性修正（设置）"}, ...}
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))          # 工作区1
LISTS = os.path.join(ROOT, "工作", "src", "AGE", "AGE_Frame", "Lists.cpp")
PLAN = os.path.join(ROOT, "工作", "used_plan.json")
OUT = os.path.join(HERE, "cmd_table.py")

ADD_RE = re.compile(r'effect_type_names\.Add\("([^"]+)"\)')


def main():
    with open(LISTS, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    raw = []
    for m in ADD_RE.finditer(text):
        s = m.group(1)
        mm = re.match(r"^(\d+)\s*-\s*(.+)$", s)
        if mm:
            raw.append((int(mm.group(1)), mm.group(2).strip(), s))
    # 去重（保留首个出现的英文原文）
    seen, items = set(), []
    for n, en, full in raw:
        if n in seen:
            continue
        seen.add(n)
        items.append((n, en, full))

    zh_map = {}
    if os.path.isfile(PLAN):
        with open(PLAN, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        bank = {}
        if isinstance(data, dict):
            bank = {str(k): v for k, v in data.items()}
        else:
            for row in data:
                if isinstance(row, (list, tuple)) and len(row) >= 3:
                    bank[str(row[1])] = row[2]
                elif isinstance(row, dict) and "en" in row:
                    bank[str(row["en"])] = row.get("zh")
        zh_map = bank

    out = {}
    misses = []
    for n, en, full in items:
        zh = zh_map.get(full) or zh_map.get(en) or ""
        if not zh:
            misses.append((n, en))
        out[n] = {"en": en, "zh": zh}

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# -*- coding: utf-8 -*-\n")
        fh.write('"""效果命令类型全表（由 make_cmd_table.py 从 AGE3 源码 Lists.cpp 生成）。"""\n\n')
        fh.write("CMD_TYPES = {\n")
        for n in sorted(out):
            fh.write("    %d: {\"en\": %r, \"zh\": %r},\n" % (n, out[n]["en"], out[n]["zh"]))
        fh.write("}\n\n")
        fh.write("CMD_ZH = {n: (v[\"zh\"] or v[\"en\"]) for n, v in CMD_TYPES.items()}\n")
        fh.write("CMD_EN = {n: v[\"en\"] for n, v in CMD_TYPES.items()}\n\n")
        fh.write("if __name__ == \"__main__\":\n")
        fh.write("    for n in sorted(CMD_TYPES):\n")
        fh.write("        print(\"%4d  %-48s %s\" % (n, CMD_TYPES[n][\"en\"], CMD_TYPES[n][\"zh\"]))\n")
    print("写出 %s：%d 种命令类型，其中无译文 %d 条" % (OUT, len(out), len(misses)))
    for n, en in misses:
        print("   缺译文 %4d  %s" % (n, en))
    return 0


if __name__ == "__main__":
    sys.exit(main())
