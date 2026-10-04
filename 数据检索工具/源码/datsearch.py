# -*- coding: utf-8 -*-
r"""AGE 数据检索工具 —— 独立运行 / 可分享的 AoE2DE dat 数据浏览器。

功能
----
* 自动定位游戏（Steam 库）里的 dat 数据文件与语言表，也可手动选择任意 dat。
* 全数据检索：单位 / 科技 / 效果 / 文明 / 图形 / 音效 / 地形。
* 查询语法：关键词、`字段=值`、`字段>值`、`#编号`、类别前缀（如 `科技:磨坊`）。
* 详情面板：把 dat 字段解码成中文（生命/攻击/护甲/射程/成本/训练场所/研究点/效果命令…）。
* 反向引用：某个建筑能研究哪些科技、某个单位由谁训练、某个效果改动了谁。
* 文明特性页：文明加成 / 团队加成 / 独特科技 / 独特单位，含效果命令逐条解读。
* 导出 CSV。

By RemonBo
（界面中文、中文名称对照表由 RemonBo 整理；本工具只读，不修改任何游戏文件）

运行：  python datsearch.py        （或直接运行打包好的 exe）
构建索引：第一次运行会解析 dat（约 8 秒）并缓存，之后秒开。
"""
from __future__ import annotations

import csv
import os
import re
import sys
import json
import queue
import threading
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

HERE = os.path.dirname(os.path.abspath(sys.argv[0] if getattr(sys, "frozen", False) else __file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import index_build  # noqa: E402

# ---- 可选的解码表（由构建流程生成；缺失时自动退化为英文标签） ----------------
try:
    from tables import (FIELD_ZH, ATTR_ZH, CLASS_ZH, UNIT_TYPE_ZH, CMD_ZH,
                        CMD_FIELDS, CIV_ZH)
except Exception:  # pragma: no cover
    FIELD_ZH, ATTR_ZH, CLASS_ZH, UNIT_TYPE_ZH, CMD_ZH, CMD_FIELDS, CIV_ZH = {}, {}, {}, {}, {}, {}, {}

# 效果命令类型全表（由 make_cmd_table.py 从 AGE3 源码生成，中文取自已完成的界面译文）
try:
    from cmd_table import CMD_ZH as _CMD_ZH_FULL
    _clean = {}
    for _k, _v in _CMD_ZH_FULL.items():
        _s = str(_v)
        if " - " in _s:
            _head, _tail = _s.split(" - ", 1)
            if _head.strip().isdigit():
                _s = _tail.strip()
        _clean[int(_k)] = _s
    CMD_ZH = dict(CMD_ZH or {})
    CMD_ZH.update(_clean)
except Exception:  # pragma: no cover
    pass

APP_TITLE = "AGE 数据检索工具"
AUTHOR = "By RemonBo"
APP_TITLE_SIGNED = "%s　·　%s" % (APP_TITLE, AUTHOR)
CONFIG_NAME = "config.json"
LANG_CHOICES = ["zh", "zh-Hans", "tw", "en", "de", "fr", "it", "es", "jp", "ko",
                "pt", "ru", "pl", "tr", "vi", "hi", "ms", "th", "br", "mx", "ar"]


# --------------------------------------------------------------- 配置 / 路径 ---
def config_dir():
    """配置与缓存的落地目录：优先 exe 同目录，不可写时退回 %LOCALAPPDATA%。"""
    for cand in (os.path.join(HERE, "age_data_search"),
                 os.path.join(os.environ.get("LOCALAPPDATA", HERE), "AGE数据检索工具")):
        try:
            os.makedirs(cand, exist_ok=True)
            probe = os.path.join(cand, ".w")
            with open(probe, "w") as fh:
                fh.write("1")
            os.remove(probe)
            return cand
        except Exception:
            continue
    return HERE


CFG_DIR = config_dir()
CFG_PATH = os.path.join(CFG_DIR, CONFIG_NAME)


def load_config():
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def steam_libraries():
    """枚举 Steam 库目录（注册表 + libraryfolders.vdf）。"""
    out = []
    try:
        import winreg
        for root, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam")):
            try:
                with winreg.OpenKey(root, key) as k:
                    for name in ("SteamPath", "InstallPath"):
                        try:
                            v = winreg.QueryValueEx(k, name)[0]
                            if v:
                                out.append(os.path.normpath(v))
                        except OSError:
                            pass
            except OSError:
                pass
    except Exception:
        pass
    for base in list(out) + [r"C:\Program Files (x86)\Steam", r"D:\Steam",
                             r"D:\Games\Steam", r"E:\Steam", r"E:\Games\Steam"]:
        vdf = os.path.join(base, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            try:
                with open(vdf, "r", encoding="utf-8", errors="replace") as fh:
                    for m in re.finditer(r'"path"\s+"([^"]+)"', fh.read()):
                        out.append(os.path.normpath(m.group(1).replace("\\\\", "\\")))
            except Exception:
                pass
    seen, uniq = set(), []
    for p in out:
        lp = p.lower()
        if p and lp not in seen and os.path.isdir(p):
            seen.add(lp)
            uniq.append(p)
    return uniq


def find_dat_files():
    """在 Steam 库里找 AoE2DE 的 dat 数据文件。"""
    found = []
    for lib in steam_libraries():
        root = os.path.join(lib, "steamapps", "common", "AoE2DE")
        if not os.path.isdir(root):
            continue
        for rel in (r"resources\_common\dat",
                    r"resources\_common\dat\.."):
            d = os.path.normpath(os.path.join(root, rel))
            if os.path.isdir(d):
                for fn in ("empires2_x2_p1.dat", "empires2_x2_p1.dat.bak"):
                    p = os.path.join(d, fn)
                    if os.path.isfile(p) and p not in found:
                        found.append(p)
        if found:
            break
    return found


def find_game_root(dat_path):
    """从 dat 路径回溯游戏根目录（resources 的上一层）。"""
    p = os.path.abspath(dat_path)
    for _ in range(8):
        p = os.path.dirname(p)
        if os.path.isdir(os.path.join(p, "resources")):
            return p
    return ""


def lang_file_for(game_root, lang):
    if not game_root:
        return ""
    cands = [lang, "zh-Hans" if lang == "zh" else lang]
    if lang == "zh":
        cands += ["zh-Hans", "zh-Hant"]
    for lg in cands:
        p = os.path.join(game_root, "resources", lg, "strings", "key-value",
                         "key-value-strings-utf8.txt")
        if os.path.isfile(p):
            return p
    return ""


# ------------------------------------------------------------------- 解码表 ---
def zh(d, key, default=None):
    if key in d:
        return d[key]
    return default if default is not None else str(key)


def attr_name(i):
    try:
        i = int(i)
    except Exception:
        return str(i)
    return ATTR_ZH.get(i, ATTR_ZH.get(str(i), "属性 %s" % i))


def class_name(i):
    try:
        i = int(i)
    except Exception:
        return str(i)
    return CLASS_ZH.get(i, CLASS_ZH.get(str(i), "类别 %s" % i))


def field_name(k):
    return FIELD_ZH.get(k, k)


def unit_type_name(t):
    if t is None:
        return "?"
    if isinstance(t, str) and t.startswith("b'") or isinstance(t, bytes):
        raw = t if isinstance(t, bytes) else t.encode("latin1", "ignore")
        try:
            return UNIT_TYPE_ZH.get(raw.decode("latin1", "ignore").strip("\x00"),
                                    "类型 %s" % (int.from_bytes(raw[:4], "little") if isinstance(raw, bytes) else "?"))
        except Exception:
            return "类型 ?"
    if isinstance(t, str) and t.startswith("b'"):
        m = re.search(r"\\x([0-9a-fA-F]{2})", t)
        if m:
            try:
                t = int(m.group(1), 16)
            except Exception:
                return t
    try:
        return UNIT_TYPE_ZH.get(int(t), "类型 %s" % t)
    except Exception:
        return str(t)


def cmd_name(t):
    try:
        t = int(t)
    except Exception:
        return str(t)
    if t in CMD_ZH:
        return CMD_ZH[t]
    if str(t) in CMD_ZH:
        return CMD_ZH[str(t)]
    return "命令 %s" % t


# 各类效果命令的参数含义（AGE3 的 A/B/C/D 四栏）
def cmd_fields_for(t):
    fields = CMD_FIELDS.get(t) or CMD_FIELDS.get(str(t))
    if fields:
        if isinstance(fields, dict):
            return sorted(fields.items())
        fields = list(fields)
        if len(fields) == 4 and isinstance(fields[0], str):
            return list(zip(("A", "B", "C", "D"), fields))
        return [(k, v) for k, v in fields]
    base = t % 10
    if t >= 200:
        return [("A", "对象"), ("B", "类别"), ("C", "属性"), ("D", "数值")]
    if base in (0, 4, 5):                      # 属性修正
        return [("A", "单位（-1=全部）"), ("B", "单位类别"), ("C", "属性"), ("D", "数值")]
    if base in (1, 6):                         # 资源修正
        return [("A", "资源"), ("B", "单位类别"), ("C", "（忽略）"), ("D", "数值")]
    if base == 2:
        return [("A", "单位"), ("D", "开关")]
    if base == 3:
        return [("A", "原单位"), ("D", "升级为（单位编号）")]
    if base == 7:
        return [("A", "单位"), ("D", "数量")]
    if base == 8:
        return [("A", "科技"), ("D", "开关")]
    if t == 101:
        return [("A", "科技"), ("B", "资源"), ("C", "模式"), ("D", "数量")]
    if t == 102:
        return [("A", "（未用）"), ("B", "（未用）"), ("C", "（未用）"), ("D", "科技")]
    if t == 103:
        return [("A", "科技"), ("B", "资源"), ("C", "模式"), ("D", "数量")]
    return [("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")]


def cmd_value_label(t, key, val):
    """把参数值换成人看得懂的东西。"""
    try:
        iv = int(val)
        is_int = (float(val) == iv)
    except Exception:
        iv, is_int = None, False
    if is_int and iv == -1 and key in ("A", "B"):
        return "全部单位" if key == "A" else "全部类别"
    base = t % 10
    if key == "A" and is_int:
        if base in (0, 4, 5, 1, 6) and 0 <= iv <= 120:      # 资源编号
            return "%s (%d)" % (attr_name(iv), iv) if base in (1, 6) else val
        if base == 0 or base == 1 or base == 3 or base == 7 or base == 8:
            return val
    if key == "C" and is_int and base in (0, 4, 5):
        return "%s (%d)" % (attr_name(iv), iv)
    if key == "B" and is_int and base in (0, 4, 5, 1, 6):
        return "%s (%d)" % (class_name(iv), iv)
    return val


def cmd_text(c, resolver=None):
    """把一条效果命令翻译成一句人话。resolver(key, label, value) 可把编号换成名字。"""
    t = c.get("Type", -1)
    try:
        t = int(t)
    except Exception:
        t = -1
    vals = {"A": c.get("A", -1), "B": c.get("B", -1), "C": c.get("C", -1), "D": c.get("D", 0)}
    parts = []
    for key, label in cmd_fields_for(t):
        val = vals.get(key, "")
        lab = str(label)
        if " / " in lab:                     # 表里常写成「数量 [+] / 数量 [+/-]」，取前半
            lab = lab.split(" / ", 1)[0].strip()
        if lab.startswith(("未用", "（未用", "（忽略")):
            if val in (-1, "", None, "-1"):
                continue
        if val in ("", None) and key in ("B", "C"):
            continue
        shown = None
        if resolver is not None:
            try:
                shown = resolver(key, lab, val)
            except Exception:
                shown = None
        if shown is None:
            shown = cmd_value_label(t, key, val)
        if isinstance(shown, (int, float)) and not isinstance(shown, bool):
            shown = fmt_num(shown)
        parts.append("%s=%s" % (lab, shown))
    if not parts:
        return cmd_name(t)
    return "%s → %s" % (cmd_name(t), "，".join(parts))


# 官方「文明卡」文本在语言表里的起始编号：文明 i（1 起）→ 120149 + i
CIV_CARD_BASE = 120149


def unescape_text(s):
    """语言表里的 \\n 是字面的两个字符，还夹着 <b> 之类的标记，这里还原成可读文本。"""
    if not s:
        return ""
    s = str(s)
    s = s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "  ")
    s = re.sub(r"</?[a-zA-Z][^>]{0,20}>", "", s)
    return s.strip()


def lang_text(idx, sid):
    """按编号取合并后语言表里的文本（兼容 int / str 键）。"""
    lang = (idx or {}).get("lang") or {}
    if sid in (None, "", -1, "-1"):
        return ""
    v = lang.get(sid)
    if v is None:
        try:
            v = lang.get(int(sid))
        except Exception:
            v = None
    if v is None:
        v = lang.get(str(sid))
    return unescape_text(v or "")


def tech_cn_text(idx, t, resolver=None):
    """科技的中文：官方名称 → 效果解读（加成科技在 dat 里通常没有名字，只有效果）。
    注意：有些科技的「说明」文本编号是开发残留（例如 382 指向别的科技），所以说明只放在详情页。"""
    for key in ("zh", "LanguageDLLName_zh"):
        v = unescape_text((t or {}).get(key) or "")
        if v:
            return v
    eid = (t or {}).get("EffectID", -1)
    eff = ((idx or {}).get("effects") or {}).get(eid) or {}
    cmds = eff.get("commands") or []
    if cmds:
        return cmd_text(cmds[0], resolver)
    d = unescape_text((t or {}).get("LanguageDLLDescription_zh") or "")
    if d:
        return d.replace("\n", " ")
    return ""


def fmt_num(v):
    """把 1025.0 / 0.8695650100708008 这类数值显示成人看的样子。"""
    try:
        f = float(v)
    except Exception:
        return v
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    if abs(f) < 1:
        return ("%.4f" % f).rstrip("0").rstrip(".")
    return ("%.4g" % f)


# ---------------------------------------------------------------- 主窗口 ---
class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=6)
        self.pack(fill="both", expand=True)
        self.cfg = load_config()
        self.index = None
        self.cache_file = ""
        self.results = []
        self.civ_by_name = {}
        self.unit_view_civ = tk.IntVar(value=0)
        self.build_ui()
        self.after(200, self.startup)

    # ---- 界面骨架 ---------------------------------------------------------
    def build_ui(self):
        self.master.title(APP_TITLE_SIGNED)
        top = ttk.Frame(self)
        top.pack(fill="x")

        ttk.Label(top, text="数据文件").pack(side="left")
        self.dat_var = tk.StringVar(value=self.cfg.get("dat_path", ""))
        ttk.Entry(top, textvariable=self.dat_var, width=62).pack(side="left", padx=4)
        ttk.Button(top, text="选择…", command=self.pick_dat).pack(side="left")
        ttk.Button(top, text="重新载入", command=lambda: self.load_index(rebuild=True)).pack(side="left", padx=4)

        top2 = ttk.Frame(self)
        top2.pack(fill="x", pady=(4, 0))
        ttk.Label(top2, text="语言").pack(side="left")
        self.lang_var = tk.StringVar(value=self.cfg.get("lang", "zh"))
        cb = ttk.Combobox(top2, textvariable=self.lang_var, values=LANG_CHOICES, width=8)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda _e: self.load_index(rebuild=False))
        ttk.Label(top2, text="（用于把 dat 里的字符串编号翻成中文）").pack(side="left")
        self.status = tk.StringVar(value="就绪")
        ttk.Label(top2, textvariable=self.status, foreground="#0a5").pack(side="left", padx=12)
        self.auto_btn = ttk.Button(top2, text="自动查找游戏", command=self.auto_find)
        self.auto_btn.pack(side="right")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, pady=6)
        self.nb = nb
        self.tab_search = ttk.Frame(nb, padding=4)
        self.tab_civ = ttk.Frame(nb, padding=4)
        self.tab_info = ttk.Frame(nb, padding=4)
        nb.add(self.tab_search, text="检索")
        nb.add(self.tab_civ, text="文明特性")
        nb.add(self.tab_info, text="索引信息")
        self.build_search_tab()
        self.build_civ_tab()
        self.build_info_tab()

    def build_search_tab(self):
        f = self.tab_search
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Label(bar, text="查询").pack(side="left")
        self.query_var = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.query_var, width=60)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _e: self.do_search())
        ttk.Button(bar, text="搜索", command=self.do_search).pack(side="left")
        ttk.Button(bar, text="清空", command=self.clear_search).pack(side="left", padx=4)
        ttk.Button(bar, text="导出 CSV", command=self.export_csv).pack(side="left")

        bar2 = ttk.Frame(f)
        bar2.pack(fill="x", pady=(4, 2))
        self.cat_vars = {}
        for cat in ("单位", "科技", "效果", "文明", "图形", "音效", "地形"):
            v = tk.BooleanVar(value=cat in ("单位", "科技"))
            self.cat_vars[cat] = v
            ttk.Checkbutton(bar2, text=cat, variable=v).pack(side="left")
        ttk.Label(bar2, text="   单位所属文明").pack(side="left")
        self.civ_combo = ttk.Combobox(bar2, width=18, state="readonly")
        self.civ_combo.pack(side="left", padx=4)
        self.civ_combo.bind("<<ComboboxSelected>>", lambda _e: self.apply_unit_civ())

        hint = ttk.Label(f, foreground="#666",
                         text="语法：关键词（名称/编号/中文名）｜ 字段=值（如 生命>100、类型=70、研究点=68、文明=British）｜ #编号 ｜ 类别前缀（科技:磨坊）　　" + AUTHOR)
        hint.pack(fill="x")

        pane = ttk.Panedwindow(f, orient="vertical")
        pane.pack(fill="both", expand=True, pady=4)
        cols = ("类别", "编号", "名称", "中文名", "关键信息")
        self.tree = ttk.Treeview(pane, columns=cols, show="headings", height=16)
        for c, w in zip(cols, (70, 70, 330, 240, 420)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w")
        vs = ttk.Scrollbar(pane, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        pane.add(self.tree, weight=3)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_detail())

        detail = ttk.Frame(pane)
        pane.add(detail, weight=2)
        self.detail = tk.Text(detail, wrap="word", height=12, font=("Microsoft YaHei UI", 9))
        ds = ttk.Scrollbar(detail, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=ds.set, state="disabled")
        ds.pack(side="right", fill="y")
        self.detail.pack(side="left", fill="both", expand=True)

    def build_civ_tab(self):
        f = self.tab_civ
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        ttk.Label(bar, text="文明").pack(side="left")
        self.civ_pick = ttk.Combobox(bar, width=28, state="readonly")
        self.civ_pick.pack(side="left", padx=4)
        self.civ_pick.bind("<<ComboboxSelected>>", lambda _e: self.show_civ())
        ttk.Label(bar, foreground="#666",
                  text="（文明加成 / 团队加成 / 独特科技 / 独特单位，均来自 dat；效果命令逐条解读）").pack(side="left", padx=8)

        cols = ("类别", "编号", "名称", "中文名")
        self.civ_tree = ttk.Treeview(f, columns=cols, show="headings")
        for c, w in zip(cols, (100, 64, 330, 500)):
            self.civ_tree.heading(c, text=c)
            self.civ_tree.column(c, width=w, anchor="w")
        self.civ_tree.pack(fill="both", expand=True, pady=4)
        self.civ_tree.bind("<<TreeviewSelect>>", lambda _e: self.show_civ_detail())

        self.civ_detail = tk.Text(f, wrap="word", height=12, font=("Microsoft YaHei UI", 9), state="disabled")
        self.civ_detail.pack(fill="both", expand=True)

    def build_info_tab(self):
        self.info = tk.Text(self.tab_info, wrap="word", font=("Microsoft YaHei UI", 9), state="disabled")
        self.info.pack(fill="both", expand=True)

    # ---- 数据加载 ---------------------------------------------------------
    def startup(self):
        if not self.dat_var.get():
            self.auto_find(quiet=True)
        if self.dat_var.get() and os.path.isfile(self.dat_var.get()):
            self.load_index(rebuild=False)
        else:
            self.set_info("还没有指定数据文件。\n\n点击「自动查找游戏」或「选择…」指定 AoE2DE 的 "
                          "empires2_x2_p1.dat。\n")
            self.status.set("请先选择数据文件")

    def auto_find(self, quiet=False):
        files = find_dat_files()
        if not files:
            if not quiet:
                messagebox.showinfo(APP_TITLE, "没有自动找到 AoE2DE 的 dat 文件，请手动选择。")
            return
        self.dat_var.set(files[0])
        if not quiet:
            self.load_index(rebuild=False)

    def pick_dat(self):
        p = filedialog.askopenfilename(title="选择 AoE2DE 的 dat 数据文件",
                                       filetypes=[("dat 数据文件", "*.dat"), ("所有文件", "*.*")])
        if p:
            self.dat_var.set(p)
            self.load_index(rebuild=False)

    def game_lang(self):
        root = find_game_root(self.dat_var.get())
        return root, lang_file_for(root, self.lang_var.get())

    def load_index(self, rebuild=False):
        dat = self.dat_var.get()
        if not dat or not os.path.isfile(dat):
            messagebox.showwarning(APP_TITLE, "请先选择一个存在的 dat 数据文件。")
            return
        root, lang = self.game_lang()
        cfg = dict(self.cfg)
        cfg.update({"dat_path": dat, "lang": self.lang_var.get(), "lang_path": lang})
        self.cfg = cfg
        save_config(cfg)
        self.status.set("正在载入索引…")
        self.progress = tk.Toplevel(self)
        self.progress.title("正在建立索引")
        self.progress.geometry("420x110")
        ttk.Label(self.progress, text="正在解析 dat 数据文件，第一次约需 10 秒…").pack(pady=8)
        bar = ttk.Progressbar(self.progress, mode="determinate", maximum=100)
        bar.pack(fill="x", padx=14)
        self.progress.grab_set()
        self.q = queue.Queue()

        def work():
            try:
                idx, cp, rebuilt = index_build.cached(
                    dat, lang or None, None, index_dir=CFG_DIR, rebuild=rebuild,
                    progress=lambda m: self.q.put(("log", m)))
                self.q.put(("done", (idx, cp, rebuilt)))
            except Exception:
                self.q.put(("err", traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()
        self.after(120, lambda: self.poll(bar))

    def poll(self, bar):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.status.set(payload)
                elif kind == "done":
                    self.index, self.cache_file, rebuilt = payload
                    self.progress.grab_release()
                    self.progress.destroy()
                    self.on_index(payload[2])
                    return
                elif kind == "err":
                    self.progress.grab_release()
                    self.progress.destroy()
                    self.status.set("索引失败")
                    messagebox.showerror(APP_TITLE, payload)
                    return
        except queue.Empty:
            pass
        bar.step(3)
        self.after(120, lambda: self.poll(bar))

    def on_index(self, rebuilt):
        meta = self.index["meta"]
        names = ["全部（盖亚/基础）"] + [
            "%d - %s%s" % (c["i"], c["name"], (" / " + c["zh"]) if c["zh"] else "")
            for c in self.index["civs"]]
        self.civ_combo["values"] = names
        self.civ_combo.current(0)
        self.civ_pick["values"] = names[1:]
        self.civ_pick.current(0)
        self.unit_view_civ.set(0)
        self.status.set("索引就绪（%s，%s）" % (meta["built_at"], "刚重建" if rebuilt else "用缓存"))
        if getattr(self, "pending_query", None):
            self.query_var.set(self.pending_query)
            self.pending_query = None
            self.after(100, self.do_search)
        if getattr(self, "pending_civ", None):
            want = str(self.pending_civ).strip().lower()
            self.pending_civ = None
            for k, nm in enumerate(names[1:]):
                if nm.lower().startswith(want + " -") or want in nm.lower():
                    self.civ_pick.current(k)
                    try:
                        self.nb.select(1)
                    except Exception:
                        pass
                    self.after(150, self.show_civ)
                    break
        counts = meta["counts"]
        lang_files = meta.get("lang_files") or 0
        lang_names = meta.get("lang_names") or []
        lang_desc = ("%s（%d 条，合并 %d 个字符串表）"
                     % (meta["lang_path"] or "（未找到，界面会退化为英文/编号）",
                        meta["lang_count"], lang_files)) if lang_files else \
                    "%s（%d 条）" % (meta["lang_path"] or "（未找到，界面会退化为英文/编号）",
                                     meta["lang_count"])
        if lang_names:
            lang_desc += "\n　└ " + "、".join(lang_names)
        self.set_info(
            "数据文件：%s\n大小：%.2f MB ｜ 版本标识：%s\n语言表：%s\n"
            "索引缓存：%s\n\n"
            "记录数：文明 %d ｜ 单位 %d（盖亚基底）｜ 科技 %d ｜ 效果 %d ｜ 图形 %d ｜ 音效 %d ｜ 地形 %d\n"
            "单位跨文明差异条目：%d\n构建耗时：%s 秒\n"
            % (meta["dat_path"], meta["dat_size"] / 1048576.0, meta["file_version"],
               lang_desc,
               self.cache_file,
               counts["civs"], counts["base_units"], counts["techs"], counts["effects"],
               counts["graphics"], counts["sounds"], counts["terrains"],
               sum(len(d) for d in self.index["unit_diffs"].values()),
               meta["build_seconds"]))
        self.show_civ()

    # ---- 检索 -------------------------------------------------------------
    def active_cats(self):
        return [c for c, v in self.cat_vars.items() if v.get()]

    def resolve_unit_civ(self):
        txt = self.civ_combo.get()
        m = re.match(r"^(\d+)", txt or "")
        return int(m.group(1)) if m else 0

    def apply_unit_civ(self):
        self.unit_view_civ.set(self.resolve_unit_civ())
        if self.results:
            self.do_search()

    def parse_query(self, q):
        """返回 (关键词列表, [(字段, 比较符, 值)], 类别前缀)。"""
        cats = None
        m = re.match(r"^(单位|科技|效果|文明|图形|音效|地形)\s*[:：]\s*(.*)$", q)
        if m:
            cats = [m.group(1)]
            q = m.group(2)
        words, conds = [], []
        for tok in re.split(r"\s+", q.strip()):
            if not tok:
                continue
            m = re.match(r"^([\u4e00-\u9fffA-Za-z_]+)(>=|<=|!=|=|>|<|~)(.+)$", tok)
            if m:
                conds.append((m.group(1), m.group(2), m.group(3)))
            elif tok.startswith("#") and tok[1:].isdigit():
                conds.append(("编号", "=", tok[1:]))
            else:
                words.append(tok.lower())
        return words, conds, cats

    def do_search(self):
        if not self.index:
            return
        words, conds, cats = self.parse_query(self.query_var.get())
        cats = cats or self.active_cats()
        rows = []
        idx = self.index
        limit = 4000
        for cat in cats:
            if cat == "单位":
                rows += self.search_units(words, conds)
            elif cat == "科技":
                rows += self.search_techs(words, conds)
            elif cat == "效果":
                rows += self.search_effects(words, conds)
            elif cat == "文明":
                rows += self.search_civs(words, conds)
            elif cat in ("图形", "音效", "地形"):
                rows += self.search_misc(cat, words, conds)
            if len(rows) > limit:
                break
        self.results = rows[:limit]
        self.tree.delete(*self.tree.get_children())
        for r in self.results:
            self.tree.insert("", "end", values=(r["cat"], r["id"], r["name"], r["zh"], r["info"]))
        extra = "（结果过多，只显示前 %d 条，可用导出 CSV 取全量）" % limit if len(rows) > limit else ""
        self.status.set("命中 %d 条%s" % (len(rows), extra))

    def _match_words(self, words, *texts):
        if not words:
            return True
        blob = " ".join(str(t) for t in texts if t is not None).lower()
        return all(w in blob for w in words)

    def _cond_ok(self, conds, getter):
        for key, op, val in conds:
            got = getter(key)
            if got is None:
                return False
            if isinstance(got, (list, tuple, set, dict)):
                items = list(got.keys()) if isinstance(got, dict) else list(got)
                if op in ("=", "!="):
                    ok = any(str(x) == str(val) for x in items)
                    if op == "!=":
                        ok = not ok
                elif op in (">", "<", ">=", "<="):
                    nums = []
                    for x in items:
                        try:
                            nums.append(float(x))
                        except Exception:
                            pass
                    try:
                        v = float(val)
                    except Exception:
                        v = None
                    ok = bool(nums) and v is not None and any(
                        {"<": n < v, ">": n > v, "<=": n <= v, ">=": n >= v}[op] for n in nums)
                else:
                    ok = any(str(val).lower() in str(x).lower() for x in items)
                if not ok:
                    return False
                continue
            if op in ("=", "!=", ">", "<", ">=", "<="):
                try:
                    f, v = float(got), float(val)
                    num = True
                except Exception:
                    f, v, num = str(got), str(val), False
                if num:
                    ok = {"=": f == v, "!=": f != v, ">": f > v, "<": f < v,
                          ">=": f >= v, "<=": f <= v}[op]
                else:
                    a, b = str(got).lower(), str(val).lower()
                    ok = (a == b) if op == "=" else (a != b) if op == "!=" else (b in a)
            else:  # ~ 模糊
                ok = str(val).lower() in str(got).lower()
            if not ok:
                return False
        return True

    def unit_effective(self, uid, civ=None):
        civ = self.unit_view_civ.get() if civ is None else civ
        base = self.index["base_units"].get(uid)
        diff = self.index["unit_diffs"].get(civ, {}).get(uid)
        if base is None:
            return diff
        return index_build.apply_diff(base, diff)

    def search_units(self, words, conds):
        out = []
        civ = self.unit_view_civ.get()
        ids = sorted(self.index["civ_unit_ids"][civ]) if civ < len(self.index["civ_unit_ids"]) else []
        for uid in ids:
            rec = self.unit_effective(uid, civ)
            if rec is None:
                continue
            name = rec.get("Name", "") or ""
            nm, ldl, lzh = self.index["unit_names"].get(uid, ("", -1, ""))
            zh_name = lzh or self.index["lang"].get(str(rec.get("LanguageDLLName", -1)), "") \
                or self.index["lang"].get(rec.get("LanguageDLLName", -1), "")

            def g(key, rec=rec, uid=uid, name=name, zh_name=zh_name):
                if key in ("编号", "ID", "id"):
                    return uid
                if key in ("名称", "Name"):
                    return name
                if key in ("中文名", "中文"):
                    return zh_name
                if key in ("类型", "Type"):
                    return rec.get("Type")
                if key in ("生命", "HP", "HitPoints"):
                    return rec.get("HitPoints")
                if key in ("攻击",):
                    return rec.get("t50", {}).get("DisplayedAttack")
                if key in ("射程",):
                    return rec.get("t50", {}).get("MaxRange")
                if key in ("速度", "Speed"):
                    return rec.get("Speed")
                if key in ("文明", "Civ"):
                    return self.index["civs"][civ]["name"] if civ < len(self.index["civs"]) else ""
                if key in ("类别", "Class"):
                    return rec.get("Class")
                if key in ("研究点", "建筑"):
                    return list(self.index["refs"]["tech_at_unit"].get(uid, []))
                if key in ("训练地点", "训练点"):
                    return [t[0] for t in rec.get("train_locations", ())]
                if key in ("成本",):
                    return " ".join("%s:%s" % (attr_name(a), b) for a, b in rec.get("costs", ()))
                return rec.get(key)

            if not self._match_words(words, name, zh_name, uid):
                continue
            if not self._cond_ok(conds, g):
                continue
            t50 = rec.get("t50", {})
            info = "HP %s ｜ 攻 %s ｜ 射程 %s ｜ 速度 %s" % (
                rec.get("HitPoints", ""), t50.get("DisplayedAttack", ""),
                t50.get("MaxRange", ""), rec.get("Speed", ""))
            out.append({"cat": "单位", "id": uid, "name": name or "（无内部名）",
                        "zh": zh_name, "info": info,
                        "rec": rec, "civ": civ})
        return out

    def search_techs(self, words, conds):
        out = []
        for ti, t in self.index["techs"].items():
            name = t.get("Name", "") or ""
            zhn = t.get("zh", "") or t.get("LanguageDLLName_zh", "")
            locs = " ".join(str(l.get("unit")) for l in t["locations"])

            def g(key, t=t, ti=ti, name=name, zhn=zhn, locs=locs):
                if key in ("编号", "ID", "id"):
                    return ti
                if key in ("名称", "Name"):
                    return name
                if key in ("中文名", "中文"):
                    return zhn
                if key in ("研究点", "建筑", "研究地点"):
                    return [l.get("unit") for l in t["locations"]]
                if key in ("时间", "研究时间"):
                    return min([l.get("time", 0) for l in t["locations"]] or [0])
                if key in ("文明", "Civ"):
                    c = t.get("Civ", -1)
                    if isinstance(c, int) and 0 <= c < len(self.index["civs"]):
                        return self.index["civs"][c]["name"]
                    return "全部" if c == -1 else str(c)
                if key in ("效果", "Effect", "EffectID"):
                    return t.get("EffectID")
                if key in ("成本",):
                    return " ".join("%s:%s" % (attr_name(a), b) for a, b in t.get("costs", ()))
                if key in ("前置", "RequiredTechs"):
                    return " ".join(str(x) for x in t.get("RequiredTechs", []) if x >= 0)
                return t.get(key)

            if not self._match_words(words, name, zhn, ti, locs):
                continue
            if not self._cond_ok(conds, g):
                continue
            times = [l.get("time", 0) for l in t["locations"]]
            civi = t.get("Civ", -1)
            civ_txt = "全部文明" if civi == -1 else (
                self.index["civs"][civi]["name"] if isinstance(civi, int)
                and 0 <= civi < len(self.index["civs"]) else str(civi))
            info = "研究点 %s ｜ %ss ｜ 按钮 %s ｜ %s ｜ 效果 %s" % (
                locs or "-", times[0] if times else "-",
                ",".join(str(l.get("button")) for l in t["locations"]), civ_txt,
                t.get("EffectID"))
            out.append({"cat": "科技", "id": ti, "name": name or "（无内部名）",
                        "zh": zhn, "info": info, "rec": t})
        return out

    def search_effects(self, words, conds):
        out = []
        for ei, e in self.index["effects"].items():
            name = e.get("name", "")
            cmds = e.get("commands", [])
            txt = " ".join(cmd_text(c) for c in cmds)

            def g(key, e=e, ei=ei, name=name, cmds=cmds, txt=txt):
                if key in ("编号", "ID", "id"):
                    return ei
                if key in ("名称", "Name"):
                    return name
                if key in ("命令数", "条数"):
                    return len(cmds)
                if key in ("科技", "被引用"):
                    return list(self.index["refs"]["effect_of_tech"].get(ei, []))
                return txt if key in ("命令", "内容") else None

            if not self._match_words(words, name, ei, txt):
                continue
            if not self._cond_ok(conds, g):
                continue
            out.append({"cat": "效果", "id": ei, "name": name or "（无名字）", "zh": "",
                        "info": "%d 条命令 ｜ %s" % (len(cmds), txt[:90]), "rec": e})
        return out

    def search_civs(self, words, conds):
        out = []
        for c in self.index["civs"]:
            def g(key, c=c):
                if key in ("编号", "ID", "id"):
                    return c["i"]
                if key in ("名称", "Name"):
                    return c["name"]
                if key in ("中文名", "中文"):
                    return c["zh"]
                if key in ("特性数", "加成数"):
                    return len(c["bonuses"])
                if key in ("科技树", "TechTree"):
                    return c["tech_tree"]
                return None
            if not self._match_words(words, c["name"], c["zh"], c["i"]):
                continue
            if not self._cond_ok(conds, g):
                continue
            out.append({"cat": "文明", "id": c["i"], "name": c["name"], "zh": c["zh"],
                        "info": "特性科技 %d 条 ｜ 科技树 %s ｜ 单位 %d" % (
                            len(c["bonuses"]), c["tech_tree"], c["units_count"]),
                        "rec": c})
        return out

    def search_misc(self, cat, words, conds):
        out = []
        table = {"图形": self.index["graphics"], "音效": self.index["sounds"],
                 "地形": {t["i"]: t for t in self.index["terrains"]}}[cat]
        for i, r in table.items():
            name = r.get("Name") or r.get("FileName") or ""
            zhn = r.get("zh", "")
            if not self._match_words(words, name, zhn, i):
                continue
            if not self._cond_ok(conds, lambda k, r=r, i=i: i if k in ("编号", "ID") else r.get(k)):
                continue
            info = r.get("FileName") or r.get("SLP") or r.get("StringID") or ""
            out.append({"cat": cat, "id": i, "name": name or "（无名字）", "zh": zhn,
                        "info": str(info)[:120], "rec": r})
        return out

    def clear_search(self):
        self.query_var.set("")
        self.tree.delete(*self.tree.get_children())
        self.results = []
        self.set_detail("")

    def set_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def show_detail(self):
        sel = self.tree.selection()
        if not sel:
            return
        i = self.tree.index(sel[0])
        if i >= len(self.results):
            return
        r = self.results[i]
        self.set_detail(self.describe(r))

    def _cmd_resolver(self, key, label, val):
        """把效果命令参数里的编号换成中文名，让命令读起来是人话。"""
        idx = self.index
        try:
            iv = int(val)
        except Exception:
            return None
        lab = str(label)
        if "科技" in lab and iv >= 0:
            rec = idx["techs"].get(iv)
            if rec:
                return "%s（%s）" % (rec.get("zh") or rec.get("Name") or "?", iv)
            return None
        if "单位" in lab and iv >= 0:
            return "%s（%s）" % (self.unit_label(iv), iv)
        if ("资源" in lab or "属性" in lab) and iv >= 0:
            return "%s（%s）" % (attr_name(iv), iv)
        if "类别" in lab and iv >= 0:
            return "%s（%s）" % (class_name(iv), iv)
        return None

    def describe(self, r):
        idx = self.index
        cat, rec = r["cat"], r.get("rec") or {}
        L = []
        if cat == "单位":
            L.append("【单位 %s】%s  %s" % (r["id"], r["name"], r["zh"]))
            L.append("=" * 60)
            for key in ("Type", "Class", "HitPoints", "LineOfSight", "Speed", "GarrisonCapacity",
                        "CollisionSize", "ResourceCapacity", "ResourceDecay", "BlastDefenseLevel",
                        "CombatLevel", "InteractionMode", "MinimapMode", "SortNumber", "IconID",
                        "StandingGraphic", "DyingGraphic", "UndeadGraphic", "DeadUnitID",
                        "BloodUnitID", "CopyID", "BaseID"):
                if key in rec:
                    v = rec[key]
                    extra = ""
                    if key in ("Type", "Class"):
                        extra = "  (%s)" % (unit_type_name(v) if key == "Type" else class_name(v))
                    L.append("%-18s %s%s" % (field_name(key), v, extra))
            t50 = rec.get("t50", {})
            for key in ("DisplayedAttack", "DisplayedMeleeArmour", "DisplayedRange",
                        "DisplayedReloadTime", "MaxRange", "MinRange", "ReloadTime", "AccuracyPercent",
                        "BlastWidth", "BlastDamage", "ProjectileUnitID", "AttackGraphic"):
                if key in t50 and t50[key] not in (None, -1, 0, 0.0):
                    L.append("%-18s %s" % (field_name(key), t50[key]))
            if rec.get("attacks"):
                L.append("攻击加成          " + "；".join("%s +%s" % (class_name(c), a)
                                                          for c, a in rec["attacks"]))
            if rec.get("armours"):
                L.append("护甲              " + "；".join("%s %s" % (class_name(c), a)
                                                          for c, a in rec["armours"]))
            if rec.get("costs"):
                L.append("成本              " + "；".join("%s %s" % (attr_name(a), b)
                                                          for a, b in rec["costs"]))
            if rec.get("train_locations"):
                L.append("训练场所          " + "；".join(
                    "%s（%s 秒，按钮 %s）" % (self.unit_label(t[0]), t[1], t[2])
                    for t in rec["train_locations"]))
            if rec.get("creatable"):
                cr = rec["creatable"]
                for key in ("ButtonIconID", "AttackPriority", "InvulnerabilityLevel",
                            "ChargeType", "MaxCharge", "RechargeRate", "TotalProjectiles",
                            "HeroMode", "CreatableType"):
                    if key in cr and cr[key] not in (None, -1, 0, 0.0):
                        L.append("%-18s %s" % (field_name(key), cr[key]))
            if rec.get("building"):
                for key, v in list(rec["building"].items())[:12]:
                    if v not in (None, -1, 0, 0.0, []):
                        L.append("%-18s %s" % (field_name(key), v))
            techs = idx["refs"]["tech_at_unit"].get(r["id"], [])
            if techs:
                L.append("该单位可研究      " + "；".join(
                    "%s(%s)" % (idx["techs"][t].get("zh") or idx["techs"][t].get("Name"), t)
                    for t in techs[:40]))
            tr = idx["refs"]["trainers"].get(r["id"], [])
            if tr:
                L.append("由谁训练          " + "；".join(
                    "%s" % self.unit_label(t[1]) for t in tr[:20]))
            eff = idx["refs"]["units_touched"].get(r["id"], [])
            if eff:
                L.append("被哪些效果改动    " + "；".join(
                    "效果 %s（%s）" % (e, idx["effects"].get(e, {}).get("name", "")) for e, _t in eff[:20]))
            L.append("\n语言名编号        %s（%s）" % (
                rec.get("LanguageDLLName"), lang_text(idx, rec.get("LanguageDLLName"))))
        elif cat == "科技":
            t = rec
            L.append("【科技 %s】%s  %s" % (r["id"], r["name"], r["zh"]))
            L.append("=" * 60)
            for label, key in (("官方名称", "LanguageDLLName_zh"), ("官方说明", "LanguageDLLDescription_zh"),
                               ("帮助文本", "LanguageDLLHelp_zh"), ("科技树文本", "LanguageDLLTechTree_zh")):
                v = unescape_text(t.get(key) or "")
                if v:
                    L.append("%-20s %s" % (label, v.replace("\n", " ")))
            if not unescape_text(t.get("LanguageDLLName_zh") or ""):
                L.append("%-20s %s" % ("中文缺失原因",
                                       "dat 里这条科技的文本编号是 %s，官方没给它名字；"
                                       "游戏里显示的是按效果数据生成的说明" % t.get("LanguageDLLName")))
            for key in ("Civ", "RequiredTechCount", "Type", "IconID", "FullTechMode", "Repeatable",
                        "LanguageDLLName", "LanguageDLLDescription", "LanguageDLLHelp",
                        "LanguageDLLTechTree", "EffectID"):
                if key in t:
                    v = t[key]
                    if key == "Civ" and isinstance(v, int) and 0 <= v < len(idx["civs"]):
                        v = "%s (%s)" % (idx["civs"][v]["name"], v)
                    elif key == "Civ" and v == -1:
                        v = "全部文明 (-1)"
                    L.append("%-20s %s" % (field_name(key), v))
            if t.get("RequiredTechs"):
                L.append("前置科技          " + "；".join(
                    "%s(%s)" % (idx["techs"].get(x, {}).get("zh") or idx["techs"].get(x, {}).get("Name", "?"), x)
                    for x in t["RequiredTechs"] if x >= 0))
            if t.get("costs"):
                L.append("成本              " + "；".join("%s %s" % (attr_name(a), b) for a, b in t["costs"]))
            L.append("研究地点：")
            for loc in t["locations"]:
                L.append("   %s（%s 秒，按钮 %s，热键 %s）" % (
                    self.unit_label(loc.get("unit")), loc.get("time"), loc.get("button"), loc.get("hotkey")))
            eid = t.get("EffectID", -1)
            if eid is not None and eid >= 0:
                eff = idx["effects"].get(eid, {})
                L.append("效果 %s：%s" % (eid, eff.get("name", "")))
                for c in eff.get("commands", []):
                    L.append("   • " + cmd_text(c, self._cmd_resolver))
        elif cat == "效果":
            e = rec
            L.append("【效果 %s】%s" % (r["id"], e.get("name", "")))
            L.append("=" * 60)
            for c in e.get("commands", []):
                L.append("• " + cmd_text(c, self._cmd_resolver))
            tis = idx["refs"]["effect_of_tech"].get(r["id"], [])
            if tis:
                L.append("\n被这些科技使用：")
                for t in tis[:60]:
                    L.append("   %s - %s" % (t, idx["techs"][t].get("zh") or idx["techs"][t].get("Name")))
        elif cat == "文明":
            c = rec
            L.append("【文明 %s】%s  %s" % (c["i"], c["name"], c["zh"]))
            L.append("=" * 60)
            L.append("科技树 ID        %s" % c["tech_tree"])
            L.append("团队加成科技 ID  %s" % c["team_bonus"])
            card = lang_text(idx, CIV_CARD_BASE + c["i"]) if c["i"] >= 1 else ""
            if card:
                L.append("")
                L.append("【官方文明介绍（游戏内文明卡原文，语言表编号 %s）】" % (CIV_CARD_BASE + c["i"]))
                for line in card.split("\n"):
                    L.append(("   " + line) if line.strip() else "")
            for kind in ("文明加成", "团队加成", "独特科技", "其他加成", "其他"):
                tis = (c.get("tech_kinds") or {}).get(kind, [])
                if not tis:
                    continue
                L.append("\n%s（%d 条）：" % (kind, len(tis)))
                for ti in tis:
                    t = idx["techs"][ti]
                    L.append("   %s - %s  %s" % (ti, t.get("Name", ""),
                                                 tech_cn_text(idx, t, self._cmd_resolver)))
        else:
            L.append("【%s %s】%s" % (cat, r["id"], r["name"]))
            L.append("=" * 60)
            for k, v in list(rec.items())[:40]:
                if k in ("i",):
                    continue
                L.append("%-20s %s" % (field_name(k), str(v)[:160]))
        return "\n".join(str(x) for x in L)

    def unit_label(self, uid):
        if uid is None or uid < 0:
            return "-"
        nm, ldl, lzh = self.index["unit_names"].get(uid, ("", -1, ""))
        base = self.index["base_units"].get(uid) or {}
        dbg = nm or base.get("Name", "")
        return "%s (%s)" % (lzh or dbg or "?", uid)

    def export_csv(self):
        if not self.results:
            messagebox.showinfo(APP_TITLE, "还没有检索结果。")
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="dat_search.csv",
                                         filetypes=[("CSV", "*.csv")])
        if not p:
            return
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["类别", "编号", "名称", "中文名", "关键信息"])
            for r in self.results:
                w.writerow([r["cat"], r["id"], r["name"], r["zh"], r["info"]])
        self.status.set("已导出 %d 条到 %s" % (len(self.results), p))

    # ---- 文明特性页 -------------------------------------------------------
    def show_civ(self):
        if not self.index:
            return
        txt = self.civ_pick.get()
        m = re.match(r"^(\d+)", txt or "1")
        ci = int(m.group(1)) if m else 1
        civ = self.index["civs"][ci]
        self.civ_tree.delete(*self.civ_tree.get_children())
        for kind in ("文明加成", "团队加成", "独特科技", "其他加成", "其他"):
            for ti in (civ.get("tech_kinds") or {}).get(kind, []):
                t = self.index["techs"][ti]
                cn = tech_cn_text(self.index, t, self._cmd_resolver)
                if len(cn) > 70:
                    cn = cn[:69] + "…"
                self.civ_tree.insert("", "end", values=(kind, ti, t.get("Name", ""), cn))
        # 独特单位：该文明独有的单位（其他文明没有该 ID，或该文明与盖亚差异很大）
        base_ids = set(self.index["civ_unit_ids"][0])
        for uid in sorted(self.index["civ_unit_ids"][ci]):
            if uid not in base_ids:
                rec = self.unit_effective(uid, ci)
                nm, ldl, lzh = self.index["unit_names"].get(uid, ("", -1, ""))
                self.civ_tree.insert("", "end", values=(
                    "独特单位", uid, (rec or {}).get("Name", "") or nm, lzh))
        self.set_civ_detail(self.describe({"cat": "文明", "id": ci, "name": civ["name"],
                                           "zh": civ["zh"], "rec": civ}))

    def set_civ_detail(self, text):
        self.civ_detail.configure(state="normal")
        self.civ_detail.delete("1.0", "end")
        self.civ_detail.insert("1.0", text)
        self.civ_detail.configure(state="disabled")

    def show_civ_detail(self):
        sel = self.civ_tree.selection()
        if not sel:
            return
        vals = self.civ_tree.item(sel[0], "values")
        if len(vals) < 2:
            return
        kind, i = vals[0], int(vals[1])
        if kind == "独特单位":
            rec = self.unit_effective(i, self.resolve_civ_index())
            nm, ldl, lzh = self.index["unit_names"].get(i, ("", -1, ""))
            self.set_civ_detail(self.describe({"cat": "单位", "id": i,
                                               "name": (rec or {}).get("Name", "") or nm,
                                               "zh": lzh, "rec": rec or {}}))
        else:
            t = self.index["techs"][i]
            cn = tech_cn_text(self.index, t, self._cmd_resolver)
            self.set_civ_detail(self.describe({"cat": "科技", "id": i,
                                               "name": t.get("Name", ""), "zh": cn,
                                               "rec": t}))

    def resolve_civ_index(self):
        m = re.match(r"^(\d+)", self.civ_pick.get() or "1")
        return int(m.group(1)) if m else 1

    def set_info(self, text):
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("1.0", text.rstrip() + "\n\n"
                              "——————————————————————————————\n"
                              "AGE 数据检索工具　" + AUTHOR + "\n"
                              "（本工具只读，不会修改你的 dat 文件）\n")
        self.info.configure(state="disabled")


def main():
    argv = sys.argv[1:]
    query = None
    civ = None
    for i, a in enumerate(argv):
        if a in ("--query", "-q") and i + 1 < len(argv):
            query = argv[i + 1]
        if a.startswith("--query="):
            query = a.split("=", 1)[1]
        if a in ("--civ", "-c") and i + 1 < len(argv):
            civ = argv[i + 1]
        if a.startswith("--civ="):
            civ = a.split("=", 1)[1]
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.3)
    except Exception:
        pass
    style = ttk.Style()
    try:
        style.theme_use("vista")
    except Exception:
        pass
    app = App(root)
    if query:
        app.pending_query = query
    if civ:
        app.pending_civ = civ
    root.geometry("1180x760")
    root.minsize(900, 600)
    root.mainloop()


if __name__ == "__main__":
    main()
