#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 .r 存档渲染成俯视地图 PNG（每格=1 方块，颜色按顶层方块映射后的 MC 名字，亮度按高度）。

    python render_map.py --save "D:\\...\\w85624992381678" --dim m0 --out "docs\\preview_m0.png"
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r_reader import RegionFile, pidx
from to_mca import Mapper, load_map

# MC 方块名 -> 颜色
C = {
    'minecraft:bedrock': (62, 62, 68),
    'minecraft:water': (54, 108, 200),
    'minecraft:lava': (255, 120, 26),
    'minecraft:grass_block': (86, 160, 72),
    'minecraft:dirt': (134, 96, 67),
    'minecraft:stone': (128, 128, 130),
    'minecraft:sand': (222, 208, 156),
    'minecraft:red_sand': (196, 116, 60),
    'minecraft:gravel': (136, 128, 124),
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
    'minecraft:black_concrete': (25, 25, 30),
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
FALLBACK = (150, 150, 120)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--save', required=True)
    ap.add_argument('--dim', default='m0')
    ap.add_argument('--out', required=True)
    ap.add_argument('--map', default=None)
    args = ap.parse_args()

    from PIL import Image

    map_path = args.map or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mc_block_map.json')
    mapper = Mapper(load_map(map_path))

    src = os.path.join(args.save, args.dim)
    files = sorted(glob.glob(os.path.join(src, '*.r')))

    chunks = []
    xs = []
    zs = []
    for f in files:
        rf = RegionFile(f)
        base = os.path.basename(f)[:-2]
        a, b = base[1:].split('z')
        for idx in rf.chunk_indexes():
            cx, cz = idx & 31, idx >> 5
            gx = int(a) * 32 + cx
            gz = int(b) * 32 + cz
            chunks.append((rf, idx, gx, gz))
            xs.append(gx)
            zs.append(gz)
    minx, maxx = min(xs), max(xs)
    minz, maxz = min(zs), max(zs)
    W = (maxx - minx + 1) * 16
    H = (maxz - minz + 1) * 16
    img = Image.new('RGB', (W, H), (24, 24, 28))
    px = img.load()

    heights = []
    for rf, idx, gx, gz in chunks:
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
                heights.append(top_y)
                if not top_v:
                    px[ox + x, oz + z] = (10, 10, 12)
                    continue
                nm = mapper.name_of(args.dim, top_v)[0]
                c = C.get(nm, FALLBACK)
                k = 0.55 + 0.45 * min(1.0, top_y / 90.0)
                px[ox + x, oz + z] = (int(c[0] * k), int(c[1] * k), int(c[2] * k))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    img.save(args.out)
    print('image', W, 'x', H, '->', args.out,
          '| 高度范围', min(heights), '-', max(heights), '| 区块数', len(chunks))


if __name__ == '__main__':
    main()
