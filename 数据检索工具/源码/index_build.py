# -*- coding: utf-8 -*-
r"""索引构建器：把 AoE2DE 的 .dat 数据文件解析成可供检索的紧凑索引（pickle 缓存）。

设计要点
--------
* 只依赖同目录的 datlib.py（自定义 dat 解析器）与 Python 标准库，不依赖任何外部工作区脚本。
* 单位表按「基底 + 差异」存储：文明 0（盖亚）存全量单位字段，其余文明只存与盖亚不同的字段。
  这样 63 个文明 × 2750 单位 ≈ 17 万条记录既省内存，也天然支持“跨文明对比”。
* 反向引用表：单位→科技（研究点）、科技→前置科技、效果→科技、单位→训练场所、单位→改动它的效果。
* 索引以 dat 的路径+大小+修改时间做失效判断，缓存为 pickle。

命令行自检：
    python index_build.py <dat路径> [语言表路径] [--out 缓存路径] [--check]
"""
from __future__ import annotations

import os
import re
import sys
import time
import pickle

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import datlib  # noqa: E402

# ---------------------------------------------------------------- 字段摘取 ---
UNIT_DROP = {"DeadFish", "Bird", "Type50", "Projectile", "Creatable", "Building",
             "ResourceStorages", "DamageGraphics", "SelectionEffect"}
UNIT_SCALARS = [s for s in datlib.Unit.__slots__ if s not in UNIT_DROP]
TYPE50_SCALARS = [s for s in datlib.Type50.__slots__ if s not in ("Attacks", "Armours")]
CREATABLE_SCALARS = [s for s in datlib.Creatable.__slots__
                     if s not in ("ResourceCosts", "TrainLocations")]
BUILDING_SCALARS = list(datlib.Building.__slots__)
GRAPHIC_SCALARS = [s for s in datlib.Graphic.__slots__
                   if s not in ("Deltas", "AngleSounds", "SpritePtr")]
SOUND_SCALARS = [s for s in datlib.Sound.__slots__ if s != "Items"]
TERRAIN_SCALARS = list(datlib.Terrain.__slots__)
TECH_SCALARS = list(datlib.Tech.__slots__)


def _plain(v):
    """把解析器返回的 DStr 等类型转换成可直接 pickle 的普通 Python 值。"""
    if isinstance(v, str):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


def _scalars(obj, names):
    out = {}
    for n in names:
        try:
            v = getattr(obj, n)
        except Exception:
            continue
        if v is None:
            continue
        out[n] = _plain(v)
    return out


def _pairs(items):
    """AttackOrArmor 列表 -> [(Class, Amount)]"""
    out = []
    for it in items or ():
        try:
            out.append((getattr(it, "Class", -1), getattr(it, "Amount", 0)))
        except Exception:
            pass
    return out


def _costs(items):
    out = []
    for it in items or ():
        try:
            amt = getattr(it, "Amount", 0)
            if amt:
                out.append((getattr(it, "Type", -1), amt))
        except Exception:
            pass
    return out


def _train_locations(items):
    out = []
    for it in items or ():
        try:
            out.append((getattr(it, "UnitID", -1), getattr(it, "TrainTime", 0),
                        getattr(it, "ButtonID", -1), getattr(it, "HotKeyID", -1)))
        except Exception:
            pass
    return out


# ------------------------------------------------------------- 语言字符串表 ---
LANG_LINE = re.compile(r'^\s*(\d+)\s*"(.*)"\s*$')


def load_lang(path):
    """读取游戏/工具的 key-value 字符串表：每行 `ID "文本"`，返回 {id: 文本}。"""
    table = {}
    if not path or not os.path.isfile(path):
        return table
    with open(path, "rb") as fh:
        raw = fh.read()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "replace")
    for line in text.splitlines():
        m = LANG_LINE.match(line)
        if m:
            table[int(m.group(1))] = m.group(2)
    return table


def sibling_lang_files(path):
    """同一个 strings/key-value 目录下的全部字符串表文件（DLC 专属表也在里面）。
    返回按“先 DLC、后主表”排序的列表，主表最后读，同 ID 时主表优先。"""
    if not path:
        return []
    ap = os.path.abspath(path)
    d = os.path.dirname(ap)
    if not os.path.isdir(d):
        return [ap] if os.path.isfile(ap) else []
    files = []
    for fn in os.listdir(d):
        fp = os.path.join(d, fn)
        if fn.lower().endswith(".txt") and os.path.isfile(fp):
            files.append(fp)
    base = os.path.basename(ap).lower()
    files.sort(key=lambda p: (os.path.basename(p).lower() == base, os.path.basename(p).lower()))
    # 附带 resources\_common\strings\key-value\*.txt（非本地化字符串，优先级最低）
    strings_dir = os.path.dirname(d)
    res_dir = os.path.dirname(strings_dir)
    if os.path.basename(res_dir).lower() == "resources":
        common = os.path.join(res_dir, "_common", "strings", "key-value")
        if os.path.isdir(common):
            extra = [os.path.join(common, fn) for fn in sorted(os.listdir(common))
                     if fn.lower().endswith(".txt") and os.path.isfile(os.path.join(common, fn))]
            files = extra + files
    return files


def load_lang_multi(path):
    """合并读取该目录下所有 key-value 字符串表。返回 (表, 参与合并的文件列表)。"""
    files = sibling_lang_files(path)
    table = {}
    for fp in files:
        table.update(load_lang(fp))
    return table, files


def lang_signature(path):
    """语言表的失效标记：主表路径 + 目录下所有表的文件名/大小/修改时间。"""
    files = sibling_lang_files(path)
    parts = []
    for fp in files:
        try:
            st = os.stat(fp)
            parts.append("%s:%d:%d" % (os.path.basename(fp), st.st_size, int(st.st_mtime)))
        except OSError:
            parts.append(os.path.basename(fp) + ":?")
    return "|".join(parts)


CIV_NOISE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]")


def civ_zh_names(civ_en_names, lang_en, lang_zh):
    """用英文语言表反查文明中文名：找到 en 表中值等于文明英文名的最小 ID，再取同 ID 的中文。"""
    if not lang_en or not lang_zh:
        return {}
    by_text = {}
    for sid, txt in lang_en.items():
        key = CIV_NOISE.sub("", txt).lower()
        if key and key not in by_text:
            by_text[key] = sid
    out = {}
    for name in civ_en_names:
        sid = by_text.get(CIV_NOISE.sub("", name or "").lower())
        if sid is not None and sid in lang_zh:
            out[name] = lang_zh[sid]
    return out


# ---------------------------------------------------------------- 主构建器 ---
def build(dat_path, lang_path=None, lang_en_path=None, progress=None, want_graphics=True):
    """解析 dat 并构建索引字典。progress(str) 用于回报进度。"""
    say = progress or (lambda *_a, **_k: None)
    t0 = time.time()
    say("读取 dat：%s" % dat_path)
    payload, _info = datlib.load_input(dat_path)
    say("解析数据（约 40 秒）…")
    dat = datlib.load_payload(payload)
    say("解析完成，用时 %.1f 秒" % (time.time() - t0))

    lang, lang_files = load_lang_multi(lang_path)
    lang_en = load_lang(lang_en_path) if lang_en_path else {}
    say("语言表：合并 %d 个文件，共 %d 条" % (len(lang_files), len(lang)))

    def zh_of(sid):
        try:
            sid = int(sid)
        except Exception:
            return ""
        if sid < 0:
            return ""
        return lang.get(sid, "")

    # ---- 文明 -------------------------------------------------------------
    say("整理文明 / 单位 / 科技 / 效果 …")
    civ_records = []
    base_units = {}
    unit_diffs = {}
    unit_names = {}
    civ_unit_ids = []
    trainers = {}          # 单位 -> [(训练它的单位ID, 训练时间, 按钮, 热键)]
    tech_at_unit = {}      # 建筑单位 -> [科技ID]（含研究时间等，存在 techs 里）

    for ci, civ in enumerate(dat.Civs):
        civ_records.append({
            "i": ci,
            "name": str(getattr(civ, "Name", "") or ""),
            "zh": "",
            "team_bonus": int(getattr(civ, "TeamBonusID", -1)),
            "tech_tree": int(getattr(civ, "TechTreeID", 0)),
            "player_type": int(getattr(civ, "PlayerType", 0)),
            "resources": _plain(getattr(civ, "Resources", {})),
            "bonuses": [],
            "units_count": len(getattr(civ, "UnitPointers", []) or []),
        })
        ptrs = list(getattr(civ, "UnitPointers", []) or [])
        units = list(getattr(civ, "Units", []) or [])
        civ_unit_ids.append([i for i, p in enumerate(ptrs) if p])
        for i, p in enumerate(ptrs):
            if not p:
                continue
            rec = _unit_record(units[i], want_graphics=want_graphics)
            if ci == 0:
                base_units[i] = rec
                unit_names[i] = (rec.get("Name", ""), rec.get("LanguageDLLName", -1),
                                 zh_of(rec.get("LanguageDLLName", -1)))
            else:
                base = base_units.get(i)
                if base is None:
                    unit_diffs.setdefault(ci, {})[i] = rec
                    continue
                diff = _diff(base, rec)
                if diff:
                    unit_diffs.setdefault(ci, {})[i] = diff
            # 训练场所反向表
            for (tid, tt, btn, hk) in rec.get("train_locations", ()):
                if tid is not None and tid >= 0:
                    trainers.setdefault(tid, []).append((ci, i, tt, btn, hk))

    # ---- 科技 -------------------------------------------------------------
    techs = {}
    for ti, tech in enumerate(dat.Techs):
        rec = _tech_record(tech, zh_of)
        rec["i"] = ti
        techs[ti] = rec
        for loc in rec["locations"]:
            lid = loc.get("unit", -1)
            if lid is not None and lid >= 0:
                tech_at_unit.setdefault(lid, []).append(ti)

    # ---- 效果 -------------------------------------------------------------
    effects = {}
    effect_of_tech = {}
    units_touched = {}
    for ei, eff in enumerate(dat.Effects):
        cmds = []
        for c in getattr(eff, "EffectCommands", []) or []:
            cmds.append({"Type": int(getattr(c, "Type", -1)),
                         "A": _plain(getattr(c, "A", -1)),
                         "B": _plain(getattr(c, "B", -1)),
                         "C": _plain(getattr(c, "C", -1)),
                         "D": _plain(getattr(c, "D", 0))})
        effects[ei] = {"i": ei, "name": str(getattr(eff, "Name", "") or ""),
                       "commands": cmds}
        for c in cmds:
            a = c["A"]
            if isinstance(a, int) and a >= 0:
                units_touched.setdefault(a, []).append((ei, c["Type"]))
    for ti, rec in techs.items():
        eid = rec.get("EffectID", -1)
        if isinstance(eid, int) and eid >= 0:
            effect_of_tech.setdefault(eid, []).append(ti)

    # 文明特性：该文明名下名字像特性/加成的科技（kind: 团队加成 / 文明加成 / 独特科技 / 其他）
    for ti, rec in techs.items():
        civ_i = rec.get("Civ", -1)
        if not isinstance(civ_i, int) or civ_i < 0 or civ_i >= len(civ_records):
            continue
        nm = rec.get("Name", "") or rec.get("zh", "")
        low = nm.lower()
        if ti == civ_records[civ_i]["team_bonus"] or "team bonus" in low:
            kind = "团队加成"
        elif "c-bonus" in low:
            kind = "文明加成"
        elif "unique" in low:
            kind = "独特科技"
        elif re.search(r"(bonus|加成)", low):
            kind = "其他加成"
        else:
            kind = "其他"
        civ_records[civ_i]["bonuses"].append(ti)
        rec["civ_kind"] = kind
        civ_records[civ_i].setdefault("tech_kinds", {}).setdefault(kind, []).append(ti)

    # 文明中文名
    zh_names = civ_zh_names([c["name"] for c in civ_records], lang_en, lang)
    for c in civ_records:
        c["zh"] = zh_names.get(c["name"], "")

    # 文明名兜底：科技树数据里带名字的文明
    # ---- 图形 / 音效 / 地形 / 颜色 ---------------------------------------
    graphics = {}
    if want_graphics:
        for gi, g in enumerate(dat.Graphics):
            if g is None:
                continue
            try:
                rec = _scalars(g, GRAPHIC_SCALARS)
            except Exception:
                continue
            rec["i"] = gi
            graphics[gi] = rec
    sounds = {}
    for si, s in enumerate(dat.Sounds):
        try:
            rec = _scalars(s, SOUND_SCALARS)
        except Exception:
            continue
        rec["i"] = si
        rec["items"] = [str(getattr(it, "FileName", "")) for it in (getattr(s, "Items", []) or [])]
        sounds[si] = rec
    terrains = []
    tblock = getattr(dat, "TerrainBlockObj", None)
    for ti, t in enumerate(getattr(tblock, "Terrains", []) or []):
        rec = _scalars(t, TERRAIN_SCALARS)
        sid = rec.get("StringID", -1)
        rec["i"] = ti
        rec["zh"] = zh_of(sid)
        terrains.append(rec)

    index = {
        "meta": {
            "dat_path": os.path.abspath(dat_path),
            "dat_size": os.path.getsize(dat_path),
            "dat_mtime": os.path.getmtime(dat_path),
            "file_version": repr(getattr(dat, "FileVersion", b"")),
            "lang_path": os.path.abspath(lang_path) if lang_path else "",
            "lang_en_path": os.path.abspath(lang_en_path) if lang_en_path else "",
            "lang_count": len(lang),
            "lang_files": len(lang_files),
            "lang_names": [os.path.basename(p) for p in lang_files],
            "lang_sig": lang_signature(lang_path),
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "build_seconds": round(time.time() - t0, 1),
            "counts": {
                "civs": len(dat.Civs), "techs": len(dat.Techs),
                "effects": len(dat.Effects), "graphics": len(graphics),
                "sounds": len(sounds), "terrains": len(terrains),
                "base_units": len(base_units),
            },
        },
        "civs": civ_records,
        "techs": techs,
        "effects": effects,
        "base_units": base_units,
        "unit_diffs": unit_diffs,
        "civ_unit_ids": civ_unit_ids,
        "unit_names": unit_names,
        "graphics": graphics,
        "sounds": sounds,
        "terrains": terrains,
        "refs": {
            "tech_at_unit": tech_at_unit,
            "effect_of_tech": effect_of_tech,
            "units_touched": units_touched,
            "trainers": {k: v for k, v in trainers.items()},
        },
        "lang": lang,
    }
    return index


def _unit_record(u, want_graphics=True):
    rec = _scalars(u, UNIT_SCALARS)
    rec["attacks"] = _pairs(getattr(getattr(u, "Type50", None), "Attacks", []))
    rec["armours"] = _pairs(getattr(getattr(u, "Type50", None), "Armours", []))
    t50 = getattr(u, "Type50", None)
    if t50 is not None:
        sub = _scalars(t50, TYPE50_SCALARS)
        sub.pop("DisplayedAttack", None)
        rec["t50"] = sub
    cr = getattr(u, "Creatable", None)
    if cr is not None:
        rec["costs"] = _costs(getattr(cr, "ResourceCosts", []))
        rec["train_locations"] = _train_locations(getattr(cr, "TrainLocations", []))
        rec["creatable"] = _scalars(cr, CREATABLE_SCALARS)
    bd = getattr(u, "Building", None)
    if bd is not None:
        rec["building"] = _scalars(bd, BUILDING_SCALARS)
    return rec


def _tech_record(t, zh_of):
    rec = _scalars(t, TECH_SCALARS)
    locs = []
    for loc in getattr(t, "ResearchLocations", []) or []:
        try:
            locs.append({"unit": int(getattr(loc, "LocationID", -1)),
                         "time": getattr(loc, "ResearchTime", 0),
                         "button": getattr(loc, "ButtonID", -1),
                         "hotkey": getattr(loc, "HotKeyID", -1)})
        except Exception:
            pass
    rec["locations"] = locs
    rec["costs"] = _costs(getattr(t, "ResourceCosts", []))
    for key in ("LanguageDLLName", "LanguageDLLDescription", "LanguageDLLHelp",
                "LanguageDLLTechTree"):
        rec[key + "_zh"] = zh_of(rec.get(key, -1))
    rec["zh"] = rec.get("LanguageDLLName_zh", "")
    return rec


def _diff(base, rec):
    out = {}
    for k, v in rec.items():
        if k not in base or base[k] != v:
            out[k] = v
    return out


def apply_diff(base_rec, diff):
    """把差异套在基底记录上，得到某文明的实际字段。"""
    if not diff:
        return dict(base_rec)
    out = dict(base_rec)
    out.update(diff)
    return out


# ------------------------------------------------------------------ 缓存 IO ---
def cache_path(index_dir, dat_path):
    st = os.stat(dat_path)
    tag = "%s_%d_%d" % (re.sub(r"[^0-9A-Za-z]+", "_", os.path.basename(dat_path)),
                        st.st_size, int(st.st_mtime))
    return os.path.join(index_dir, "cache", tag + ".pkl")


def save(index, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump(index, fh, protocol=4)
    return os.path.getsize(path)


def load(path):
    with open(path, "rb") as fh:
        return pickle.load(fh)


def cached(dat_path, lang_path=None, lang_en_path=None, index_dir=None,
           progress=None, rebuild=False, want_graphics=True):
    """有缓存就用缓存，否则构建并写缓存。返回 (index, cache_path, rebuilt)。"""
    index_dir = index_dir or HERE
    cp = cache_path(index_dir, dat_path)
    if not rebuild and os.path.isfile(cp):
        try:
            idx = load(cp)
            meta = idx.get("meta", {})
            if (meta.get("dat_size") == os.path.getsize(dat_path)
                    and abs(meta.get("dat_mtime", 0) - os.path.getmtime(dat_path)) < 1
                    and (not lang_path or (meta.get("lang_path") == os.path.abspath(lang_path)
                                           and meta.get("lang_sig") == lang_signature(lang_path)))):
                return idx, cp, False
        except Exception:
            pass
    idx = build(dat_path, lang_path, lang_en_path, progress=progress,
                want_graphics=want_graphics)
    save(idx, cp)
    return idx, cp, True


# ---------------------------------------------------------------------- CLI ---
def _main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    dat_path = args[0] if args else ""
    lang_path = args[1] if len(args) > 1 else ""
    if not dat_path or not os.path.isfile(dat_path):
        print("用法: python index_build.py <dat路径> [语言表路径] [--rebuild] [--check]")
        return 2
    out = None
    for i, a in enumerate(argv):
        if a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
    idx, cp, rebuilt = cached(dat_path, lang_path or None, None, progress=print,
                              rebuild="--rebuild" in flags)
    if out and os.path.abspath(out) != os.path.abspath(cp):
        size = save(idx, out)
        print("另存索引: %s (%.1f MB)" % (out, size / 1048576.0))
    meta = idx["meta"]
    print("缓存: %s (重建=%s, %.1f MB)" % (cp, rebuilt, os.path.getsize(cp) / 1048576.0))
    print("计数: %s" % meta["counts"])
    print("单位差异条目: %s" % {c: len(d) for c, d in sorted(idx["unit_diffs"].items())[:8]})
    print("总差异条目: %d" % sum(len(d) for d in idx["unit_diffs"].values()))
    print("语言表: %d 条 (%s)" % (meta["lang_count"], meta["lang_path"]))
    if "--check" in flags:
        _checks(idx)
    return 0


def _checks(idx):
    print("\n--- 自检 ---")
    mill = 68
    tis = idx["refs"]["tech_at_unit"].get(mill, [])
    print("磨坊(68) 科技数: %d" % len(tis))
    for ti in tis:
        t = idx["techs"][ti]
        print("   %4d %-28s %-16s 时间=%s 按钮=%s 文明=%s" % (
            ti, t.get("Name", "")[:28], t.get("zh", "")[:16],
            [l["time"] for l in t["locations"]], [l["button"] for l in t["locations"]],
            t.get("Civ", -1)))
    # 不列颠特性
    for c in idx["civs"]:
        if c["name"].lower() == "british":
            print("不列颠 中文名=%r 特性科技数=%d 团队加成ID=%s" % (
                c["zh"], len(c["bonuses"]), c["team_bonus"]))
            for ti in c["bonuses"][:8]:
                t = idx["techs"][ti]
                print("   %4d %-32s %-12s [%s]" % (
                    ti, t.get("Name", "")[:32], t.get("zh", ""), t.get("civ_kind", "")))
            if c["bonuses"]:
                t = idx["techs"][c["bonuses"][0]]
                eid = t.get("EffectID", -1)
                print("   首个特性的效果 %s: %s" % (
                    eid, (idx["effects"].get(eid, {}).get("commands") or [])[:4]))
            break
    print("单位名示例: %s" % [(k, v[2]) for k, v in list(idx["unit_names"].items())[:5]])
    print("科技→效果 反向表示例: %s" % list(idx["refs"]["effect_of_tech"].items())[:3])
    print("地形 100-105: %s" % [(t["i"], t.get("Name", ""), t.get("zh", ""))
                                for t in idx["terrains"][100:106]])


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
