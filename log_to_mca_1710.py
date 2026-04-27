import os
import re
import argparse

from anvil.legacy import LEGACY_ID_MAP
from nbt import nbt
import math
import zlib
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from io import BytesIO
import anvil
import numpy as np

LOG_PREFIX_RE = re.compile(r'^\[\d{2}:\d{2}:\d{2}\]\[.*?\]\s*')
REGION_COORD_RE = re.compile(r'-?\d+')
CHUNK_PREFIX = chr(0x533a)
EMPTY_CHUNK_MARKER = chr(0x7a7a)
LAYER_INDEXES = tuple((i % 16) * 16 + (i // 16) for i in range(256))
COMPRESSION_LEVEL = 6

# Block ID mappings (MiniWorld ID -> MC name -> MC 1.7.10 ID/Data)
# REVERSE_ID_MAP is a fast lookup from "minecraft:stone" -> (1, 0)
REVERSE_ID_MAP = {}
for legacy_str, (name, props) in LEGACY_ID_MAP.items():
    if name not in REVERSE_ID_MAP:
        block_id, data = legacy_str.split(':')
        REVERSE_ID_MAP[name] = (int(block_id), int(data))
# Manual fallbacks
REVERSE_ID_MAP['water'] = (9, 0)
REVERSE_ID_MAP['lava'] = (11, 0)

block_id_map = {}

def load_block_ids():
    global block_id_map
    block_id_map = {}
    try:
        with open('block_id_data.txt', 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and line.startswith('block_id_map'):
                    try:
                        exec(line)
                    except Exception as e:
                        pass
    except Exception as e:
        print(f"读取block_id_data.txt时出错: {e}")
        block_id_map["0"] = "air"
        block_id_map["1"] = "bedrock"
        block_id_map["3"] = "water"
        block_id_map["4"] = "water"
        block_id_map["5"] = "lava"
        block_id_map["6"] = "lava"
        block_id_map["25"] = "stone"
        block_id_map["101"] = "dirt"

def convert_block_1710(custom_id):
    custom_id_str = str(custom_id)
    mc_name = block_id_map.get(custom_id_str, "dirt")
    return REVERSE_ID_MAP.get(mc_name, (3, 0)) # Default to dirt

def clean_log_line(s):
    return LOG_PREFIX_RE.sub('', s.strip()).strip()

def extract_region_coords(filename):
    numbers = REGION_COORD_RE.findall(filename)
    return (int(numbers[0]), int(numbers[1])) if len(numbers) >= 2 else None

def extract_chunk_coords(s):
    try:
        if s.startswith('区'):
            s = s[1:]
        num_strs = s.split('/')
        return (int(num_strs[0]), int(num_strs[1]))
    except:
        return None

class EmptySection1710:
    def __init__(self, y: int):
        self.y = y
        self.blocks = bytearray(4096)
        self.data = bytearray(2048)
        self.blocklight = bytearray(2048)
        self.skylight = bytearray(2048)
        self.is_empty = True

    def set_block(self, block_id: int, data_val: int, x: int, y: int, z: int):
        if block_id != 0:
            self.is_empty = False
        index = y * 256 + z * 16 + x
        self.blocks[index] = block_id
        
        data_index = index // 2
        if index % 2 == 0:
            self.data[data_index] = (self.data[data_index] & 0xF0) | (data_val & 0x0F)
        else:
            self.data[data_index] = (self.data[data_index] & 0x0F) | ((data_val & 0x0F) << 4)

    def save(self):
        sec_tag = nbt.TAG_Compound()
        sec_tag.tags.append(nbt.TAG_Byte(name='Y', value=self.y))
        
        blocks_tag = nbt.TAG_Byte_Array(name='Blocks')
        blocks_tag.value = self.blocks
        sec_tag.tags.append(blocks_tag)
        
        data_tag = nbt.TAG_Byte_Array(name='Data')
        data_tag.value = self.data
        sec_tag.tags.append(data_tag)
        
        blocklight_tag = nbt.TAG_Byte_Array(name='BlockLight')
        blocklight_tag.value = self.blocklight
        sec_tag.tags.append(blocklight_tag)
        
        skylight_tag = nbt.TAG_Byte_Array(name='SkyLight')
        skylight_tag.value = self.skylight
        sec_tag.tags.append(skylight_tag)
        
        return sec_tag

class EmptyChunk1710:
    def __init__(self, x: int, z: int):
        self.x = x
        self.z = z
        self.sections = []
        for _ in range(16):
            self.sections.append(None)
        self.heightmap = [0] * 256

    def set_block(self, block_id: int, data_val: int, x: int, y: int, z: int):
        if y < 0 or y > 255: return
        
        sy = y // 16
        if self.sections[sy] is None:
            self.sections[sy] = EmptySection1710(sy)
        self.sections[sy].set_block(block_id, data_val, x, y % 16, z)

    def save(self):
        root = nbt.NBTFile()
        level = nbt.TAG_Compound()
        level.name = 'Level'
        level.tags.extend([
            nbt.TAG_Int(name='xPos', value=self.x),
            nbt.TAG_Int(name='zPos', value=self.z),
            nbt.TAG_Long(name='LastUpdate', value=0),
            nbt.TAG_Byte(name='LightPopulated', value=1),
            nbt.TAG_Byte(name='TerrainPopulated', value=1),
            nbt.TAG_Byte(name='V', value=1),
            nbt.TAG_Long(name='InhabitedTime', value=0),
            nbt.TAG_List(name='Entities', type=nbt.TAG_Compound),
            nbt.TAG_List(name='TileEntities', type=nbt.TAG_Compound),
        ])
        
        heightmap_tag = nbt.TAG_Int_Array(name='HeightMap')
        heightmap_tag.value = self.heightmap
        level.tags.append(heightmap_tag)
        
        biomes_tag = nbt.TAG_Byte_Array(name='Biomes')
        biomes_tag.value = bytearray([1] * 256) # 1 = plains biome
        level.tags.append(biomes_tag)

        sections = nbt.TAG_List(name='Sections', type=nbt.TAG_Compound)
        for s in self.sections:
            if s and not s.is_empty:
                sections.tags.append(s.save())
        level.tags.append(sections)
        root.tags.append(level)
        return root

def fast_region_save_1710(self, file=None):
    compressed_chunks = []
    for chunk in self.chunks:
        if chunk is None:
            compressed_chunks.append(None)
            continue

        chunk_buffer = BytesIO()
        chunk.save().write_file(buffer=chunk_buffer)
        compressed_chunks.append(zlib.compress(chunk_buffer.getvalue(), COMPRESSION_LEVEL))

    chunk_parts = []
    offsets = []
    sector_offset = 0
    for chunk_data in compressed_chunks:
        if chunk_data is None:
            offsets.append(None)
            continue

        chunk_bytes = (len(chunk_data) + 1).to_bytes(4, 'big') + b'\x02' + chunk_data
        sector_count = math.ceil(len(chunk_bytes) / 4096)
        offsets.append((sector_offset, sector_count))
        chunk_bytes += bytes(sector_count * 4096 - len(chunk_bytes))
        chunk_parts.append(chunk_bytes)
        sector_offset += sector_count

    locations_header = bytearray()
    for offset in offsets:
        if offset is None:
            locations_header += bytes(4)
        else:
            locations_header += (offset[0] + 2).to_bytes(3, 'big') + offset[1].to_bytes(1, 'big')

    final = bytes(locations_header) + bytes(4096) + b''.join(chunk_parts)
    final += bytes((4096 - (len(final) % 4096)) % 4096)

    if file:
        if isinstance(file, str):
            with open(file, 'wb') as f:
                f.write(final)
        else:
            file.write(final)
    return final

anvil.EmptyRegion.save = fast_region_save_1710

def calculate_region_lighting(region):
    print(f"开始使用全局 NumPy 渲染光照 (Region: {region.x}, {region.z})...")
    blocks = np.zeros((512, 256, 512), dtype=np.uint8)
    
    # 1. 提取所有区块数据到 3D NumPy 数组
    for chunk_idx, chunk in enumerate(region.chunks):
        if chunk is None:
            continue
        cx_local = chunk_idx % 32
        cz_local = chunk_idx // 32
        
        x_start = cx_local * 16
        z_start = cz_local * 16
        
        for sy, sec in enumerate(chunk.sections):
            if sec is None or sec.is_empty:
                continue
            
            y_start = sy * 16
            
            sec_blocks = np.frombuffer(sec.blocks, dtype=np.uint8).reshape((16, 16, 16))
            blocks[x_start:x_start+16, y_start:y_start+16, z_start:z_start+16] = sec_blocks.transpose((2, 0, 1))

    # 2. 准备光照数组
    opacity = np.full((512, 256, 512), 15, dtype=np.int8)
    transparent = {0: 0, 8: 2, 9: 2, 18: 1, 20: 0, 161: 1, 65: 0}
    for b_id, op in transparent.items():
        opacity[blocks == b_id] = op
        
    emitters = {10: 15, 11: 15, 50: 14, 51: 15, 89: 15, 138: 15, 169: 15}
    blocklight = np.zeros((512, 256, 512), dtype=np.int8)
    for b_id, em in emitters.items():
        blocklight[blocks == b_id] = em

    skylight = np.zeros((512, 256, 512), dtype=np.int8)
    heightmap_global = np.zeros((512, 512), dtype=int)
    
    # 垂直光照 & HeightMap
    current_light = np.full((512, 512), 15, dtype=np.int8)
    for y in range(255, -1, -1):
        op = opacity[:, y, :]
        is_solid = (op == 15)
        
        mask = (heightmap_global == 0) & (op > 0)
        heightmap_global[mask] = y + 1
        
        current_light[is_solid] = 0
        current_light[~is_solid] -= op[~is_solid]
        np.clip(current_light, 0, 15, out=current_light)
        
        skylight[:, y, :] = current_light

    # 3. NumPy 14-pass 3D 卷积光照扩散 (借用 MCEdit 原理)
    falloff = np.maximum(opacity, 1)
    
    for _ in range(14):
        changed = False
        
        # --- BlockLight ---
        bl_max = np.zeros_like(blocklight)
        bl_max[:-1, :, :] = np.maximum(bl_max[:-1, :, :], blocklight[1:, :, :])
        bl_max[1:, :, :] = np.maximum(bl_max[1:, :, :], blocklight[:-1, :, :])
        bl_max[:, :-1, :] = np.maximum(bl_max[:, :-1, :], blocklight[:, 1:, :])
        bl_max[:, 1:, :] = np.maximum(bl_max[:, 1:, :], blocklight[:, :-1, :])
        bl_max[:, :, :-1] = np.maximum(bl_max[:, :, :-1], blocklight[:, :, 1:])
        bl_max[:, :, 1:] = np.maximum(bl_max[:, :, 1:], blocklight[:, :, :-1])
        
        new_bl = bl_max - falloff
        np.clip(new_bl, 0, 15, out=new_bl)
        
        mask_bl = new_bl > blocklight
        if np.any(mask_bl):
            blocklight[mask_bl] = new_bl[mask_bl]
            changed = True
            
        # --- SkyLight ---
        sl_max = np.zeros_like(skylight)
        sl_max[:-1, :, :] = np.maximum(sl_max[:-1, :, :], skylight[1:, :, :])
        sl_max[1:, :, :] = np.maximum(sl_max[1:, :, :], skylight[:-1, :, :])
        sl_max[:, :-1, :] = np.maximum(sl_max[:, :-1, :], skylight[:, 1:, :])
        sl_max[:, 1:, :] = np.maximum(sl_max[:, 1:, :], skylight[:, :-1, :])
        sl_max[:, :, :-1] = np.maximum(sl_max[:, :, :-1], skylight[:, :, 1:])
        sl_max[:, :, 1:] = np.maximum(sl_max[:, :, 1:], skylight[:, :, :-1])
        
        new_sl = sl_max - falloff
        np.clip(new_sl, 0, 15, out=new_sl)
        
        mask_sl = new_sl > skylight
        if np.any(mask_sl):
            skylight[mask_sl] = new_sl[mask_sl]
            changed = True
            
        if not changed:
            break

    # 4. 把计算结果写回 Chunk
    for chunk_idx, chunk in enumerate(region.chunks):
        if chunk is None:
            continue
        cx_local = chunk_idx % 32
        cz_local = chunk_idx // 32
        
        x_start = cx_local * 16
        z_start = cz_local * 16
        
        hm = heightmap_global[x_start:x_start+16, z_start:z_start+16]
        chunk.heightmap = hm.T.flatten().tolist()
        
        for sy in range(16):
            sec = chunk.sections[sy]
            if sec is None or sec.is_empty:
                continue
                
            y_start = sy * 16
            
            sl = skylight[x_start:x_start+16, y_start:y_start+16, z_start:z_start+16]
            bl = blocklight[x_start:x_start+16, y_start:y_start+16, z_start:z_start+16]
            
            sl_transposed = sl.transpose((1, 2, 0)).flatten().tolist()
            bl_transposed = bl.transpose((1, 2, 0)).flatten().tolist()
            
            sl_packed = bytearray(2048)
            bl_packed = bytearray(2048)
            
            for i in range(2048):
                idx1 = i * 2
                idx2 = i * 2 + 1
                sl_packed[i] = (sl_transposed[idx1] & 0x0F) | ((sl_transposed[idx2] & 0x0F) << 4)
                bl_packed[i] = (bl_transposed[idx1] & 0x0F) | ((bl_transposed[idx2] & 0x0F) << 4)
                
            sec.skylight = sl_packed
            sec.blocklight = bl_packed

block_cache = {}

def get_block_1710(value):
    if value not in block_cache:
        block_cache[value] = convert_block_1710(value)
    return block_cache[value]

def get_or_create_chunk_1710(region, cx, cz):
    chunk_idx = (cz % 32) * 32 + (cx % 32)
    chunk = region.chunks[chunk_idx]
    if chunk is None:
        chunk = EmptyChunk1710(cx, cz)
        region.chunks[chunk_idx] = chunk
    return chunk

def get_or_create_section_1710(chunk, sy):
    section = chunk.sections[sy]
    if section is None:
        section = EmptySection1710(sy)
        chunk.sections[sy] = section
    return section

def set_data_nibble(data_array, index, data_val):
    data_index = index // 2
    if index % 2 == 0:
        data_array[data_index] = (data_array[data_index] & 0xF0) | (data_val & 0x0F)
    else:
        data_array[data_index] = (data_array[data_index] & 0x0F) | ((data_val & 0x0F) << 4)

def apply_blocks_to_region_1710(current_region, line, chunk_base_global_x, current_y, chunk_base_global_z):
    try:
        if current_y < 0 or current_y > 255:
            return False

        cx = chunk_base_global_x // 16
        cz = chunk_base_global_z // 16
        if not current_region.inside(cx, 0, cz, chunk=True):
            return False

        local_x_base = chunk_base_global_x % 16
        local_z_base = chunk_base_global_z % 16
        if local_x_base != 0 or local_z_base != 0:
            return apply_blocks_to_region_1710_slow(current_region, line, chunk_base_global_x, current_y, chunk_base_global_z)

        section = None
        section_blocks = None
        section_data = None
        section_y_offset = (current_y % 16) * 256
        block_index = 0
        for segment in line.split('/'):
            if '-' not in segment:
                continue
            count_str, value = segment.split('-', 1)
            count = int(count_str)

            if count <= 0:
                continue

            end_index = min(block_index + count, 256)
            b_id, b_data = get_block_1710(value)

            if b_id != 0:
                if section_blocks is None:
                    chunk = get_or_create_chunk_1710(current_region, cx, cz)
                    section = get_or_create_section_1710(chunk, current_y // 16)
                    section_blocks = section.blocks
                    section_data = section.data
                    section.is_empty = False

                for layer_index in LAYER_INDEXES[block_index:end_index]:
                    section_index = section_y_offset + layer_index
                    section_blocks[section_index] = b_id
                    if b_data:
                        set_data_nibble(section_data, section_index, b_data)

            block_index = end_index

            if block_index >= 256:
                break
        return block_index >= 256
    except Exception as e:
        return False

def apply_blocks_to_region_1710_slow(current_region, line, chunk_base_global_x, current_y, chunk_base_global_z):
    try:
        block_index = 0
        for segment in line.split('/'):
            if '-' not in segment:
                continue
            count_str, value = segment.split('-', 1)
            count = int(count_str)

            if count <= 0:
                continue

            b_id, b_data = get_block_1710(value)

            for _ in range(count):
                if block_index >= 256:
                    break
                if b_id != 0:
                    dx = block_index // 16
                    dz = block_index % 16
                    global_x = chunk_base_global_x + dx
                    global_z = chunk_base_global_z + dz

                    if current_region.inside(global_x, current_y, global_z):
                        cx = global_x // 16
                        cz = global_z // 16
                        chunk = get_or_create_chunk_1710(current_region, cx, cz)
                        chunk.set_block(b_id, b_data, global_x % 16, current_y, global_z % 16)

                block_index += 1

            if block_index >= 256:
                break
        return block_index >= 256
    except Exception:
        return False

def reset_runtime_caches():
    block_cache.clear()

def decode_log_line(raw_line):
    return clean_log_line(raw_line.decode("utf-8", errors="replace"))

def scan_region_ranges(input_path):
    ranges = []
    current = None

    with open(input_path, "rb") as f:
        f.readline()
        while True:
            line_start = f.tell()
            raw_line = f.readline()
            if not raw_line:
                break

            line = decode_log_line(raw_line)
            if not line.endswith(".r"):
                continue

            coords = extract_region_coords(line)
            if not coords:
                continue

            if current is not None:
                ranges.append((*current, line_start))
            current = (coords[0], coords[1], line_start)

        eof = f.tell()

    if current is not None:
        ranges.append((*current, eof))

    return ranges

def convert_region_range_1710(input_path, output_dir, region_task):
    reset_runtime_caches()
    load_block_ids()

    region_rx, region_rz, start_offset, end_offset = region_task
    current_region = None
    chunk_bx = 0
    chunk_bz = 0
    current_y = 0
    inside_chunk = False
    line_count = 0
    layer_count = 0
    started = time.perf_counter()

    with open(input_path, "rb") as f:
        f.seek(start_offset)
        while f.tell() < end_offset:
            raw_line = f.readline()
            if not raw_line:
                break

            line_count += 1
            line = decode_log_line(raw_line)
            if not line:
                continue

            if line.endswith(".r"):
                coords = extract_region_coords(line)
                if coords:
                    region_rx, region_rz = coords
                    current_region = anvil.EmptyRegion(region_rx, region_rz)
                inside_chunk = False
                continue

            if line == EMPTY_CHUNK_MARKER:
                inside_chunk = False
                continue

            if line.startswith(CHUNK_PREFIX):
                chunk_coords = extract_chunk_coords(line)
                if chunk_coords:
                    chunk_bx, chunk_bz = chunk_coords
                    current_y = 0
                    inside_chunk = True
                continue

            if inside_chunk and current_region:
                if apply_blocks_to_region_1710(current_region, line, chunk_bx, current_y, chunk_bz):
                    current_y += 1
                    layer_count += 1

    if current_region is None:
        current_region = anvil.EmptyRegion(region_rx, region_rz)

    light_started = time.perf_counter()
    calculate_region_lighting(current_region)
    light_seconds = time.perf_counter() - light_started

    os.makedirs(output_dir, exist_ok=True)
    save_path = os.path.join(output_dir, f"r.{region_rx}.{region_rz}.mca")
    save_started = time.perf_counter()
    current_region.save(save_path)
    save_seconds = time.perf_counter() - save_started

    return {
        "region": (region_rx, region_rz),
        "path": save_path,
        "lines": line_count,
        "layers": layer_count,
        "seconds": time.perf_counter() - started,
        "light_seconds": light_seconds,
        "save_seconds": save_seconds,
    }

def parse_jobs(value, region_count):
    if value == "auto":
        cpu_count = os.cpu_count() or 1
        return max(1, min(region_count, cpu_count, 2))

    jobs = int(value)
    if jobs < 1:
        raise ValueError("--jobs 必须是 auto 或大于 0 的整数")
    return min(jobs, max(region_count, 1))

def parse_args():
    parser = argparse.ArgumentParser(description="Convert TriggerScript.log to Minecraft 1.7.10 MCA region files.")
    parser.add_argument("--input", default="TriggerScript.log", help="输入日志文件路径，默认 TriggerScript.log")
    parser.add_argument("--output", default="MCA_Output_1710", help="输出目录，默认 MCA_Output_1710")
    parser.add_argument("--jobs", default="auto", help="并行区域数：auto 或整数；1710 的 auto 最多使用 2 个 worker")
    return parser.parse_args()

def main():
    args = parse_args()
    input_path = os.path.abspath(args.input)
    output_dir = os.path.abspath(args.output)
    load_block_ids()

    if not os.path.exists(input_path):
        print(f"未找到 {input_path} 文件！请确保已经在游戏内运行导出脚本。")
        return

    os.makedirs(output_dir, exist_ok=True)

    try:
        region_tasks = scan_region_ranges(input_path)
        jobs = parse_jobs(args.jobs, len(region_tasks))
    except Exception as e:
        print(f"初始化转换任务时发生错误: {e}")
        return

    if not region_tasks:
        print("未在日志中检测到任何 Region 头。")
        return

    print("=========================开始转换日志为1.7.10 MCA格式=========================")
    print(f"检测到 {len(region_tasks)} 个区域，jobs={jobs}")

    started = time.perf_counter()
    results = []
    if jobs == 1:
        for task in region_tasks:
            print(f"正在转换 r.{task[0]}.{task[1]}.mca")
            result = convert_region_range_1710(input_path, output_dir, task)
            results.append(result)
            print(
                f"保存区域文件: {result['path']} "
                f"({result['seconds']:.2f}s, 光照 {result['light_seconds']:.2f}s, 保存 {result['save_seconds']:.2f}s)"
            )
    else:
        with ProcessPoolExecutor(max_workers=jobs) as executor:
            futures = [
                executor.submit(convert_region_range_1710, input_path, output_dir, task)
                for task in region_tasks
            ]
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                    rx, rz = result["region"]
                    print(
                        f"保存区域文件: r.{rx}.{rz}.mca "
                        f"({result['seconds']:.2f}s, 光照 {result['light_seconds']:.2f}s, 保存 {result['save_seconds']:.2f}s)"
                    )
                except Exception as e:
                    print(f"转换区域时发生错误: {e}")

    total_seconds = time.perf_counter() - started
    total_layers = sum(result["layers"] for result in results)
    print(f"=========================转换全部完成！区域 {len(results)}/{len(region_tasks)}，层 {total_layers}，耗时 {total_seconds:.2f}s=========================")

if __name__ == "__main__":
    main()
