#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回读校验：把生成出来的 .mca 解回方块，逐块与源 .r 对比（走的是独立实现，能验证打包/压缩/头表是否写对）。

    python verify_mca.py --save "D:\\...\\w85624992381678" --mca "D:\\...\\world" --dim m0
"""
import argparse
import json
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r_reader import RegionFile, parse_region_name
from to_mca import Mapper, load_map

# ------------------------------------------------------------------ NBT 读取
class NbtReader(object):
    def __init__(self, data):
        self.b = data
        self.p = 0

    def u1(self):
        v = self.b[self.p]; self.p += 1; return v

    def i1(self):
        v = struct.unpack('>b', self.b[self.p:self.p+1])[0]; self.p += 1; return v

    def i2(self):
        v = struct.unpack('>h', self.b[self.p:self.p+2])[0]; self.p += 2; return v

    def u2(self):
        v = struct.unpack('>H', self.b[self.p:self.p+2])[0]; self.p += 2; return v

    def i4(self):
        v = struct.unpack('>i', self.b[self.p:self.p+4])[0]; self.p += 4; return v

    def i8(self):
        v = struct.unpack('>q', self.b[self.p:self.p+8])[0]; self.p += 8; return v

    def f4(self):
        v = struct.unpack('>f', self.b[self.p:self.p+4])[0]; self.p += 4; return v

    def f8(self):
        v = struct.unpack('>d', self.b[self.p:self.p+8])[0]; self.p += 8; return v

    def string(self):
        n = self.u2()
        s = self.b[self.p:self.p+n].decode('utf-8', 'replace')
        self.p += n
        return s

    def payload(self, t, depth=0):
        if t == 1: return self.i1()
        if t == 2: return self.i2()
        if t == 3: return self.i4()
        if t == 4: return self.i8()
        if t == 5: return self.f4()
        if t == 6: return self.f8()
        if t == 7:
            n = self.i4(); v = self.b[self.p:self.p+n]; self.p += n; return v
        if t == 8: return self.string()
        if t == 9:
            sub = self.u1()
            n = self.i4()
            items = []
            for _ in range(n):
                if sub == 10:
                    items.append(self.compound(depth+1))
                else:
                    items.append(self.payload(sub, depth+1))
            return ('LIST', sub, items)
        if t == 10: return self.compound(depth+1)
        if t == 11:
            n = self.i4(); v = [self.i4() for _ in range(n)]; return ('IA', v)
        if t == 12:
            n = self.i4(); v = [self.i8() for _ in range(n)]; return ('LA', v)
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
        name = self.string()
        return self.payload(t)


# ------------------------------------------------------------------ 位解包
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


def read_chunk_nbt(raw):
    r = NbtReader(raw)
    return r.root()


def decode_section(comp):
    """返回 (Y, 4096 个方块名)"""
    y = comp['Y']
    bs = comp['block_states']
    palette = [e['Name'] for e in bs['palette'][2]]
    if len(palette) == 1:
        return y, [palette[0]] * 4096
    data = bs['data'][1]
    bits = 4
    while (1 << bits) < len(palette):
        bits += 1
    vals = unpack_bits(data, bits)
    return y, [palette[i] for i in vals]


def read_mca(path):
    """返回 {chunk_index: {section_y: [names]}}"""
    with open(path, 'rb') as f:
        data = f.read()
    out = {}
    for idx in range(1024):
        loc = struct.unpack('>I', data[idx*4:idx*4+4])[0]
        if not loc:
            continue
        sector = loc >> 8
        count = loc & 0xFF
        off = sector * 4096
        length = struct.unpack('>I', data[off:off+4])[0]
        ctype = data[off+4]
        payload = data[off+5:off+4+length]
        if ctype == 1:
            raw = zlib.decompress(payload)
        elif ctype == 2:
            raw = zlib.decompress(payload)
        elif ctype == 3:
            raw = payload
        else:
            raise ValueError('bad compression %d' % ctype)
        root = read_chunk_nbt(raw)
        secs = {}
        for comp in root['sections'][2]:
            y, names = decode_section(comp)
            secs[y] = names
        out[idx] = secs
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--save', required=True)
    ap.add_argument('--mca', required=True, help='转换输出的世界目录（含 region/ 等）')
    ap.add_argument('--dim', default='m0')
    ap.add_argument('--region', default=None)
    ap.add_argument('--map', default=None)
    args = ap.parse_args()

    map_path = args.map or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mc_block_map.json')
    mapper = Mapper(load_map(map_path))
    subdir = {'m0': 'region', 'm1': os.path.join('DIM-1', 'region'),
              'm2': os.path.join('DIM1', 'region')}[args.dim]

    src = os.path.join(args.save, args.dim)
    files = sorted(f for f in os.listdir(src) if f.endswith('.r'))
    if args.region:
        files = [f for f in files if f[:-2] == args.region]

    bad = 0
    total = 0
    for fn in files:
        rf = RegionFile(os.path.join(src, fn))
        rx, rz = parse_region_name(rf.path)
        mca_path = os.path.join(args.mca, subdir, 'r.%d.%d.mca' % (rx, rz))
        mca = read_mca(mca_path)
        for idx in rf.chunk_indexes():
            ch = rf.read_chunk(idx)
            secs = mca.get(idx)
            if secs is None:
                print('%s chunk %d: 缺失' % (fn, idx)); bad += 1; continue
            for band in range(0, 16):
                src_sec = ch.sections.get(band)
                dst = secs.get(band)
                if dst is None:
                    dst = ['minecraft:air'] * 4096
                for i in range(4096):
                    v = src_sec.ids[i] if src_sec else 0
                    want = mapper.name_of(args.dim, v)[0] if v else 'minecraft:air'
                    total += 1
                    if dst[i] != want:
                        bad += 1
                        if bad <= 5:
                            print('  不一致 %s chunk %d band %d i %d: got %s want %s'
                                  % (fn, idx, band, i, dst[i], want))
    print('\n对比方块数 = %d，不一致 = %d' % (total, bad))
    print('结果:', 'PASS' if bad == 0 else 'FAIL')


if __name__ == '__main__':
    main()
