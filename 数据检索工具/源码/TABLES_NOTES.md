# tables.py 数据来源与验证笔记

对象：`工作\datsearch\tables.py`（UTF-8 无 BOM，1193 行 / 43,714 字节，首字节为 `#`）。
运行自检：

```powershell
$env:PYTHONIOENCODING='utf-8'; [Console]::OutputEncoding=[System.Text.Encoding]::UTF8
& 'C:\Users\26913\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe' `
  'D:\Software\DeepSeek Harness\工作区1\工作\datsearch\tables.py'
```

## 0. 全表硬性规则

1. **中文只来自四张已交付的表**：`工作\used_plan.json`（3116 个去重英文键）、`工作\zh_full.py`（`ZH_FULL`，2343 条）、
   `工作\zh_extra.py`（`ZH_EXTRA`，77 条）、游戏中文语言表
   `D:\Games\Steam\steamapps\common\AoE2DE\resources\zh\strings\key-value\key-value-strings-utf8.txt`（19,677 条 `ID "文本"`）。
   合并后 3821 个英文键。取值优先级：used_plan → zh_full → zh_extra（同键取先出现的）；命中的每一条都能在这三张表里原样找到，
   **没有任何一条是本次新译的**。找不到的条目一律不写。
2. **匹配方式是精确匹配，不是模糊匹配**。译文表的英文键常带前导/尾随空格（源码里用前导空格做右对齐），
   所以每一条标签依次尝试 `原文` / `首尾去空白` / `加前导空格` / `去尾部 *` 四种拼写，命中即止；
   本次实际命中的形式只有两种：337 条 `原文精确命中`、334 条 `规范化首尾空白后精确命中`。
3. 排除 `used_plan.json` 里值等于 `__KEEP__` 的哨兵条目（源码标签 `"="`），它不是译文。
4. 目标值必须含汉字才收录（`[\u4e00-\u9fff]`）；否则视为未翻译而丢弃。
5. `N - Name` 形式的条目，取译文后剥掉前缀 `N - `，再剥掉尾部「（…类型…）」括号
   （只剥括号内容仅含「类型/数字/空格/连字符/顿号/逗号」的尾括号，因此
   `18 - Terrain Defense Bonus (always sets, types 50-80)` 这种带额外文字的括号不会被误剥）。

## 1. 游戏版本判定（决定各表取哪条分支）

`工作\de2.dat` 的 FileVersion 是 `VER 8.9`；`工作\src\genieutils\include\genie\Types.h:94` 把 `"VER 8.9"` 映射为 `GV_C32`，
`Types.h:103` 定义 `const GameVersion GV_LatestDE2 = GV_C32;`。枚举（`Types.h:33-69`）：
`GV_AoEB(40) < GV_RoR < GV_Tapsa(43) < GV_AoKA(45) < GV_AoKB(46) < GV_AoK < GV_TC < GV_Cysion(50) < GV_C2(51) … GV_C32(68) < GV_SWGB(69)`。
所以在 DE 下：

| 源码条件 | DE 取值 |
|---|---|
| `GenieVersion < GV_SWGB` | **真** |
| `GenieVersion < GV_AoKA` | 假 |
| `GenieVersion >= GV_AoKA` / `>= GV_AoKB` | 真 |
| `GenieVersion >= GV_Cysion && <= GV_LatestDE2` | 真 |
| `isAoE2DE`（`>= GV_C2 && <= GV_LatestDE2`） | 真 |
| `GameVersion == EV_UP`（UserPatch） | 假 |

---

## 2. `FIELD_ZH` — 794 条

**内容**：AGE3 界面字段英文标签 → 中文。

**来源文件与抽取口径**（全部是只读解析，没有改源码）：

| 抽取目标 | 源码写法 | 举例 |
|---|---|---|
| 字段标签 | `new SolidText(<父窗口>, "<标签>", …)` 里的**第一个字符串字面量**（构造签名见 `工作\src\AGE\CustomWidgets.cpp:46`，类定义 `工作\src\AGE\CustomWidgets.h:27`） | `工作\src\AGE\AGE_Frame\Units.cpp:4050` `Units_HitPoints_Text = new SolidText(Units_Scroller, " Hit Points *");` |
| 分组标题 | `new wxStaticBoxSizer(wxVERTICAL, Tab_Units, "Units")` 里的第一个字面量 | `"Units"` / `"Research Locations"`（`工作\src\AGE\AGE_Frame\Research.cpp:623`） |
| 筛选器名 | `<name>_filters.Add("<名>")`：`unit_filters` / `research_filters` / `terrain_filters` / `graphic_filters` / `tt_unit_filters` / `tt_building_filters` / `tt_research_filters` | `工作\src\AGE\AGE_Frame\Lists.cpp:2509` `unit_filters.Add("Internal Name");`、`Lists.cpp:2731` `research_filters.Add("Cost Types");` |

扫描范围：`工作\src\AGE\` 下全部 `.cpp`。共采集到 887 处标签（582 处 `SolidText`/`wxStaticBoxSizer` + 305 处筛选器名），
去重后 **680 个不同标签**，其中 **671 个拿到已交付译文**（命中率 98.7%），9 个丢弃。

**键的形式**：保留源码原样的去空白键（含尾部 ` *`，例如 `"Hit Points *"`），另外对以 `*` 结尾的标签额外补一条去掉 `*` 的键
（`"Hit Points"`）。794 = 587 个不带 `*` 的键 + 207 个带 `*` 的键。这样无论调用方拿到的字段名带不带 `*` 都能查到。

**任务点名的字段名实测**（键 → 值）：

| 键 | 值 | 源码位置 |
|---|---|---|
| `Internal Name` | 内部名称 | `工作\src\AGE\AGE_Frame\Lists.cpp:2509`（`unit_filters.Add("Internal Name")`）等 |
| `Lang File Name` | 语言文件名 | `工作\src\AGE\AGE_Frame\Lists.cpp:2718` |
| `Hit Points` | 生命值 | `工作\src\AGE\AGE_Frame\Lists.cpp:2473`（`unit_filters.Add("Hit Points")`）/ `Units.cpp:4050` |
| `Research Locations` | 研究点 | `工作\src\AGE\AGE_Frame\Research.cpp:623` |
| `Research Location` | 研究点 | `工作\src\AGE\AGE_Frame\Lists.cpp:2722` |
| `Button ID` | 按钮 ID | `工作\src\AGE\AGE_Frame\Units.cpp:4834`、`Research.cpp:661` |
| `Hotkey ID` | 热键 ID | `工作\src\AGE\AGE_Frame\Units.cpp:4839`、`Research.cpp:666` |
| `Research Time` | 研究时间 | `工作\src\AGE\AGE_Frame\Lists.cpp:2723` |
| `Train Location` | 训练点 | `工作\src\AGE\AGE_Frame\Lists.cpp:2633` |
| `Train Time` | 训时间 | `工作\src\AGE\AGE_Frame\Lists.cpp:2632`（**译文表里就是"训时间"，少了"练"字，按"与已交付汉化一致"的要求原样照抄**） |
| `Type` / `Class` / `Civilization` | 类型 / 类别 / 文明 | `工作\src\AGE\AGE_Frame\Lists.cpp:2538` 等 |
| `Cost Types` / `Cost Amounts` | 价格类型 / 价格数量 | `工作\src\AGE\AGE_Frame\Lists.cpp:2731` / `2732` |

**丢弃的 9 个标签**（这三张表里确实没有对应译文，按"宁可不写"处理）：
`*Choose*`（`Lists.cpp:2464`）、`=`（`General.cpp:177`，译文表里是哨兵 `__KEEP__`）、
`Advanced Genie Editor\nVersion`（`AboutDialog.cpp:18`）、`Credits:\nYkkrosh - GeniEd 1 source code`（`AboutDialog.cpp:27`）、
`Libraries used:`（`AboutDialog.cpp:32`）、`DRS`（`Lists.cpp:2832`）、`SLP`（`TerrainBorders.cpp:519`）、
`Tile Graphics: flat, 2 x 8 elevation, 2 x 1:1\n Frame Count, Animations, Frame Index`（`Terrains.cpp:800`）、
`\n Unit/Tech Groups *`（`TechTrees.cpp:2539`）。
其中只有最后一条和 `\n Unit/Tech Groups *` 勉强算字段标签，其余都是"关于"对话框文本、文件类型名和长工具提示，不影响字段渲染。

**未能给出**：任务里点名的 `Language DLL Name` —— AGE3 源码中**根本不存在这个字符串**（全树搜索无命中），
`工作\used_plan.json` 里也没有它的键，所以 `FIELD_ZH` 里没有这一项，也不应凭"Lang File Name"类推。

---

## 3. `ATTR_ZH` — 89 条（ID 0–34、40–77、100–115）

**来源**：`工作\src\AGE\AGE_Frame\Lists.cpp` 的 `effect_attribute_names`（`Clear()` 在 `Lists.cpp:250`，填充到 `Lists.cpp:387`），
即属性下拉框的名字表。原文字面量形如 `"101 - Train Time (types 70-80)"`。

**为什么是这些 ID**：DE 下 `GenieVersion < GV_AoKA` 为假，所以走 else 分支；`isAoE2DE` 为真，所以 24–34、40–77、110–115 也在列表里。
按行区间取字面量（这些区间是逐段核对过的，**不是**"全文搜 `N - `"）：

```
252–259 → 0–7        267–268 → 8,9（else 分支，AoE2 的 Armor/Attack，非 "no multiply" 版本）
270–278 → 10–18      281     → 19
289–343 → 20–77（其中 35–39 不存在，列表本来就跳号）
345–348 → 100–103    351–353 → 104–106（GenieVersion < GV_SWGB 分支 = 木/金/石，不是 SWGB 的 Carbon/Nova/Ore）
361     → 107        364     → 108（>= GV_AoKB）      368 → 109（GV_Cysion..GV_LatestDE2，DE 命中）
372–377 → 110–115
```
`Lists.cpp:380–387` 是 AoE1 的 else 分支，里面也有 `"100 - Resource Costs"` 和 `"101 - Population (set only)"`，**必须排除**——
如果不排除，101 就会变成"人口"而不是"训练时间"。ID 跳号：无 35–39、无 78–99。

**覆盖率：89/89，0 条缺失。** 分布：0–34（35 条）、40–77（38 条）、100–115（16 条）。

**抽样验证（101 + 随机 5 个 ID，直接从 Lists.cpp 读原文再对 used_plan.json）**

```
id 101  Lists.cpp:346  raw='101 - Train Time (types 70-80)'
        used_plan['101 - Train Time (types 70-80)'] = '101 - 训练时间（类型 70-80）'
        ATTR_ZH[101] = '训练时间'                      ← 剥掉 "101 - " 与尾部"（类型 70-80）"
id 8    Lists.cpp:267  raw='8 - Armor (types 50-80)'
        used_plan['8 - Armor (types 50-80)'] = '8 - 护甲（类型 50-80）'   → ATTR_ZH[8]  = '护甲'
id 21   Lists.cpp:290  raw='21 - Amount of 1st resource storage'
        used_plan[...] = '21 - 第1资源存放数量'                            → ATTR_ZH[21] = '第1资源存放数量'
id 25   Lists.cpp:296  raw='25 - Icon'
        used_plan[...] = '25 - 图标'                                        → ATTR_ZH[25] = '图标'
id 77   Lists.cpp:343  raw='77 - Special Graphic (types 70-80)'
        used_plan[...] = '77 - 特殊图像（类型 70-80）'                       → ATTR_ZH[77] = '特殊图像'
id 108  Lists.cpp:364  raw='108 - Garrison Heal Rate (type 80)'
        used_plan[...] = '108 - 驻疗速率（类型 80）'                         → ATTR_ZH[108] = '驻疗速率'
```
（随机抽样用 `random.seed(20240613)`，抽到的 5 个 ID 是 8 / 21 / 25 / 77 / 108，外加固定的 101；6 条全部与 used_plan.json 一致。）
另外 `ATTR_ZH[42] = '训练点'`，对应 `Lists.cpp:308` 的 `effect_attribute_names.Add("42 - Train Location (types 70-80)");`
（`工作\used_plan.json` 里是 `'42 - Train Location (types 70-80)' -> '42 - 训练点（类型 70-80）'`）。

---

## 4. `CLASS_ZH` — 62 条（ID 0–61）

**来源**：`工作\src\AGE\AGE_Frame\Lists.cpp` 的 `class_names`（`Clear()` 在 `Lists.cpp:104`，`if (GenieVersion < genie::GV_SWGB)` 在 `Lists.cpp:106`）。
DE 下该条件为**真**，所以取 `Lists.cpp:108–176` 这一段（AoE1/AoE2 通用名单）；`Lists.cpp:182–247` 的 else 分支是 SWGB/星战名单
（里面也有 0–61 的重复 ID，如 `"0 - Unused"`），**必须排除**。

该段里有 4 处条件表达式，DE 取 AoE2 侧：

| 行 | 表达式 | DE 取 |
|---|---|---|
| `Lists.cpp:126` | `"18 - Priest" : "18 - Monk"` | `18 - Monk` → 僧侣 |
| `Lists.cpp:131` | `"23 - Chariot Archer" : "23 - Conquistador"` | `23 - Conquistador` → 征服者 |
| `Lists.cpp:143` | `"35 - Chariot" : "35 - Petard"` | `35 - Petard` → 爆破兵 |
| `Lists.cpp:147–154` | `if (< GV_AoKA) "39 - Slinger" else "39 - Gate"` | `39 - Gate` → 门 |

实现上是"在 108–176 区间内同一 ID 取**最后**出现者"，正好等价于上表。

**覆盖率：62/62（ID 0–61 连续），0 条缺失。** 抽样：

```
Lists.cpp:144  raw='36 - Cavalry Archer'     used_plan='36 - 骑射手'      → CLASS_ZH[36] = '骑射手'
Lists.cpp:126  raw='18 - Monk'              used_plan='18 - 僧侣'        → CLASS_ZH[18] = '僧侣'
Lists.cpp:131  raw='23 - Conquistador'      used_plan='23 - 征服者'      → CLASS_ZH[23] = '征服者'
Lists.cpp:143  raw='35 - Petard'            used_plan='35 - 爆破兵'      → CLASS_ZH[35] = '爆破兵'
Lists.cpp:154  raw='39 - Gate'              used_plan='39 - 门'          → CLASS_ZH[39] = '门'
Lists.cpp:108  raw='0 - Archer'             used_plan='0 - 弓兵'         → CLASS_ZH[0]  = '弓兵'
Lists.cpp:176  raw='61 - Controlled Animal' used_plan='61 - 受控动物'    → CLASS_ZH[61] = '受控动物'
```

---

## 5. `UNIT_TYPE_ZH` — 11 条

**来源**：`工作\src\AGE\AGE_Frame\Units.cpp:5133` 起 `unit_type_names` 的 11 条（`Units.cpp:5134–5144`），
`Units.cpp:5145` 是 `Units_Type_ComboBox->Flash();`；下拉框创建于 `Units.cpp:3793`。`Units.cpp:5133` 的
`"No Type/Invalid Type"` 是哨兵（Selection 0，不对应任何数字 ID），跳过。

**覆盖率：11/11（ID 10/15/20/25/30/40/50/60/70/80/90），0 条缺失。** 抽样：

```
Units.cpp:5142  raw='70 - Combatant'  used_plan='70 - 战斗单位'  → UNIT_TYPE_ZH[70] = '战斗单位'
Units.cpp:5134  raw='10 - Eye Candy'  used_plan='10 - 装饰物'    → UNIT_TYPE_ZH[10] = '装饰物'
Units.cpp:5144  raw='90 - Tree (AoE)' used_plan='90 - 树(帝国1)' → UNIT_TYPE_ZH[90] = '树(帝国1)'
```

---

## 6. `CMD_ZH` — 55 条

**来源**：`工作\src\AGE\AGE_Frame\Lists.cpp` 的 `effect_type_names`（`Clear()` 在 `Lists.cpp:584`，填充到 `Lists.cpp:701`），
即效果页「命令类型」下拉框。**列表里的顺序就是 ID**，但 ID 有跳号。

DE 下不在列表中的 ID：`9/19/29/39`（这四个只在 `GameVersion == EV_UP` 的 UserPatch 分支里出现，DE 根本没有这类效果）。
其余 `7/8/17/18/27/28/37/38` 在文件里出现两次（DE 分支 + UP 分支），取先出现的 DE 版本
（`"7 - Spawn Unit"`、`"8 - Modify Tech"`），UP 分支的 `Enable/Disable/Force Multiuse Tech` 被丢弃。

**覆盖率：55/55，0 条缺失。** ID 集合：
`0–8, 10–18, 20–28, 30–38, 40–48, 101, 102, 103, 200–206`。
（10–18 是 Team 版、20–28 是 Enemy 版、30–38 是 Neutral 版、40–48 是 Gaia 版，
与 0–8 的基础版一一对应；200–205 是 Own Master Objects / Selected Unit 的属性修正系列，206 是 Transform Selected Unit。）

抽样：

```
Lists.cpp:586  raw='0 - Attribute Modifier (Set)'       used_plan='0 - 属性修正（设置）'        → CMD_ZH[0]   = '属性修正（设置）'
Lists.cpp:590  raw='4 - Attribute Modifier (+/-)'       used_plan='4 - 属性修正（+/-）'        → CMD_ZH[4]   = '属性修正（+/-）'
Lists.cpp:591  raw='5 - Attribute Modifier (Multiply)'  used_plan='5 - 属性修正（乘法）'        → CMD_ZH[5]   = '属性修正（乘法）'
Lists.cpp:598  raw='7 - Spawn Unit'                     used_plan='7 - 生成单位'              → CMD_ZH[7]   = '生成单位'
Lists.cpp:599  raw='8 - Modify Tech'                    used_plan='8 - 修改科技'              → CMD_ZH[8]   = '修改科技'
Lists.cpp:680  raw='48 - Gaia Modify Tech'              used_plan='48 - 盖亚修改科技'          → CMD_ZH[48]  = '盖亚修改科技'
Lists.cpp:684  raw='101 - Tech Cost Modifier (Set/+/-)' used_plan='101 - 科技价格修改(设定/+/-)' → CMD_ZH[101] = '科技价格修改(设定/+/-)'
Lists.cpp:686  raw='102 - Disable Tech'                 used_plan='102 - 禁用科技'            → CMD_ZH[102] = '禁用科技'
Lists.cpp:700  raw='206 - Transform Selected Unit'      used_plan='206 - 变身所选单位'         → CMD_ZH[206] = '变身所选单位'
```

---

## 7. `CMD_FIELDS` — 55 条（ID 集与 `CMD_ZH` 完全一致）

**来源**：`工作\src\AGE\AGE_Frame\Techs.cpp` 的 `OnEffectCmdSelect`。里面定义了 16 个 lambda
（`auto ShowXxx = …`），每个 lambda 用 `Effects_A_Text->SetLabel("…")` / `Effects_B_Text` / `Effects_C_Text` / `Effects_D_Text`
给 A/B/C/D 四个输入框设标签；再由 `switch (EffectPointer->Type)`（`Techs.cpp:1264–1780`）的 `case` 选 lambda。

lambda 起始行（实测）：

```
ShowSetAttributeModifier 731       ShowSetOrChangeResourceModifier 772   ShowEnableDisableUnit 809
ShowUpgradeUnit 837                ShowChangeAttributeModifier 867       ShowMultiplyAttributeModifier 909
ShowMultiplyResourceModifier 951   ShowEnableTech 978                    ShowSpawnUnit 1005
ShowModifyTech 1034                ShowSetPlayerCivName 1072             ShowTechCostModifier 1106
ShowDisableTech 1143               ShowTechTimeModifier 1169             ShowNothing 1204
ShowTransformUnit 1230
```

case → lambda（59 个 `case` 标签，其中 8/18/28/38 与 9/19/29/39 等是多标签 fallthrough，已按 C++ 语义向下继承；
DE 不存在的 9/19/29/39 已在 `CMD_ZH`/`CMD_FIELDS` 中剔除）：

```
0→SetAttributeModifier       1→SetOrChangeResourceModifier  2→EnableDisableUnit      3→UpgradeUnit
4→ChangeAttributeModifier    5→MultiplyAttributeModifier    6→MultiplyResourceModifier 7→SpawnUnit
8,18,28,38→ModifyTech        10–16 同 0–6                   17→SpawnUnit
20–26 同 0–6                 27→SpawnUnit                   28→(见上)
30–36 同 0–6                 37→SpawnUnit                   40–46 同 0–6            47→SpawnUnit
48→ModifyTech                101→TechCostModifier            102→DisableTech         103→TechTimeModifier
200,203→SetAttributeModifier 201,204→ChangeAttributeModifier 202,205→MultiplyAttributeModifier
206→TransformUnit
```

四个标签各自的实测字面量（`Techs.cpp` 行号）：

| lambda | A | B | C | D |
|---|---|---|---|---|
| SetAttributeModifier | 749 `"Unit "` | 750 `"Class "` | 751 `"Attribute "` | 752 `"Amount [Set] "` |
| SetOrChangeResourceModifier | 792 `"Resource "` | 793 `"Mode "` | 794 `"Resource [*] "` | 797 `"Amount [Set] "` **或** 801 `"Amount [+/-] "` |
| EnableDisableUnit | 827 `"Unit "` | 828 `"Mode "` | 829 `"Unused "` | 830 `"Unused "` |
| UpgradeUnit | 857 `"Unit "` | 858 `"To Unit "` | 859 `"Mode "` | 860 `"Unused "` |
| ChangeAttributeModifier | 885 `"Unit "` | 886 `"Class "` | 887 `"Attribute "` | 896 `"Amount [+] "` **或** 904 `"Amount [+/-] "` |
| MultiplyAttributeModifier | 927 `"Unit "` | 928 `"Class "` | 929 `"Attribute "` | 938 `"Amount [%] "` **或** 946 `"Amount [*] "` |
| MultiplyResourceModifier | 968 `"Resource "` | 969 `"Unused "` | 970 `"Unused "` | 971 `"Amount [*] "` |
| SpawnUnit | 1024 `"Unit "` | 1025 `"From Building "` | 1026 `"Amount "` | 1027 `"Unused "` |
| ModifyTech | 1057 `"Tech "` | 1058 `"Action * "` | 1059 `"Research Location"` | 1068 `"Amount "` |
| TechCostModifier | 1126 `"Tech "` | 1127 `"Resource "` | 1128 `"Mode "` | 1131 `"Amount [Set] "` **或** 1135 `"Amount [+/-] "` |
| DisableTech | 1159–1161 `"Unused "` | 1160 `"Unused "` | 1161 `"Unused "` | 1162 `"Tech "` |
| TechTimeModifier | 1187 `"Tech "` | 1188 `"Unused "` | 1189 `"Mode "` | 1192 `"Amount [Set] "` **或** 1196 `"Amount [+/-] "` |
| ShowNothing | 1220 `"Attribute A "` | 1221 `"Attribute B "` | 1222 `"Attribute C "` | 1223 `"Attribute D "` |
| TransformUnit | 1252 `"Target Unit "` | 1253 `"Preserve Actions "` | 1254 `"Retain Local Tech Upgrades "` | 1255 `""`（空） |

**关于 D 的动态标签**：`1 / 4 / 5 / 101 / 103` 这 5 类命令的 D 标签由 A/B/C 的取值决定（同一个 lambda 里
先 `SetLabel(x)` 再按条件改成 `SetLabel(y)`）。这些 lambda 里的**两个**字面量都存在已交付译文，
所以 `CMD_FIELDS` 用 `" / "` 并列两种可能，例如

```python
1:   ("资源", "模式", "资源 [*]", "数量 [设置] / 数量 [+/-]"),
4:   ("单位", "类别", "属性", "数量 [+] / 数量 [+/-]"),
5:   ("单位", "类别", "属性", "数量 [%] / 数量 [*]"),
101: ("科技", "资源", "模式", "数量 [设置] / 数量 [+/-]"),
103: ("科技", "未用", "模式", "数量 [设置] / 数量 [+/-]"),
```

并列用的两个词都来自译文表（`'Amount [Set] '→'数量 [设置] '`、`'Amount [+/-] '→'数量 [+/-] '`、
`'Amount [+] '→'数量 [+] '`、`'Amount [%] '→'数量 [%] '`、`'Amount [*] '→'数量 [*] '`），没有新造字。

**关于 9/19/29/39**：DE 下 `ShowSetPlayerCivName` 不会被调用（`GameVersion == EV_UP` 才走该分支），
对应的效果类型 ID 在 DE 的 `effect_type_names` 里也不存在，因此不出现在任何一张表里。
`ShowEnableTech`（ID 9 的 UP 含义）同理。

样例（完整 55 条见 tables.py）：

```
0:   ('单位', '类别', '属性', '数量 [设置]')
1:   ('资源', '模式', '资源 [*]', '数量 [设置] / 数量 [+/-]')
4:   ('单位', '类别', '属性', '数量 [+] / 数量 [+/-]')
7:   ('单位', '来自建筑', '数量', '未用')
8:   ('科技', '动作 *', '研究点', '数量')
48:  ('科技', '动作 *', '研究点', '数量')
102: ('未用', '未用', '未用', '科技')
206: ('目标单位', '保留动作', '保留本地科技升级', '')
```

---

## 8. `CIV_ZH` — 63 条

**采用的规则（规则 A）**：

> **`CIV_ZH[文明内部英文名] = 中文语言表[10270 + 该文明在 dat 里的下标]`**

数据源：`工作\de2.dat`（93 MB，`VER 8.9`；用只读的 `工作\datsearch\datlib.py` 解析：
`datlib.load_input(path)` 返回 `(payload, mode)`，再 `datlib.load_payload(payload)` 得到 `Dat`，取 `Dat.Civs[i].Name` 与 `.index`）。
中文语言表：`D:\Games\Steam\steamapps\common\AoE2DE\resources\zh\strings\key-value\key-value-strings-utf8.txt`（19,677 条）。
实测 dat 里共 **63 个文明**，`10270 + i` 对 **63/63** 全部命中。

**为什么不用"按英文名反查语言表"**：英文语言表（`resources\en\...\key-value-strings-utf8.txt`，同为 19,677 条）
按规范化文本反查内部名的做法只命中 **58/63**，漏掉 `British`、`French`、`Byzantine`、`Mayan`、`Incas`——
因为 AGE3 的文明内部名与游戏显示名并不相同（内部 `British` 对应显示名 `Britons`，`French`↔`Franks`，`Byzantine`↔`Byzantines`，
`Mayan`↔`Mayans`，`Incas`↔`Incas` 大小写/复数差异）。所以放弃该做法。

**Gaia 特例**：下标 0 是 `Gaia`，但 `10270` 在语言表里是 `"Random"`/`随机`（大厅的"随机文明"选项），不是 Gaia 的名字。
所以下标 0 改用"英文语言表按文本反查 `Gaia`"得到 ID `10102`，再取中文 `大地之母`（已实测 `en[10102]="Gaia"`，`zh[10102]="大地之母"`）。
其余 62 条走规则 A。

**覆盖率：63/63，0 条缺失。**

```
 0 Gaia=大地之母(10102)   1 British=不列颠      2 French=法兰克      3 Goths=哥特        4 Teutons=条顿
 5 Japanese=日本          6 Chinese=中国        7 Byzantine=拜占庭   8 Persians=波斯     9 Saracens=萨拉森
10 Turks=土耳其          11 Vikings=维京       12 Mongols=蒙古      13 Celts=凯尔特     14 Spanish=西班牙
15 Aztecs=阿兹特克       16 Mayan=玛雅         17 Huns=匈奴         18 Koreans=高丽     19 Italians=意大利
20 Hindustanis=印度斯坦  21 Incas=印加         22 Magyars=马扎尔     23 Slavs=斯拉夫     24 Portuguese=葡萄牙
25 Ethiopians=埃塞俄比亚 26 Malians=马里       27 Berbers=柏柏尔     28 Khmer=高棉        29 Malay=马来
30 Burmese=缅甸          31 Vietnamese=越南    32 Bulgarians=保加利亚 33 Tatars=鞑靼     34 Cumans=库曼
35 Lithuanians=立陶宛    36 Burgundians=勃艮第 37 Sicilians=西西里   38 Poles=波兰        39 Bohemians=波希米亚
40 Dravidians=达罗毗荼   41 Bengalis=孟加拉    42 Gurjaras=瞿折罗    43 Romans=罗马       44 Armenians=亚美尼亚
45 Georgians=格鲁吉亚    46 Achaemenids=阿契美尼德 47 Athenians=雅典  48 Spartans=斯巴达   49 Shu=蜀
50 Wu=吴                 51 Wei=魏             52 Jurchens=女真      53 Khitans=契丹     54 Macedonians=马其顿人
55 Thracians=色雷斯人    56 Puru=普鲁人        57 Muisca=穆伊斯卡    58 Mapuche=马普切    59 Tupi=图皮
60 Saxons=撒克逊人       61 Varangians=瓦兰吉人 62 Danes=丹麦人
```

**注意**：`CIV_ZH` 的键是 AGE3/dat 的**内部名**（`British`、`French`…），不是游戏界面上的显示名（`Britons`、`Franks`…）。
如果检索工具拿到的文明名来自游戏文本（显示名），需要另配一张显示名表；本表按任务要求（例：`"British" -> "不列颠"`）用内部名。

---

## 9. 自检输出（实测，2024-06-13 运行）

```
FIELD_ZH       794 entries
ATTR_ZH         89 entries
CLASS_ZH        62 entries
UNIT_TYPE_ZH    11 entries
CMD_ZH          55 entries
CMD_FIELDS      55 entries
CIV_ZH          63 entries

ATTR_ZH[101]      = 训练时间
ATTR_ZH[42]       = 训练点
UNIT_TYPE_ZH[70]  = 战斗单位
CLASS_ZH[36]      = 骑射手
CMD_ZH[4]         = 属性修正（+/-）
CMD_ZH[5]         = 属性修正（乘法）
CMD_FIELDS[4]     = ('单位', '类别', '属性', '数量 [+] / 数量 [+/-]')
CIV_ZH['British'] = 不列颠

self-check OK
```

自检块里除任务要求的 `assert ATTR_ZH.get(101) == "训练时间"` 外，还加了
`UNIT_TYPE_ZH[70] == "战斗单位"`、`CLASS_ZH[36] == "骑射手"` 两条断言，以及上述打印。
字符编码实测：文件首字节 `0x23`（`#`），无 BOM；用 `io.open(..., "w", encoding="utf-8")` 写出，换行统一为 `\n`。

## 10. 覆盖率汇总

| 表 | 条目数 | 覆盖率 | 说明 |
|---|---|---|---|
| `FIELD_ZH` | 794 | 671/680 源码标签（98.7%） | 9 条源码串在译文表里没有译文，已丢弃；`Language DLL Name` 源码中不存在 |
| `ATTR_ZH` | 89 | 89/89（100%） | DE 生效 ID 全集 |
| `CLASS_ZH` | 62 | 62/62（100%） | ID 0–61 连续 |
| `UNIT_TYPE_ZH` | 11 | 11/11（100%） | DE 全部单位类型 |
| `CMD_ZH` | 55 | 55/55（100%） | DE 全部效果命令类型 |
| `CMD_FIELDS` | 55 | 55/55（100%） | 与 `CMD_ZH` 同 ID 集；5 类命令的 D 用 `" / "` 并列两种动态标签 |
| `CIV_ZH` | 63 | 63/63（100%） | Gaia 用语言表 10102 特例 |

没有任何一张表是完全没找到的；唯一"只能部分给出"的是 `FIELD_ZH`（少 9 条非字段的 UI 串），
以及任务点名但源码中不存在的 `Language DLL Name`。
