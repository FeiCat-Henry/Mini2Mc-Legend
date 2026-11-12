#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Mini World (MiniwAr) 的 .r 区块文件转换成 Minecraft Java 版 .mca (Anvil Region)。

用法示例：
    python to_mca.py --save "D:\\小世界存档\\w85624992381678" --out "D:\\out\\world"
    python to_mca.py --save ... --out ... --dims m0          # 只转主世界
    python to_mca.py --save ... --out ... --region x0z0      # 只转一个区块文件

输出目录按 MC 世界结构摆放：
    out/world/region/r.<rx>.<rz>.mca         <- m0（主世界，y 0..255）
    out/world/DIM-1/region/r.<rx>.<rz>.mca   <- m1（地心/下界，y 0..127）
    out/world/DIM1/region/r.<rx>.<rz>.mca    <- m2（另一维度）
"""
import argparse
import json
import os
import struct
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r_reader import RegionFile, parse_region_name

# --------------------------------------------------------------------- NBT 编码
TYPE_ID = {'B': 1, 'S': 2, 'I': 3, 'L': 4, 'F': 5, 'D': 6,
           'BA': 7, 'STR': 8, 'LST': 9, 'C': 10, 'IA': 11, 'LA': 12}


def B(v):   return ('B', v)
def S(v):   return ('S', v)
def I(v):   return ('I', v)
def L(v):   return ('L', v)
def STR(v): return ('STR', v)
def BA(v):  return ('BA', v)
def IA(v):  return ('IA', v)
def LA(v):  return ('LA', v)


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
        body = b''.join(_tag(n, t) for n, t in tag[1].items())
        return body + b'\x00'
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


# --------------------------------------------------------------------- 位打包
def pack_bits(values, bits, values_per_long=None):
    """MC 1.16+ 的紧凑位打包：每个 long 内不跨值。values 最多 4096。"""
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
    """256 个 9-bit 高度值 -> 37 个 long"""
    return pack_bits(heights, 9, 7)


# --------------------------------------------------------------------- 区块构建
def build_section(y, names):
    """names: 4096 个方块名字符串。返回 section compound dict"""
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
    """sections: [(y, names)]；names 为 4096 个方块名"""
    sec_list = []
    for y, names in sections:
        comp = build_section(y, names)
        comp['biomes'] = C({'palette': LST('STR', [biome])})
        sec_list.append(comp)

    # 高度图：稍微抬高一点的“最高非空气方块 + 1”
    surface = [0] * 256
    motion = [0] * 256
    for y, names in sections:
        base = y * 16
        for i, nm in enumerate(names):
            if nm == 'minecraft:air' or nm == 'minecraft:cave_air':
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
        'LastUpdate': L(int(time.time() * 1000)),
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


# --------------------------------------------------------------------- 区域写出
def write_region(path, chunk_nbt):
    """chunk_nbt: [(chunk_index 0..1023, nbt bytes)]"""
    header = bytearray(8192)
    body = bytearray()
    now = int(time.time())
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


# --------------------------------------------------------------------- 转换主体
def load_map(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


class Mapper(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.mask = cfg.get('id_mask', 0xFFF)
        self.fallback = cfg.get('fallback', 'minecraft:dirt')

    def name_of(self, dim, raw_id):
        """raw_id 是存档里的 u16 原值：低 12 位是 id，高 4 位是状态标志。
        返回 (方块名, 是否用了占位兜底)"""
        base = raw_id & self.mask
        if base == 0:
            return 'minecraft:air', False
        key = str(base)
        m = self.cfg['dims'].get(dim, {})
        if key in m:
            return m[key], False
        common = self.cfg.get('common', {})
        if key in common:
            return common[key], False
        if '*' in m:
            return m['*'], True
        return self.fallback, True


def convert_region(rf, mapper, dim, biome, data_version, ypos, max_band=15):
    x0, z0 = parse_region_name(rf.path)
    out = []
    stats = {'chunks': 0, 'blocks': 0, 'unknown': 0, 'unknown_ids': {}}
    for idx in rf.chunk_indexes():
        ch = rf.read_chunk(idx)
        cx, cz = ch.index & 31, ch.index >> 5
        xpos = x0 * 32 + cx
        zpos = z0 * 32 + cz
        sections = []
        for band in range(0, max_band + 1):
            sec = ch.sections.get(band)
            if sec is None:
                names = ['minecraft:air'] * 4096
            else:
                ids = sec.ids
                cache = {}
                names = []
                # 4096 个 id 原样按顺序写进 block_states（与 MC 的段内顺序一致）
                for v in ids:
                    hit = cache.get(v)
                    if hit is None:
                        nm, guessed = mapper.name_of(dim, v)
                        hit = (nm, guessed)
                        cache[v] = hit
                        if guessed:
                            stats['unknown_ids'][str(v & mapper.mask)] = \
                                stats['unknown_ids'].get(str(v & mapper.mask), 0) + 1
                    if hit[1] and hit[0] != 'minecraft:air':
                        stats['unknown'] += 1
                    names.append(hit[0])
                stats['blocks'] += sum(1 for v in ids if v)
            if any(n != 'minecraft:air' for n in names) or band == 0:
                sections.append((band, names))
        nbt = build_chunk(xpos, zpos, sections, biome, data_version, ypos)
        out.append((idx, nbt))
        stats['chunks'] += 1
    return out, stats


DIM_INFO = {
    # dim: (输出子目录, biome, max y band, yPos, DataVersion)
    'm0': ('region', 'minecraft:plains', 15, -4),
    'm1': ('DIM-1/region', 'minecraft:nether_wastes', 15, -4),
    'm2': ('DIM1/region', 'minecraft:the_end', 15, -4),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--save', required=True, help='存档根目录，例如 ...\\w85624992381678')
    ap.add_argument('--out', required=True, help='输出目录（MC 世界目录）')
    ap.add_argument('--dims', default='m0,m1,m2')
    ap.add_argument('--region', default=None, help='只转换指定文件名，如 x0z0')
    ap.add_argument('--map', default=None, help='方块映射表 JSON，默认同目录 mc_block_map.json')
    ap.add_argument('--data-version', type=int, default=3465, help='MC DataVersion，默认 3465 (1.20.1)')
    args = ap.parse_args()

    map_path = args.map or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mc_block_map.json')
    mapper = Mapper(load_map(map_path))

    total = {'chunks': 0, 'blocks': 0, 'unknown': 0}
    unknown_ids = {}
    for dim in args.dims.split(','):
        dim = dim.strip()
        if not dim:
            continue
        src = os.path.join(args.save, dim)
        if not os.path.isdir(src):
            print('skip missing dim %s' % dim)
            continue
        sub, biome, max_band, ypos = DIM_INFO[dim]
        dst = os.path.join(args.out, sub)
        os.makedirs(dst, exist_ok=True)
        files = sorted(f for f in os.listdir(src) if f.endswith('.r'))
        if args.region:
            files = [f for f in files if f[:-2] == args.region]
        for fn in files:
            rf = RegionFile(os.path.join(src, fn))
            chunk_nbt, st = convert_region(rf, mapper, dim, biome, args.data_version, ypos, max_band)
            rx, rz = parse_region_name(rf.path)
            out_path = os.path.join(dst, 'r.%d.%d.mca' % (rx, rz))
            write_region(out_path, chunk_nbt)
            for k in total:
                total[k] += st[k]
            for k, v in st['unknown_ids'].items():
                unknown_ids[k] = unknown_ids.get(k, 0) + v
            print('%-24s -> %-28s chunks=%-4d blocks=%d'
                  % (dim + '/' + fn, os.path.relpath(out_path, args.out),
                     st['chunks'], st['blocks']))

    print('\n合计: chunks=%d blocks=%d 未知方块=%d (%.2f%%)'
          % (total['chunks'], total['blocks'], total['unknown'],
             100.0 * total['unknown'] / max(1, total['blocks'])))
    if unknown_ids:
        top = sorted(unknown_ids.items(), key=lambda kv: -kv[1])[:12]
        print('未知 id Top:', ', '.join('%s:%d' % kv for kv in top))


if __name__ == '__main__':
    main()
