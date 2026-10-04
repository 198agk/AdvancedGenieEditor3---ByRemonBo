# -*- coding: utf-8 -*-
r"""检索工具自检：不弹窗，直接对已建好的索引跑一组查询并核对预期结果。

用法:
    python selftest.py [dat路径] [语言表路径]
不传参数时使用 config.json 里记录的数据文件。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tkinter as tk  # noqa: E402
import index_build      # noqa: E402
import datsearch        # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("%-44s %s %s" % (name, "OK  " if cond else "FAIL", detail))
    if not cond:
        FAILS.append(name)


def main(argv):
    cfg = datsearch.load_config()
    dat = argv[1] if len(argv) > 1 else cfg.get("dat_path", "")
    lang = argv[2] if len(argv) > 2 else cfg.get("lang_path", "")
    if not dat or not os.path.isfile(dat):
        print("没有可用的数据文件，先运行 datsearch.py 或传入 dat 路径")
        return 2
    index, cache, rebuilt = index_build.cached(
        dat, lang or None, None, index_dir=datsearch.CFG_DIR)
    print("索引: %s（重建=%s）" % (cache, rebuilt))
    print("计数: %s\n" % index["meta"]["counts"])

    root = tk.Tk()
    root.withdraw()                     # 不显示窗口
    app = datsearch.App(root)
    app.index = index
    app.cache_file = cache
    app.civ_combo["values"] = ["全部（盖亚/基础）"] + ["%d - %s" % (c["i"], c["name"])
                                                    for c in index["civs"]]
    app.civ_combo.current(0)
    app.civ_pick["values"] = ["%d - %s" % (c["i"], c["name"]) for c in index["civs"]]
    app.civ_pick.current(0)

    def run(q, cats=("单位", "科技")):
        app.query_var.set(q)
        for c, v in app.cat_vars.items():
            v.set(c in cats)
        app.do_search()
        return app.results

    print("=== 基本计数 ===")
    check("科技 1510 条", index["meta"]["counts"]["techs"] == 1510,
          index["meta"]["counts"]["techs"])
    check("效果 1500 条", index["meta"]["counts"]["effects"] == 1500,
          index["meta"]["counts"]["effects"])
    check("文明 63 个", index["meta"]["counts"]["civs"] == 63)
    check("磨坊(68) 恰好 6 个科技",
          len(index["refs"]["tech_at_unit"].get(68, [])) == 6,
          str(index["refs"]["tech_at_unit"].get(68)))

    print("\n=== 查询：研究点=68（磨坊科技）===")
    rows = run("研究点=68", cats=("科技",))
    names = [(r["id"], r["name"], r["zh"]) for r in rows]
    for n in names:
        print("   ", n)
    check("按研究点筛磨坊命中 6 条", len(names) == 6, len(names))
    check("含 轮作 / 重犁 / 马轭",
          {"轮作", "重犁", "马轭"} <= {n[2] for n in names})

    print("\n=== 查询：关键词 磨坊 ===")
    rows = run("磨坊")
    print("    命中 %d 条，示例 %s" % (len(rows), [(r["cat"], r["id"], r["name"], r["zh"])
                                                 for r in rows[:5]]))
    check("关键词命中 > 0", len(rows) > 0, len(rows))

    print("\n=== 查询：生命>500（单位）===")
    rows = run("生命>500", cats=("单位",))
    print("    命中 %d 条，示例 %s" % (len(rows), [(r["id"], r["name"], r["rec"].get("HitPoints"))
                                                 for r in rows[:5]]))
    check("血量筛选有结果", len(rows) > 0, len(rows))
    check("结果血量都 > 500", all((r["rec"].get("HitPoints") or 0) > 500 for r in rows))

    print("\n=== 详情面板：科技 12（轮作）===")
    rows = run("#12", cats=("科技",))
    check("按编号命中科技 12", rows and rows[0]["id"] == 12, [r["id"] for r in rows[:3]])
    text = app.describe(rows[0])
    print("\n".join("    " + ln for ln in text.splitlines()[:14]))
    check("详情含研究地点与中文名", "研究地点" in text and "轮作" in text)
    check("详情含效果命令解读", "效果" in text)

    print("\n=== 详情面板：单位 83（长弓兵）===")
    rows = run("#83", cats=("单位",))
    if rows:
        text = app.describe(rows[0])
        print("\n".join("    " + ln for ln in text.splitlines()[:16]))
        check("单位详情含攻击/护甲/训练场所", "攻击加成" in text or "护甲" in text
              or "训练场所" in text)

    print("\n=== 效果：命令翻译 ===")
    rows = run("命令数>3", cats=("效果",))
    print("    命中 %d 条，示例 %s" % (len(rows), [(r["id"], r["name"]) for r in rows[:3]]))
    if rows:
        text = app.describe(rows[0])
        print("\n".join("    " + ln for ln in text.splitlines()[:8]))
    text = app.describe(rows[0]) if rows else ""
    lines = [ln for ln in text.splitlines() if "•" in ln]
    check("效果命令能翻译成人话",
          bool(lines) and all("→" in ln for ln in lines)
          and not any("命令 " in ln for ln in lines))

    print("\n=== 文明特性：不列颠 ===")
    app.civ_pick.set([v for v in app.civ_pick["values"] if "British" in v][0])
    app.show_civ()
    kinds = {}
    for item in app.civ_tree.get_children():
        v = app.civ_tree.item(item, "values")
        kinds[v[0]] = kinds.get(v[0], 0) + 1
    print("    特性条目统计:", kinds)
    check("不列颠有文明加成", kinds.get("文明加成", 0) >= 4, kinds)
    check("不列颠有独特科技/其他条目", sum(kinds.values()) >= 9, kinds)

    print("\n=== 跨文明对比：同一个单位在不同文明 ===")
    uid = 83
    diffs = [(c, index["unit_diffs"].get(c, {}).get(uid)) for c in range(0, 63)]
    changed = [(c, d) for c, d in diffs if d]
    print("    单位 83 有差异的文明数: %d" % len(changed))
    if changed:
        c, d = changed[0]
        print("    示例 civ %d: %s" % (c, {k: v for k, v in list(d.items())[:6]}))
    check("单位差异表可用", isinstance(index["unit_diffs"], dict))

    root.destroy()
    print("\n===== 自检结果: %s =====" % ("全部通过" if not FAILS else "失败 %d 项: %s" % (len(FAILS), FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
