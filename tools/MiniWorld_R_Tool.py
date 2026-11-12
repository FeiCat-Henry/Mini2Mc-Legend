#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
迷你世界（MiniwAr 引擎）存档转换器  ·  单文件图形界面版
=========================================================
把 .r 区块文件读出来，按 mc_block_map.json 映射成 MC 方块名，写成 Minecraft Java 版 .mca。

功能（全部在这一个文件里，不依赖 r_reader.py / to_mca.py 等脚本）：
  ① 校验映射表  读 mc_block_map.json，检查条目/非法名/超范围 id（不写文件）
  ② 转换        .r -> .mca（按 MC 世界结构放到 region/、DIM-1/region/、DIM1/region/）
  ③ 统计覆盖率  每个维度有多少方块落到土块占位
  ④ 生成预览    俯视地图 PNG
  ⑤ 回读校验    把 .mca 解回方块，逐块与源 .r 比对

直接运行：  python MiniWorld_R_Tool.py
自检模式：  python MiniWorld_R_Tool.py --selftest    （不开窗，跑一遍小样本转换+校验）

默认路径：映射表按「当前目录 → 本文件所在目录 → 项目根目录」的顺序找 mc_block_map.json，
          所以把本文件和映射表放在同一个文件夹里，直接运行就能读到，不必改路径。
          mc_block_map.json 是唯一的映射来源（改它、或用 block_id_Editor.py 编辑）。

.r 格式要点（详见 docs\r_format.md）：
  容器：4096 B 扇区；扇区 0 = 1024×u32 索引表；扇区 1 = 1024×u32 时间戳表
        索引项 e: offset = (e & 0x7FFFFF) ^ KEYS[idx & 0xF]，len = (e>>24)&0xFF
  压缩：记录 = [u32 size][u32 tag][payload]；tag>>28 == 2 表示 LZMA；载荷 = props + dictSize + raw LZMA1
  数据：FlatBuffer，root -> field1 是 section 向量；每个 section 含 4096 个 u16 方块值
        方块值 = 低 12 位 id | 高 4 位状态；体素下标 i = (yLocal<<8)|(x<<4)|z
        （与 MC 的 block_states 顺序一致，转换时原样写入；缩略预览图另按
          (yLocal<<8)|(z<<4)|x 取样，见 pidx()，仅影响出图不影响 .mca）
"""

# ============================================================ 0. 常量
import collections
import json
import os
import queue
import re
import struct
import sys
import threading
import time
import traceback
import zlib

KEYS = [0x5d351c, 0x30f308, 0x524d19, 0x6cf2bc, 0x04b262, 0x35e03b, 0x3a7c26, 0x4ba765,
        0x2762ed, 0x44f986, 0x6f7114, 0x6a4acf, 0x7bb584, 0x72ca13, 0x22ad76, 0x178bbb]
SECTOR = 4096
ID_MAX = 4095

# 名字规范化：表里写的 -> MC 1.20.1 里真实存在的方块
FIX = {
    'waterlily': 'lily_pad',
    'wheat_seeds': 'wheat',
    'double_stone_slab': 'smooth_stone_slab',
    'log': 'oak_log',
    'bush': 'grass',
    'thatch': 'hay_block',
    'pale_oak_log': 'dark_oak_log',
    'pale_oak_fence': 'dark_oak_fence',
}
DROP_TO_DIRT = {'apple', 'armor_stand', 'item_frame'}

# 维度：输出子目录 / 群系 / 最高 y 段 / yPos
DIM_INFO = {
    'm0': ('region', 'minecraft:plains', 15, -4, '主世界'),
    'm1': ('DIM-1/region', 'minecraft:nether_wastes', 15, -4, '地心/下界'),
    'm2': ('DIM1/region', 'minecraft:the_end', 15, -4, '萌眼星'),
}

AIR = 'minecraft:air'

# 预览用颜色表（MC 方块名 -> RGB）
COLORS = {
    'minecraft:bedrock': (62, 62, 68),
    'minecraft:water': (54, 108, 200),
    'minecraft:lava': (255, 120, 26),
    'minecraft:grass_block': (86, 160, 72),
    'minecraft:dirt': (134, 96, 67),
    'minecraft:stone': (128, 128, 130),
    'minecraft:sand': (222, 208, 156),
    'minecraft:red_sand': (196, 116, 60),
    'minecraft:sandstone': (216, 202, 155),
    'minecraft:clay': (160, 166, 179),
    'minecraft:netherrack': (110, 62, 62),
    'minecraft:cobblestone': (120, 120, 120),
    'minecraft:mossy_cobblestone': (104, 122, 100),
    'minecraft:stone_bricks': (122, 122, 122),
    'minecraft:obsidian': (40, 30, 60),
    'minecraft:grass': (120, 190, 92),
    'minecraft:tall_grass': (110, 180, 84),
    'minecraft:fern': (100, 170, 80),
    'minecraft:dead_bush': (150, 130, 70),
    'minecraft:oak_leaves': (58, 126, 56),
    'minecraft:spruce_leaves': (46, 96, 60),
    'minecraft:birch_leaves': (112, 160, 76),
    'minecraft:jungle_leaves': (60, 140, 50),
    'minecraft:acacia_leaves': (108, 140, 58),
    'minecraft:dark_oak_leaves': (48, 100, 44),
    'minecraft:cherry_leaves': (226, 160, 196),
    'minecraft:oak_log': (110, 86, 52),
    'minecraft:spruce_log': (78, 62, 40),
    'minecraft:birch_log': (216, 214, 200),
    'minecraft:jungle_log': (96, 76, 44),
    'minecraft:acacia_log': (124, 96, 56),
    'minecraft:dark_oak_log': (70, 52, 32),
    'minecraft:cherry_log': (120, 74, 74),
    'minecraft:oak_planks': (162, 130, 78),
    'minecraft:white_concrete': (232, 236, 236),
    'minecraft:black_concrete': (30, 32, 36),
    'minecraft:red_concrete': (160, 40, 36),
    'minecraft:yellow_concrete': (240, 200, 60),
    'minecraft:green_concrete': (72, 110, 40),
    'minecraft:blue_concrete': (44, 60, 160),
    'minecraft:glowstone': (250, 220, 130),
    'minecraft:coal_block': (34, 34, 34),
    'minecraft:iron_block': (216, 216, 216),
    'minecraft:diamond_block': (120, 220, 220),
    'minecraft:glass': (200, 230, 240),
    'minecraft:coal_ore': (96, 96, 100),
    'minecraft:iron_ore': (190, 150, 120),
    'minecraft:emerald_ore': (90, 190, 120),
    'minecraft:gravel': (136, 128, 124),
    'minecraft:lily_pad': (70, 150, 80),
    'minecraft:seagrass': (60, 150, 110),
    'minecraft:kelp': (70, 140, 100),
    'minecraft:bamboo': (150, 190, 90),
    'minecraft:sugar_cane': (160, 200, 120),
    'minecraft:cactus': (80, 150, 80),
    'minecraft:melon': (120, 170, 70),
    'minecraft:vine': (70, 120, 60),
    'minecraft:sea_pickle': (110, 150, 90),
    'minecraft:brain_coral': (230, 120, 190),
    'minecraft:tube_coral_block': (60, 90, 220),
    'minecraft:fire_coral_block': (200, 60, 60),
    'minecraft:bubble_coral_block': (180, 70, 200),
    'minecraft:ochre_froglight': (230, 210, 140),
    'minecraft:glow_lichen': (120, 170, 140),
    'minecraft:mud_bricks': (130, 100, 80),
    'minecraft:terracotta': (150, 96, 74),
    'minecraft:dirt_path': (160, 130, 80),
    'minecraft:moss_block': (90, 130, 70),
    'minecraft:white_stained_glass': (220, 235, 240),
    'minecraft:end_rod': (230, 230, 210),
    'minecraft:beehive': (180, 140, 70),
    'minecraft:barrel': (140, 110, 70),
    'minecraft:chest': (150, 120, 70),
    'minecraft:lantern': (230, 200, 120),
    'minecraft:pink_tulip': (230, 160, 200),
    'minecraft:orange_concrete': (240, 130, 20),
    'minecraft:light_blue_concrete': (40, 140, 200),
    'minecraft:lime_concrete': (90, 190, 40),
    'minecraft:pink_concrete': (235, 160, 170),
    'minecraft:gray_concrete': (80, 80, 86),
    'minecraft:light_gray_concrete': (160, 160, 156),
    'minecraft:cyan_concrete': (20, 120, 140),
    'minecraft:purple_concrete': (120, 50, 180),
    'minecraft:brown_concrete': (120, 75, 45),
    'minecraft:allium': (200, 150, 220),
    'minecraft:azure_bluet': (220, 225, 235),
    'minecraft:blue_orchid': (70, 170, 200),
    'minecraft:cornflower': (90, 120, 220),
    'minecraft:dandelion': (240, 210, 60),
    'minecraft:white_tulip': (240, 240, 240),
    'minecraft:orange_tulip': (235, 150, 60),
    'minecraft:rose_bush': (200, 60, 70),
    'minecraft:wither_rose': (60, 50, 50),
    'minecraft:red_mushroom': (200, 70, 60),
    'minecraft:cocoa': (150, 95, 50),
    'minecraft:sweet_berry_bush': (110, 140, 70),
    'minecraft:wheat': (210, 190, 110),
}
COLOR_FALLBACK = (150, 150, 120)
COLOR_EMPTY = (10, 10, 12)


# ============================================================ 1. .r 读取
import lzma
import struct


def _u32(b, o):
    return struct.unpack('<I', b[o:o + 4])[0]


def _i32(b, o):
    return struct.unpack('<i', b[o:o + 4])[0]


def _u16(b, o):
    return struct.unpack('<H', b[o:o + 2])[0]


def fb_fields(b, table):
    """FlatBuffer 表 -> 槽位偏移数组（fields[slot]，缺省为 0）"""
    vtable = table - _i32(b, table)
    vlen = _u16(b, vtable)
    n = (vlen - 4) // 2
    return [_u16(b, vtable + 4 + 2 * i) for i in range(n)]


def fb_vector(b, field_off):
    """field_off 指向 uoffset，返回 (元素起始偏移, 元素个数)"""
    p = field_off + _u32(b, field_off)
    return p + 4, _u32(b, p)


def lzma_raw_decompress(payload):
    """payload = [1B props][4B dictSize][raw LZMA1]"""
    prop = payload[0]
    if prop >= 225:
        raise ValueError('bad lzma props byte 0x%02x' % prop)
    lc = prop % 9
    lp = (prop // 9) % 5
    pb = prop // 45
    dict_size = struct.unpack('<I', payload[1:5])[0]
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW,
                                filters=[{'id': lzma.FILTER_LZMA1, 'lc': lc, 'lp': lp,
                                          'pb': pb, 'dict_size': dict_size}])
    return dec.decompress(payload[5:])


def vidx(x, y_local, z):
    """段内下标（转换/读取用）：(yLocal<<8) | (x<<4) | z

    与 Minecraft Anvil 的 block_states 顺序一致，所以写 .mca 时可以原样倒过去。
    这是已经核对过的版本（产物在游戏里朝向正确），不要改。
    """
    return (y_local << 8) | (x << 4) | z


def pidx(x, y_local, z):
    """预览取样下标（仅出图用）：(yLocal<<8) | (z<<4) | x

    它和 vidx 在 16×16 平面内互为转置。用 vidx 直接铺成图时，每个区块的图案是被
    转置的，拼起来就会像"每个区块各自转了 90°、接缝错位"；出图时按 pidx 取样，
    图像即连续。**只影响缩略图，.mca 仍用 vidx 原样写。**
    """
    return (y_local << 8) | (z << 4) | x


class Section(object):
    """一个 16³ 段。ids/extra 下标 i = (yLocal<<8)|(x<<4)|z，见 vidx()"""
    __slots__ = ('segment', 'band', 'ids', 'extra', 'bitmap_a', 'bitmap_b',
                 'scalar_a', 'scalar_b')

    def __init__(self, segment, ids, extra, bitmap_a, bitmap_b, scalar_a, scalar_b):
        self.segment = segment
        self.band = segment
        self.ids = ids
        self.extra = extra
        self.bitmap_a = bitmap_a
        self.bitmap_b = bitmap_b
        self.scalar_a = scalar_a
        self.scalar_b = scalar_b

    def get(self, x, y_local, z):
        return self.ids[vidx(x, y_local, z)]


class Chunk(object):
    def __init__(self, index, compressed_size, tag, raw):
        self.index = index
        self.compressed_size = compressed_size
        self.tag = tag
        self.raw = raw
        self.root_fields = []
        self.sections = {}
        self._parse()

    def _parse(self):
        b = self.raw
        root = _u32(b, 0)
        fields = fb_fields(b, root)
        self.root_fields = fields
        if len(fields) < 2 or not fields[1]:
            return
        start, count = fb_vector(b, root + fields[1])
        for i in range(count):
            tbl = start + 4 * i + _u32(b, start + 4 * i)
            fs = fb_fields(b, tbl)
            if len(fs) < 3 or not fs[1] or not fs[2]:
                continue
            segment = b[tbl + fs[0]] if fs[0] else 0
            p1 = tbl + fs[1]
            vs, n1 = fb_vector(b, p1)
            ids = struct.unpack('<%dH' % n1, b[vs:vs + n1 * 2])
            p2 = tbl + fs[2]
            vs2, n2 = fb_vector(b, p2)
            extra = b[vs2:vs2 + n2]
            bit_a = bit_b = None
            s_a = s_b = None
            if len(fs) > 3 and fs[3]:
                p = tbl + fs[3]
                va, na = fb_vector(b, p)
                bit_a = b[va:va + na * 4]
            if len(fs) > 4 and fs[4]:
                p = tbl + fs[4]
                vb, nb = fb_vector(b, p)
                bit_b = b[vb:vb + nb * 4]
            if len(fs) > 5 and fs[5]:
                s_a = _u16(b, tbl + fs[5])
            if len(fs) > 6 and fs[6]:
                s_b = _u16(b, tbl + fs[6])
            self.sections[segment] = Section(segment, ids, extra, bit_a, bit_b, s_a, s_b)

    def block(self, x, y, z):
        """区块内坐标 -> (原始 u16 值, extra)"""
        band, yl = divmod(y, 16)
        sec = self.sections.get(band)
        if sec is None:
            return 0, 0
        i = vidx(x, yl, z)
        return sec.ids[i], sec.extra[i]

    def max_y(self):
        top = -1
        for band, sec in self.sections.items():
            for yl in range(16):
                base = yl << 8
                if any(sec.ids[base:base + 256]):
                    top = max(top, band * 16 + yl)
        return top


class RegionFile(object):
    """.r = 1024 项索引表 + 1024 项时间戳表 + 数据区，按 4096 对齐"""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.data = f.read()
        if len(self.data) % SECTOR:
            raise ValueError('size not multiple of 4096: %s' % path)
        self.n_sectors = len(self.data) // SECTOR
        self.table = list(struct.unpack('<1024I', self.data[:SECTOR]))
        self._index = self._decode_index()

    def _decode_index(self):
        out = {}
        for i, e in enumerate(self.table):
            if not e:
                continue
            off = (e & 0x7FFFFF) ^ KEYS[i & 0xF]
            out[i] = (off, (e >> 24) & 0xFF)
        return out

    def chunk_indexes(self):
        return sorted(self._index)

    def read_chunk(self, index):
        off = self._index[index][0]
        base = off * SECTOR
        size, tag = struct.unpack('<II', self.data[base:base + 8])
        payload = self.data[base + 8:base + 8 + size]
        ctype = tag >> 28
        raw_expect = tag & 0x0FFFFFFF
        if ctype != 2:
            raise ValueError('unsupported compress type %d (chunk %d)' % (ctype, index))
        out = lzma_raw_decompress(payload)
        if len(out) > raw_expect:
            out = out[:raw_expect]
        return Chunk(index, size, tag, out)

    def chunks(self):
        for i in self.chunk_indexes():
            yield i, self.read_chunk(i)

    def chunk_xy(self, index):
        return index & 31, index >> 5


def parse_region_name(path):
    """x-1z-1.r -> (-1, -1)"""
    import os
    name = os.path.basename(path)
    if not name.startswith('x') or '.r' not in name:
        raise ValueError('bad region name %s' % name)
    body = name[1:name.index('.r')]
    zi = body.index('z', 1)
    return int(body[:zi]), int(body[zi + 1:])


# ============================================================ 2. 映射表
import collections
import json


MAP_SUFFIX = 'minecraft:'
MAP_FILENAME = 'mc_block_map.json'


def _clean_name(v):
    """把 JSON 里的名字规范成 minecraft:xxx；返回 (名字, 处理类型)"""
    s = str(v).strip().lower().replace(' ', '_')
    if s.startswith(MAP_SUFFIX):
        s = s[len(MAP_SUFFIX):]
    if not s:
        return None, 'empty'
    if s in DROP_TO_DIRT:
        return MAP_SUFFIX + 'dirt', 'dropped'
    if s in FIX:
        return MAP_SUFFIX + FIX[s], 'fixed'
    return MAP_SUFFIX + s, 'ok'


def load_map_json(path):
    """读 mc_block_map.json（唯一的映射来源）。

    容忍手写错误：去前缀/小写/空格转下划线、非法名落土块、超范围 id 丢弃；
    返回 (cfg, notes)，notes 记录被自动修掉的东西，给界面提示用。
    """
    with open(path, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError('映射表不是一个 JSON 对象：%s' % path)

    mask = int(raw.get('id_mask', ID_MAX) or ID_MAX)
    mapping = {}
    over, fixed, dropped, bad = [], [], [], []
    common = raw.get('common') or {}
    if not isinstance(common, dict):
        raise ValueError('映射表的 common 必须是一个 {id: 方块名} 对象')
    for k, v in common.items():
        try:
            i = int(k)
        except Exception:
            bad.append((k, v))
            continue
        if i < 0 or i > mask:
            over.append((i, v))
            continue
        nm, kind = _clean_name(v)
        if nm is None:
            bad.append((i, v))
            continue
        if kind == 'fixed':
            fixed.append((i, str(v), nm))
        elif kind == 'dropped':
            dropped.append((i, str(v)))
        mapping[i] = nm

    cfg = dict(raw)                       # 保留 _readme / notes / dims 等原始字段
    cfg['id_mask'] = mask
    cfg['fallback'] = _clean_name(raw.get('fallback') or 'minecraft:dirt')[0] or (MAP_SUFFIX + 'dirt')
    cfg['common'] = {str(i): v for i, v in sorted(mapping.items())}

    notes = {'raw': len(common), 'used': len(mapping), 'over': over,
             'fixed': fixed, 'dropped': dropped, 'bad': bad,
             'empty': len(mapping) == 0}
    return cfg, notes



class Mapper(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.mask = int(cfg.get('id_mask', 0xFFF))
        self.fallback = cfg.get('fallback', 'minecraft:dirt')
        self.dims = cfg.get('dims', {})
        self.common = cfg.get('common', {})

    def name_of(self, dim, raw_id):
        """返回 (方块名, 是否用了占位兜底)"""
        base = raw_id & self.mask
        if base == 0:
            return AIR, False
        key = str(base)
        m = self.dims.get(dim, {})
        if key in m:
            return m[key], False
        if key in self.common:
            return self.common[key], False
        if '*' in m:
            return m['*'], True
        return self.fallback, True


# ============================================================ 3. NBT 编码
TYPE_ID = {'B': 1, 'S': 2, 'I': 3, 'L': 4, 'F': 5, 'D': 6,
           'BA': 7, 'STR': 8, 'LST': 9, 'C': 10, 'IA': 11, 'LA': 12}


def B(v):
    return ('B', v)


def S(v):
    return ('S', v)


def I(v):
    return ('I', v)


def L(v):
    return ('L', v)


def STR(v):
    return ('STR', v)


def BA(v):
    return ('BA', v)


def IA(v):
    return ('IA', v)


def LA(v):
    return ('LA', v)


def LST(subtype, items):
    return ('LST', subtype, items)


def C(d):
    return ('C', d)


def _name(n):
    raw = n.encode('utf-8')
    return struct.pack('>H', len(raw)) + raw


def _payload(tag):
    kind = tag[0]
    if kind == 'C':
        return b''.join(_tag(n, t) for n, t in tag[1].items()) + b'\x00'
    if kind == 'B':
        return struct.pack('>b', tag[1])
    if kind == 'S':
        return struct.pack('>h', tag[1])
    if kind == 'I':
        return struct.pack('>i', tag[1])
    if kind == 'L':
        return struct.pack('>q', tag[1])
    if kind == 'F':
        return struct.pack('>f', tag[1])
    if kind == 'D':
        return struct.pack('>d', tag[1])
    if kind == 'STR':
        raw = tag[1].encode('utf-8')
        return struct.pack('>H', len(raw)) + raw
    if kind == 'BA':
        return struct.pack('>i', len(tag[1])) + bytes(tag[1])
    if kind == 'IA':
        return struct.pack('>i', len(tag[1])) + struct.pack('>%di' % len(tag[1]), *tag[1])
    if kind == 'LA':
        vals = [v - (1 << 64) if v >= (1 << 63) else v for v in tag[1]]
        return struct.pack('>i', len(vals)) + struct.pack('>%dq' % len(vals), *vals)
    if kind == 'LST':
        subtype, items = tag[1], tag[2]
        head = bytes([TYPE_ID[subtype]]) + struct.pack('>i', len(items))
        if subtype == 'C':
            return head + b''.join(_payload(('C', it)) for it in items)
        if subtype == 'LST':
            return head + b''.join(_payload(it) for it in items)
        return head + b''.join(_payload((subtype, it)) for it in items)
    raise ValueError('bad tag %r' % (tag,))


def _tag(name, tag):
    return bytes([TYPE_ID[tag[0]]]) + _name(name) + _payload(tag)


def nbt_bytes(root_dict):
    return _tag('', C(root_dict))


# ============================================================ 4. 位打包 / 区块构建
def pack_bits(values, bits, values_per_long=None):
    """MC 1.16+ 紧凑位打包：每个 long 内不跨值"""
    if values_per_long is None:
        values_per_long = 64 // bits
    longs = []
    cur = 0
    n = 0
    for v in values:
        cur |= (v & ((1 << bits) - 1)) << (n * bits)
        n += 1
        if n == values_per_long:
            longs.append(cur & 0xFFFFFFFFFFFFFFFF)
            cur = 0
            n = 0
    if n:
        longs.append(cur & 0xFFFFFFFFFFFFFFFF)
    return longs


def pack_heightmap(heights):
    """256 个 9 bit 高度 -> 37 个 long"""
    return pack_bits(heights, 9, 7)


def build_section(y, names):
    palette = []
    index = {}
    data_vals = []
    for nm in names:
        i = index.get(nm)
        if i is None:
            i = len(palette)
            index[nm] = i
            palette.append(nm)
        data_vals.append(i)
    bits = 4
    while (1 << bits) < len(palette):
        bits += 1
    bs = {'palette': LST('C', [{'Name': STR(nm)} for nm in palette])}
    if len(palette) > 1:
        bs['data'] = LA(pack_bits(data_vals, bits))
    return {'Y': B(y), 'block_states': C(bs)}


def build_chunk(xpos, zpos, sections, biome, data_version, ypos):
    import time as _t
    sec_list = []
    for y, names in sections:
        comp = build_section(y, names)
        comp['biomes'] = C({'palette': LST('STR', [biome])})
        sec_list.append(comp)

    surface = [0] * 256
    motion = [0] * 256
    for y, names in sections:
        base = y * 16
        for i, nm in enumerate(names):
            if nm == AIR or nm == 'minecraft:cave_air':
                continue
            col = ((i >> 4) & 0xF) * 16 + (i & 0xF)
            h = base + ((i >> 8) & 0xF) + 1
            if h > surface[col]:
                surface[col] = h
            if h > motion[col]:
                motion[col] = h

    root = {
        'DataVersion': I(data_version),
        'xPos': I(xpos),
        'yPos': I(ypos),
        'zPos': I(zpos),
        'Status': STR('minecraft:full'),
        'LastUpdate': L(int(_t.time() * 1000)),
        'InhabitedTime': L(0),
        'isLightOn': B(0),
        'sections': LST('C', sec_list),
        'block_entities': LST('C', []),
        'Heightmaps': C({
            'MOTION_BLOCKING': LA(pack_heightmap(motion)),
            'WORLD_SURFACE': LA(pack_heightmap(surface)),
        }),
        'Structures': C({'starts': C({}), 'References': C({})}),
    }
    return nbt_bytes(root)


def write_region(path, chunk_nbt):
    """chunk_nbt: [(chunk_index, nbt bytes)] -> [u32 长度][u8 类型=2][zlib] 按 4096 对齐"""
    import time as _t
    header = bytearray(8192)
    body = bytearray()
    now = int(_t.time())
    for idx, nbt in chunk_nbt:
        comp = zlib.compress(nbt, 6)
        payload = struct.pack('>I', len(comp) + 1) + b'\x02' + comp
        pad = (-len(payload)) % 4096
        sector = 2 + len(body) // 4096
        count = (len(payload) + pad) // 4096
        if count > 255:
            raise ValueError('chunk %d too big: %d sectors' % (idx, count))
        struct.pack_into('>I', header, idx * 4, (sector << 8) | count)
        struct.pack_into('>I', header, 4096 + idx * 4, now)
        body += payload + b'\x00' * pad
    with open(path, 'wb') as f:
        f.write(bytes(header))
        f.write(bytes(body))


# ============================================================ 5. 转换
def convert_region(rf, mapper, dim, data_version, max_band=15):
    """返回 ([(idx, nbt)], stats)"""
    x0, z0 = parse_region_name(rf.path)
    biome = DIM_INFO[dim][1]
    ypos = DIM_INFO[dim][3]
    out = []
    stats = {'chunks': 0, 'blocks': 0, 'unknown': 0, 'unknown_ids': collections.Counter()}
    for idx in rf.chunk_indexes():
        ch = rf.read_chunk(idx)
        cx, cz = ch.index & 31, ch.index >> 5
        sections = []
        for band in range(0, max_band + 1):
            sec = ch.sections.get(band)
            if sec is None:
                names = [AIR] * 4096
            else:
                cache = {}
                names = []
                # 4096 个 id 原样按顺序写进 block_states（.r 与 MC 的段内顺序一致）
                for v in sec.ids:
                    hit = cache.get(v)
                    if hit is None:
                        hit = mapper.name_of(dim, v)
                        cache[v] = hit
                        if hit[1]:
                            stats['unknown_ids'][v & mapper.mask] += 1
                    if hit[1]:
                        stats['unknown'] += 1
                    names.append(hit[0])
                stats['blocks'] += sum(1 for v in sec.ids if v)
            if any(n != AIR for n in names) or band == 0:
                sections.append((band, names))
        out.append((idx, build_chunk(x0 * 32 + cx, z0 * 32 + cz, sections,
                                     biome, data_version, ypos)))
        stats['chunks'] += 1
    return out, stats


def list_regions(save_dir, dim, only=None):
    """返回该维度下的 .r 文件名列表（可按 x0z0 过滤）"""
    src = os.path.join(save_dir, dim)
    if not os.path.isdir(src):
        return [], src
    files = sorted(f for f in os.listdir(src) if f.endswith('.r'))
    if only:
        files = [f for f in files if f[:-2] == only]
    return files, src


# ============================================================ 6. 回读校验（独立 NBT 解析）
class NbtReader(object):
    def __init__(self, data):
        self.b = data
        self.p = 0

    def u1(self):
        v = self.b[self.p]
        self.p += 1
        return v

    def i1(self):
        v = struct.unpack('>b', self.b[self.p:self.p + 1])[0]
        self.p += 1
        return v

    def i2(self):
        v = struct.unpack('>h', self.b[self.p:self.p + 2])[0]
        self.p += 2
        return v

    def u2(self):
        v = struct.unpack('>H', self.b[self.p:self.p + 2])[0]
        self.p += 2
        return v

    def i4(self):
        v = struct.unpack('>i', self.b[self.p:self.p + 4])[0]
        self.p += 4
        return v

    def i8(self):
        v = struct.unpack('>q', self.b[self.p:self.p + 8])[0]
        self.p += 8
        return v

    def f4(self):
        v = struct.unpack('>f', self.b[self.p:self.p + 4])[0]
        self.p += 4
        return v

    def f8(self):
        v = struct.unpack('>d', self.b[self.p:self.p + 8])[0]
        self.p += 8
        return v

    def string(self):
        n = self.u2()
        s = self.b[self.p:self.p + n].decode('utf-8', 'replace')
        self.p += n
        return s

    def payload(self, t, depth=0):
        if t == 1:
            return self.i1()
        if t == 2:
            return self.i2()
        if t == 3:
            return self.i4()
        if t == 4:
            return self.i8()
        if t == 5:
            return self.f4()
        if t == 6:
            return self.f8()
        if t == 7:
            n = self.i4()
            v = self.b[self.p:self.p + n]
            self.p += n
            return v
        if t == 8:
            return self.string()
        if t == 9:
            sub = self.u1()
            n = self.i4()
            items = []
            for _ in range(n):
                items.append(self.compound(depth + 1) if sub == 10 else self.payload(sub, depth + 1))
            return ('LIST', sub, items)
        if t == 10:
            return self.compound(depth + 1)
        if t == 11:
            n = self.i4()
            return ('IA', [self.i4() for _ in range(n)])
        if t == 12:
            n = self.i4()
            return ('LA', [self.i8() for _ in range(n)])
        raise ValueError('bad tag type %d at %d' % (t, self.p))

    def compound(self, depth=0):
        d = {}
        while True:
            t = self.u1()
            if t == 0:
                return d
            name = self.string()
            d[name] = self.payload(t, depth)

    def root(self):
        t = self.u1()
        self.string()
        return self.payload(t)


def unpack_bits(longs, bits, count=4096):
    per = 64 // bits
    mask = (1 << bits) - 1
    vals = []
    for lo in longs:
        u = lo & 0xFFFFFFFFFFFFFFFF
        for k in range(per):
            vals.append((u >> (k * bits)) & mask)
            if len(vals) == count:
                return vals
    return vals


def decode_section(comp):
    y = comp['Y']
    bs = comp['block_states']
    palette = [e['Name'] for e in bs['palette'][2]]
    if len(palette) == 1:
        return y, [palette[0]] * 4096
    data = bs['data'][1]
    bits = 4
    while (1 << bits) < len(palette):
        bits += 1
    return y, [palette[i] for i in unpack_bits(data, bits)]


def read_mca(path):
    """返回 {chunk_index: {section_y: [4096 个名字]}}"""
    import zlib
    with open(path, 'rb') as f:
        data = f.read()
    out = {}
    for idx in range(1024):
        if idx * 4 + 4 > len(data):
            break
        loc = struct.unpack('>I', data[idx * 4:idx * 4 + 4])[0]
        if not loc:
            continue
        off = (loc >> 8) * 4096
        length = struct.unpack('>I', data[off:off + 4])[0]
        ctype = data[off + 4]
        payload = data[off + 5:off + 4 + length]
        raw = payload if ctype == 3 else zlib.decompress(payload)
        root = NbtReader(raw).root()
        secs = {}
        for comp in root['sections'][2]:
            y, names = decode_section(comp)
            secs[y] = names
        out[idx] = secs
    return out


def verify_region(src_path, mca_path, mapper, dim):
    """返回 (对比方块数, 不一致数, 前几条差异)"""
    import os
    if not os.path.exists(mca_path):
        return 0, -1, ['缺少目标文件: %s' % mca_path]
    rf = RegionFile(src_path)
    mca = read_mca(mca_path)
    bad = 0
    total = 0
    diffs = []
    for idx in rf.chunk_indexes():
        ch = rf.read_chunk(idx)
        secs = mca.get(idx)
        if secs is None:
            bad += 4096 * 16
            diffs.append('chunk %d 缺失' % idx)
            continue
        for band in range(0, 16):
            src_sec = ch.sections.get(band)
            dst = secs.get(band) or [AIR] * 4096
            for i in range(4096):
                v = src_sec.ids[i] if src_sec else 0
                want = mapper.name_of(dim, v)[0] if v else AIR
                total += 1
                if dst[i] != want:
                    bad += 1
                    if len(diffs) < 5:
                        diffs.append('chunk %d band %d i %d (x%d z%d): 得到 %s 期望 %s'
                                     % (idx, band, i, (i >> 4) & 0xF, i & 0xF, dst[i], want))
    return total, bad, diffs


# ============================================================ 7. 覆盖率统计
def coverage(save_dir, dim, files, src, mapper, on_progress=None, cancel=None):
    """快速统计：每个 section 用 Counter 聚合后按倍率累加，不做逐方块循环"""
    tot = 0
    miss = 0
    miss_ids = collections.Counter()
    names = collections.Counter()
    for n, fn in enumerate(files):
        if cancel is not None and cancel.is_set():
            break
        rf = RegionFile(os.path.join(src, fn))
        for idx in rf.chunk_indexes():
            ch = rf.read_chunk(idx)
            for band, sec in ch.sections.items():
                c = collections.Counter(sec.ids)
                c.pop(0, None)
                for v, cnt in c.items():
                    nm, guessed = mapper.name_of(dim, v)
                    tot += cnt
                    if guessed:
                        miss += cnt
                        miss_ids[v & mapper.mask] += cnt
                    else:
                        names[nm] += cnt
        if on_progress:
            on_progress(n + 1, len(files))
    return {'total': tot, 'miss': miss, 'miss_ids': miss_ids, 'names': names}


# ============================================================ 8. 预览渲染
def render_preview(save_dir, dim, files, src, mapper, out_png, on_progress=None, cancel=None):
    from PIL import Image
    import os
    chunks = []
    xs = []
    zs = []
    for fn in files:
        f = os.path.join(src, fn)
        rf = RegionFile(f)
        a, b = parse_region_name(f)
        for idx in rf.chunk_indexes():
            cx, cz = idx & 31, idx >> 5
            gx, gz = a * 32 + cx, b * 32 + cz
            chunks.append((rf, idx, gx, gz))
            xs.append(gx)
            zs.append(gz)
    if not chunks:
        raise ValueError('没有可用区块')
    minx, maxx = min(xs), max(xs)
    minz, maxz = min(zs), max(zs)
    W = (maxx - minx + 1) * 16
    H = (maxz - minz + 1) * 16
    img = Image.new('RGB', (W, H), (24, 24, 28))
    px = img.load()
    hmin, hmax = 999, -999
    for n, (rf, idx, gx, gz) in enumerate(chunks):
        if cancel is not None and cancel.is_set():
            break
        ch = rf.read_chunk(idx)
        ox = (gx - minx) * 16
        oz = (gz - minz) * 16
        for x in range(16):
            for z in range(16):
                top_v = 0
                top_y = 0
                for band in sorted(ch.sections, reverse=True):
                    sec = ch.sections[band]
                    for yl in range(15, -1, -1):
                        v = sec.ids[pidx(x, yl, z)]
                        if v:
                            top_v = v
                            top_y = band * 16 + yl
                            break
                    if top_v:
                        break
                if not top_v:
                    px[ox + x, oz + z] = COLOR_EMPTY
                    continue
                hmin = min(hmin, top_y)
                hmax = max(hmax, top_y)
                c = COLORS.get(mapper.name_of(dim, top_v)[0], COLOR_FALLBACK)
                k = 0.55 + 0.45 * min(1.0, top_y / 90.0)
                px[ox + x, oz + z] = (int(c[0] * k), int(c[1] * k), int(c[2] * k))
        if on_progress:
            on_progress(n + 1, len(chunks))
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    img.save(out_png)
    return {'w': W, 'h': H, 'chunks': len(chunks), 'ymin': hmin, 'ymax': hmax}


# ============================================================ 9. 任务实现（与界面无关）
APP_TITLE = '迷你世界 .r → MC .mca 转换器'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)


def _find_upward(name):
    """按「当前目录 → 脚本目录 → 项目根目录」的顺序找一个文件/目录，返回第一个存在的绝对路径。

    优先当前工作目录：把工具（或它的快捷方式）和 mc_block_map.json 放在同一个文件夹里，
    直接双击/命令行运行时就能读到，不用改路径。
    """
    cands = [os.path.join(os.getcwd(), name),
             os.path.join(SCRIPT_DIR, name),
             os.path.join(ROOT_DIR, name)]
    for c in cands:
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(cands[0])


def _default_paths():
    return (os.path.join(ROOT_DIR, 'w85624992381678'),
            _find_upward(MAP_FILENAME),
            os.path.join(ROOT_DIR, 'out', 'world'))


class Ctx(object):
    """把「日志/状态/进度/取消/预览」从界面里抽出来，原生窗口和网页界面共用同一套任务代码"""

    def __init__(self, log, status, progress, cancel=None, preview=None):
        self._log = log
        self._status = status
        self._progress = progress
        self.cancel = cancel if cancel is not None else threading.Event()
        self._preview = preview

    def log(self, msg):
        self._log(msg)

    def status(self, msg):
        self._status(msg)

    def progress(self, done, total):
        self._progress(done, total)

    def preview(self, path, dim, info):
        if self._preview:
            self._preview(path, dim, info)


def _build_mapper(path):
    if not os.path.isfile(path):
        raise ValueError('找不到映射表 JSON：%s\n（把 mc_block_map.json 放到当前目录、本文件所在目录或项目根目录即可）'
                         % path)
    cfg, notes = load_map_json(path)
    if notes['empty']:
        raise ValueError('映射表里没有任何可用的方块项（common 为空），检查 %s' % path)
    return Mapper(cfg), cfg, notes


def _dims(p):
    ds = [d for d in (p.get('dims') or []) if d in ('m0', 'm1', 'm2')]
    return ds or ['m0', 'm1', 'm2']


def _dv(p):
    try:
        return int(str(p.get('dv') or '3465').strip())
    except Exception:
        return 3465


def _check_save(p):
    save = (p.get('save') or '').strip()
    if not os.path.isdir(save):
        raise ValueError('存档目录不存在：%s' % save)
    return save


def op_mapcheck(ctx, p):
    """① 校验映射表：读 JSON、统计生效项、列出自动修正/被丢弃的条目（不写文件）"""
    path = (p.get('map') or '').strip()
    mapper, cfg, notes = _build_mapper(path)
    ctx.log('映射表：%s' % path)
    ctx.log('条目 %d → 生效 %d 项（id_mask=0x%X，fallback=%s）'
            % (notes['raw'], notes['used'], mapper.mask, mapper.fallback))
    if notes['used']:
        ids = sorted(int(k) for k in cfg['common'])
        ctx.log('id 范围 %d .. %d' % (ids[0], ids[-1]))
    if notes['fixed']:
        ctx.log('名字自动规范化 %d 条：%s' % (len(notes['fixed']),
                                            '; '.join('%s:%s→%s' % f for f in notes['fixed'])))
    if notes['dropped']:
        ctx.log('物品名已落土块 %d 条：%s' % (len(notes['dropped']),
                                            '; '.join('%s=%s' % d for d in notes['dropped'])))
    if notes['over']:
        ctx.log('超出 id 上限被忽略 %d 条：%s' % (len(notes['over']),
                                               ', '.join(str(o[0]) for o in notes['over'])))
    if notes['bad']:
        ctx.log('无法解析的条目 %d 条：%s' % (len(notes['bad']), notes['bad'][:5]))
    ctx.log('表里没有的 id 一律落 %s（土块占位）。想补：编辑 %s 的 common，或打开映射表编辑器。'
            % (mapper.fallback, MAP_FILENAME))
    ctx.progress(1, 1)


def op_convert(ctx, p):
    mapper, cfg, notes = _build_mapper((p.get('map') or '').strip())
    ctx.log('映射表：%d 项生效（源 %d 项）' % (notes['used'], notes['raw']))
    save = _check_save(p)
    out = (p.get('out') or '').strip()
    if not out:
        raise ValueError('输出目录没填')
    dv = _dv(p)
    only = (p.get('only') or '').strip() or None

    plan = []
    for dim in _dims(p):
        files, src = list_regions(save, dim, only)
        if not os.path.isdir(src):
            ctx.log('跳过：存档里没有 %s 目录' % dim)
            continue
        if not files:
            ctx.log('跳过：%s 下没有匹配的 .r' % dim)
            continue
        plan.append((dim, src, files))
    total_files = sum(len(x[2]) for x in plan)
    if not total_files:
        raise ValueError('没有可转换的 .r 文件')
    ctx.log('计划：%d 个文件，DataVersion=%d' % (total_files, dv))

    agg = {'chunks': 0, 'blocks': 0, 'unknown': 0}
    unknown_ids = collections.Counter()
    done = 0
    t0 = time.time()
    for dim, src, files in plan:
        dst = os.path.join(out, DIM_INFO[dim][0])
        os.makedirs(dst, exist_ok=True)
        for fn in files:
            if ctx.cancel.is_set():
                ctx.log('已取消。')
                return
            rf = RegionFile(os.path.join(src, fn))
            chunk_nbt, st = convert_region(rf, mapper, dim, dv)
            rx, rz = parse_region_name(rf.path)
            out_path = os.path.join(dst, 'r.%d.%d.mca' % (rx, rz))
            write_region(out_path, chunk_nbt)
            for k in agg:
                agg[k] += st[k]
            unknown_ids.update(st['unknown_ids'])
            done += 1
            pct = 100.0 * st['unknown'] / max(1, st['blocks'])
            ctx.log('%-22s → %-26s 区块 %-4d 方块 %-10d 土块占位 %.2f%%'
                    % (dim + '/' + fn, os.path.relpath(out_path, out),
                       st['chunks'], st['blocks'], pct))
            ctx.progress(done, total_files)
            ctx.status('转换中 %d/%d' % (done, total_files))

    pct = 100.0 * agg['unknown'] / max(1, agg['blocks'])
    ctx.log('')
    ctx.log('合计：区块 %d，非空方块 %d，落土块 %d（%.2f%%），用时 %.1f s'
            % (agg['chunks'], agg['blocks'], agg['unknown'], pct, time.time() - t0))
    if unknown_ids:
        ctx.log('未收录 id（按段计）Top：%s'
                % ', '.join('%d:%d' % kv for kv in unknown_ids.most_common(12)))
    ctx.log('输出目录：%s' % out)
    if pct > 20:
        ctx.log('提示：土块占比偏高，多半是某个维度的主体方块 id 不在表里（看上面 Top 列表），'
                '在 mc_block_map.json 的 common 里补一条 "<id>": "minecraft:<名字>"（或用映射表编辑器），'
                '再重跑「转换」即可。')


def op_coverage(ctx, p):
    mapper, cfg, notes = _build_mapper((p.get('map') or '').strip())
    ctx.log('映射表：%d 项生效' % notes['used'])
    save = _check_save(p)
    only = (p.get('only') or '').strip() or None
    grand_total = grand_miss = 0
    for dim in _dims(p):
        files, src = list_regions(save, dim, only)
        if not files:
            ctx.log('跳过：%s 没有 .r' % dim)
            continue
        ctx.log('统计 %s（%d 个文件）…' % (dim, len(files)))
        res = coverage(save, dim, files, src, mapper,
                       on_progress=lambda d, t: ctx.progress(d, t), cancel=ctx.cancel)
        pct = 100.0 * res['miss'] / max(1, res['total'])
        ctx.log('  %s 方块 %d，落土块 %d（%.2f%%）' % (dim, res['total'], res['miss'], pct))
        if res['miss_ids']:
            ctx.log('  未收录 id Top：%s'
                    % ', '.join('%d(%d,%.2f%%)' % (k, v, 100.0 * v / max(1, res['total']))
                                for k, v in res['miss_ids'].most_common(10)))
        ctx.log('  映射命中 Top：%s'
                % ', '.join('%s(%d)' % (k.replace('minecraft:', ''), v)
                            for k, v in res['names'].most_common(8)))
        grand_total += res['total']
        grand_miss += res['miss']
    if grand_total:
        ctx.log('合计 %d 方块，落土块 %d（%.2f%%）'
                % (grand_total, grand_miss, 100.0 * grand_miss / grand_total))


def op_preview(ctx, p):
    mapper, cfg, notes = _build_mapper((p.get('map') or '').strip())
    save = _check_save(p)
    only = (p.get('only') or '').strip() or None
    outdir = (p.get('outdir') or '').strip() or os.path.join(ROOT_DIR, 'docs')
    for dim in _dims(p):
        files, src = list_regions(save, dim, only)
        if not files:
            ctx.log('跳过：%s 没有 .r' % dim)
            continue
        png = os.path.join(outdir, 'preview_%s.png' % dim)
        ctx.log('渲染 %s …' % dim)
        info = render_preview(save, dim, files, src, mapper, png,
                              on_progress=lambda d, t: ctx.progress(d, t), cancel=ctx.cancel)
        ctx.log('  %s：%dx%d，%d 区块，地表高度 %d..%d → %s'
                % (dim, info['w'], info['h'], info['chunks'],
                   info['ymin'], info['ymax'], png))
        ctx.preview(png, dim, info)


def op_verify(ctx, p):
    mapper, cfg, notes = _build_mapper((p.get('map') or '').strip())
    save = _check_save(p)
    mca_root = (p.get('out') or '').strip()
    if not os.path.isdir(mca_root):
        raise ValueError('输出目录不存在：%s' % mca_root)
    only = (p.get('only') or '').strip() or None
    all_flag = bool(p.get('all'))
    overall_bad = 0
    overall_total = 0
    for dim in _dims(p):
        files, src = list_regions(save, dim, only)
        if not files:
            ctx.log('跳过：%s 没有 .r' % dim)
            continue
        pick = files if all_flag else files[:1]
        ctx.log('校验 %s：%d 个文件' % (dim, len(pick)))
        for n, fn in enumerate(pick):
            if ctx.cancel.is_set():
                ctx.log('已取消。')
                return
            rf_path = os.path.join(src, fn)
            rx, rz = parse_region_name(rf_path)
            mca = os.path.join(mca_root, DIM_INFO[dim][0], 'r.%d.%d.mca' % (rx, rz))
            total, bad, diffs = verify_region(rf_path, mca, mapper, dim)
            overall_total += max(0, total)
            if bad < 0:
                ctx.log('  %s/%s：%s' % (dim, fn, diffs[0]))
                overall_bad += 1
            else:
                overall_bad += bad
                ctx.log('  %s/%s：对比 %d 块，不一致 %d %s'
                        % (dim, fn, total, bad, 'PASS' if bad == 0 else 'FAIL'))
                for d in diffs:
                    ctx.log('     ' + d)
            ctx.progress(n + 1, len(pick))
    ctx.log('')
    ctx.log('总计对比 %d 块，不一致 %d → %s'
            % (overall_total, overall_bad, 'PASS' if overall_bad == 0 else 'FAIL'))


OPS = {'mapcheck': op_mapcheck, 'convert': op_convert, 'coverage': op_coverage,
       'preview': op_preview, 'verify': op_verify}
OP_NAMES = {'mapcheck': '校验映射表', 'convert': '转换', 'coverage': '统计覆盖率',
            'preview': '生成预览图', 'verify': '回读校验'}


def run_op(op, params, ctx):
    """同步执行一个操作；异常抛给调用方"""
    fn = OPS.get(op)
    if fn is None:
        raise ValueError('未知操作：%s' % op)
    fn(ctx, params)


# ============================================================ 10. 原生窗口（tkinter）
def tk_available():
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        return True
    except Exception:
        return False


class TkApp(object):
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        self.root = root
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.preview_img = None

        root.title(APP_TITLE)
        root.geometry('1190x790')
        root.minsize(1000, 640)

        style = ttk.Style()
        for th in ('vista', 'clam'):
            try:
                style.theme_use(th)
                break
            except Exception:
                continue
        style.configure('TLabelframe.Label', font=('微软雅黑', 9, 'bold'))
        for k in ('TButton', 'TCheckbutton', 'TLabel', 'TRadiobutton'):
            style.configure(k, font=('微软雅黑', 9))

        topf = ttk.Frame(root, padding=(12, 10, 12, 2))
        topf.pack(fill='x')
        ttk.Label(topf, text='迷你世界存档 → Minecraft Java 世界',
                  font=('微软雅黑', 15, 'bold')).pack(side='left')
        ttk.Label(topf, text='  读取 .r → 映射方块 → 写出 .mca',
                  foreground='#666').pack(side='left', pady=(5, 0))

        body = ttk.Frame(root, padding=(12, 6, 12, 10))
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=3, minsize=650)
        body.columnconfigure(1, weight=1, minsize=340)
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        right = ttk.LabelFrame(body, text='预览', padding=6)
        right.grid(row=0, column=1, sticky='nsew')

        save0, map0, out0 = _default_paths()
        pf = ttk.LabelFrame(left, text='路径', padding=8)
        pf.pack(fill='x')
        pf.columnconfigure(1, weight=1)
        self.v_save = tk.StringVar(value=save0)
        self.v_map = tk.StringVar(value=map0)
        self.v_out = tk.StringVar(value=out0)
        self._row(pf, 0, '存档目录', self.v_save, 'dir')
        self._row(pf, 1, '映射表 JSON', self.v_map, 'json')
        self._row(pf, 2, '输出目录', self.v_out, 'dir')

        of = ttk.LabelFrame(left, text='选项', padding=8)
        of.pack(fill='x', pady=(8, 0))
        df = ttk.Frame(of)
        df.pack(fill='x')
        ttk.Label(df, text='维度：').pack(side='left')
        self.v_dim = {}
        for d in ('m0', 'm1', 'm2'):
            v = tk.BooleanVar(value=True)
            self.v_dim[d] = v
            ttk.Checkbutton(df, text='%s %s' % (d, DIM_INFO[d][4]),
                            variable=v).pack(side='left', padx=(0, 8))
        ttk.Label(df, text='  DataVersion').pack(side='left')
        self.v_dv = tk.StringVar(value='3465')
        ttk.Entry(df, textvariable=self.v_dv, width=8).pack(side='left')
        ttk.Label(df, text='(1.20.1=3465)', foreground='#666').pack(side='left', padx=(4, 0))

        df2 = ttk.Frame(of)
        df2.pack(fill='x', pady=(6, 0))
        ttk.Label(df2, text='只处理指定区块文件：').pack(side='left')
        self.v_only = tk.StringVar(value='')
        ttk.Entry(df2, textvariable=self.v_only, width=14).pack(side='left')
        ttk.Label(df2, text='（留空=全部，如 x0z0）', foreground='#666').pack(side='left', padx=(4, 0))
        self.v_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(df2, text='校验全部区块（慢）', variable=self.v_all).pack(side='right')

        af = ttk.LabelFrame(left, text='操作', padding=8)
        af.pack(fill='x', pady=(8, 0))
        bf = ttk.Frame(af)
        bf.pack(fill='x')
        bf.columnconfigure(0, weight=1)
        bf.columnconfigure(1, weight=1)
        specs = [('① 校验映射表', 'mapcheck', 0, 0), ('② 转换 .r → .mca', 'convert', 0, 1),
                 ('③ 统计覆盖率', 'coverage', 1, 0), ('④ 生成预览图', 'preview', 1, 1),
                 ('⑤ 回读校验', 'verify', 2, 0)]
        for text, op, r, c in specs:
            ttk.Button(bf, text=text,
                       command=lambda o=op: self.on_op(o)).grid(row=r, column=c, sticky='ew',
                                                                padx=4, pady=3)
        ttk.Button(bf, text='打开输出目录', command=self.on_open_out).grid(
            row=2, column=1, sticky='ew', padx=4, pady=3)

        pf2 = ttk.Frame(af)
        pf2.pack(fill='x', pady=(8, 0))
        self.pb = ttk.Progressbar(pf2, mode='determinate', maximum=100)
        self.pb.pack(side='left', fill='x', expand=True)
        self.btn_cancel = ttk.Button(pf2, text='取消', width=6,
                                     command=lambda: self.cancel.set(), state='disabled')
        self.btn_cancel.pack(side='left', padx=(6, 0))
        self.v_status = tk.StringVar(value='就绪')
        ttk.Label(af, textvariable=self.v_status).pack(fill='x', pady=(4, 0))

        lf = ttk.LabelFrame(left, text='日志', padding=6)
        lf.pack(fill='both', expand=True, pady=(8, 0))
        self.txt_log = tk.Text(lf, height=12, wrap='none', font=('Consolas', 9),
                               background='#fbfbfb', relief='flat', padx=6, pady=4)
        sb = ttk.Scrollbar(lf, orient='vertical', command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        self.lbl_prev = ttk.Label(right, text='（点「④ 生成预览图」后显示）',
                                  anchor='center', justify='center', foreground='#888')
        self.lbl_prev.pack(fill='both', expand=True)
        self.v_prev_info = tk.StringVar(value='')
        ttk.Label(right, textvariable=self.v_prev_info, foreground='#666',
                  wraplength=320, justify='left').pack(fill='x', pady=(6, 0))

        self.log('建议顺序：① 校验映射表 → ③ 统计覆盖率 → ② 转换 → ④ 预览 → ⑤ 校验')
        self.log('映射来源只有 mc_block_map.json；表里没有的方块按规则落成 minecraft:dirt（土块占位）。')
        self._pump()

    # ---------- 界面小工具
    def _row(self, parent, r, label, var, kind):
        from tkinter import ttk
        from tkinter import filedialog
        ttk.Label(parent, text=label, width=10, anchor='w').grid(row=r, column=0, sticky='w', pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=r, column=1, sticky='ew', pady=3)

        def pick():
            if kind == 'json':
                p = filedialog.askopenfilename(initialdir=os.path.dirname(var.get()) or ROOT_DIR,
                                               filetypes=[('映射表 JSON', '*.json'), ('全部', '*.*')])
            else:
                p = filedialog.askdirectory(initialdir=var.get() or ROOT_DIR)
            if p:
                var.set(p)

        ttk.Button(parent, text='浏览…', width=7, command=pick).grid(row=r, column=2, padx=(6, 0))

    def log(self, msg):
        self.q.put(('log', msg))

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == 'log':
                    self.txt_log.insert('end', payload + '\n')
                    self.txt_log.see('end')
                elif kind == 'status':
                    self.v_status.set(payload)
                elif kind == 'prog':
                    d, t = payload
                    self.pb['maximum'] = max(1, t)
                    self.pb['value'] = d
                elif kind == 'preview':
                    self._apply_preview(*payload)
                elif kind == 'done':
                    self.worker = None
                    self.btn_cancel.configure(state='disabled')
                elif kind == 'error':
                    from tkinter import messagebox
                    self.log(payload)
                    self.v_status.set('出错，见日志')
                    self.worker = None
                    self.btn_cancel.configure(state='disabled')
                    messagebox.showerror('出错', payload.strip().splitlines()[-1][:400])
        except queue.Empty:
            pass
        self.root.after(60, self._pump)

    def _apply_preview(self, png, dim, info):
        try:
            from PIL import Image, ImageTk
            im = Image.open(png).convert('RGB')
            k = min(330.0 / im.width, 620.0 / im.height)
            if k < 1:
                im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.NEAREST)
            self.preview_img = ImageTk.PhotoImage(im)
            self.lbl_prev.configure(image=self.preview_img, text='')
            self.v_prev_info.set('%s  %d×%d（1 像素=1 方块）\n区块 %d，地表高度 %d..%d'
                                 % (dim, info['w'], info['h'], info['chunks'],
                                    info['ymin'], info['ymax']))
        except Exception as e:
            self.v_prev_info.set('预览已保存：%s（内嵌显示失败：%s）' % (png, e))

    # ---------- 参数与执行
    def params(self):
        return {'save': self.v_save.get(), 'map': self.v_map.get(), 'out': self.v_out.get(),
                'dims': [d for d in ('m0', 'm1', 'm2') if self.v_dim[d].get()],
                'dv': self.v_dv.get(), 'only': self.v_only.get(), 'all': self.v_all.get()}

    def on_op(self, op):
        if self.worker is not None and self.worker.is_alive():
            from tkinter import messagebox
            messagebox.showwarning('忙', '任务还在跑，先等它结束或点取消。')
            return
        self.cancel = threading.Event()
        self.cancel.clear()
        ctx = Ctx(log=self.log,
                  status=lambda m: self.q.put(('status', m)),
                  progress=lambda d, t: self.q.put(('prog', (d, t))),
                  cancel=self.cancel,
                  preview=lambda *a: self.q.put(('preview', a)))
        p = self.params()
        name = OP_NAMES[op]
        self.log('')
        self.log('==== %s ====' % name)
        self.pb['value'] = 0
        self.btn_cancel.configure(state='normal')

        def wrapper():
            t0 = time.time()
            try:
                run_op(op, p, ctx)
                self.log('---- %s 完成，用时 %.1f 秒' % (name, time.time() - t0))
                self.q.put(('status', '%s 完成（%.1f s）' % (name, time.time() - t0)))
            except Exception:
                self.q.put(('error', traceback.format_exc()))
            finally:
                self.q.put(('done', None))

        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    def on_open_out(self):
        p = self.v_out.get().strip()
        if os.path.isdir(p):
            os.startfile(p)
        else:
            from tkinter import messagebox
            messagebox.showinfo('提示', '输出目录还不存在：%s' % p)


# ============================================================ 11. 网页界面（tkinter 不可用时的备用窗口）
WEB_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>迷你世界 .r → MC .mca 转换器</title>
<style>
:root{--bd:#dcdfe4;--bg:#f6f7f9;--fg:#23272e;--mut:#697180;--acc:#2f7d5c;}
*{box-sizing:border-box}
body{margin:0;font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;color:var(--fg);background:var(--bg)}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 40px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--mut);font-size:13px;margin-bottom:18px}
.card{background:#fff;border:1px solid var(--bd);border-radius:10px;padding:14px 16px;margin-bottom:14px}
.card h2{font-size:13px;letter-spacing:.06em;color:var(--mut);margin:0 0 10px;font-weight:600}
.row{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.row label{width:96px;color:#3b4252}
.row input[type=text]{flex:1;min-width:120px;padding:6px 9px;border:1px solid var(--bd);border-radius:7px;font:13px inherit}
.chk{display:inline-flex;gap:6px;align-items:center;margin-right:14px;color:#3b4252}
.btns{display:flex;flex-wrap:wrap;gap:8px}
button{padding:8px 14px;border:1px solid var(--bd);background:#fff;border-radius:8px;cursor:pointer;font:13px inherit;color:var(--fg)}
button:hover{border-color:#b9c0cb;background:#fafbfc}
button.primary{background:var(--acc);color:#fff;border-color:var(--acc)}
button:disabled{opacity:.5;cursor:not-allowed}
.bar{height:8px;background:#eceff3;border-radius:6px;overflow:hidden;margin:12px 0 6px}
.bar>i{display:block;height:100%;width:0;background:var(--acc);transition:width .2s}
.status{color:var(--mut);font-size:12.5px}
.log{margin-top:12px;background:#fbfbfc;border:1px solid var(--bd);border-radius:8px;padding:10px;height:250px;overflow:auto;
     font:12px/1.55 Consolas,monospace;white-space:pre-wrap}
.prev{margin-top:14px}
.prev img{max-width:330px;max-height:640px;border:1px solid var(--bd);border-radius:8px;background:#181818}
.hint{color:var(--mut);font-size:12px}
</style></head><body><div class="wrap">
<h1>迷你世界存档 → Minecraft Java 世界</h1>
<div class="sub">读取 .r 区块 → 按 mc_block_map.json 映射方块 → 写出 Anvil .mca</div>

<div class="card"><h2>路径</h2>
<div class="row"><label>存档目录</label><input id="save" type="text"></div>
<div class="row"><label>映射表 JSON</label><input id="map" type="text"></div>
<div class="row"><label>输出目录</label><input id="out" type="text"></div>
</div>

<div class="card"><h2>选项</h2>
<div class="row">
  <span class="chk"><input type="checkbox" id="m0" checked><label for="m0" style="width:auto">m0 主世界</label></span>
  <span class="chk"><input type="checkbox" id="m1" checked><label for="m1" style="width:auto">m1 地心</label></span>
  <span class="chk"><input type="checkbox" id="m2" checked><label for="m2" style="width:auto">m2 萌眼星</label></span>
</div>
<div class="row"><label>DataVersion</label><input id="dv" type="text" style="max-width:110px" value="3465">
  <span class="hint">1.20.1=3465 / 1.20=3463 / 1.21=3953</span></div>
<div class="row"><label>只处理</label><input id="only" type="text" style="max-width:150px" placeholder="留空=全部，如 x0z0">
  <span class="chk"><input type="checkbox" id="all"><label for="all" style="width:auto">校验全部区块（慢）</label></span></div>
</div>

<div class="card"><h2>操作</h2>
<div class="btns">
  <button onclick="run('mapcheck')">① 校验映射表</button>
  <button class="primary" onclick="run('convert')">② 转换 .r → .mca</button>
  <button onclick="run('coverage')">③ 统计覆盖率</button>
  <button onclick="run('preview')">④ 生成预览图</button>
  <button onclick="run('verify')">⑤ 回读校验</button>
</div>
<div class="bar"><i id="bar"></i></div>
<div class="status" id="status">就绪</div>
<div class="log" id="log"></div>
<div class="prev" id="prev"></div>
<div class="hint">映射来源只有 mc_block_map.json（可用「映射表编辑器」维护）；表里没有的方块按规则落成 minecraft:dirt（土块占位）。所有路径都在这台机器的真实目录下，不会写系统盘无关位置。</div>
</div>

<script>
let seq = 0, timer = null;
function val(id){ return document.getElementById(id).value; }
function chk(id){ return document.getElementById(id).checked; }
function params(){
  return {save:val('save'), map:val('map'), out:val('out'), dv:val('dv'), only:val('only'),
          all:chk('all'), dims:['m0','m1','m2'].filter(d=>chk(d))};
}
async function run(op){
  const r = await fetch('/run', {method:'POST', headers:{'Content-Type':'application/json'},
                                 body: JSON.stringify({op:op, params:params()})});
  if(!r.ok){ const t = await r.text(); addLog('✗ '+t); }
  if(!timer) timer = setInterval(poll, 400);
}
function addLog(s){
  const log = document.getElementById('log');
  log.textContent += s + '\\n';
  log.scrollTop = log.scrollHeight;
}
async function poll(){
  const r = await fetch('/state?since='+seq);
  const j = await r.json();
  if(j.lines.length){ j.lines.forEach(addLog); seq = j.seq; }
  document.getElementById('status').textContent = j.status;
  document.getElementById('bar').style.width = (100*Math.min(1, j.prog[0]/Math.max(1,j.prog[1]))) + '%';
  if(j.prev && j.prev.dim){
    document.getElementById('prev').innerHTML =
      '<img src="/img/'+j.prev.dim+'.png?v='+j.prev.v+'"><div class="hint">'+j.prev.dim+
      '  '+j.prev.w+'×'+j.prev.h+'（1 像素=1 方块），区块 '+j.prev.chunks+'</div>';
  }
}
async function init(){
  const r = await fetch('/init'); const j = await r.json();
  document.getElementById('save').value = j.save;
  document.getElementById('map').value = j.map;
  document.getElementById('out').value = j.out;
  timer = setInterval(poll, 400);
}
init();
</script></body></html>
"""


class WebState(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.lines = []
        self.seq = 0
        self.status = '就绪'
        self.prog = [0, 1]
        self.busy = False
        self.prev = None
        self.thread = None

    def add(self, msg):
        with self.lock:
            self.lines.append(msg)
            self.seq += 1
            if len(self.lines) > 4000:
                del self.lines[:1000]

    def snapshot(self, since):
        with self.lock:
            new = self.lines[since:] if since < len(self.lines) else []
            return {'lines': new, 'seq': self.seq, 'status': self.status,
                    'prog': self.prog, 'busy': self.busy, 'prev': self.prev}


def run_web(host='127.0.0.1', port=None):
    import json as _json
    import socket
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse, parse_qs

    save0, map0, out0 = _default_paths()
    docs = os.path.join(ROOT_DIR, 'docs')
    st = WebState()
    st.add('建议顺序：① 校验映射表 → ③ 统计覆盖率 → ② 转换 → ④ 预览 → ⑤ 校验')
    st.add('映射来源只有 mc_block_map.json；表里没有的方块按规则落成 minecraft:dirt（土块占位）。')

    class Handler(BaseHTTPRequestHandler):
        server_version = 'MiniWorldRTool'

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype='application/json; charset=utf-8'):
            raw = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == '/':
                return self._send(200, WEB_PAGE, 'text/html; charset=utf-8')
            if u.path == '/init':
                return self._send(200, _json.dumps({'save': save0, 'map': map0, 'out': out0},
                                                   ensure_ascii=False))
            if u.path == '/state':
                since = 0
                q = parse_qs(u.query)
                if 'since' in q:
                    try:
                        since = int(q['since'][0])
                    except Exception:
                        since = 0
                snap = st.snapshot(since)
                return self._send(200, _json.dumps(snap, ensure_ascii=False))
            if u.path.startswith('/img/'):
                dim = os.path.basename(u.path)[:-4]
                if dim not in ('m0', 'm1', 'm2'):
                    return self._send(404, 'bad dim', 'text/plain; charset=utf-8')
                with st.lock:
                    rec = st.prev or {}
                p = rec.get('path') if rec.get('dim') == dim else None
                if not p or not os.path.isfile(p):
                    p = os.path.join(docs, 'preview_%s.png' % dim)
                if not os.path.isfile(p):
                    return self._send(404, 'no preview', 'text/plain; charset=utf-8')
                with open(p, 'rb') as f:
                    return self._send(200, f.read(), 'image/png')
            return self._send(404, 'not found', 'text/plain; charset=utf-8')

        def do_POST(self):
            if urlparse(self.path).path != '/run':
                return self._send(404, 'not found', 'text/plain; charset=utf-8')
            try:
                n = int(self.headers.get('Content-Length') or 0)
                payload = _json.loads(self.rfile.read(n).decode('utf-8') or '{}')
            except Exception:
                return self._send(400, 'bad json', 'text/plain; charset=utf-8')
            op = payload.get('op')
            params = payload.get('params') or {}
            if op not in OPS:
                return self._send(400, '未知操作', 'text/plain; charset=utf-8')
            if st.busy:
                return self._send(409, '任务还在跑，等它结束或刷新页面', 'text/plain; charset=utf-8')

            cancel = threading.Event()

            def log(m):
                st.add(m)

            def status(m):
                with st.lock:
                    st.status = m

            def prog(d, t):
                with st.lock:
                    st.prog = [d, max(1, t)]

            def preview(path, dim, info):
                with st.lock:
                    st.prev = {'dim': dim, 'v': int(time.time()), 'w': info['w'],
                               'h': info['h'], 'chunks': info['chunks'], 'path': path}

            ctx = Ctx(log, status, prog, cancel, preview)
            name = OP_NAMES.get(op, op)
            st.add('')
            st.add('==== %s ====' % name)
            with st.lock:
                st.busy = True
                st.prog = [0, 1]
            status('%s …' % name)

            def worker():
                t0 = time.time()
                try:
                    run_op(op, params, ctx)
                    log('---- %s 完成，用时 %.1f 秒' % (name, time.time() - t0))
                    status('%s 完成（%.1f s）' % (name, time.time() - t0))
                except Exception:
                    tb = traceback.format_exc()
                    for line in tb.splitlines():
                        log(line)
                    status('%s 出错，见日志' % name)
                finally:
                    with st.lock:
                        st.busy = False

            st.thread = threading.Thread(target=worker, daemon=True)
            st.thread.start()
            return self._send(200, _json.dumps({'ok': True}, ensure_ascii=False))

    if port is None:
        port = 8123
        for cand in range(8123, 8210):
            s = socket.socket()
            try:
                s.bind((host, cand))
                s.close()
                port = cand
                break
            except Exception:
                s.close()
                continue

    httpd = ThreadingHTTPServer((host, port), Handler)
    url = 'http://%s:%d/' % (host, port)
    print('界面已启动：%s' % url)
    print('（关掉这个黑窗口就等于关闭程序）')
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


# ============================================================ 12. 自检 / 入口
def main_selftest(argv):
    print('自检（不开界面）')
    save, map_path, out = _default_paths()
    args = [a for a in argv[1:] if not a.startswith('-')]
    if args:
        save = args[0]
    cfg, notes = load_map_json(map_path)
    print('映射表：%s（条目 %d → 生效 %d 项）' % (map_path, notes['raw'], notes['used']))
    mapper = Mapper(cfg)
    files, src = list_regions(save, 'm0', 'x-1z-6')
    if not files:
        files, src = list_regions(save, 'm0')
        files = files[:1]
    if not files:
        print('在 %s 下没找到 .r 样本，无法自检。' % save)
        print('用法：python tools/MiniWorld_R_Tool.py --selftest <存档目录>')
        print('存档目录里应该有 m0/ 目录，里面是 x<rx>z<rz>.r 文件。')
        return 2
    print('样本：m0/%s' % files[0])
    dst = os.path.join(ROOT_DIR, '_work', 'out', 'selftest')
    os.makedirs(dst, exist_ok=True)
    rf = RegionFile(os.path.join(src, files[0]))
    nbt_list, stt = convert_region(rf, mapper, 'm0', 3465)
    rx, rz = parse_region_name(rf.path)
    target = os.path.join(dst, 'r.%d.%d.mca' % (rx, rz))
    write_region(target, nbt_list)
    print('写出 %s（区块 %d，方块 %d，土块 %d）' % (target, stt['chunks'], stt['blocks'], stt['unknown']))
    total, bad, diffs = verify_region(os.path.join(src, files[0]), target, mapper, 'm0')
    print('回读校验：对比 %d 块，不一致 %d → %s' % (total, bad, 'PASS' if bad == 0 else 'FAIL'))
    for d in diffs:
        print('  ' + d)
    print('原生窗口可用：', tk_available())
    return 0 if bad == 0 else 1


def main():
    argv = sys.argv
    if '--selftest' in argv:
        sys.exit(main_selftest(argv))
    if '--web' in argv or not tk_available():
        if '--web' not in argv:
            print('这个 Python 没有可用的 Tk（缺 Tcl/Tk 运行库），改用浏览器窗口界面。')
        run_web()
        return
    import tkinter as tk
    root = tk.Tk()
    root.title(APP_TITLE)
    TkApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
