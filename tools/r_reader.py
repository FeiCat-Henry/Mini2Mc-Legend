r"""Mini World (MiniwAr) .r region file reader.

Container + LZMA + FlatBuffer(chunk) 三层解析，纯标准库。
用法：
    from r_reader import RegionFile
    rf = RegionFile(r"D:\save\m0\x0z0.r")
    for idx, chunk in rf.chunks():
        print(idx, chunk.sections.keys())
"""
import os
import struct
import lzma

SECTOR = 4096
# 16 个 21-bit XOR key，按 "区块表项序号 & 0xF" 取用（已用 46 个文件 16008 条记录验证）
KEYS = [0x5d351c, 0x30f308, 0x524d19, 0x6cf2bc, 0x04b262, 0x35e03b, 0x3a7c26, 0x4ba765,
        0x2762ed, 0x44f986, 0x6f7114, 0x6a4acf, 0x7bb584, 0x72ca13, 0x22ad76, 0x178bbb]

# 区块里的段号：0..15 就是 y 段号（0 = y 0..15，15 = y 240..255）
# 注意：字段缺省(slot 0 未写)时 FlatBuffer 默认值为 0，即最底层段；
# 曾把表头 soffset 0x12=18 误读成段号，真实段号从来没有 18。
SEGMENT_FOR_Y0 = 0


def band_of_segment(seg):
    """section 表里的段号 -> 世界 y 段号(0..15)。"""
    return seg


# ---------------------------------------------------------------- LZMA
def lzma_raw_decompress(payload):
    """payload = [1B props][4B dictSize][raw LZMA1 stream]"""
    prop = payload[0]
    if prop >= 225:
        raise ValueError("bad lzma props byte 0x%02x" % prop)
    lc = prop % 9
    lp = (prop // 9) % 5
    pb = prop // 45
    dict_size = struct.unpack('<I', payload[1:5])[0]
    dec = lzma.LZMADecompressor(
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA1, "lc": lc, "lp": lp, "pb": pb, "dict_size": dict_size}])
    return dec.decompress(payload[5:])


# ---------------------------------------------------------------- FlatBuffer helpers
def _u32(b, o):
    return struct.unpack('<I', b[o:o + 4])[0]


def _i32(b, o):
    return struct.unpack('<i', b[o:o + 4])[0]


def _u16(b, o):
    return struct.unpack('<H', b[o:o + 2])[0]


def fb_fields(b, table):
    """返回表的槽位偏移数组：fields[slot] = 字段相对表头的偏移，缺省为 0。"""
    vtable = table - _i32(b, table)
    vlen = _u16(b, vtable)
    n = (vlen - 4) // 2
    return [_u16(b, vtable + 4 + 2 * i) for i in range(n)]


def fb_vector(b, field_off):
    """field_off 指向 uoffset，返回 (元素起始偏移, 元素个数)"""
    p = field_off + _u32(b, field_off)
    return p + 4, _u32(b, p)


def vidx(x, y_local, z):
    """段内下标：(yLocal<<8) | (x<<4) | z

    与 MC 的 block_states 顺序一致，写 .mca 时原样使用（这一版已在游戏里核对过）。
    """
    return (y_local << 8) | (x << 4) | z


def pidx(x, y_local, z):
    """预览取样下标（仅出图用）：(yLocal<<8) | (z<<4) | x

    和 vidx 在 16×16 平面内互为转置。渲染缩略图时用它，区块之间才连续；
    转换 .mca 不受影响。
    """
    return (y_local << 8) | (z << 4) | x


class Section(object):
    """一个 16x16x16 的段。ids/extra 的下标 i = (yLocal<<8)|(x<<4)|z"""
    __slots__ = ("segment", "band", "ids", "extra", "bitmap_a", "bitmap_b", "scalar_a", "scalar_b")

    def __init__(self, segment, band, ids, extra, bitmap_a, bitmap_b, scalar_a, scalar_b):
        self.segment = segment
        self.band = band
        self.ids = ids
        self.extra = extra
        self.bitmap_a = bitmap_a
        self.bitmap_b = bitmap_b
        self.scalar_a = scalar_a
        self.scalar_b = scalar_b

    def get(self, x, y_local, z):
        return self.ids[vidx(x, y_local, z)]


class Chunk(object):
    def __init__(self, index, raw_size, tag, out):
        self.index = index
        self.compressed_size = raw_size
        self.tag = tag
        self.raw = out
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
            band = band_of_segment(segment)
            self.sections[band] = Section(segment, band, ids, extra, bit_a, bit_b, s_a, s_b)

    def block(self, x, y, z):
        """世界坐标(区块内 0..15)：返回 (block_id, extra)"""
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
    def __init__(self, path):
        self.path = os.path.abspath(path)
        with open(self.path, 'rb') as f:
            self.data = f.read()
        self.n_sectors = len(self.data) // SECTOR
        if len(self.data) % SECTOR:
            raise ValueError("size not multiple of 4096: %s" % path)
        self.table = []
        for i in range(1024):
            e = struct.unpack('<I', self.data[i * 4:i * 4 + 4])[0]
            self.table.append(e)
        self._index = self._decode_index()

    def _decode_index(self):
        """表项 -> (扇区偏移, len字节)。返回 {表项序号: (offset_sector, len)}"""
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
        off, _len = self._index[index]
        base = off * SECTOR
        size, tag = struct.unpack('<II', self.data[base:base + 8])
        payload = self.data[base + 8:base + 8 + size]
        ctype = tag >> 28
        raw_expect = tag & 0x0FFFFFFF
        if ctype != 2:
            raise ValueError("unsupported compress type %d (chunk %d)" % (ctype, index))
        out = lzma_raw_decompress(payload)
        if len(out) > raw_expect:
            out = out[:raw_expect]
        return Chunk(index, size, tag, out)

    def chunks(self):
        for i in self.chunk_indexes():
            yield i, self.read_chunk(i)

    def chunk_xy(self, index):
        """表项序号 -> 区块坐标（局部 0..31；世界坐标 = 文件名里的 x,z * 32 + 该值）"""
        return index & 31, index >> 5


def parse_region_name(path):
    """x-1z-1.r -> (-1, -1)"""
    name = os.path.basename(path)
    if not name.startswith('x') or '.r' not in name:
        raise ValueError("bad region name %s" % name)
    body = name[1:name.index('.r')]
    zi = body.index('z', 1)
    return int(body[:zi]), int(body[zi + 1:])


if __name__ == '__main__':
    import sys
    p = sys.argv[1]
    rf = RegionFile(p)
    print("%s: %d sectors, %d chunks" % (p, rf.n_sectors, len(rf._index)))
    for idx, ch in list(rf.chunks())[:3]:
        print(" chunk %d  xz=%s  raw=%d  root_fields=%s  bands=%s"
              % (idx, rf.chunk_xy(idx), len(ch.raw), ch.root_fields, sorted(ch.sections)))
        for band, sec in sorted(ch.sections.items()):
            uniq = len(set(sec.ids))
            print("   band %2d (seg %2d): %d 种方块  bitmap=%s/%s scalar=%s/%s"
                  % (band, sec.segment, uniq,
                     len(sec.bitmap_a) if sec.bitmap_a else None,
                     len(sec.bitmap_b) if sec.bitmap_b else None, sec.scalar_a, sec.scalar_b))
