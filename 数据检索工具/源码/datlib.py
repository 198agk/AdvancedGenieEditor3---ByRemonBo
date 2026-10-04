#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
datfile_c32.py -- pure-Python reader/writer for Age of Empires II: DE .dat files
(GameVersion GV_C32 / "VER 8.9").  Field order and types are transcribed from the
genieutils C++ sources (read-only reference at 工作\\src\\genieutils).

Public API
----------
    load_payload(bytes) -> Dat        parse an *uncompressed* dat payload
    Dat.to_bytes()      -> bytes      re-serialise (byte-identical roundtrip)
    expand(dat, units=20000, techs=20000, effects=20000, unit_headers=20000)
    build_manifest(orig, expanded, orig_offsets, info)  byte-exact edit script
    verify_manifest(orig, expanded, manifest)           replay + check

CLI
---
    python datfile_c32.py verify  [path]
    python datfile_c32.py info    [path]
    python datfile_c32.py expand --out <file.dat> [--src=P] [--units=N]
                        [--techs=N] [--effects=N] [--unit-headers=N]
                        [--manifest=P]

Both an already-decompressed payload and the real raw-deflate .dat are
accepted; the format is auto-detected from the leading b"VER " magic.
"""

import json
import os
import struct
import sys
import zlib
import hashlib

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

GV_C32 = 32                      # == GV_LatestDE2 (this file's version)
TILE_TYPE_COUNT = 19             # SharedTerrain::TILE_TYPE_COUNT
TERRAIN_UNITS_SIZE = 30          # Terrain::TERRAIN_UNITS_SIZE
TERRAIN_COUNT_C32 = 200          # Terrain::getTerrainCount() for gv>=GV_C8
ZONE_COUNT_C32 = 10              # TechTreeAge::getZoneCount()  for gv>=GV_AoKB
TECHTREE_SLOTS_C32 = 10          # techtree::Common::getSlots() for gv>=GV_AoKB
TECHTREE_AGES = 5                # BuildingConnection::AGES
BUILDING_ANNEXES_SIZE = 4
LOOTABLE_RES_COUNT = 6
DEBUG_STRING_MARKER = 0x0A60

# UnitType enum (Unit.h)
UT_EYECANDY = 10
UT_TREES = 15
UT_FLAG = 20
UT_25 = 25
UT_DEAD_FISH = 30
UT_BIRD = 40
UT_COMBATANT = 50
UT_PROJECTILE = 60
UT_CREATABLE = 70
UT_BUILDING = 80
UT_AOE_TREES = 90

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PAYLOAD = os.path.join(os.path.dirname(HERE), "de2.dat")   # 工作\de2.dat
DEFAULT_ORIG_DAT = os.path.join(
    os.path.dirname(os.path.dirname(HERE)),
    "官方内容", "Tools_Builds", "官方数据", "Ver.185872", "empires2_x2_p1.dat")


# --------------------------------------------------------------------------- #
# low level codec
# --------------------------------------------------------------------------- #

class DStr(str):
    """A debug string that remembers its exact on-disk byte image.

    The official .dat is NOT self-consistent about the trailing NUL of
    serializeDebugString payloads: e.g. SoundItem file names are stored as
    size == len(name) with no NUL ("BLAC.wav" -> 08 00 + 8 bytes) while
    Graphic names are stored as size == len(name) + 1 with a NUL.  genieutils
    always adds the NUL on write (serializeSize(..., str.size())), so a
    text-only representation cannot round-trip this file byte-for-byte.
    DStr keeps the raw bytes and behaves like a normal str everywhere else.
    """

    __slots__ = ("raw",)

    def __new__(cls, raw):
        raw = bytes(raw)
        text = raw.split(b"\x00", 1)[0].decode("latin-1")
        obj = super().__new__(cls, text)
        obj.raw = raw
        return obj


class Writer:
    __slots__ = ("buf",)

    def __init__(self):
        self.buf = bytearray()

    # ---- primitives -------------------------------------------------------
    def u8(self, v):
        self.buf.append(v & 0xFF)

    def i16(self, v):
        self.buf += struct.pack("<h", _clamp_s(v, 16))

    def u16(self, v):
        self.buf += struct.pack("<H", v & 0xFFFF)

    def i32(self, v):
        self.buf += struct.pack("<i", _clamp_s(v, 32))

    def u32(self, v):
        self.buf += struct.pack("<I", v & 0xFFFFFFFF)

    def f32(self, v):
        self.buf += struct.pack("<f", v)

    # ---- strings ----------------------------------------------------------
    def fixed_string(self, s, n):
        """ISerializable::writeString -- write exactly n bytes, '\\0' padded."""
        if n <= 0:
            return
        b = s if isinstance(s, (bytes, bytearray)) else s.encode("latin-1", "replace")
        if len(b) > n:
            b = b[:n]
        self.buf += b + b"\x00" * (n - len(b))

    def debug_string(self, s):
        """serializeDebugString: uint16 0x0A60 + exact on-disk sized string.

        A DStr is written back verbatim (raw bytes + raw size).  A plain str /
        bytes is written the way the official file writes a *new* record: the
        size is the byte count and no NUL is appended (an empty string is
        therefore 0x0A60 + 0x0000 with no payload, exactly as the game emits
        for blank records).
        """
        self.u16(DEBUG_STRING_MARKER)
        raw = s.raw if isinstance(s, DStr) else (
            s if isinstance(s, (bytes, bytearray)) else s.encode("latin-1", "replace"))
        raw = bytes(raw)
        self.u16(len(raw))
        if raw:
            self.buf += raw


def _clamp_s(v, bits):
    """mimic C++ static_cast<T> wraparound for signed integers"""
    m = 1 << bits
    v &= (m - 1)
    if v >= (m >> 1):
        v -= m
    return v


class Reader:
    __slots__ = ("data", "pos", "n")

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.n = len(data)

    # ---- primitives -------------------------------------------------------
    def u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def i16(self):
        v = struct.unpack_from("<h", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u16(self):
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    # ---- strings ----------------------------------------------------------
    def fixed_string(self, n):
        """ISerializable::readString -- advance n bytes, cut at first NUL.

        Returns a DStr so the exact on-disk image (including any garbage that
        follows an embedded NUL) survives a read -> write round trip.
        """
        if n <= 0:
            return DStr(b"")
        raw = bytes(self.data[self.pos:self.pos + n])
        self.pos += n
        return DStr(raw)

    def debug_string(self):
        marker = self.u16()
        assert marker == DEBUG_STRING_MARKER, \
            "debug-string marker mismatch at %#x: %#x" % (self.pos - 2, marker)
        n = self.u16()
        raw = bytes(self.data[self.pos:self.pos + n])
        self.pos += n
        return DStr(raw)


# --------------------------------------------------------------------------- #
# default-constructed "blank" records (for the appended slots)
# --------------------------------------------------------------------------- #

def default_effect_bytes():
    """Effect == Techage, all defaults: debug string "" + int16 0 commands.

    == 6 bytes `60 0a 00 00 00 00`.  The official file already contains 184
    effects with an empty Name, and every one of them is exactly these bytes.
    """
    return _ser(Effect())


def default_tech_bytes():
    """Tech == Research, all defaults for GV_C32.

    == 62 bytes: 12 (RequiredTechs[6]) + 15 (ResourceCosts[3], 5 B each)
       + 2 (RequiredTechCount) + 2 (Civ) + 2 (FullTechMode)
       + 4 + 4 (LanguageDLLName / LanguageDLLDescription)
       + 2 + 2 + 2 (EffectID / Type / IconID)
       + 4 + 4 (LanguageDLLHelp / LanguageDLLTechTree)
       + 4 (debug string, empty Name) + 1 (Repeatable)
       + 2 (ResearchLocations count, empty).
    """
    return _ser(Tech())


def default_unit_bytes():
    """Unit with all defaults, Type=10 (returns before Speed and sub-structs)."""
    return _ser(Unit())


def default_unit_header_bytes():
    """UnitHeader with all defaults.

    C++ (include/genie/dat/UnitHeader.h:36) is ``uint8_t Exists = 1;`` and
    AGE's own "add unit" button appends a default-constructed UnitHeader, so
    the default image is 3 bytes: 01 (Exists=1) + 00 00 (uint16 task count 0).
    """
    return _ser(UnitHeader())


def _ser(obj):
    """Serialise any _S object to bytes."""
    w = Writer()
    obj.write(w)
    return bytes(w.buf)


# --------------------------------------------------------------------------- #
# data model -- only what C32 actually serialises
# --------------------------------------------------------------------------- #

class _S:
    """tiny struct base"""
    __slots__ = ()


class ResourceUsage(_S):
    """ResourceUsage<T,A,E>"""
    __slots__ = ("Type", "Amount", "Flag")

    def __init__(self):
        self.Type = -1
        self.Amount = 0
        self.Flag = 0

    def read(self, r, t_bytes=2, a_bytes=2, e_bytes=1):
        self.Type = _read_int(r, t_bytes, True)
        self.Amount = _read_int(r, a_bytes, "float") if a_bytes == "float" else _read_int(r, a_bytes, True)
        self.Flag = _read_int(r, e_bytes, e_bytes > 1)

    def write(self, w, t_bytes=2, a_bytes=2, e_bytes=1):
        _write_int(w, self.Type, t_bytes, True)
        if a_bytes == "float":
            w.f32(self.Amount)
        else:
            _write_int(w, self.Amount, a_bytes, True)
        _write_int(w, self.Flag, e_bytes, e_bytes > 1)


def _read_int(r, size, signed):
    if size == "float":
        return r.f32()
    if size == 1:
        return r.u8()
    if size == 2:
        return r.i16() if signed else r.u16()
    if size == 4:
        return r.i32() if signed else r.u32()
    raise ValueError(size)


def _write_int(w, v, size, signed):
    if size == "float":
        w.f32(v)
    elif size == 1:
        w.u8(v)
    elif size == 2:
        w.i16(v) if signed else w.u16(v)
    elif size == 4:
        w.i32(v) if signed else w.u32(v)
    else:
        raise ValueError(size)


class AttackOrArmor(_S):
    __slots__ = ("Class", "Amount")

    def __init__(self):
        self.Class = -1
        self.Amount = 0

    def read(self, r):
        self.Class = r.i16()
        self.Amount = r.i16()

    def write(self, w):
        w.i16(self.Class)
        w.i16(self.Amount)


class DamageGraphic(_S):
    __slots__ = ("GraphicID", "DamagePercent", "ApplyMode")

    def __init__(self):
        self.GraphicID = -1
        self.DamagePercent = 0
        self.ApplyMode = 0

    def read(self, r):
        self.GraphicID = r.i16()
        self.DamagePercent = r.i16()
        self.ApplyMode = r.u8()

    def write(self, w):
        w.i16(self.GraphicID)
        w.i16(self.DamagePercent)
        w.u8(self.ApplyMode)


class Task(_S):
    __slots__ = ("TaskType", "ID", "IsDefault", "ActionType", "ClassID", "UnitID",
                 "TerrainID", "ResourceIn", "ResourceMultiplier", "ResourceOut",
                 "UnusedResource", "WorkValue1", "WorkValue2", "WorkRange",
                 "AutoSearchTargets", "SearchWaitTime", "EnableTargeting",
                 "CombatLevelFlag", "GatherType", "WorkFlag2", "TargetDiplomacy",
                 "CarryCheck", "PickForConstruction", "MovingGraphicID",
                 "ProceedingGraphicID", "WorkingGraphicID", "CarryingGraphicID",
                 "ResourceGatheringSoundID", "ResourceDepositSoundID",
                 "WwiseResourceGatheringSoundID", "WwiseResourceDepositSoundID",
                 "Enabled")

    def __init__(self):
        self.TaskType = 1
        self.ID = -1
        self.IsDefault = 0
        self.ActionType = 0
        self.ClassID = -1
        self.UnitID = -1
        self.TerrainID = -1
        self.ResourceIn = -1
        self.ResourceMultiplier = -1
        self.ResourceOut = -1
        self.UnusedResource = -1
        self.WorkValue1 = 0.0
        self.WorkValue2 = 0.0
        self.WorkRange = 0.0
        self.AutoSearchTargets = 0
        self.SearchWaitTime = 0.0
        self.EnableTargeting = 0
        self.CombatLevelFlag = 0
        self.GatherType = 0
        self.WorkFlag2 = 0
        self.TargetDiplomacy = 0
        self.CarryCheck = 0
        self.PickForConstruction = 0
        self.MovingGraphicID = -1
        self.ProceedingGraphicID = -1
        self.WorkingGraphicID = -1
        self.CarryingGraphicID = -1
        self.ResourceGatheringSoundID = -1
        self.ResourceDepositSoundID = -1
        self.WwiseResourceGatheringSoundID = 0
        self.WwiseResourceDepositSoundID = 0
        self.Enabled = -1

    def read(self, r):
        s = self
        s.TaskType = r.i16(); s.ID = r.i16(); s.IsDefault = r.u8()
        s.ActionType = r.i16(); s.ClassID = r.i16(); s.UnitID = r.i16()
        s.TerrainID = r.i16(); s.ResourceIn = r.i16()
        s.ResourceMultiplier = r.i16(); s.ResourceOut = r.i16()
        s.UnusedResource = r.i16()
        s.WorkValue1 = r.f32(); s.WorkValue2 = r.f32(); s.WorkRange = r.f32()
        s.AutoSearchTargets = r.u8(); s.SearchWaitTime = r.f32()
        s.EnableTargeting = r.u8(); s.CombatLevelFlag = r.u8()
        s.GatherType = r.i16(); s.WorkFlag2 = r.i16()
        s.TargetDiplomacy = r.u8(); s.CarryCheck = r.u8()
        s.PickForConstruction = r.u8()
        s.MovingGraphicID = r.i16(); s.ProceedingGraphicID = r.i16()
        s.WorkingGraphicID = r.i16(); s.CarryingGraphicID = r.i16()
        s.ResourceGatheringSoundID = r.i16(); s.ResourceDepositSoundID = r.i16()
        s.WwiseResourceGatheringSoundID = r.u32()
        s.WwiseResourceDepositSoundID = r.u32()
        s.Enabled = r.i16()

    def write(self, w):
        s = self
        w.i16(s.TaskType); w.i16(s.ID); w.u8(s.IsDefault)
        w.i16(s.ActionType); w.i16(s.ClassID); w.i16(s.UnitID)
        w.i16(s.TerrainID); w.i16(s.ResourceIn)
        w.i16(s.ResourceMultiplier); w.i16(s.ResourceOut)
        w.i16(s.UnusedResource)
        w.f32(s.WorkValue1); w.f32(s.WorkValue2); w.f32(s.WorkRange)
        w.u8(s.AutoSearchTargets); w.f32(s.SearchWaitTime)
        w.u8(s.EnableTargeting); w.u8(s.CombatLevelFlag)
        w.i16(s.GatherType); w.i16(s.WorkFlag2)
        w.u8(s.TargetDiplomacy); w.u8(s.CarryCheck)
        w.u8(s.PickForConstruction)
        w.i16(s.MovingGraphicID); w.i16(s.ProceedingGraphicID)
        w.i16(s.WorkingGraphicID); w.i16(s.CarryingGraphicID)
        w.i16(s.ResourceGatheringSoundID); w.i16(s.ResourceDepositSoundID)
        w.u32(s.WwiseResourceGatheringSoundID)
        w.u32(s.WwiseResourceDepositSoundID)
        w.i16(s.Enabled)


class TrainLocation(_S):
    __slots__ = ("TrainTime", "UnitID", "ButtonID", "HotKeyID")

    def __init__(self):
        self.TrainTime = 0
        self.UnitID = -1
        self.ButtonID = 0
        self.HotKeyID = 16000

    def read(self, r):
        self.TrainTime = r.i16()
        self.UnitID = r.i16()
        self.ButtonID = r.u8()
        self.HotKeyID = r.i32()          # gv >= GV_C30

    def write(self, w):
        w.i16(self.TrainTime)
        w.i16(self.UnitID)
        w.u8(self.ButtonID)
        w.i32(self.HotKeyID)


class Unit(_S):
    __slots__ = (
        "Type", "ID", "LanguageDLLName", "LanguageDLLCreation", "Class",
        "StandingGraphic", "DyingGraphic", "UndeadGraphic", "UndeadMode",
        "HitPoints", "LineOfSight", "GarrisonCapacity", "CollisionSize",
        "TrainSound", "DamageSound", "DeadUnitID", "BloodUnitID", "SortNumber",
        "CanBeBuiltOn", "IconID", "HideInEditor", "OldPortraitPict", "Enabled",
        "Disabled", "PlacementSideTerrain", "PlacementTerrain", "ClearanceSize",
        "HillMode", "FogVisibility", "TerrainRestriction", "FlyMode",
        "ResourceCapacity", "ResourceDecay", "BlastDefenseLevel", "CombatLevel",
        "InteractionMode", "MinimapMode", "InterfaceKind",
        "MultipleAttributeMode", "MinimapColor", "LanguageDLLHelp",
        "LanguageDLLHotKeyText", "Recyclable", "EnableAutoGather",
        "CreateDoppelgangerOnDeath", "ResourceGatherGroup", "OcclusionMode",
        "ObstructionType", "ObstructionClass", "Trait", "Civilization",
        "Nothing", "SelectionEffect", "EditorSelectionColour", "OutlineSize",
        "ResourceStorages", "DamageGraphics", "SelectionSound", "DyingSound",
        "WwiseTrainSoundID", "WwiseDamageSoundID", "WwiseSelectionSoundID",
        "WwiseDyingSoundID", "OldAttackReaction", "ConvertTerrain", "Name",
        "CopyID", "BaseID",

        # type-dependent tails
        "Speed",
        "DeadFish", "Bird", "Type50", "Projectile", "Creatable", "Building")

    def __init__(self):
        self.Type = 10
        self.ID = -1
        self.LanguageDLLName = 5000
        self.LanguageDLLCreation = 6000
        self.Class = -1
        self.StandingGraphic = [-1, -1]
        self.DyingGraphic = -1
        self.UndeadGraphic = -1
        self.UndeadMode = 0
        self.HitPoints = 1
        self.LineOfSight = 2.0
        self.GarrisonCapacity = 0
        self.CollisionSize = [0.0, 0.0, 0.0]
        self.TrainSound = -1
        self.DamageSound = -1
        self.DeadUnitID = -1
        self.BloodUnitID = -1
        self.SortNumber = 0
        self.CanBeBuiltOn = 0
        self.IconID = -1
        self.HideInEditor = 0
        self.OldPortraitPict = -1
        self.Enabled = 1
        self.Disabled = 0
        self.PlacementSideTerrain = [-1, -1]
        self.PlacementTerrain = [-1, -1]
        self.ClearanceSize = [0.0, 0.0]
        self.HillMode = 0
        self.FogVisibility = 0
        self.TerrainRestriction = 0
        self.FlyMode = 0
        self.ResourceCapacity = 0
        self.ResourceDecay = 0.0
        self.BlastDefenseLevel = 0
        self.CombatLevel = 0
        self.InteractionMode = 0
        self.MinimapMode = 0
        self.InterfaceKind = 0
        self.MultipleAttributeMode = 0.0
        self.MinimapColor = 0
        self.LanguageDLLHelp = 105000
        self.LanguageDLLHotKeyText = 155000
        self.Recyclable = 0
        self.EnableAutoGather = 0
        self.CreateDoppelgangerOnDeath = 0
        self.ResourceGatherGroup = 0
        self.OcclusionMode = 0
        self.ObstructionType = 0
        self.ObstructionClass = 0
        self.Trait = 0
        self.Civilization = 0
        self.Nothing = 0
        self.SelectionEffect = 1
        self.EditorSelectionColour = 52
        self.OutlineSize = [0.0, 0.0, 0.0]
        self.ResourceStorages = [ResourceUsage(), ResourceUsage(), ResourceUsage()]
        self.DamageGraphics = []
        self.SelectionSound = -1
        self.DyingSound = -1
        self.WwiseTrainSoundID = 0
        self.WwiseDamageSoundID = 0
        self.WwiseSelectionSoundID = 0
        self.WwiseDyingSoundID = 0
        self.OldAttackReaction = 0
        self.ConvertTerrain = 0
        self.Name = ""
        self.CopyID = -1
        self.BaseID = -1
        self.Speed = None
        self.DeadFish = None
        self.Bird = None
        self.Type50 = None
        self.Projectile = None
        self.Creatable = None
        self.Building = None

    # -- reading ------------------------------------------------------------
    def read(self, r):
        s = self
        s.Type = r.u8()
        s.ID = r.i16()
        s.LanguageDLLName = r.i32()
        s.LanguageDLLCreation = r.i32()
        s.Class = r.i16()
        s.StandingGraphic = [r.i16(), r.i16()]
        s.DyingGraphic = r.i16()
        s.UndeadGraphic = r.i16()
        s.UndeadMode = r.u8()
        s.HitPoints = r.i16()
        s.LineOfSight = r.f32()
        s.GarrisonCapacity = r.u8()
        s.CollisionSize = [r.f32(), r.f32(), r.f32()]
        s.TrainSound = r.i16()
        s.DamageSound = r.i16()
        s.DeadUnitID = r.i16()
        s.BloodUnitID = r.i16()
        s.SortNumber = r.u8()
        s.CanBeBuiltOn = r.u8()
        s.IconID = r.i16()
        s.HideInEditor = r.u8()
        s.OldPortraitPict = r.i16()
        s.Enabled = r.u8()
        s.Disabled = r.u8()
        s.PlacementSideTerrain = [r.i16(), r.i16()]
        s.PlacementTerrain = [r.i16(), r.i16()]
        s.ClearanceSize = [r.f32(), r.f32()]
        s.HillMode = r.u8()
        s.FogVisibility = r.u8()
        s.TerrainRestriction = r.i16()
        s.FlyMode = r.u8()
        s.ResourceCapacity = r.i16()
        s.ResourceDecay = r.f32()
        s.BlastDefenseLevel = r.u8()
        s.CombatLevel = r.u8()
        s.InteractionMode = r.u8()
        s.MinimapMode = r.u8()
        s.InterfaceKind = r.u8()
        s.MultipleAttributeMode = r.f32()
        s.MinimapColor = r.u8()
        s.LanguageDLLHelp = r.i32()
        s.LanguageDLLHotKeyText = r.i32()
        s.Recyclable = r.u8()
        s.EnableAutoGather = r.u8()
        s.CreateDoppelgangerOnDeath = r.u8()
        s.ResourceGatherGroup = r.u8()
        s.OcclusionMode = r.u8()
        s.ObstructionType = r.u8()
        s.ObstructionClass = r.u8()
        s.Trait = r.u8()
        s.Civilization = r.u8()
        s.Nothing = r.i16()
        s.SelectionEffect = r.u8()
        s.EditorSelectionColour = r.u8()
        s.OutlineSize = [r.f32(), r.f32(), r.f32()]
        r.i32(); r.i32()                      # -2, 0 literals (GV_CK..)
        for i in range(3):
            s.ResourceStorages[i].read(r, 2, "float", 1)
        n = r.u8()
        s.DamageGraphics = [DamageGraphic() for _ in range(n)]
        for d in s.DamageGraphics:
            d.read(r)
        s.SelectionSound = r.i16()
        s.DyingSound = r.i16()
        s.WwiseTrainSoundID = r.u32()
        s.WwiseDamageSoundID = r.u32()
        s.WwiseSelectionSoundID = r.u32()
        s.WwiseDyingSoundID = r.u32()
        s.OldAttackReaction = r.u8()
        s.ConvertTerrain = r.u8()
        s.Name = r.debug_string()
        s.CopyID = r.i16()
        s.BaseID = r.i16()

        if s.Type == UT_AOE_TREES:
            return
        if s.Type < UT_FLAG:
            return
        s.Speed = r.f32()

        if s.Type >= UT_DEAD_FISH:
            df = DeadFish(); df.read(r); s.DeadFish = df
        if s.Type >= UT_BIRD:
            b = Bird(); b.read(r); s.Bird = b
        if s.Type >= UT_COMBATANT:
            t = Type50(); t.read(r); s.Type50 = t
        if s.Type == UT_PROJECTILE:
            p = Projectile(); p.read(r); s.Projectile = p
        if s.Type >= UT_CREATABLE:
            c = Creatable(); c.read(r); s.Creatable = c
        if s.Type == UT_BUILDING:
            bd = Building(); bd.read(r); s.Building = bd

    # -- writing ------------------------------------------------------------
    def write(self, w):
        s = self
        w.u8(s.Type)
        w.i16(s.ID)
        w.i32(s.LanguageDLLName)
        w.i32(s.LanguageDLLCreation)
        w.i16(s.Class)
        w.i16(s.StandingGraphic[0]); w.i16(s.StandingGraphic[1])
        w.i16(s.DyingGraphic)
        w.i16(s.UndeadGraphic)
        w.u8(s.UndeadMode)
        w.i16(s.HitPoints)
        w.f32(s.LineOfSight)
        w.u8(s.GarrisonCapacity)
        w.f32(s.CollisionSize[0]); w.f32(s.CollisionSize[1]); w.f32(s.CollisionSize[2])
        w.i16(s.TrainSound)
        w.i16(s.DamageSound)
        w.i16(s.DeadUnitID)
        w.i16(s.BloodUnitID)
        w.u8(s.SortNumber)
        w.u8(s.CanBeBuiltOn)
        w.i16(s.IconID)
        w.u8(s.HideInEditor)
        w.i16(s.OldPortraitPict)
        w.u8(s.Enabled)
        w.u8(s.Disabled)
        w.i16(s.PlacementSideTerrain[0]); w.i16(s.PlacementSideTerrain[1])
        w.i16(s.PlacementTerrain[0]); w.i16(s.PlacementTerrain[1])
        w.f32(s.ClearanceSize[0]); w.f32(s.ClearanceSize[1])
        w.u8(s.HillMode)
        w.u8(s.FogVisibility)
        w.i16(s.TerrainRestriction)
        w.u8(s.FlyMode)
        w.i16(s.ResourceCapacity)
        w.f32(s.ResourceDecay)
        w.u8(s.BlastDefenseLevel)
        w.u8(s.CombatLevel)
        w.u8(s.InteractionMode)
        w.u8(s.MinimapMode)
        w.u8(s.InterfaceKind)
        w.f32(s.MultipleAttributeMode)
        w.u8(s.MinimapColor)
        w.i32(s.LanguageDLLHelp)
        w.i32(s.LanguageDLLHotKeyText)
        w.u8(s.Recyclable)
        w.u8(s.EnableAutoGather)
        w.u8(s.CreateDoppelgangerOnDeath)
        w.u8(s.ResourceGatherGroup)
        w.u8(s.OcclusionMode)
        w.u8(s.ObstructionType)
        w.u8(s.ObstructionClass)
        w.u8(s.Trait)
        w.u8(s.Civilization)
        w.i16(s.Nothing)
        w.u8(s.SelectionEffect)
        w.u8(s.EditorSelectionColour)
        w.f32(s.OutlineSize[0]); w.f32(s.OutlineSize[1]); w.f32(s.OutlineSize[2])
        w.i32(-2); w.i32(0)
        for rs in s.ResourceStorages:
            rs.write(w, 2, "float", 1)
        w.u8(len(s.DamageGraphics))
        for d in s.DamageGraphics:
            d.write(w)
        w.i16(s.SelectionSound)
        w.i16(s.DyingSound)
        w.u32(s.WwiseTrainSoundID)
        w.u32(s.WwiseDamageSoundID)
        w.u32(s.WwiseSelectionSoundID)
        w.u32(s.WwiseDyingSoundID)
        w.u8(s.OldAttackReaction)
        w.u8(s.ConvertTerrain)
        w.debug_string(s.Name)
        w.i16(s.CopyID)
        w.i16(s.BaseID)

        if s.Type == UT_AOE_TREES:
            return
        if s.Type < UT_FLAG:
            return
        w.f32(s.Speed)

        if s.Type >= UT_DEAD_FISH:
            s.DeadFish.write(w)
        if s.Type >= UT_BIRD:
            s.Bird.write(w)
        if s.Type >= UT_COMBATANT:
            s.Type50.write(w)
        if s.Type == UT_PROJECTILE:
            s.Projectile.write(w)
        if s.Type >= UT_CREATABLE:
            s.Creatable.write(w)
        if s.Type == UT_BUILDING:
            s.Building.write(w)

    def to_bytes(self):
        w = Writer()
        self.write(w)
        return bytes(w.buf)


class DeadFish(_S):
    __slots__ = ("WalkingGraphic", "RunningGraphic", "RotationSpeed",
                 "OldSizeClass", "TrackingUnit", "TrackingUnitMode",
                 "TrackingUnitDensity", "OldMoveAlgorithm", "TurnRadius",
                 "TurnRadiusSpeed", "MaxYawPerSecondMoving",
                 "StationaryYawRevolutionTime", "MaxYawPerSecondStationary",
                 "MinCollisionSizeMultiplier")

    def __init__(self):
        self.WalkingGraphic = -1
        self.RunningGraphic = -1
        self.RotationSpeed = 0.0
        self.OldSizeClass = 0
        self.TrackingUnit = -1
        self.TrackingUnitMode = 0
        self.TrackingUnitDensity = 0.0
        self.OldMoveAlgorithm = 0
        self.TurnRadius = 0.0
        self.TurnRadiusSpeed = 3.402823466e+38
        self.MaxYawPerSecondMoving = 3.402823466e+38
        self.StationaryYawRevolutionTime = 0.0
        self.MaxYawPerSecondStationary = 3.402823466e+38
        self.MinCollisionSizeMultiplier = 1.0

    def read(self, r):
        s = self
        s.WalkingGraphic = r.i16()
        s.RunningGraphic = r.i16()
        s.RotationSpeed = r.f32()
        s.OldSizeClass = r.u8()
        s.TrackingUnit = r.i16()
        s.TrackingUnitMode = r.u8()
        s.TrackingUnitDensity = r.f32()
        s.OldMoveAlgorithm = r.u8()
        s.TurnRadius = r.f32()
        s.TurnRadiusSpeed = r.f32()
        s.MaxYawPerSecondMoving = r.f32()
        s.StationaryYawRevolutionTime = r.f32()
        s.MaxYawPerSecondStationary = r.f32()
        s.MinCollisionSizeMultiplier = r.f32()

    def write(self, w):
        s = self
        w.i16(s.WalkingGraphic)
        w.i16(s.RunningGraphic)
        w.f32(s.RotationSpeed)
        w.u8(s.OldSizeClass)
        w.i16(s.TrackingUnit)
        w.u8(s.TrackingUnitMode)
        w.f32(s.TrackingUnitDensity)
        w.u8(s.OldMoveAlgorithm)
        w.f32(s.TurnRadius)
        w.f32(s.TurnRadiusSpeed)
        w.f32(s.MaxYawPerSecondMoving)
        w.f32(s.StationaryYawRevolutionTime)
        w.f32(s.MaxYawPerSecondStationary)
        w.f32(s.MinCollisionSizeMultiplier)


class Bird(_S):
    __slots__ = ("DefaultTaskID", "SearchRadius", "WorkRate", "DropSites",
                 "TaskSwapGroup", "AttackSound", "MoveSound",
                 "WwiseAttackSoundID", "WwiseMoveSoundID", "RunPattern",
                 "TaskList")

    def __init__(self):
        self.DefaultTaskID = -1
        self.SearchRadius = 0.0
        self.WorkRate = 0.0
        self.DropSites = []
        self.TaskSwapGroup = 0
        self.AttackSound = -1
        self.MoveSound = -1
        self.WwiseAttackSoundID = 0
        self.WwiseMoveSoundID = 0
        self.RunPattern = 0
        self.TaskList = []

    def read(self, r):
        s = self
        s.DefaultTaskID = r.i16()
        s.SearchRadius = r.f32()
        s.WorkRate = r.f32()
        n = r.i16()                       # gv >= GV_C21
        s.DropSites = [r.i16() for _ in range(n)]
        s.TaskSwapGroup = r.u8()
        s.AttackSound = r.i16()
        s.MoveSound = r.i16()
        s.WwiseAttackSoundID = r.u32()
        s.WwiseMoveSoundID = r.u32()
        s.RunPattern = r.u8()
        n = r.i16()                       # gv >= GV_C15
        s.TaskList = [Task() for _ in range(n)]
        for t in s.TaskList:
            t.read(r)

    def write(self, w):
        s = self
        w.i16(s.DefaultTaskID)
        w.f32(s.SearchRadius)
        w.f32(s.WorkRate)
        w.i16(len(s.DropSites))
        for d in s.DropSites:
            w.i16(d)
        w.u8(s.TaskSwapGroup)
        w.i16(s.AttackSound)
        w.i16(s.MoveSound)
        w.u32(s.WwiseAttackSoundID)
        w.u32(s.WwiseMoveSoundID)
        w.u8(s.RunPattern)
        w.i16(len(s.TaskList))
        for t in s.TaskList:
            t.write(w)


class Type50(_S):
    __slots__ = ("BaseArmor", "Attacks", "Armours", "DefenseTerrainBonus",
                 "BonusDamageResistance", "MaxRange", "BlastWidth", "ReloadTime",
                 "ProjectileUnitID", "AccuracyPercent", "BreakOffCombat",
                 "FrameDelay", "GraphicDisplacement", "BlastAttackLevel",
                 "MinRange", "AccuracyDispersion", "AttackGraphic",
                 "DisplayedMeleeArmour", "DisplayedAttack", "DisplayedRange",
                 "DisplayedReloadTime", "BlastDamage", "DamageReflection",
                 "FriendlyFireDamage", "InterruptFrame", "GarrisonFirepower",
                 "AttackGraphic2")

    def __init__(self):
        self.BaseArmor = 1000
        self.Attacks = []
        self.Armours = []
        self.DefenseTerrainBonus = -1
        self.BonusDamageResistance = 0.0
        self.MaxRange = 0.0
        self.BlastWidth = 0.0
        self.ReloadTime = 0.0
        self.ProjectileUnitID = -1
        self.AccuracyPercent = 0
        self.BreakOffCombat = 0
        self.FrameDelay = 0
        self.GraphicDisplacement = [0.0, 0.0, 0.0]
        self.BlastAttackLevel = 0
        self.MinRange = 0.0
        self.AccuracyDispersion = 0.0
        self.AttackGraphic = -1
        self.DisplayedMeleeArmour = 0
        self.DisplayedAttack = 0
        self.DisplayedRange = 0.0
        self.DisplayedReloadTime = 0.0
        self.BlastDamage = 0.0
        self.DamageReflection = 0.0
        self.FriendlyFireDamage = 1.0
        self.InterruptFrame = -1
        self.GarrisonFirepower = 0.0
        self.AttackGraphic2 = -1

    def read(self, r):
        s = self
        s.BaseArmor = r.i16()
        n = r.i16()
        s.Attacks = [AttackOrArmor() for _ in range(n)]
        for a in s.Attacks:
            a.read(r)
        n = r.i16()
        s.Armours = [AttackOrArmor() for _ in range(n)]
        for a in s.Armours:
            a.read(r)
        s.DefenseTerrainBonus = r.i16()
        s.BonusDamageResistance = r.f32()
        s.MaxRange = r.f32()
        s.BlastWidth = r.f32()
        s.ReloadTime = r.f32()
        s.ProjectileUnitID = r.i16()
        s.AccuracyPercent = r.i16()
        s.BreakOffCombat = r.u8()
        s.FrameDelay = r.i16()
        s.GraphicDisplacement = [r.f32(), r.f32(), r.f32()]
        s.BlastAttackLevel = r.u8()
        s.MinRange = r.f32()
        s.AccuracyDispersion = r.f32()
        s.AttackGraphic = r.i16()
        s.DisplayedMeleeArmour = r.i16()
        s.DisplayedAttack = r.i16()
        s.DisplayedRange = r.f32()
        s.DisplayedReloadTime = r.f32()
        s.BlastDamage = r.f32()
        s.DamageReflection = r.f32()
        s.FriendlyFireDamage = r.f32()
        s.InterruptFrame = r.i16()
        s.GarrisonFirepower = r.f32()
        s.AttackGraphic2 = r.i16()

    def write(self, w):
        s = self
        w.i16(s.BaseArmor)
        w.i16(len(s.Attacks))
        for a in s.Attacks:
            a.write(w)
        w.i16(len(s.Armours))
        for a in s.Armours:
            a.write(w)
        w.i16(s.DefenseTerrainBonus)
        w.f32(s.BonusDamageResistance)
        w.f32(s.MaxRange)
        w.f32(s.BlastWidth)
        w.f32(s.ReloadTime)
        w.i16(s.ProjectileUnitID)
        w.i16(s.AccuracyPercent)
        w.u8(s.BreakOffCombat)
        w.i16(s.FrameDelay)
        w.f32(s.GraphicDisplacement[0]); w.f32(s.GraphicDisplacement[1]); w.f32(s.GraphicDisplacement[2])
        w.u8(s.BlastAttackLevel)
        w.f32(s.MinRange)
        w.f32(s.AccuracyDispersion)
        w.i16(s.AttackGraphic)
        w.i16(s.DisplayedMeleeArmour)
        w.i16(s.DisplayedAttack)
        w.f32(s.DisplayedRange)
        w.f32(s.DisplayedReloadTime)
        w.f32(s.BlastDamage)
        w.f32(s.DamageReflection)
        w.f32(s.FriendlyFireDamage)
        w.i16(s.InterruptFrame)
        w.f32(s.GarrisonFirepower)
        w.i16(s.AttackGraphic2)


class Projectile(_S):
    __slots__ = ("ProjectileType", "SmartMode", "HitMode", "VanishMode",
                 "AreaEffectSpecials", "ProjectileArc")

    def __init__(self):
        self.ProjectileType = 0
        self.SmartMode = 0
        self.HitMode = 0
        self.VanishMode = 0
        self.AreaEffectSpecials = 0
        self.ProjectileArc = 0.0

    def read(self, r):
        s = self
        s.ProjectileType = r.u8()
        s.SmartMode = r.u8()
        s.HitMode = r.u8()
        s.VanishMode = r.u8()
        s.AreaEffectSpecials = r.u8()
        s.ProjectileArc = r.f32()

    def write(self, w):
        s = self
        w.u8(s.ProjectileType)
        w.u8(s.SmartMode)
        w.u8(s.HitMode)
        w.u8(s.VanishMode)
        w.u8(s.AreaEffectSpecials)
        w.f32(s.ProjectileArc)


class Creatable(_S):
    __slots__ = ("ResourceCosts", "TrainLocations", "RearAttackModifier",
                 "FlankAttackModifier", "CreatableType", "HeroMode",
                 "GarrisonGraphic", "SpawningGraphic", "UpgradeGraphic",
                 "HeroGlowGraphic", "IdleAttackGraphic", "MaxCharge",
                 "RechargeRate", "ChargeEvent", "ChargeType", "ChargeTarget",
                 "ChargeProjectileUnit", "AttackPriority", "InvulnerabilityLevel",
                 "ButtonIconID", "ButtonShortTooltipID", "ButtonExtendedTooltipID",
                 "ButtonHotkeyAction", "MinConversionTimeMod",
                 "MaxConversionTimeMod", "ConversionChanceMod", "TotalProjectiles",
                 "MaxTotalProjectiles", "ProjectileSpawningArea",
                 "SecondaryProjectileUnit", "SpecialGraphic", "SpecialAbility",
                 "DisplayedPierceArmour")

    def __init__(self):
        self.ResourceCosts = [ResourceUsage() for _ in range(3)]
        self.TrainLocations = []
        self.RearAttackModifier = 0.0
        self.FlankAttackModifier = 0.0
        self.CreatableType = 0
        self.HeroMode = 0
        self.GarrisonGraphic = -1
        self.SpawningGraphic = -1
        self.UpgradeGraphic = -1
        self.HeroGlowGraphic = -1
        self.IdleAttackGraphic = -1
        self.MaxCharge = 0.0
        self.RechargeRate = 0.0
        self.ChargeEvent = 0
        self.ChargeType = 0
        self.ChargeTarget = 0
        self.ChargeProjectileUnit = -1
        self.AttackPriority = 0
        self.InvulnerabilityLevel = 0.0
        self.ButtonIconID = -1
        self.ButtonShortTooltipID = -1
        self.ButtonExtendedTooltipID = -1
        self.ButtonHotkeyAction = -1
        self.MinConversionTimeMod = 0.0
        self.MaxConversionTimeMod = 0.0
        self.ConversionChanceMod = 0.0
        self.TotalProjectiles = 0.0
        self.MaxTotalProjectiles = 0
        self.ProjectileSpawningArea = [0.0, 0.0, 0.0]
        self.SecondaryProjectileUnit = -1
        self.SpecialGraphic = -1
        self.SpecialAbility = 0
        self.DisplayedPierceArmour = 0

    def read(self, r):
        s = self
        for rc in s.ResourceCosts:
            rc.read(r, 2, 2, 2)                    # ResourceUsage<int16,int16,int16>
        n = r.i16()                                # gv >= GV_C29
        s.TrainLocations = [TrainLocation() for _ in range(n)]
        for t in s.TrainLocations:
            t.read(r)
        # gv < GV_C30 side effect is read-only in C++, nothing on OP_READ path here
        s.RearAttackModifier = r.f32()
        s.FlankAttackModifier = r.f32()
        s.CreatableType = r.u8()
        s.HeroMode = r.u8()
        s.GarrisonGraphic = r.i32()
        s.SpawningGraphic = r.i16()
        s.UpgradeGraphic = r.i16()
        s.HeroGlowGraphic = r.i16()
        s.IdleAttackGraphic = r.i16()
        s.MaxCharge = r.f32()
        s.RechargeRate = r.f32()
        s.ChargeEvent = r.i16()
        s.ChargeType = r.i16()
        s.ChargeTarget = r.i16()
        s.ChargeProjectileUnit = r.i32()
        s.AttackPriority = r.u8()
        s.InvulnerabilityLevel = r.f32()
        s.ButtonIconID = r.i16()
        s.ButtonShortTooltipID = r.i32()
        s.ButtonExtendedTooltipID = r.i32()
        s.ButtonHotkeyAction = r.i16()
        s.MinConversionTimeMod = r.f32()
        s.MaxConversionTimeMod = r.f32()
        s.ConversionChanceMod = r.f32()
        s.TotalProjectiles = r.f32()
        s.MaxTotalProjectiles = r.u8()
        s.ProjectileSpawningArea = [r.f32(), r.f32(), r.f32()]
        s.SecondaryProjectileUnit = r.i32()
        s.SpecialGraphic = r.i32()
        s.SpecialAbility = r.u8()
        s.DisplayedPierceArmour = r.i16()

    def write(self, w):
        s = self
        for rc in s.ResourceCosts:
            rc.write(w, 2, 2, 2)
        w.i16(len(s.TrainLocations))
        for t in s.TrainLocations:
            t.write(w)
        w.f32(s.RearAttackModifier)
        w.f32(s.FlankAttackModifier)
        w.u8(s.CreatableType)
        w.u8(s.HeroMode)
        w.i32(s.GarrisonGraphic)
        w.i16(s.SpawningGraphic)
        w.i16(s.UpgradeGraphic)
        w.i16(s.HeroGlowGraphic)
        w.i16(s.IdleAttackGraphic)
        w.f32(s.MaxCharge)
        w.f32(s.RechargeRate)
        w.i16(s.ChargeEvent)
        w.i16(s.ChargeType)
        w.i16(s.ChargeTarget)
        w.i32(s.ChargeProjectileUnit)
        w.u8(s.AttackPriority)
        w.f32(s.InvulnerabilityLevel)
        w.i16(s.ButtonIconID)
        w.i32(s.ButtonShortTooltipID)
        w.i32(s.ButtonExtendedTooltipID)
        w.i16(s.ButtonHotkeyAction)
        w.f32(s.MinConversionTimeMod)
        w.f32(s.MaxConversionTimeMod)
        w.f32(s.ConversionChanceMod)
        w.f32(s.TotalProjectiles)
        w.u8(s.MaxTotalProjectiles)
        w.f32(s.ProjectileSpawningArea[0]); w.f32(s.ProjectileSpawningArea[1]); w.f32(s.ProjectileSpawningArea[2])
        w.i32(s.SecondaryProjectileUnit)
        w.i32(s.SpecialGraphic)
        w.u8(s.SpecialAbility)
        w.i16(s.DisplayedPierceArmour)


class BuildingAnnex(_S):
    __slots__ = ("UnitID", "Misplacement")

    def __init__(self):
        self.UnitID = -1
        self.Misplacement = [0.0, 0.0]

    def read(self, r):
        self.UnitID = r.i16()
        self.Misplacement = [r.f32(), r.f32()]

    def write(self, w):
        w.i16(self.UnitID)
        w.f32(self.Misplacement[0]); w.f32(self.Misplacement[1])


class Building(_S):
    __slots__ = ("ConstructionGraphicID", "SnowGraphicID", "DestructionGraphicID",
                 "DestructionRubbleGraphicID", "ResearchingGraphic",
                 "ResearchCompletedGraphic", "AdjacentMode", "GraphicsAngle",
                 "DisappearsWhenBuilt", "StackUnitID", "FoundationTerrainID",
                 "OldOverlayID", "TechID", "CanBurn", "Annexes", "HeadUnit",
                 "TransformUnit", "TransformSound", "ConstructionSound",
                 "WwiseTransformSoundID", "WwiseConstructionSoundID",
                 "GarrisonType", "GarrisonHealRate", "GarrisonRepairRate",
                 "PileUnit", "LootingTable")

    def __init__(self):
        self.ConstructionGraphicID = -1
        self.SnowGraphicID = -1
        self.DestructionGraphicID = -1
        self.DestructionRubbleGraphicID = -1
        self.ResearchingGraphic = -1
        self.ResearchCompletedGraphic = -1
        self.AdjacentMode = 0
        self.GraphicsAngle = 0
        self.DisappearsWhenBuilt = 0
        self.StackUnitID = -1
        self.FoundationTerrainID = -1
        self.OldOverlayID = -1
        self.TechID = -1
        self.CanBurn = 0
        self.Annexes = [BuildingAnnex() for _ in range(BUILDING_ANNEXES_SIZE)]
        self.HeadUnit = -1
        self.TransformUnit = -1
        self.TransformSound = -1
        self.ConstructionSound = -1
        self.WwiseTransformSoundID = 0
        self.WwiseConstructionSoundID = 0
        self.GarrisonType = 0
        self.GarrisonHealRate = 0.0
        self.GarrisonRepairRate = 0.0
        self.PileUnit = -1
        self.LootingTable = [0] * LOOTABLE_RES_COUNT

    def read(self, r):
        s = self
        s.ConstructionGraphicID = r.i16()
        s.SnowGraphicID = r.i16()
        s.DestructionGraphicID = r.i16()
        s.DestructionRubbleGraphicID = r.i16()
        s.ResearchingGraphic = r.i16()
        s.ResearchCompletedGraphic = r.i16()
        s.AdjacentMode = r.u8()
        s.GraphicsAngle = r.i16()
        s.DisappearsWhenBuilt = r.u8()
        s.StackUnitID = r.i16()
        s.FoundationTerrainID = r.i16()
        s.OldOverlayID = r.i16()
        s.TechID = r.i16()
        s.CanBurn = r.u8()
        for a in s.Annexes:
            a.read(r)
        s.HeadUnit = r.i16()
        s.TransformUnit = r.i16()
        s.TransformSound = r.i16()
        s.ConstructionSound = r.i16()
        s.WwiseTransformSoundID = r.u32()
        s.WwiseConstructionSoundID = r.u32()
        s.GarrisonType = r.u8()
        s.GarrisonHealRate = r.f32()
        s.GarrisonRepairRate = r.f32()
        s.PileUnit = r.i16()
        s.LootingTable = [r.u8() for _ in range(LOOTABLE_RES_COUNT)]

    def write(self, w):
        s = self
        w.i16(s.ConstructionGraphicID)
        w.i16(s.SnowGraphicID)
        w.i16(s.DestructionGraphicID)
        w.i16(s.DestructionRubbleGraphicID)
        w.i16(s.ResearchingGraphic)
        w.i16(s.ResearchCompletedGraphic)
        w.u8(s.AdjacentMode)
        w.i16(s.GraphicsAngle)
        w.u8(s.DisappearsWhenBuilt)
        w.i16(s.StackUnitID)
        w.i16(s.FoundationTerrainID)
        w.i16(s.OldOverlayID)
        w.i16(s.TechID)
        w.u8(s.CanBurn)
        for a in s.Annexes:
            a.write(w)
        w.i16(s.HeadUnit)
        w.i16(s.TransformUnit)
        w.i16(s.TransformSound)
        w.i16(s.ConstructionSound)
        w.u32(s.WwiseTransformSoundID)
        w.u32(s.WwiseConstructionSoundID)
        w.u8(s.GarrisonType)
        w.f32(s.GarrisonHealRate)
        w.f32(s.GarrisonRepairRate)
        w.i16(s.PileUnit)
        for b in s.LootingTable:
            w.u8(b)


# --------------------------------------------------------------------------- #
# top-level structures
# --------------------------------------------------------------------------- #

class Civ(_S):
    __slots__ = ("PlayerType", "Name", "Resources", "TechTreeID", "TeamBonusID",
                 "IconSet", "UnitPointers", "Units",
                 # offsets recorded while parsing the ORIGINAL payload
                 "index", "record_off", "count_off", "pointers_off",
                 "pointers_end_off", "record_end_off")

    def __init__(self):
        self.PlayerType = 0
        self.Name = ""
        self.Resources = [0.0, 0.0, 0.0]
        self.TechTreeID = 0
        self.TeamBonusID = 0
        self.IconSet = 0
        self.UnitPointers = []
        self.Units = []
        self.index = -1
        self.record_off = -1
        self.count_off = -1          # int16 Units count field
        self.pointers_off = -1       # int32 UnitPointers[] starts here
        self.pointers_end_off = -1   # int32 UnitPointers[] ends here
        self.record_end_off = -1     # end of the whole civ record

    def read(self, r):
        self.record_off = r.pos
        self.PlayerType = r.u8()
        self.Name = r.debug_string()
        n = r.i16()
        self.Resources = [r.f32() for _ in range(n)]
        self.TechTreeID = r.i16()
        self.TeamBonusID = r.i16()
        self.IconSet = r.u8()
        self.count_off = r.pos
        n = r.i16()
        self.pointers_off = r.pos
        self.UnitPointers = [r.i32() for _ in range(n)]
        self.pointers_end_off = r.pos
        self.Units = [Unit() for _ in range(n)]
        for i in range(n):
            if self.UnitPointers[i]:
                self.Units[i].read(r)
        self.record_end_off = r.pos

    def write(self, w):
        w.u8(self.PlayerType)
        w.debug_string(self.Name)
        w.i16(len(self.Resources))
        for f in self.Resources:
            w.f32(f)
        w.i16(self.TechTreeID)
        w.i16(self.TeamBonusID)
        w.u8(self.IconSet)
        n = len(self.Units)
        w.i16(n)
        for p in self.UnitPointers:
            w.i32(p)
        for i in range(n):
            if self.UnitPointers[i]:
                self.Units[i].write(w)


class Effect(_S):                # == Techage
    __slots__ = ("Name", "EffectCommands")

    def __init__(self):
        self.Name = ""
        self.EffectCommands = []

    def read(self, r):
        self.Name = r.debug_string()
        n = r.i16()
        self.EffectCommands = [EffectCommand() for _ in range(n)]
        for c in self.EffectCommands:
            c.read(r)

    def write(self, w):
        w.debug_string(self.Name)
        w.i16(len(self.EffectCommands))
        for c in self.EffectCommands:
            c.write(w)


class EffectCommand(_S):         # == TechageEffect
    __slots__ = ("Type", "A", "B", "C", "D")

    def __init__(self):
        self.Type = 255
        self.A = -1
        self.B = -1
        self.C = -1
        self.D = 0.0

    def read(self, r):
        self.Type = r.u8()
        self.A = r.i16()
        self.B = r.i16()
        self.C = r.i16()
        self.D = r.f32()

    def write(self, w):
        w.u8(self.Type)
        w.i16(self.A)
        w.i16(self.B)
        w.i16(self.C)
        w.f32(self.D)


class Tech(_S):                  # == Research
    __slots__ = ("RequiredTechs", "ResourceCosts", "RequiredTechCount", "Civ",
                 "FullTechMode", "LanguageDLLName", "LanguageDLLDescription",
                 "EffectID", "Type", "IconID", "LanguageDLLHelp",
                 "LanguageDLLTechTree", "Name", "Repeatable", "ResearchLocations")

    def __init__(self):
        self.RequiredTechs = [-1] * 6
        self.ResourceCosts = [ResourceUsage() for _ in range(3)]
        self.RequiredTechCount = 0
        self.Civ = -1
        self.FullTechMode = 0
        self.LanguageDLLName = 7000
        self.LanguageDLLDescription = 8000
        self.EffectID = -1
        self.Type = 0
        self.IconID = -1
        self.LanguageDLLHelp = 107000
        self.LanguageDLLTechTree = 150000
        self.Name = ""
        self.Repeatable = 0
        self.ResearchLocations = []

    def read(self, r):
        s = self
        s.RequiredTechs = [r.i16() for _ in range(6)]
        for rc in s.ResourceCosts:
            rc.read(r, 2, 2, 1)
        s.RequiredTechCount = r.i16()
        s.Civ = r.i16()
        s.FullTechMode = r.i16()
        # gv < GV_C31 -> ResearchLocation skipped (C32)
        s.LanguageDLLName = r.i32()
        s.LanguageDLLDescription = r.i32()
        s.EffectID = r.i16()
        s.Type = r.i16()
        s.IconID = r.i16()
        s.LanguageDLLHelp = r.i32()
        s.LanguageDLLTechTree = r.i32()
        s.Name = r.debug_string()
        s.Repeatable = r.u8()
        n = r.i16()
        s.ResearchLocations = [ResearchLocation() for _ in range(n)]
        for rl in s.ResearchLocations:
            rl.read(r)

    def write(self, w):
        s = self
        for v in s.RequiredTechs:
            w.i16(v)
        for rc in s.ResourceCosts:
            rc.write(w, 2, 2, 1)
        w.i16(s.RequiredTechCount)
        w.i16(s.Civ)
        w.i16(s.FullTechMode)
        w.i32(s.LanguageDLLName)
        w.i32(s.LanguageDLLDescription)
        w.i16(s.EffectID)
        w.i16(s.Type)
        w.i16(s.IconID)
        w.i32(s.LanguageDLLHelp)
        w.i32(s.LanguageDLLTechTree)
        w.debug_string(s.Name)
        w.u8(s.Repeatable)
        w.i16(len(s.ResearchLocations))
        for rl in s.ResearchLocations:
            rl.write(w)


class ResearchLocation(_S):
    __slots__ = ("LocationID", "ResearchTime", "ButtonID", "HotKeyID")

    def __init__(self):
        self.LocationID = -1
        self.ResearchTime = 0
        self.ButtonID = 0
        self.HotKeyID = 16000

    def read(self, r):
        self.LocationID = r.i16()
        self.ResearchTime = r.i16()
        self.ButtonID = r.u8()
        self.HotKeyID = r.i32()

    def write(self, w):
        w.i16(self.LocationID)
        w.i16(self.ResearchTime)
        w.u8(self.ButtonID)
        w.i32(self.HotKeyID)


class TerrainPassGraphic(_S):
    __slots__ = ("ExitTileSpriteID", "EnterTileSpriteID", "WalkTileSpriteID",
                 "WalkSpriteRate")

    def __init__(self):
        self.ExitTileSpriteID = 0
        self.EnterTileSpriteID = 0
        self.WalkTileSpriteID = 0
        self.WalkSpriteRate = 0.0

    def read(self, r):
        self.ExitTileSpriteID = r.i32()
        self.EnterTileSpriteID = r.i32()
        self.WalkTileSpriteID = r.i32()
        self.WalkSpriteRate = r.f32()

    def write(self, w):
        w.i32(self.ExitTileSpriteID)
        w.i32(self.EnterTileSpriteID)
        w.i32(self.WalkTileSpriteID)
        w.f32(self.WalkSpriteRate)


class TerrainRestriction(_S):
    __slots__ = ("PassableBuildableDmgMultiplier", "TerrainPassGraphics")

    def __init__(self, terrain_count):
        self.PassableBuildableDmgMultiplier = [0.0] * terrain_count
        self.TerrainPassGraphics = [TerrainPassGraphic() for _ in range(terrain_count)]

    def read(self, r):
        n = len(self.PassableBuildableDmgMultiplier)
        self.PassableBuildableDmgMultiplier = [r.f32() for _ in range(n)]
        for tpg in self.TerrainPassGraphics:
            tpg.read(r)

    def write(self, w):
        for f in self.PassableBuildableDmgMultiplier:
            w.f32(f)
        for tpg in self.TerrainPassGraphics:
            tpg.write(w)


class PlayerColour(_S):
    __slots__ = ("ID", "PlayerColorBase", "UnitOutlineColor", "UnitSelectionColor1",
                 "UnitSelectionColor2", "MinimapColour", "MinimapColor2",
                 "MinimapColor3", "StatisticsText")

    def __init__(self):
        self.ID = 0
        self.PlayerColorBase = 0
        self.UnitOutlineColor = 0
        self.UnitSelectionColor1 = 0
        self.UnitSelectionColor2 = 0
        self.MinimapColour = 0
        self.MinimapColor2 = 0
        self.MinimapColor3 = 0
        self.StatisticsText = 0

    def read(self, r):
        self.ID = r.i32()
        self.PlayerColorBase = r.i32()
        self.UnitOutlineColor = r.i32()
        self.UnitSelectionColor1 = r.i32()
        self.UnitSelectionColor2 = r.i32()
        self.MinimapColour = r.i32()
        self.MinimapColor2 = r.i32()
        self.MinimapColor3 = r.i32()
        self.StatisticsText = r.i32()

    def write(self, w):
        w.i32(self.ID)
        w.i32(self.PlayerColorBase)
        w.i32(self.UnitOutlineColor)
        w.i32(self.UnitSelectionColor1)
        w.i32(self.UnitSelectionColor2)
        w.i32(self.MinimapColour)
        w.i32(self.MinimapColor2)
        w.i32(self.MinimapColor3)
        w.i32(self.StatisticsText)


class SoundItem(_S):
    __slots__ = ("FileName", "ResourceID", "Probability", "Civilization", "IconSet")

    def __init__(self):
        self.FileName = ""
        self.ResourceID = -1
        self.Probability = 100
        self.Civilization = -1
        self.IconSet = -1

    def read(self, r):
        self.FileName = r.debug_string()
        self.ResourceID = r.i32()
        self.Probability = r.i16()
        self.Civilization = r.i16()
        self.IconSet = r.i16()

    def write(self, w):
        w.debug_string(self.FileName)
        w.i32(self.ResourceID)
        w.i16(self.Probability)
        w.i16(self.Civilization)
        w.i16(self.IconSet)


class Sound(_S):
    __slots__ = ("ID", "PlayDelay", "CacheTime", "TotalProbability", "Items")

    def __init__(self):
        self.ID = -1
        self.PlayDelay = 0
        self.CacheTime = 300000
        self.TotalProbability = 100
        self.Items = []

    def read(self, r):
        self.ID = r.i16()
        self.PlayDelay = r.i16()
        n = r.i16()
        self.CacheTime = r.i32()
        self.TotalProbability = r.i16()
        self.Items = [SoundItem() for _ in range(n)]
        for it in self.Items:
            it.read(r)

    def write(self, w):
        w.i16(self.ID)
        w.i16(self.PlayDelay)
        w.i16(len(self.Items))
        w.i32(self.CacheTime)
        w.i16(self.TotalProbability)
        for it in self.Items:
            it.write(w)


class GraphicDelta(_S):
    __slots__ = ("GraphicID", "Padding1", "SpritePtr", "OffsetX", "OffsetY",
                 "DisplayAngle", "Padding2")

    def __init__(self):
        self.GraphicID = -1
        self.Padding1 = 0
        self.SpritePtr = 0
        self.OffsetX = 0
        self.OffsetY = 0
        self.DisplayAngle = 0
        self.Padding2 = 0

    def read(self, r):
        self.GraphicID = r.i16()
        self.Padding1 = r.i16()
        self.SpritePtr = r.i32()
        self.OffsetX = r.i16()
        self.OffsetY = r.i16()
        self.DisplayAngle = r.i16()
        self.Padding2 = r.i16()

    def write(self, w):
        w.i16(self.GraphicID)
        w.i16(self.Padding1)
        w.i32(self.SpritePtr)
        w.i16(self.OffsetX)
        w.i16(self.OffsetY)
        w.i16(self.DisplayAngle)
        w.i16(self.Padding2)


class GraphicAngleSound(_S):
    __slots__ = ("Frames", "Sounds", "Wwise")

    def __init__(self):
        self.Frames = [0, 0, 0]
        self.Sounds = [-1, -1, -1]
        self.Wwise = [0, 0, 0]

    def read(self, r):
        for i in range(3):
            self.Frames[i] = r.i16()
            self.Sounds[i] = r.i16()
            self.Wwise[i] = r.u32()

    def write(self, w):
        for i in range(3):
            w.i16(self.Frames[i])
            w.i16(self.Sounds[i])
            w.u32(self.Wwise[i])


class Graphic(_S):
    __slots__ = ("Name", "FileName", "ParticleEffectName", "SLP", "IsLoaded",
                 "OldColorFlag", "Layer", "PlayerColor", "TransparentSelection",
                 "Coordinates", "SoundID", "WwiseSoundID", "AngleSoundsUsed",
                 "FrameCount", "AngleCount", "SpeedMultiplier", "FrameDuration",
                 "AnimationDuration", "ReplayDelay", "SequenceType", "ID",
                 "MirroringMode", "EditorFlag", "Deltas", "AngleSounds")

    def __init__(self):
        self.Name = ""
        self.FileName = ""
        self.ParticleEffectName = ""
        self.SLP = -1
        self.IsLoaded = 0
        self.OldColorFlag = 0
        self.Layer = 0
        self.PlayerColor = -1
        self.TransparentSelection = 0
        self.Coordinates = [0, 0, 0, 0]
        self.SoundID = -1
        self.WwiseSoundID = 0
        self.AngleSoundsUsed = 0
        self.FrameCount = 0
        self.AngleCount = 0
        self.SpeedMultiplier = 0.0
        self.FrameDuration = 0.0
        self.AnimationDuration = 0.0
        self.ReplayDelay = 0.0
        self.SequenceType = 0
        self.ID = -1
        self.MirroringMode = 0
        self.EditorFlag = 0
        self.Deltas = []
        self.AngleSounds = []

    def read(self, r):
        s = self
        s.Name = r.debug_string()
        s.FileName = r.debug_string()
        s.ParticleEffectName = r.debug_string()      # gv >= GV_C5
        s.SLP = r.i32()
        s.IsLoaded = r.u8()
        s.OldColorFlag = r.u8()
        s.Layer = r.u8()
        s.PlayerColor = r.i16()
        s.TransparentSelection = r.u8()
        s.Coordinates = [r.i16(), r.i16(), r.i16(), r.i16()]
        n = r.i16()
        s.SoundID = r.i16()
        s.WwiseSoundID = r.u32()
        s.AngleSoundsUsed = r.u8()
        s.FrameCount = r.i16()
        s.AngleCount = r.i16()
        s.SpeedMultiplier = r.f32()
        # C++ serializes SpeedMultiplier, FrameDuration, ReplayDelay only.
        # AnimationDuration is COMPUTED (FrameDuration * FrameCount), never on disk.
        s.FrameDuration = r.f32()
        s.AnimationDuration = s.FrameDuration * s.FrameCount
        s.ReplayDelay = r.f32()
        s.SequenceType = r.u8()
        s.ID = r.i16()
        s.MirroringMode = r.u8()
        s.EditorFlag = r.u8()
        s.Deltas = [GraphicDelta() for _ in range(n)]
        for d in s.Deltas:
            d.read(r)
        if s.AngleSoundsUsed != 0:
            s.AngleSounds = [GraphicAngleSound() for _ in range(s.AngleCount)]
            for g in s.AngleSounds:
                g.read(r)

    def write(self, w):
        s = self
        w.debug_string(s.Name)
        w.debug_string(s.FileName)
        w.debug_string(s.ParticleEffectName)
        w.i32(s.SLP)
        w.u8(s.IsLoaded)
        w.u8(s.OldColorFlag)
        w.u8(s.Layer)
        w.i16(s.PlayerColor)
        w.u8(s.TransparentSelection)
        for c in s.Coordinates:
            w.i16(c)
        w.i16(len(s.Deltas))
        w.i16(s.SoundID)
        w.u32(s.WwiseSoundID)
        w.u8(s.AngleSoundsUsed)
        w.i16(s.FrameCount)
        w.i16(s.AngleCount)
        w.f32(s.SpeedMultiplier)
        # C++ writes FrameDuration (recomputed in memory) then ReplayDelay.
        # We write back the exact value read from disk so the round trip is
        # bit-exact even when (F*N)/N would round differently.
        w.f32(s.FrameDuration)
        w.f32(s.ReplayDelay)
        w.u8(s.SequenceType)
        w.i16(s.ID)
        w.u8(s.MirroringMode)
        w.u8(s.EditorFlag)
        for d in s.Deltas:
            d.write(w)
        if s.AngleSoundsUsed != 0:
            assert len(s.AngleSounds) == s.AngleCount, "angle sound count mismatch"
            for g in s.AngleSounds:
                g.write(w)


class TileSize(_S):
    __slots__ = ("Width", "Height", "DeltaY")

    def __init__(self):
        self.Width = 0
        self.Height = 0
        self.DeltaY = 0

    def read(self, r):
        self.Width = r.i16()
        self.Height = r.i16()
        self.DeltaY = r.i16()

    def write(self, w, force16=True):
        w.i16(self.Width)
        w.i16(self.Height)
        w.i16(self.DeltaY)


class FrameData(_S):
    __slots__ = ("FrameCount", "AngleCount", "ShapeID")

    def __init__(self):
        self.FrameCount = 0
        self.AngleCount = 0
        self.ShapeID = -1

    def read(self, r):
        self.FrameCount = r.i16()
        self.AngleCount = r.i16()
        self.ShapeID = r.i16()

    def write(self, w):
        w.i16(self.FrameCount)
        w.i16(self.AngleCount)
        w.i16(self.ShapeID)


class Terrain(_S):
    __slots__ = ("Enabled", "Random", "IsWater", "HideInEditor", "StringID",
                 "Name", "Name2", "SLP", "ShapePtr", "SoundID", "WwiseSoundID",
                 "WwiseSoundStopID", "BlendPriority", "BlendType",
                 "OverlayMaskName", "Colors", "CliffColors", "PassableTerrain",
                 "ImpassableTerrain", "IsAnimated", "AnimationFrames",
                 "PauseFames", "Interval", "PauseBetweenLoops", "Frame",
                 "DrawFrame", "AnimateLast", "FrameChanged", "Drawn",
                 "ElevationGraphics", "TerrainToDraw", "TerrainDimensions",
                 "TerrainUnitMaskedDensity", "TerrainUnitID", "TerrainUnitDensity",
                 "TerrainUnitCentering", "NumberOfTerrainUnitsUsed", "Phantom")

    def __init__(self):
        self.Enabled = 0
        self.Random = 0
        self.IsWater = 0
        self.HideInEditor = 0
        self.StringID = 0
        self.Name = ""
        self.Name2 = ""
        self.SLP = -1
        self.ShapePtr = 0
        self.SoundID = -1
        self.WwiseSoundID = 0
        self.WwiseSoundStopID = 0
        self.BlendPriority = 0
        self.BlendType = 0
        self.OverlayMaskName = ""
        self.Colors = [0, 0, 0]
        self.CliffColors = [0, 0]
        self.PassableTerrain = 0
        self.ImpassableTerrain = 0
        self.IsAnimated = 0
        self.AnimationFrames = 0
        self.PauseFames = 0
        self.Interval = 0.0
        self.PauseBetweenLoops = 0.0
        self.Frame = 0
        self.DrawFrame = 0
        self.AnimateLast = 0.0
        self.FrameChanged = 0
        self.Drawn = 0
        self.ElevationGraphics = [FrameData() for _ in range(TILE_TYPE_COUNT)]
        self.TerrainToDraw = 0
        self.TerrainDimensions = [0, 0]
        self.TerrainUnitMaskedDensity = [0] * TERRAIN_UNITS_SIZE
        self.TerrainUnitID = [0] * TERRAIN_UNITS_SIZE
        self.TerrainUnitDensity = [0] * TERRAIN_UNITS_SIZE
        self.TerrainUnitCentering = [0] * TERRAIN_UNITS_SIZE
        self.NumberOfTerrainUnitsUsed = 0
        self.Phantom = 0

    def read(self, r):
        s = self
        s.Enabled = r.u8()
        s.Random = r.u8()
        s.IsWater = r.u8()
        s.HideInEditor = r.u8()
        s.StringID = r.i32()
        s.Name = r.debug_string()
        s.Name2 = r.debug_string()
        s.SLP = r.i32()
        s.ShapePtr = r.i32()
        s.SoundID = r.i32()
        s.WwiseSoundID = r.u32()
        s.WwiseSoundStopID = r.u32()
        s.BlendPriority = r.i32()
        s.BlendType = r.i32()
        s.OverlayMaskName = r.debug_string()
        s.Colors = [r.u8(), r.u8(), r.u8()]
        s.CliffColors = [r.u8(), r.u8()]
        s.PassableTerrain = r.u8()
        s.ImpassableTerrain = r.u8()
        s.IsAnimated = r.u8()
        s.AnimationFrames = r.i16()
        s.PauseFames = r.i16()
        s.Interval = r.f32()
        s.PauseBetweenLoops = r.f32()
        s.Frame = r.i16()
        s.DrawFrame = r.i16()
        s.AnimateLast = r.f32()
        s.FrameChanged = r.u8()
        s.Drawn = r.u8()
        for fd in s.ElevationGraphics:
            fd.read(r)
        s.TerrainToDraw = r.i16()
        s.TerrainDimensions = [r.i16(), r.i16()]
        s.TerrainUnitMaskedDensity = [r.i16() for _ in range(TERRAIN_UNITS_SIZE)]
        s.TerrainUnitID = [r.i16() for _ in range(TERRAIN_UNITS_SIZE)]
        s.TerrainUnitDensity = [r.i16() for _ in range(TERRAIN_UNITS_SIZE)]
        s.TerrainUnitCentering = [r.u8() for _ in range(TERRAIN_UNITS_SIZE)]
        s.NumberOfTerrainUnitsUsed = r.i16()
        s.Phantom = r.i16()

    def write(self, w):
        s = self
        w.u8(s.Enabled)
        w.u8(s.Random)
        w.u8(s.IsWater)
        w.u8(s.HideInEditor)
        w.i32(s.StringID)
        w.debug_string(s.Name)
        w.debug_string(s.Name2)
        w.i32(s.SLP)
        w.i32(s.ShapePtr)
        w.i32(s.SoundID)
        w.u32(s.WwiseSoundID)
        w.u32(s.WwiseSoundStopID)
        w.i32(s.BlendPriority)
        w.i32(s.BlendType)
        w.debug_string(s.OverlayMaskName)
        for c in s.Colors:
            w.u8(c)
        for c in s.CliffColors:
            w.u8(c)
        w.u8(s.PassableTerrain)
        w.u8(s.ImpassableTerrain)
        w.u8(s.IsAnimated)
        w.i16(s.AnimationFrames)
        w.i16(s.PauseFames)
        w.f32(s.Interval)
        w.f32(s.PauseBetweenLoops)
        w.i16(s.Frame)
        w.i16(s.DrawFrame)
        w.f32(s.AnimateLast)
        w.u8(s.FrameChanged)
        w.u8(s.Drawn)
        for fd in s.ElevationGraphics:
            fd.write(w)
        w.i16(s.TerrainToDraw)
        w.i16(s.TerrainDimensions[0]); w.i16(s.TerrainDimensions[1])
        for v in s.TerrainUnitMaskedDensity:
            w.i16(v)
        for v in s.TerrainUnitID:
            w.i16(v)
        for v in s.TerrainUnitDensity:
            w.i16(v)
        for v in s.TerrainUnitCentering:
            w.u8(v)
        w.i16(s.NumberOfTerrainUnitsUsed)
        w.i16(s.Phantom)


class TerrainBlock(_S):
    __slots__ = ("VirtualFunctionPtr", "MapPointer", "MapWidth", "MapHeight",
                 "WorldWidth", "WorldHeight", "TileSizes", "PaddingTS",
                 "Terrains", "MapMinX", "MapMinY", "MapMaxX", "MapMaxY",
                 "MapMaxXplus1", "MapMaxYplus1", "Scalars", "SearchMapPtr",
                 "SearchMapRowsPtr", "AnyFrameChange", "MapVisibleFlag", "FogFlag")

    def __init__(self):
        self.VirtualFunctionPtr = 0
        self.MapPointer = 0
        self.MapWidth = 0
        self.MapHeight = 0
        self.WorldWidth = 0
        self.WorldHeight = 0
        self.TileSizes = [TileSize() for _ in range(TILE_TYPE_COUNT)]
        self.PaddingTS = 0
        self.Terrains = [Terrain() for _ in range(TERRAIN_COUNT_C32)]
        self.MapMinX = 0.0
        self.MapMinY = 0.0
        self.MapMaxX = 0.0
        self.MapMaxY = 0.0
        self.MapMaxXplus1 = 0.0
        self.MapMaxYplus1 = 0.0
        self.Scalars = [0] * 14
        self.SearchMapPtr = 0
        self.SearchMapRowsPtr = 0
        self.AnyFrameChange = 0
        self.MapVisibleFlag = 0
        self.FogFlag = 0

    def read(self, r):
        s = self
        s.VirtualFunctionPtr = r.u32()
        s.MapPointer = r.u32()
        s.MapWidth = r.i32()
        s.MapHeight = r.i32()
        s.WorldWidth = r.i32()
        s.WorldHeight = r.i32()
        for ts in s.TileSizes:
            ts.read(r)
        s.PaddingTS = r.i16()
        for t in s.Terrains:
            t.read(r)
        s.MapMinX = r.f32()
        s.MapMinY = r.f32()
        s.MapMaxX = r.f32()
        s.MapMaxY = r.f32()
        s.MapMaxXplus1 = r.f32()
        s.MapMaxYplus1 = r.f32()
        s.Scalars = [r.i16() for _ in range(14)]
        s.SearchMapPtr = r.u32()
        s.SearchMapRowsPtr = r.u32()
        s.AnyFrameChange = r.u8()
        s.MapVisibleFlag = r.u8()
        s.FogFlag = r.u8()

    def write(self, w):
        s = self
        w.u32(s.VirtualFunctionPtr)
        w.u32(s.MapPointer)
        w.i32(s.MapWidth)
        w.i32(s.MapHeight)
        w.i32(s.WorldWidth)
        w.i32(s.WorldHeight)
        for ts in s.TileSizes:
            ts.write(w)
        w.i16(s.PaddingTS)
        for t in s.Terrains:
            t.write(w)
        w.f32(s.MapMinX)
        w.f32(s.MapMinY)
        w.f32(s.MapMaxX)
        w.f32(s.MapMaxY)
        w.f32(s.MapMaxXplus1)
        w.f32(s.MapMaxYplus1)
        for v in s.Scalars:
            w.i16(v)
        w.u32(s.SearchMapPtr)
        w.u32(s.SearchMapRowsPtr)
        w.u8(s.AnyFrameChange)
        w.u8(s.MapVisibleFlag)
        w.u8(s.FogFlag)


# ---- random maps ---------------------------------------------------------- #

class MapLand(_S):
    __slots__ = ("LandID", "Terrain", "LandSpacing", "BaseSize", "Zone",
                 "PlacementType", "Padding1", "BaseX", "BaseY", "LandProportion",
                 "ByPlayerFlag", "Padding2", "StartAreaRadius", "TerrainEdgeFade",
                 "Clumpiness")

    def __init__(self):
        self.LandID = 0
        self.Terrain = 0
        self.LandSpacing = 0
        self.BaseSize = 0
        self.Zone = 0
        self.PlacementType = 0
        self.Padding1 = 0
        self.BaseX = 0
        self.BaseY = 0
        self.LandProportion = 100
        self.ByPlayerFlag = 1
        self.Padding2 = 0
        self.StartAreaRadius = 10
        self.TerrainEdgeFade = 25
        self.Clumpiness = 8

    def read(self, r):
        self.LandID = r.i32()
        self.Terrain = r.u32()
        self.LandSpacing = r.i32()
        self.BaseSize = r.i32()
        self.Zone = r.u8()
        self.PlacementType = r.u8()
        self.Padding1 = r.i16()
        self.BaseX = r.i32()
        self.BaseY = r.i32()
        self.LandProportion = r.u8()
        self.ByPlayerFlag = r.u8()
        self.Padding2 = r.i16()
        self.StartAreaRadius = r.i32()
        self.TerrainEdgeFade = r.i32()
        self.Clumpiness = r.i32()

    def write(self, w):
        w.i32(self.LandID)
        w.u32(self.Terrain)
        w.i32(self.LandSpacing)
        w.i32(self.BaseSize)
        w.u8(self.Zone)
        w.u8(self.PlacementType)
        w.i16(self.Padding1)
        w.i32(self.BaseX)
        w.i32(self.BaseY)
        w.u8(self.LandProportion)
        w.u8(self.ByPlayerFlag)
        w.i16(self.Padding2)
        w.i32(self.StartAreaRadius)
        w.i32(self.TerrainEdgeFade)
        w.i32(self.Clumpiness)


class MapTerrain(_S):
    __slots__ = ("Proportion", "Terrain", "ClumpCount", "EdgeSpacing",
                 "PlacementTerrain", "Clumpiness")

    def __init__(self):
        self.Proportion = 0
        self.Terrain = 0
        self.ClumpCount = 0
        self.EdgeSpacing = 0
        self.PlacementTerrain = 0
        self.Clumpiness = 0

    def read(self, r):
        self.Proportion = r.i32()
        self.Terrain = r.i32()
        self.ClumpCount = r.i32()
        self.EdgeSpacing = r.i32()
        self.PlacementTerrain = r.i32()
        self.Clumpiness = r.i32()

    def write(self, w):
        w.i32(self.Proportion)
        w.i32(self.Terrain)
        w.i32(self.ClumpCount)
        w.i32(self.EdgeSpacing)
        w.i32(self.PlacementTerrain)
        w.i32(self.Clumpiness)


class MapUnit(_S):
    __slots__ = ("Unit", "HostTerrain", "GroupPlacing", "ScaleFlag", "Padding1",
                 "ObjectsPerGroup", "Fluctuation", "GroupsPerPlayer", "GroupArea",
                 "PlayerID", "SetPlaceForAllPlayers", "MinDistanceToPlayers",
                 "MaxDistanceToPlayers")

    def __init__(self):
        self.Unit = 0
        self.HostTerrain = 0
        self.GroupPlacing = 0
        self.ScaleFlag = 0
        self.Padding1 = 0
        self.ObjectsPerGroup = 0
        self.Fluctuation = 0
        self.GroupsPerPlayer = 0
        self.GroupArea = 0
        self.PlayerID = 0
        self.SetPlaceForAllPlayers = 0
        self.MinDistanceToPlayers = 0
        self.MaxDistanceToPlayers = 0

    def read(self, r):
        self.Unit = r.i32()
        self.HostTerrain = r.i32()
        self.GroupPlacing = r.u8()
        self.ScaleFlag = r.u8()
        self.Padding1 = r.i16()
        self.ObjectsPerGroup = r.i32()
        self.Fluctuation = r.i32()
        self.GroupsPerPlayer = r.i32()
        self.GroupArea = r.i32()
        self.PlayerID = r.i32()
        self.SetPlaceForAllPlayers = r.i32()
        self.MinDistanceToPlayers = r.i32()
        self.MaxDistanceToPlayers = r.i32()

    def write(self, w):
        w.i32(self.Unit)
        w.i32(self.HostTerrain)
        w.u8(self.GroupPlacing)
        w.u8(self.ScaleFlag)
        w.i16(self.Padding1)
        w.i32(self.ObjectsPerGroup)
        w.i32(self.Fluctuation)
        w.i32(self.GroupsPerPlayer)
        w.i32(self.GroupArea)
        w.i32(self.PlayerID)
        w.i32(self.SetPlaceForAllPlayers)
        w.i32(self.MinDistanceToPlayers)
        w.i32(self.MaxDistanceToPlayers)


class MapElevation(_S):
    __slots__ = ("Proportion", "Terrain", "ClumpCount", "BaseTerrain",
                 "BaseElevation", "TileSpacing")

    def __init__(self):
        self.Proportion = 0
        self.Terrain = 0
        self.ClumpCount = 0
        self.BaseTerrain = 0
        self.BaseElevation = 0
        self.TileSpacing = 0

    def read(self, r):
        self.Proportion = r.i32()
        self.Terrain = r.i32()
        self.ClumpCount = r.i32()
        self.BaseTerrain = r.i32()
        self.BaseElevation = r.i32()
        self.TileSpacing = r.i32()

    def write(self, w):
        w.i32(self.Proportion)
        w.i32(self.Terrain)
        w.i32(self.ClumpCount)
        w.i32(self.BaseTerrain)
        w.i32(self.BaseElevation)
        w.i32(self.TileSpacing)


class MapInfo(_S):
    __slots__ = ("MapID", "BorderSouthWest", "BorderNorthWest", "BorderNorthEast",
                 "BorderSouthEast", "BorderUsage", "WaterShape", "BaseTerrain",
                 "LandCoverage", "UnusedID", "MapLandsPtr", "MapTerrainsPtr",
                 "MapUnitsPtr", "MapElevationsPtr", "MapLands", "MapTerrains",
                 "MapUnits", "MapElevations")

    def __init__(self):
        self.MapID = -1
        self.BorderSouthWest = 0
        self.BorderNorthWest = 0
        self.BorderNorthEast = 0
        self.BorderSouthEast = 0
        self.BorderUsage = 0
        self.WaterShape = 10
        self.BaseTerrain = -1
        self.LandCoverage = 80
        self.UnusedID = 0
        self.MapLandsPtr = 0
        self.MapTerrainsPtr = 0
        self.MapUnitsPtr = 0
        self.MapElevationsPtr = 0
        self.MapLands = []
        self.MapTerrains = []
        self.MapUnits = []
        self.MapElevations = []

    def read(self, r, io_all):
        s = self
        if not io_all:
            s.MapID = r.i32()
        s.BorderSouthWest = r.i32()
        s.BorderNorthWest = r.i32()
        s.BorderNorthEast = r.i32()
        s.BorderSouthEast = r.i32()
        s.BorderUsage = r.i32()
        s.WaterShape = r.i32()
        s.BaseTerrain = r.i32()
        s.LandCoverage = r.i32()
        s.UnusedID = r.i32()

        n = r.u32()
        s.MapLandsPtr = r.i32()
        if io_all:
            s.MapLands = [MapLand() for _ in range(n)]
            for x in s.MapLands:
                x.read(r)

        n = r.u32()
        s.MapTerrainsPtr = r.i32()
        if io_all:
            s.MapTerrains = [MapTerrain() for _ in range(n)]
            for x in s.MapTerrains:
                x.read(r)

        n = r.u32()
        s.MapUnitsPtr = r.i32()
        if io_all:
            s.MapUnits = [MapUnit() for _ in range(n)]
            for x in s.MapUnits:
                x.read(r)

        n = r.u32()
        s.MapElevationsPtr = r.i32()
        if io_all:
            s.MapElevations = [MapElevation() for _ in range(n)]
            for x in s.MapElevations:
                x.read(r)

    def write(self, w, io_all):
        s = self
        if not io_all:
            w.i32(s.MapID)
        w.i32(s.BorderSouthWest)
        w.i32(s.BorderNorthWest)
        w.i32(s.BorderNorthEast)
        w.i32(s.BorderSouthEast)
        w.i32(s.BorderUsage)
        w.i32(s.WaterShape)
        w.i32(s.BaseTerrain)
        w.i32(s.LandCoverage)
        w.i32(s.UnusedID)

        w.u32(len(s.MapLands))
        w.i32(s.MapLandsPtr)
        if io_all:
            for x in s.MapLands:
                x.write(w)

        w.u32(len(s.MapTerrains))
        w.i32(s.MapTerrainsPtr)
        if io_all:
            for x in s.MapTerrains:
                x.write(w)

        w.u32(len(s.MapUnits))
        w.i32(s.MapUnitsPtr)
        if io_all:
            for x in s.MapUnits:
                x.write(w)

        w.u32(len(s.MapElevations))
        w.i32(s.MapElevationsPtr)
        if io_all:
            for x in s.MapElevations:
                x.write(w)


class RandomMaps(_S):
    __slots__ = ("RandomMapsPtr", "Maps")

    def __init__(self):
        self.RandomMapsPtr = 0
        self.Maps = []

    def read(self, r):
        n = r.u32()
        self.RandomMapsPtr = r.i32()
        self.Maps = [MapInfo() for _ in range(n)]
        for io_all in (False, True):
            for m in self.Maps:
                m.read(r, io_all)

    def write(self, w):
        w.u32(len(self.Maps))
        w.i32(self.RandomMapsPtr)
        for io_all in (False, True):
            for m in self.Maps:
                m.write(w, io_all)


# ---- unit headers --------------------------------------------------------- #

class UnitHeader(_S):
    __slots__ = ("Exists", "TaskList")

    def __init__(self):
        # C++: include/genie/dat/UnitHeader.h:36  ->  uint8_t Exists = 1;
        self.Exists = 1
        self.TaskList = []

    def read(self, r):
        self.Exists = r.u8()
        if self.Exists:
            n = r.i16()
            self.TaskList = [Task() for _ in range(n)]
            for t in self.TaskList:
                t.read(r)

    def write(self, w):
        w.u8(self.Exists)
        if self.Exists:
            w.i16(len(self.TaskList))
            for t in self.TaskList:
                t.write(w)


# ---- tech tree ------------------------------------------------------------ #

class TTreeCommon(_S):
    __slots__ = ("SlotsUsed", "UnitResearch", "Mode")

    def __init__(self):
        self.SlotsUsed = 0
        self.UnitResearch = [0] * TECHTREE_SLOTS_C32
        self.Mode = [0] * TECHTREE_SLOTS_C32

    def read(self, r):
        self.SlotsUsed = r.i32()
        self.UnitResearch = [r.i32() for _ in range(TECHTREE_SLOTS_C32)]
        self.Mode = [r.i32() for _ in range(TECHTREE_SLOTS_C32)]

    def write(self, w):
        w.i32(self.SlotsUsed)
        for v in self.UnitResearch:
            w.i32(v)
        for v in self.Mode:
            w.i32(v)


def _read_i32_vec(r):
    n = r.u8()
    return [r.i32() for _ in range(n)]


def _write_i32_vec(w, vec):
    w.u8(len(vec))
    for v in vec:
        w.i32(v)


class TechTreeAge(_S):
    __slots__ = ("ID", "Status", "Buildings", "Units", "Techs", "Common",
                 "NumBuildingLevels", "BuildingsPerZone", "GroupLengthPerZone",
                 "MaxAgeLength", "LineMode")

    def __init__(self):
        self.ID = -1
        self.Status = 2
        self.Buildings = []
        self.Units = []
        self.Techs = []
        self.Common = TTreeCommon()
        self.NumBuildingLevels = 0
        self.BuildingsPerZone = [0] * ZONE_COUNT_C32
        self.GroupLengthPerZone = [0] * ZONE_COUNT_C32
        self.MaxAgeLength = 0
        self.LineMode = 0

    def read(self, r):
        s = self
        s.ID = r.i32()
        s.Status = r.u8()
        s.Buildings = _read_i32_vec(r)
        s.Units = _read_i32_vec(r)
        s.Techs = _read_i32_vec(r)
        s.Common.read(r)
        s.NumBuildingLevels = r.u8()
        s.BuildingsPerZone = [r.u8() for _ in range(ZONE_COUNT_C32)]
        s.GroupLengthPerZone = [r.u8() for _ in range(ZONE_COUNT_C32)]
        s.MaxAgeLength = r.u8()
        s.LineMode = r.i32()

    def write(self, w):
        s = self
        w.i32(s.ID)
        w.u8(s.Status)
        _write_i32_vec(w, s.Buildings)
        _write_i32_vec(w, s.Units)
        _write_i32_vec(w, s.Techs)
        s.Common.write(w)
        w.u8(s.NumBuildingLevels)
        for v in s.BuildingsPerZone:
            w.u8(v)
        for v in s.GroupLengthPerZone:
            w.u8(v)
        w.u8(s.MaxAgeLength)
        w.i32(s.LineMode)


class BuildingConnection(_S):
    __slots__ = ("ID", "Status", "Buildings", "Units", "Techs", "Common",
                 "LocationInAge", "UnitsTechsTotal", "UnitsTechsFirst",
                 "LineMode", "EnablingResearch")

    def __init__(self):
        self.ID = -1
        self.Status = 2
        self.Buildings = []
        self.Units = []
        self.Techs = []
        self.Common = TTreeCommon()
        self.LocationInAge = 0
        self.UnitsTechsTotal = [0] * TECHTREE_AGES
        self.UnitsTechsFirst = [0] * TECHTREE_AGES
        self.LineMode = 0
        self.EnablingResearch = 0

    def read(self, r):
        s = self
        s.ID = r.i32()
        s.Status = r.u8()
        s.Buildings = _read_i32_vec(r)
        s.Units = _read_i32_vec(r)
        s.Techs = _read_i32_vec(r)
        s.Common.read(r)
        s.LocationInAge = r.u8()
        s.UnitsTechsTotal = [r.u8() for _ in range(TECHTREE_AGES)]
        s.UnitsTechsFirst = [r.u8() for _ in range(TECHTREE_AGES)]
        s.LineMode = r.i32()
        s.EnablingResearch = r.i32()

    def write(self, w):
        s = self
        w.i32(s.ID)
        w.u8(s.Status)
        _write_i32_vec(w, s.Buildings)
        _write_i32_vec(w, s.Units)
        _write_i32_vec(w, s.Techs)
        s.Common.write(w)
        w.u8(s.LocationInAge)
        for v in s.UnitsTechsTotal:
            w.u8(v)
        for v in s.UnitsTechsFirst:
            w.u8(v)
        w.i32(s.LineMode)
        w.i32(s.EnablingResearch)


class UnitConnection(_S):
    __slots__ = ("ID", "Status", "UpperBuilding", "Common", "VerticalLine",
                 "Units", "LocationInAge", "RequiredResearch", "LineMode",
                 "EnablingResearch")

    def __init__(self):
        self.ID = -1
        self.Status = 2
        self.UpperBuilding = -1
        self.Common = TTreeCommon()
        self.VerticalLine = -1
        self.Units = []
        self.LocationInAge = 0
        self.RequiredResearch = -1
        self.LineMode = 0
        self.EnablingResearch = -1

    def read(self, r):
        s = self
        s.ID = r.i32()
        s.Status = r.u8()
        s.UpperBuilding = r.i32()
        s.Common.read(r)
        s.VerticalLine = r.i32()
        s.Units = _read_i32_vec(r)
        s.LocationInAge = r.i32()
        s.RequiredResearch = r.i32()
        s.LineMode = r.i32()
        s.EnablingResearch = r.i32()

    def write(self, w):
        s = self
        w.i32(s.ID)
        w.u8(s.Status)
        w.i32(s.UpperBuilding)
        s.Common.write(w)
        w.i32(s.VerticalLine)
        _write_i32_vec(w, s.Units)
        w.i32(s.LocationInAge)
        w.i32(s.RequiredResearch)
        w.i32(s.LineMode)
        w.i32(s.EnablingResearch)


class ResearchConnection(_S):
    __slots__ = ("ID", "Status", "UpperBuilding", "Buildings", "Units", "Techs",
                 "Common", "VerticalLine", "LocationInAge", "LineMode")

    def __init__(self):
        self.ID = -1
        self.Status = 2
        self.UpperBuilding = -1
        self.Buildings = []
        self.Units = []
        self.Techs = []
        self.Common = TTreeCommon()
        self.VerticalLine = 0
        self.LocationInAge = 0
        self.LineMode = 0

    def read(self, r):
        s = self
        s.ID = r.i32()
        s.Status = r.u8()
        s.UpperBuilding = r.i32()
        s.Buildings = _read_i32_vec(r)
        s.Units = _read_i32_vec(r)
        s.Techs = _read_i32_vec(r)
        s.Common.read(r)
        s.VerticalLine = r.i32()
        s.LocationInAge = r.i32()
        s.LineMode = r.i32()

    def write(self, w):
        s = self
        w.i32(s.ID)
        w.u8(s.Status)
        w.i32(s.UpperBuilding)
        _write_i32_vec(w, s.Buildings)
        _write_i32_vec(w, s.Units)
        _write_i32_vec(w, s.Techs)
        s.Common.write(w)
        w.i32(s.VerticalLine)
        w.i32(s.LocationInAge)
        w.i32(s.LineMode)


class TechTree(_S):
    __slots__ = ("TotalUnitTechGroups", "TechTreeAges", "BuildingConnections",
                 "UnitConnections", "ResearchConnections")

    def __init__(self):
        self.TotalUnitTechGroups = 1
        self.TechTreeAges = []
        self.BuildingConnections = []
        self.UnitConnections = []
        self.ResearchConnections = []

    def read(self, r):
        s = self
        age_count = r.u8()
        building_count = r.u8()
        unit_count = r.i16()            # gv >= GV_C32
        research_count = r.u8()
        s.TotalUnitTechGroups = r.i32()
        s.TechTreeAges = [TechTreeAge() for _ in range(age_count)]
        for a in s.TechTreeAges:
            a.read(r)
        s.BuildingConnections = [BuildingConnection() for _ in range(building_count)]
        for b in s.BuildingConnections:
            b.read(r)
        s.UnitConnections = [UnitConnection() for _ in range(unit_count)]
        for u in s.UnitConnections:
            u.read(r)
        s.ResearchConnections = [ResearchConnection() for _ in range(research_count)]
        for c in s.ResearchConnections:
            c.read(r)

    def write(self, w):
        s = self
        w.u8(len(s.TechTreeAges))
        w.u8(len(s.BuildingConnections))
        w.i16(len(s.UnitConnections))
        w.u8(len(s.ResearchConnections))
        w.i32(s.TotalUnitTechGroups)
        for a in s.TechTreeAges:
            a.write(w)
        for b in s.BuildingConnections:
            b.write(w)
        for u in s.UnitConnections:
            u.write(w)
        for c in s.ResearchConnections:
            c.write(w)


# --------------------------------------------------------------------------- #
# Dat container
# --------------------------------------------------------------------------- #

class Dat:
    def __init__(self):
        self.FileVersion = b""
        self.TerrainsUsed1 = 0
        self.FloatPtrTerrainTables = []
        self.TerrainPassGraphicPointers = []
        self.TerrainRestrictions = []
        self.PlayerColours = []
        self.Sounds = []
        self.GraphicPointers = []
        self.Graphics = []
        self.TerrainBlockObj = TerrainBlock()
        self.RandomMapsObj = RandomMaps()
        self.Effects = []
        self.UnitHeaders = []
        self.Civs = []
        self.Techs = []
        self.SevenInts = [0] * 7
        self.TechTreeObj = TechTree()
        # filled by read()/write() for reporting
        self.offsets = {}

    # -- read ---------------------------------------------------------------
    def read(self, r):
        o = self.offsets
        o["file_version"] = r.pos
        self.FileVersion = bytes(r.data[r.pos:r.pos + 8])
        r.pos += 8

        o["terrain_restrictions"] = r.pos
        n = r.i16()
        self.TerrainsUsed1 = r.i16()
        self.FloatPtrTerrainTables = [r.i32() for _ in range(n)]
        self.TerrainPassGraphicPointers = [r.i32() for _ in range(n)]
        self.TerrainRestrictions = [TerrainRestriction(self.TerrainsUsed1)
                                    for _ in range(n)]
        for tr in self.TerrainRestrictions:
            tr.read(r)

        o["player_colours"] = r.pos
        n = r.i16()
        self.PlayerColours = [PlayerColour() for _ in range(n)]
        for pc in self.PlayerColours:
            pc.read(r)

        o["sounds"] = r.pos
        n = r.i16()
        self.Sounds = [Sound() for _ in range(n)]
        for s in self.Sounds:
            s.read(r)

        o["graphics"] = r.pos
        n = r.i16()
        self.GraphicPointers = [r.i32() for _ in range(n)]
        self.Graphics = [Graphic() for _ in range(n)]
        for i in range(n):
            if self.GraphicPointers[i]:
                self.Graphics[i].read(r)

        o["terrain_block"] = r.pos
        self.TerrainBlockObj.read(r)

        o["random_maps"] = r.pos
        self.RandomMapsObj.read(r)

        o["effects"] = r.pos
        n = r.i32()
        self.Effects = [Effect() for _ in range(n)]
        for e in self.Effects:
            e.read(r)

        o["unit_headers"] = r.pos
        n = r.i32()
        self.UnitHeaders = [UnitHeader() for _ in range(n)]
        for uh in self.UnitHeaders:
            uh.read(r)

        o["civs"] = r.pos
        n = r.i16()
        self.Civs = [Civ() for _ in range(n)]
        for i, c in enumerate(self.Civs):
            c.index = i
            c.read(r)

        o["techs"] = r.pos
        n = r.i16()
        self.Techs = [Tech() for _ in range(n)]
        for t in self.Techs:
            t.read(r)

        o["seven_ints"] = r.pos
        self.SevenInts = [r.i32() for _ in range(7)]

        o["techtree"] = r.pos
        self.TechTreeObj.read(r)

        o["end"] = r.pos
        return r.pos

    # -- write --------------------------------------------------------------
    def write(self, w):
        o = self.offsets
        o["file_version"] = len(w.buf)
        w.buf += self.FileVersion

        o["terrain_restrictions"] = len(w.buf)
        w.i16(len(self.TerrainRestrictions))
        w.i16(self.TerrainsUsed1)
        for p in self.FloatPtrTerrainTables:
            w.i32(p)
        for p in self.TerrainPassGraphicPointers:
            w.i32(p)
        for tr in self.TerrainRestrictions:
            tr.write(w)

        o["player_colours"] = len(w.buf)
        w.i16(len(self.PlayerColours))
        for pc in self.PlayerColours:
            pc.write(w)

        o["sounds"] = len(w.buf)
        w.i16(len(self.Sounds))
        for s in self.Sounds:
            s.write(w)

        o["graphics"] = len(w.buf)
        w.i16(len(self.Graphics))
        for p in self.GraphicPointers:
            w.i32(p)
        for i, g in enumerate(self.Graphics):
            if self.GraphicPointers[i]:
                g.write(w)

        o["terrain_block"] = len(w.buf)
        self.TerrainBlockObj.write(w)

        o["random_maps"] = len(w.buf)
        self.RandomMapsObj.write(w)

        o["effects"] = len(w.buf)
        w.i32(len(self.Effects))
        for e in self.Effects:
            e.write(w)

        o["unit_headers"] = len(w.buf)
        w.i32(len(self.UnitHeaders))
        for uh in self.UnitHeaders:
            uh.write(w)

        o["civs"] = len(w.buf)
        w.i16(len(self.Civs))
        for c in self.Civs:
            c.write(w)

        o["techs"] = len(w.buf)
        w.i16(len(self.Techs))
        for t in self.Techs:
            t.write(w)

        o["seven_ints"] = len(w.buf)
        for v in self.SevenInts:
            w.i32(v)

        o["techtree"] = len(w.buf)
        self.TechTreeObj.write(w)

        o["end"] = len(w.buf)
        return bytes(w.buf)

    def to_bytes(self):
        return self.write(Writer())


def load_payload(payload):
    """Parse an uncompressed dat payload (bytes) into a Dat object."""
    d = Dat()
    r = Reader(payload)
    end = d.read(r)
    if end != r.n:
        raise ValueError("trailing bytes: parsed %d of %d" % (end, r.n))
    return d


# --------------------------------------------------------------------------- #
# expansion
# --------------------------------------------------------------------------- #

def expand(dat, units=20000, techs=20000, effects=20000, unit_headers=20000):
    """Append blank slots in place. Returns a dict describing what changed.

    Semantics (see REPORT.md):
      * every civ gains `units` extra unit *slots*.  A slot is an entry in the
        Civ's int32 UnitPointers[] array whose value is 0 -- such an entry
        occupies ZERO bytes on disk (ISerializable::serializeSubWithPointers
        only reads/writes record i when pointers[i] != 0).  So we bump the
        int16 count and insert `units` int32 0 values right after the existing
        pointer array.  No Unit records are materialised (that would inflate
        the file ~90x and is not what a blank slot is).
        The official file ALREADY contains 36,389 such zero slots, so this is
        an idiom the game itself produces.
      * effects / techs / unit headers gain `n` default-constructed records
        appended at the end of their arrays (those arrays have no pointer
        table, so a slot must be a real record).
    """
    info = {}
    info["units_arg"] = units
    info["techs_arg"] = techs
    info["effects_arg"] = effects
    info["unit_headers_arg"] = unit_headers

    # ---- 1) per-civ unit slots -------------------------------------------
    info["civ_units_before"] = []
    info["civ_units_after"] = []
    info["civ_zero_ptrs_before"] = []
    info["civ_zero_ptrs_after"] = []
    info["civ_count_off"] = []
    info["civ_ptr_end_off"] = []
    info["civ_names"] = []
    info["civ_pointer_off"] = []
    info["units_insert_len"] = 4 * units
    for c in dat.Civs:
        before = len(c.Units)
        after = before + units
        if after > 32767:
            raise ValueError("civ %d: unit count %d would overflow int16" % (c.index, after))
        info["civ_units_before"].append(before)
        info["civ_units_after"].append(after)
        info["civ_zero_ptrs_before"].append(sum(1 for p in c.UnitPointers if not p))
        c.UnitPointers.extend([0] * units)
        info["civ_zero_ptrs_after"].append(sum(1 for p in c.UnitPointers if not p))
        c.Units.extend([None] * units)          # blank slots (0 bytes on disk)
        info["civ_count_off"].append(c.count_off)
        info["civ_ptr_end_off"].append(c.pointers_end_off)
        info["civ_pointer_off"].append(c.pointers_off)
        info["civ_names"].append(str(c.Name))

    # ---- 2) effects: append N default-constructed Effect records ---------
    blank_effect = default_effect_bytes()
    info["effects_before"] = len(dat.Effects)
    for _ in range(effects):
        dat.Effects.append(Effect())
    info["effects_after"] = len(dat.Effects)
    info["effect_blank_size"] = len(blank_effect)
    info["effect_blank_head"] = blank_effect[:32].hex()
    info["effects_insert_len"] = effects * len(blank_effect)

    # ---- 3) unit headers: append N default-constructed UnitHeader records -
    blank_uh = default_unit_header_bytes()
    info["unit_headers_before"] = len(dat.UnitHeaders)
    for _ in range(unit_headers):
        dat.UnitHeaders.append(UnitHeader())
    info["unit_headers_after"] = len(dat.UnitHeaders)
    info["unit_header_blank_size"] = len(blank_uh)
    info["unit_header_blank_head"] = blank_uh[:32].hex()
    info["unit_headers_insert_len"] = unit_headers * len(blank_uh)

    # ---- 4) techs: append N default-constructed Tech records -------------
    blank_tech = default_tech_bytes()
    info["techs_before"] = len(dat.Techs)
    for _ in range(techs):
        dat.Techs.append(Tech())
    info["techs_after"] = len(dat.Techs)
    if info["techs_after"] > 32767:
        raise ValueError("tech count %d would overflow int16" % info["techs_after"])
    info["tech_blank_size"] = len(blank_tech)
    info["tech_blank_head"] = blank_tech[:32].hex()
    info["techs_insert_len"] = techs * len(blank_tech)

    # ---- 5) blank Unit mirror (informational only: 0 bytes when ptr == 0) -
    ub = default_unit_bytes()
    info["unit_blank_size"] = len(ub)
    info["unit_blank_head"] = ub[:32].hex()

    return info


# --------------------------------------------------------------------------- #
# edit manifest: an exact, gap-free byte-level replay script
# --------------------------------------------------------------------------- #

def build_manifest(orig, expanded, orig_offsets, info):
    """Build a byte-exact edit script turning `orig` into `expanded`.

    Every edit is one of
      same   : orig[o_off:o_off+len] == expanded[e_off:e_off+len]
      insert : expanded[e_off:e_off+len] is brand new
      patch  : expanded[e_off:e_off+len] replaces orig[o_off:o_off+len]
    Edits are sorted by e_off, tile the whole expanded payload with no gaps and
    no overlaps, and replaying them over `orig` reproduces `expanded` exactly.
    """
    events = []

    def ev_insert(o_off, pattern, repeat, label):
        """Insert repeat copies of `pattern`; the total length is len*p."""
        length = len(pattern) * repeat
        if length:
            events.append({"o_off": o_off, "o_len": 0, "kind": "insert",
                           "len": length, "label": label,
                           "pattern": bytes(pattern), "repeat": repeat})

    def ev_patch(o_off, length, label):
        if length:
            events.append({"o_off": o_off, "o_len": length, "kind": "patch",
                           "len": length, "label": label})

    for i in range(len(info["civ_count_off"])):
        ev_patch(info["civ_count_off"][i], 2, "civ%d.unit_count" % i)
        ev_insert(info["civ_ptr_end_off"][i], b"\x00", info["units_insert_len"],
                  "civ%d.unit_pointers" % i)
    ev_patch(orig_offsets["effects"], 4, "effects.count")
    ev_insert(orig_offsets["unit_headers"], default_effect_bytes(),
              info["effects_arg"], "effects")
    ev_patch(orig_offsets["unit_headers"], 4, "unit_headers.count")
    ev_insert(orig_offsets["civs"], default_unit_header_bytes(),
              info["unit_headers_arg"], "unit_headers")
    ev_patch(orig_offsets["techs"], 2, "techs.count")
    ev_insert(orig_offsets["seven_ints"], default_tech_bytes(),
              info["techs_arg"], "techs")

    # a zero-length insert at the same o_off must be applied before the patch
    # that consumes original bytes at that offset -> sort by (o_off, o_len)
    events.sort(key=lambda e: (e["o_off"], e["o_len"]))

    edits = []
    o = 0
    e = 0
    for ev in events:
        if ev["o_off"] < o:
            raise ValueError("overlapping edits at original offset %d" % ev["o_off"])
        if ev["o_off"] > o:
            gap = ev["o_off"] - o
            edits.append({"kind": "same", "o_off": o, "e_off": e, "len": gap})
            o += gap
            e += gap
        if ev["kind"] == "insert":
            blk = expanded[e:e + ev["len"]]
            if len(blk) != ev["len"]:
                raise ValueError("insert %s runs past end of expanded payload" % ev["label"])
            # the inserted block must be exactly `repeat` copies of `pattern`,
            # which is what lets an independent replayer rebuild it
            if len(blk) > 4096:
                probe = ev["pattern"] * (4096 // len(ev["pattern"]))
                if blk[:len(probe)] != probe:
                    raise ValueError("insert %s does not match its pattern" % ev["label"])
            elif blk != ev["pattern"] * ev["repeat"]:
                raise ValueError("insert %s does not match its pattern" % ev["label"])
            edits.append({"kind": "insert", "e_off": e, "len": ev["len"],
                          "sha256": _sha(blk), "label": ev["label"],
                          "data": {"unit_hex": ev["pattern"].hex(),
                                   "repeat": ev["repeat"]}})
            e += ev["len"]
        else:
            before = orig[o:o + ev["len"]]
            after = expanded[e:e + ev["len"]]
            if len(before) != ev["len"] or len(after) != ev["len"]:
                raise ValueError("patch %s runs past end of payload" % ev["label"])
            edits.append({"kind": "patch", "o_off": o, "e_off": e, "len": ev["len"],
                          "before_sha256": _sha(before), "after_sha256": _sha(after),
                          # carry the replacement bytes so a replayer never has to
                          # guess: 'same' copies the original, 'patch' writes
                          # after_hex, 'insert' writes unit_hex * repeat.
                          "after_hex": after.hex(),
                          "label": ev["label"]})
            o += ev["len"]
            e += ev["len"]

    if o < len(orig):
        gap = len(orig) - o
        edits.append({"kind": "same", "o_off": o, "e_off": e, "len": gap})
        o += gap
        e += gap
    if o != len(orig) or e != len(expanded):
        raise ValueError("edit script does not tile the payloads: %d/%d of %d/%d"
                         % (o, e, len(orig), len(expanded)))

    return {
        "orig_payload_size": len(orig),
        "expanded_payload_size": len(expanded),
        "edits": edits,
        "counts": {
            "effects": [info["effects_before"], info["effects_after"]],
            "techs": [info["techs_before"], info["techs_after"]],
            "unit_headers": [info["unit_headers_before"], info["unit_headers_after"]],
            "civs": len(info["civ_units_before"]),
            "civ_units": [[info["civ_units_before"][i], info["civ_units_after"][i]]
                          for i in range(len(info["civ_units_before"]))],
            "unit_pointers_per_civ": [[info["civ_zero_ptrs_before"][i],
                                       info["civ_zero_ptrs_after"][i]]
                                      for i in range(len(info["civ_zero_ptrs_before"]))],
        },
        "extra": {
            "civ_names": info["civ_names"],
            "civ_count_field_offsets": info["civ_count_off"],
            "civ_unit_pointer_field_offsets": info["civ_pointer_off"],
            "civ_unit_pointer_array_end_offsets": info["civ_ptr_end_off"],
            "units_added_per_civ": info["units_arg"],
            "effects_added": info["effects_arg"],
            "techs_added": info["techs_arg"],
            "unit_headers_added": info["unit_headers_arg"],
            "blank_record_images": {
                "effect": {"len": info["effect_blank_size"],
                           "hex": info["effect_blank_head"],
                           "sha256": _sha(default_effect_bytes())},
                "tech": {"len": info["tech_blank_size"],
                         "hex": info["tech_blank_head"],
                         "sha256": _sha(default_tech_bytes())},
                "unit_header": {"len": info["unit_header_blank_size"],
                                "hex": info["unit_header_blank_head"],
                                "sha256": _sha(default_unit_header_bytes())},
                "unit_slot": {"len": 0,
                              "hex": "",
                              "note": "a null UnitPointer occupies zero bytes"},
            },
            "insert_block_stats": {
                "civ_unit_pointers": {
                    "len": info["units_insert_len"],
                    "all_zero": True,
                },
            },
        },
    }


def verify_manifest(orig, expanded, manifest):
    """Replay the edit script and verify it (a) covers everything and
    (b) every 'same' segment really is byte-identical."""
    out = bytearray()
    e_expect = 0
    problems = []
    for n, ed in enumerate(manifest["edits"]):
        if ed["e_off"] != e_expect:
            problems.append("edit %d: e_off %d != expected %d (gap/overlap)"
                            % (n, ed["e_off"], e_expect))
        if ed["kind"] == "same":
            if orig[ed["o_off"]:ed["o_off"] + ed["len"]] != \
                    expanded[ed["e_off"]:ed["e_off"] + ed["len"]]:
                problems.append("edit %d: 'same' segment @o%d/e%d differs"
                                % (n, ed["o_off"], ed["e_off"]))
            out += orig[ed["o_off"]:ed["o_off"] + ed["len"]]
        elif ed["kind"] == "patch":
            if _sha(orig[ed["o_off"]:ed["o_off"] + ed["len"]]) != ed["before_sha256"]:
                problems.append("edit %d: before_sha256 mismatch" % n)
            if _sha(expanded[ed["e_off"]:ed["e_off"] + ed["len"]]) != ed["after_sha256"]:
                problems.append("edit %d: after_sha256 mismatch" % n)
            if _sha(bytes.fromhex(ed["after_hex"])) != ed["after_sha256"]:
                problems.append("edit %d: after_hex does not hash to after_sha256" % n)
            out += bytes.fromhex(ed["after_hex"])
        else:
            if _sha(expanded[ed["e_off"]:ed["e_off"] + ed["len"]]) != ed["sha256"]:
                problems.append("edit %d: insert sha256 mismatch" % n)
            pattern = bytes.fromhex(ed["data"]["unit_hex"])
            rebuilt = pattern * ed["data"]["repeat"]
            if len(rebuilt) != ed["len"]:
                problems.append("edit %d: pattern*repeat = %d != len %d"
                                % (n, len(rebuilt), ed["len"]))
            elif expanded[ed["e_off"]:ed["e_off"] + ed["len"]] != rebuilt:
                problems.append("edit %d: insert bytes != pattern*repeat" % n)
            elif _sha(rebuilt) != ed["sha256"]:
                problems.append("edit %d: pattern*repeat sha256 != insert sha256" % n)
            out += rebuilt
        e_expect = ed["e_off"] + ed["len"]
    if e_expect != len(expanded):
        problems.append("edits end at %d but expanded payload is %d bytes"
                        % (e_expect, len(expanded)))
    if bytes(out) != expanded:
        problems.append("replay did not reproduce the expanded payload")
    return problems


# --------------------------------------------------------------------------- #
# compression helpers
# --------------------------------------------------------------------------- #

def read_raw_deflate(path):
    with open(path, "rb") as f:
        comp = f.read()
    return zlib.decompress(comp, -15), len(comp)


def write_raw_deflate(path, payload, level=9):
    c = zlib.compressobj(level, zlib.DEFLATED, -15)
    blob = c.compress(payload) + c.flush()
    with open(path, "wb") as f:
        f.write(blob)
    return len(blob)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _sha(b):
    return hashlib.sha256(b).hexdigest()


def load_input(path):
    """Read `path` and return (payload_bytes, mode_description).

    The CLI accepts either an already-decompressed payload, or the real .dat
    (raw deflate, no zlib header).  The payload starts with b"VER " so the
    format is auto-detected; the chosen mode is reported by the caller.
    """
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:4] == b"VER ":
        return raw, "payload (uncompressed, %d bytes)" % len(raw)
    try:
        payload = zlib.decompress(raw, -15)
    except Exception as exc:
        raise SystemExit("%s: not a payload (no 'VER ' magic) and raw-deflate "
                         "decompression failed: %s" % (path, exc))
    if payload[:4] != b"VER ":
        raise SystemExit("%s: raw-deflate decompressed to %d bytes but the "
                         "result does not start with 'VER '" % (path, len(payload)))
    return payload, "raw deflate %d bytes -> payload %d bytes" % (len(raw), len(payload))


SECTION_ORDER = ("file_version", "terrain_restrictions", "player_colours", "sounds",
                 "graphics", "terrain_block", "random_maps", "effects",
                 "unit_headers", "civs", "techs", "seven_ints", "techtree", "end")


def print_section_table(offsets, out=print):
    out("-- section table (absolute payload offsets, parse order) --")
    prev = None
    for k in SECTION_ORDER:
        v = offsets.get(k)
        if v is None:
            continue
        size = "" if prev is None else "   (prev seg %d bytes)" % (v - prev)
        out("  %-22s @ %10d%s" % (k, v, size))
        prev = v
    out("end of payload            @ %10d" % offsets["end"])


def cmd_verify(argv):
    path = argv[0] if argv else DEFAULT_PAYLOAD
    payload, mode = load_input(path)
    dat = load_payload(payload)
    out = dat.to_bytes()
    print("input             : %s  [%s]" % (path, mode))
    print("payload size      : %d" % len(payload))
    print("parsed end        : %d  (consumed exactly: %s)"
          % (dat.offsets["end"], dat.offsets["end"] == len(payload)))
    print("reserialised size : %d" % len(out))
    if dat.offsets["end"] != len(payload):
        print("PARSE: TRAILING BYTES (parsed %d of %d)"
              % (dat.offsets["end"], len(payload)))
    if out == payload:
        print("ROUNDTRIP: IDENTICAL")
    else:
        print("ROUNDTRIP: DIFFERENT")
        n = min(len(out), len(payload))
        for i in range(n):
            if out[i] != payload[i]:
                print("  first diff at offset %d: %02x vs %02x" % (i, out[i], payload[i]))
                break
        else:
            print("  length differs")
    print("sha256 original   : %s" % _sha(payload))
    print("sha256 reserial   : %s" % _sha(out))
    print()
    print_section_table(dat.offsets)
    ok = (out == payload) and (dat.offsets["end"] == len(payload))
    return 0 if ok else 1


def cmd_info(argv):
    path = argv[0] if argv else DEFAULT_PAYLOAD
    payload, mode = load_input(path)
    dat = load_payload(payload)
    o = dat.offsets
    print("=== datfile_c32 info ===")
    print("payload: %s  [%s]  (%d bytes)" % (path, mode, len(payload)))
    print("FileVersion               : %r" % dat.FileVersion)
    print()
    print_section_table(o)
    print()
    print("-- counts --")
    print("terrain restrictions      : %d" % len(dat.TerrainRestrictions))
    print("TerrainsUsed1             : %d" % dat.TerrainsUsed1)
    print("player colours            : %d" % len(dat.PlayerColours))
    print("sounds                    : %d" % len(dat.Sounds))
    print("graphics (slots)          : %d" % len(dat.Graphics))
    print("  graphics with ptr != 0  : %d" % sum(1 for p in dat.GraphicPointers if p))
    print("terrains in TerrainBlock  : %d" % len(dat.TerrainBlockObj.Terrains))
    print("random maps               : %d" % len(dat.RandomMapsObj.Maps))
    print("effects                   : %d" % len(dat.Effects))
    print("unit headers              : %d" % len(dat.UnitHeaders))
    print("civs                      : %d" % len(dat.Civs))
    print("techs                     : %d" % len(dat.Techs))
    print("techtree ages             : %d" % len(dat.TechTreeObj.TechTreeAges))
    print("techtree building conns   : %d" % len(dat.TechTreeObj.BuildingConnections))
    print("techtree unit conns       : %d" % len(dat.TechTreeObj.UnitConnections))
    print("techtree research conns   : %d" % len(dat.TechTreeObj.ResearchConnections))
    print()
    print("-- unit header Exists histogram (Exists, task_count) --")
    hist = {}
    for uh in dat.UnitHeaders:
        k = (uh.Exists, len(uh.TaskList))
        hist[k] = hist.get(k, 0) + 1
    for k in sorted(hist):
        print("  Exists=%d tasks=%2d : %d" % (k[0], k[1], hist[k]))
    print()
    print("-- per-civ unit slots --")
    total = 0
    zero_total = 0
    for i, c in enumerate(dat.Civs):
        nz = sum(1 for p in c.UnitPointers if p)
        zeros = len(c.UnitPointers) - nz
        total += len(c.Units)
        zero_total += zeros
        print("  civ %2d off=%-9d name=%-16s units=%5d  ptrs=%5d (nonzero=%5d zero=%5d)"
              "  count@%-9d ptrs@%-9d end@%-9d resources=%d techTree=%d teamBonus=%d iconSet=%d"
              % (i, c.record_off, c.Name or "(none)", len(c.Units), len(c.UnitPointers),
                 nz, zeros, c.count_off, c.pointers_off, c.pointers_end_off,
                 len(c.Resources), c.TechTreeID, c.TeamBonusID, c.IconSet))
    print("  civs: %d   TOTAL unit slots: %d   TOTAL zero (blank) slots: %d"
          % (len(dat.Civs), total, zero_total))
    allv = {}
    for c in dat.Civs:
        for p in c.UnitPointers:
            allv[p] = allv.get(p, 0) + 1
    print("  UnitPointers value histogram: %s" % sorted(allv.items()))
    print()
    print("-- sizes of default ('blank') records --")
    for label, fn in (("Unit", default_unit_bytes),
                      ("Effect", default_effect_bytes),
                      ("Tech", default_tech_bytes),
                      ("UnitHdr", default_unit_header_bytes)):
        b = fn()
        print("  %-8s %4d bytes  head32=%s" % (label, len(b), b[:32].hex()))
    return 0


def cmd_expand(argv):
    out = None
    units = techs = effects = unit_headers = 20000
    src = DEFAULT_PAYLOAD
    manifest_path = None
    i = 0
    while i < len(argv):
        a = argv[i]

        def _val(a, name):
            if a == "--" + name:
                return argv[i + 1]
            return a[len(name) + 3:]

        if a == "--out" or a.startswith("--out="):
            out = _val(a, "out")
            if a == "--out":
                i += 1
        elif a == "--units" or a.startswith("--units="):
            units = int(_val(a, "units"))
            if a == "--units":
                i += 1
        elif a == "--techs" or a.startswith("--techs="):
            techs = int(_val(a, "techs"))
            if a == "--techs":
                i += 1
        elif a == "--effects" or a.startswith("--effects="):
            effects = int(_val(a, "effects"))
            if a == "--effects":
                i += 1
        elif a == "--unit-headers" or a.startswith("--unit-headers="):
            unit_headers = int(_val(a, "unit-headers"))
            if a == "--unit-headers":
                i += 1
        elif a == "--src" or a.startswith("--src="):
            src = _val(a, "src")
            if a == "--src":
                i += 1
        elif a == "--manifest" or a.startswith("--manifest="):
            manifest_path = _val(a, "manifest")
            if a == "--manifest":
                i += 1
        else:
            raise SystemExit("unknown option: %s" % a)
        i += 1
    if not out:
        raise SystemExit("--out is required")
    if manifest_path is None:
        manifest_path = os.path.join(os.path.dirname(os.path.abspath(out)),
                                     "expand_manifest.json")

    payload, mode = load_input(src)
    print("source            : %s  [%s]" % (src, mode))
    dat = load_payload(payload)
    orig_offsets = dict(dat.offsets)

    orig_counts = {
        "effects": len(dat.Effects),
        "techs": len(dat.Techs),
        "unit_headers": len(dat.UnitHeaders),
        "civs": len(dat.Civs),
        "civ_units": [len(c.Units) for c in dat.Civs],
    }

    info = expand(dat, units=units, techs=techs, effects=effects,
                  unit_headers=unit_headers)

    new_payload = dat.to_bytes()
    print("expanded payload  : %d bytes  (delta %+d)"
          % (len(new_payload), len(new_payload) - len(payload)))

    ok = True

    # --- self checks ------------------------------------------------------
    print()
    print("-- self checks --")
    all_bumped = all(info["civ_units_after"][i] == info["civ_units_before"][i] + units
                     for i in range(len(dat.Civs)))
    print("civ unit counts: each +%d ? %s" % (units, all_bumped))
    ok = ok and all_bumped
    print("max civ unit count after expand : %d  (int16 max 32767 -> %s)"
          % (max(info["civ_units_after"]), max(info["civ_units_after"]) <= 32767))
    print("min civ unit count after expand : %d" % min(info["civ_units_after"]))
    print("max civ unit count before expand: %d" % max(info["civ_units_before"]))
    ok = ok and max(info["civ_units_after"]) <= 32767
    print("effects     : %d -> %d  (target +%d -> %s)"
          % (orig_counts["effects"], len(dat.Effects), effects,
             len(dat.Effects) == orig_counts["effects"] + effects))
    ok = ok and len(dat.Effects) == orig_counts["effects"] + effects
    print("unit headers: %d -> %d  (target +%d -> %s)"
          % (orig_counts["unit_headers"], len(dat.UnitHeaders), unit_headers,
             len(dat.UnitHeaders) == orig_counts["unit_headers"] + unit_headers))
    ok = ok and len(dat.UnitHeaders) == orig_counts["unit_headers"] + unit_headers
    print("techs       : %d -> %d  (target +%d -> %s, int16 ok: %s)"
          % (orig_counts["techs"], len(dat.Techs), techs,
             len(dat.Techs) == orig_counts["techs"] + techs, len(dat.Techs) <= 32767))
    ok = ok and len(dat.Techs) == orig_counts["techs"] + techs and len(dat.Techs) <= 32767
    print("civs        : %d -> %d  (count must not change: %s)"
          % (orig_counts["civs"], len(dat.Civs), orig_counts["civs"] == len(dat.Civs)))
    ok = ok and orig_counts["civs"] == len(dat.Civs)
    print("blank Unit       mirror: %d bytes  head32=%s" % (info["unit_blank_size"], info["unit_blank_head"]))
    print("blank Effect     mirror: %d bytes  head32=%s" % (info["effect_blank_size"], info["effect_blank_head"]))
    print("blank Tech       mirror: %d bytes  head32=%s" % (info["tech_blank_size"], info["tech_blank_head"]))
    print("blank UnitHeader mirror: %d bytes  head32=%s"
          % (info["unit_header_blank_size"], info["unit_header_blank_head"]))

    # --- reparse the new payload -----------------------------------------
    dat2 = load_payload(new_payload)
    print()
    print("-- reparse of the expanded payload --")
    print("reparse end offset %d == size %d ? %s"
          % (dat2.offsets["end"], len(new_payload), dat2.offsets["end"] == len(new_payload)))
    ok = ok and dat2.offsets["end"] == len(new_payload)
    if len(dat2.Effects) != orig_counts["effects"] + effects:
        ok = False
        print("  !! effects count mismatch after reparse")
    if len(dat2.Techs) != orig_counts["techs"] + techs:
        ok = False
        print("  !! techs count mismatch after reparse")
    if len(dat2.UnitHeaders) != orig_counts["unit_headers"] + unit_headers:
        ok = False
        print("  !! unit header count mismatch after reparse")
    if len(dat2.Civs) != orig_counts["civs"]:
        ok = False
        print("  !! civ count changed after reparse")
    for i, cc in enumerate(dat2.Civs):
        if len(cc.Units) != orig_counts["civ_units"][i] + units:
            ok = False
            print("  !! civ %d unit count mismatch after reparse" % i)
            break
        if any(cc.UnitPointers[j] != 0 for j in range(orig_counts["civ_units"][i],
                                                     len(cc.UnitPointers))):
            ok = False
            print("  !! civ %d has non-zero pointer in appended slots" % i)
            break
    if all(e.Name == "" and len(e.EffectCommands) == 0
           for e in dat2.Effects[orig_counts["effects"]:]):
        print("appended effects blank? %s" % True)
    else:
        ok = False
        print("appended effects blank? False")
    if all(t.Name == "" and t.EffectID == -1 and len(t.ResearchLocations) == 0
           for t in dat2.Techs[orig_counts["techs"]:]):
        print("appended techs blank?   %s" % True)
    else:
        ok = False
        print("appended techs blank?   False")
    if all(u.Exists == 1 and len(u.TaskList) == 0
           for u in dat2.UnitHeaders[orig_counts["unit_headers"]:]):
        print("appended headers blank? %s" % True)
    else:
        ok = False
        print("appended headers blank? False")

    # --- the expanded payload must itself round-trip -----------------------
    print()
    print("-- expanded payload round trip --")
    re2 = dat2.to_bytes()
    print("reserialised expanded size : %d" % len(re2))
    print("expanded ROUNDTRIP: %s" % ("IDENTICAL" if re2 == new_payload else "DIFFERENT"))
    if re2 != new_payload:
        ok = False
        for k in range(min(len(re2), len(new_payload))):
            if re2[k] != new_payload[k]:
                print("  first diff at %d: %02x vs %02x" % (k, re2[k], new_payload[k]))
                break
    print("sha256 expanded     : %s" % _sha(new_payload))
    print("sha256 reserialised : %s" % _sha(re2))

    # --- untouched bytes: prefix digest comparison -------------------------
    print()
    print("-- untouched-byte evidence --")
    # the very first edit is the effects count patch, so everything before it
    # must be bit-identical
    prefix_end = orig_offsets["effects"]
    print("bytes [0,%d) (everything before the first edit) identical? %s"
          % (prefix_end, payload[:prefix_end] == new_payload[:prefix_end]))
    ok = ok and payload[:prefix_end] == new_payload[:prefix_end]

    # techs body: from orig o["techs"]+2 to o["seven_ints"]
    tb0 = orig_offsets["techs"] + 2
    tb1 = orig_offsets["seven_ints"]
    tb_len = tb1 - tb0
    # find where it lands in the expanded payload: it is the first byte after
    # the patched count field of the expanded techs segment
    t_off_new = dat2.offsets["techs"] + 2
    same_body = payload[tb0:tb1] == new_payload[t_off_new:t_off_new + tb_len]
    print("original techs array body (%d bytes) relocates unchanged? %s" % (tb_len, same_body))
    ok = ok and same_body

    si0 = orig_offsets["seven_ints"]
    si1 = len(payload)
    tail_len = si1 - si0
    si_new = dat2.offsets["seven_ints"]
    same_tail = payload[si0:si1] == new_payload[si_new:si_new + tail_len]
    print("original tail (7 int32 + techtree, %d bytes) relocates unchanged? %s"
          % (tail_len, same_tail))
    ok = ok and same_tail

    # effects/unit-header bodies
    eb0, eb1 = orig_offsets["effects"] + 4, orig_offsets["unit_headers"]
    eb_new = dat2.offsets["effects"] + 4
    same_eff = payload[eb0:eb1] == new_payload[eb_new:eb_new + (eb1 - eb0)]
    print("original effects array body (%d bytes) relocates unchanged? %s"
          % (eb1 - eb0, same_eff))
    ok = ok and same_eff
    hb0, hb1 = orig_offsets["unit_headers"] + 4, orig_offsets["civs"]
    hb_new = dat2.offsets["unit_headers"] + 4
    same_uh = payload[hb0:hb1] == new_payload[hb_new:hb_new + (hb1 - hb0)]
    print("original unit header array body (%d bytes) relocates unchanged? %s"
          % (hb1 - hb0, same_uh))
    ok = ok and same_uh
    # per-civ: the original unit records of each civ survive
    same_civ_records = True
    for i, c in enumerate(dat.Civs):
        o0 = c.pointers_end_off
        o1 = c.record_end_off
        if o1 > o0:
            c2 = dat2.Civs[i]
            # the appended pointers start at the new array end
            n0 = c.count_off
            # locate the new unit-record area: after the extended pointer array
            e0 = c2.pointers_end_off
            if payload[o0:o1] != new_payload[e0:e0 + (o1 - o0)]:
                same_civ_records = False
                print("  !! civ %d original unit records changed" % i)
                break
    print("original per-civ unit records relocate unchanged? %s" % same_civ_records)
    ok = ok and same_civ_records

    # --- manifest ---------------------------------------------------------
    print()
    print("-- edit manifest --")
    manifest = build_manifest(payload, new_payload, orig_offsets, info)
    problems = verify_manifest(payload, new_payload, manifest)
    total = sum(ed["len"] for ed in manifest["edits"])
    print("edits: %d  (same=%d insert=%d patch=%d)"
          % (len(manifest["edits"]),
             sum(1 for ed in manifest["edits"] if ed["kind"] == "same"),
             sum(1 for ed in manifest["edits"] if ed["kind"] == "insert"),
             sum(1 for ed in manifest["edits"] if ed["kind"] == "patch")))
    print("sum(edit len) = %d  == expanded payload size %d ? %s"
          % (total, len(new_payload), total == len(new_payload)))
    ok = ok and total == len(new_payload)
    print("manifest replay verification: %s" % ("PASS" if not problems else "FAIL"))
    for p in problems[:20]:
        print("  !! %s" % p)
    ok = ok and not problems
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    print("wrote %s" % manifest_path)

    comp = write_raw_deflate(out, new_payload)
    print()
    print("wrote %s  (compressed %d bytes -> payload %d bytes, payload delta %+d)"
          % (out, comp, len(new_payload), len(new_payload) - len(payload)))
    print("SELFCHECK: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "verify":
        return cmd_verify(argv[1:])
    if cmd == "info":
        return cmd_info(argv[1:])
    if cmd == "expand":
        return cmd_expand(argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
