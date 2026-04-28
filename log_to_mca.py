import anvil
import argparse
import os
import re
import array
import math
import zlib
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from io import BytesIO
from nbt import nbt
from anvil.empty_section import bin_append
from anvil.empty_chunk import EmptyChunk
from anvil.empty_section import EmptySection
from anvil.empty_region import EmptyRegion

LOG_PREFIX_RE = re.compile(r'^\[\d{2}:\d{2}:\d{2}\]\[.*?\]\s*')
REGION_COORD_RE = re.compile(r'-?\d+')

# Log layer order is x-major (dx first, dz second), while anvil stores
# section blocks as y * 256 + z * 16 + x.
LAYER_INDEXES = tuple((i % 16) * 16 + (i // 16) for i in range(256))
BLOCK_KEY_CACHE = {}
AIR_BLOCK_KEY = ("minecraft", "air", ())
SECTION_META_CACHE = {}
COMPRESSION_LEVEL = 6
CHUNK_PREFIX = chr(0x533a)
EMPTY_CHUNK_MARKER = chr(0x7a7a)

def get_block_key(block):
    if block.namespace == "minecraft" and block.id == "air" and not block.properties:
        return AIR_BLOCK_KEY

    block_id = id(block)
    cached = BLOCK_KEY_CACHE.get(block_id)
    if cached is None or cached[0] is not block:
        key = (block.namespace, block.id, tuple(sorted(block.properties.items())))
        BLOCK_KEY_CACHE[block_id] = (block, key)
        return key

    return cached[1]

def create_empty_section_meta(section):
    meta = {
        "palette": [section.air],
        "lookup": {AIR_BLOCK_KEY: 0},
        "indexes": array.array("H", [0]) * 4096,
    }
    SECTION_META_CACHE[id(section)] = (section, meta)
    return meta

def build_section_meta(section):
    meta = create_empty_section_meta(section)
    for block_index, block in enumerate(section.blocks):
        if block is None:
            continue
        meta["indexes"][block_index] = get_palette_index(meta, block)
    return meta

def get_section_meta(section):
    cached = SECTION_META_CACHE.get(id(section))
    if cached is None or cached[0] is not section:
        return build_section_meta(section)

    return cached[1]

def get_section_meta_if_present(section):
    cached = SECTION_META_CACHE.get(id(section))
    if cached is None or cached[0] is not section:
        return None
    return cached[1]

def get_palette_index(meta, block):
    key = get_block_key(block)
    index = meta["lookup"].get(key)
    if index is None:
        index = len(meta["palette"])
        meta["lookup"][key] = index
        meta["palette"].append(block)
    return index

def pack_palette_indexes(indexes, palette_len):
    bits = max((palette_len - 1).bit_length(), 4)
    states = array.array("Q")

    if bits == 4:
        for start in range(0, 4096, 16):
            value = 0
            shift = 0
            for index in indexes[start:start + 16]:
                value |= (index & 0x0F) << shift
                shift += 4
            states.append(value)
        return states

    current = 0
    current_len = 0
    mask = (1 << bits) - 1
    for index in indexes:
        index &= mask
        if current_len + bits > 64:
            leftover = 64 - current_len
            states.append(bin_append(index & ((1 << leftover) - 1), current, length=current_len))
            current = index >> leftover
            current_len = bits - leftover
        else:
            current = bin_append(index, current, length=current_len)
            current_len += bits

    states.append(current)
    return states

def fast_palette(self):
    meta = get_section_meta_if_present(self)
    if meta is not None:
        return tuple(meta["palette"])

    palette = []
    seen = set()
    has_air = False

    for block in self.blocks:
        if block is None:
            has_air = True
            continue

        key = get_block_key(block)
        if key not in seen:
            seen.add(key)
            palette.append(block)

    if has_air:
        palette.append(self.air)

    return tuple(palette)

def fast_blockstates(self, palette=None):
    meta = get_section_meta_if_present(self)
    if meta is not None and (palette is None or len(palette) == len(meta["palette"])):
        return pack_palette_indexes(meta["indexes"], len(meta["palette"]))

    palette = palette or self.palette()
    palette_lookup = {get_block_key(block): index for index, block in enumerate(palette)}
    air_index = None
    bits = max((len(palette) - 1).bit_length(), 4)
    states = array.array('Q')
    current = 0
    current_len = 0

    for block in self.blocks:
        if block is None:
            if air_index is None:
                air_index = palette_lookup[get_block_key(self.air)]
            index = air_index
        else:
            index = palette_lookup[get_block_key(block)]
        if current_len + bits > 64:
            leftover = 64 - current_len
            states.append(bin_append(index & ((1 << leftover) - 1), current, length=current_len))
            current = index >> leftover
            current_len = bits - leftover
        else:
            current = bin_append(index, current, length=current_len)
            current_len += bits

    states.append(current)
    return states

def fast_chunk_save(self):
    root = nbt.NBTFile()
    root.tags.append(nbt.TAG_Int(name='DataVersion', value=self.version))
    level = nbt.TAG_Compound()
    level.name = 'Level'
    level.tags.extend([
        nbt.TAG_List(name='Entities', type=nbt.TAG_Compound),
        nbt.TAG_List(name='TileEntities', type=nbt.TAG_Compound),
        nbt.TAG_List(name='LiquidTicks', type=nbt.TAG_Compound),
        nbt.TAG_Int(name='xPos', value=self.x),
        nbt.TAG_Int(name='zPos', value=self.z),
        nbt.TAG_Long(name='LastUpdate', value=0),
        nbt.TAG_Long(name='InhabitedTime', value=0),
        nbt.TAG_Byte(name='isLightOn', value=1),
        nbt.TAG_String(name='Status', value='full')
    ])
    sections = nbt.TAG_List(name='Sections', type=nbt.TAG_Compound)
    for section in self.sections:
        if section:
            sections.tags.append(section.save())
    level.tags.append(sections)
    root.tags.append(level)
    return root

def fast_region_save(self, file=None):
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

EmptySection.palette = fast_palette
EmptySection.blockstates = fast_blockstates
EmptyChunk.save = fast_chunk_save
EmptyRegion.save = fast_region_save

# Block ID mappings
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
        # Default mapping fallback
        block_id_map["0"] = "air"
        block_id_map["1"] = "bedrock"
        block_id_map["3"] = "water"
        block_id_map["4"] = "water"
        block_id_map["5"] = "lava"
        block_id_map["6"] = "lava"
        block_id_map["25"] = "stone"
        block_id_map["101"] = "dirt"

def convert_block_id(custom_id):
    custom_id_str = str(custom_id)
    if custom_id_str in block_id_map:
        return block_id_map[custom_id_str]
    else:
        block_id_map[custom_id_str] = "dirt"
        return "dirt"

def convert_block_state(custom_id):
    custom_id_str = str(custom_id)
    mc_block = convert_block_id(custom_id_str)
    properties = {}

    if mc_block.endswith("_slab"):
        properties.update({
            "type": "bottom",
            "waterlogged": "false",
        })
    elif mc_block.endswith("_stairs"):
        properties.update({
            "facing": "north",
            "half": "bottom",
            "shape": "straight",
            "waterlogged": "false",
        })

    if mc_block.endswith("_leaves"):
        properties["persistent"] = "true"

    return mc_block, properties

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

block_cache = {}
air_value_cache = {}

def is_air_value(value):
    if value not in air_value_cache:
        block_name, _ = convert_block_state(value)
        air_value_cache[value] = block_name == "air"
    return air_value_cache[value]

def get_anvil_block(value):
    if value not in block_cache:
        mc_block, properties = convert_block_state(value)
        block_cache[value] = anvil.Block('minecraft', mc_block, properties=properties)
    return block_cache[value]

def get_or_create_chunk(region, cx, cz):
    chunk_index = (cz % 32) * 32 + (cx % 32)
    chunk = region.chunks[chunk_index]
    if chunk is None:
        chunk = EmptyChunk(cx, cz)
        region.chunks[chunk_index] = chunk
    return chunk

def get_or_create_section(chunk, sy):
    section = chunk.sections[sy]
    if section is None:
        section = EmptySection(sy)
        chunk.sections[sy] = section
        create_empty_section_meta(section)
    return section

def apply_blocks_to_region(current_region, line, chunk_bx, current_y, chunk_bz):
    try:
        if current_y < 0 or current_y > 255:
            return False

        cx = chunk_bx // 16
        cz = chunk_bz // 16
        if not current_region.inside(cx, 0, cz, chunk=True):
            return False

        local_x_base = chunk_bx % 16
        local_z_base = chunk_bz % 16
        if local_x_base != 0 or local_z_base != 0:
            return apply_blocks_to_region_slow(current_region, line, chunk_bx, current_y, chunk_bz)

        chunk = None
        section_blocks = None
        section_meta = None
        section_indexes = None
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

            if not is_air_value(value):
                if section_blocks is None:
                    chunk = get_or_create_chunk(current_region, cx, cz)
                    section = get_or_create_section(chunk, current_y // 16)
                    section_blocks = section.blocks
                    section_meta = get_section_meta(section)
                    section_indexes = section_meta["indexes"]

                anvil_block = get_anvil_block(value)
                palette_index = get_palette_index(section_meta, anvil_block)
                for layer_index in LAYER_INDEXES[block_index:end_index]:
                    section_index = section_y_offset + layer_index
                    section_blocks[section_index] = anvil_block
                    section_indexes[section_index] = palette_index

            block_index = end_index

            if block_index >= 256:
                break
        return block_index == 256
    except Exception:
        return False

def apply_blocks_to_region_slow(current_region, line, chunk_bx, current_y, chunk_bz):
    try:
        block_index = 0
        for segment in line.split('/'):
            if '-' not in segment:
                continue
            count_str, value = segment.split('-', 1)
            count = int(count_str)

            if count <= 0:
                continue

            anvil_block = None if is_air_value(value) else get_anvil_block(value)

            for _ in range(count):
                if block_index >= 256:
                    break
                if anvil_block is not None:
                    dx = block_index // 16
                    dz = block_index % 16
                    current_region.set_block(anvil_block, chunk_bx + dx, current_y, chunk_bz + dz)
                block_index += 1

            if block_index >= 256:
                break
        return block_index == 256
    except Exception:
        return False

def reset_runtime_caches():
    block_cache.clear()
    air_value_cache.clear()
    BLOCK_KEY_CACHE.clear()
    SECTION_META_CACHE.clear()

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

def convert_region_range(input_path, output_dir, region_task):
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
                    current_region = anvil.EmptyRegion(coords[0], coords[1])
                    region_rx, region_rz = coords
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
                if apply_blocks_to_region(current_region, line, chunk_bx, current_y, chunk_bz):
                    current_y += 1
                    layer_count += 1

    if current_region is None:
        current_region = anvil.EmptyRegion(region_rx, region_rz)

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
        "save_seconds": save_seconds,
    }

def parse_jobs(value, region_count):
    if value == "auto":
        cpu_count = os.cpu_count() or 1
        return max(1, min(region_count, cpu_count))

    jobs = int(value)
    if jobs < 1:
        raise ValueError("--jobs 必须是 auto 或大于 0 的整数")
    return min(jobs, max(region_count, 1))

def parse_args():
    parser = argparse.ArgumentParser(description="Convert TriggerScript.log to Minecraft MCA region files.")
    parser.add_argument("--input", default="TriggerScript.log", help="输入日志文件路径，默认 TriggerScript.log")
    parser.add_argument("--output", default="MCA_Output", help="输出目录，默认 MCA_Output")
    parser.add_argument("--jobs", default="auto", help="并行区域数：auto 或整数；默认 auto")
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

    print("=========================开始直接转换日志为MCA=========================")
    print(f"检测到 {len(region_tasks)} 个区域，jobs={jobs}")

    started = time.perf_counter()
    results = []
    if jobs == 1:
        for task in region_tasks:
            print(f"正在转换 r.{task[0]}.{task[1]}.mca")
            result = convert_region_range(input_path, output_dir, task)
            results.append(result)
            print(f"保存区域文件: {result['path']} ({result['seconds']:.2f}s, 保存 {result['save_seconds']:.2f}s)")
    else:
        with ProcessPoolExecutor(max_workers=jobs) as executor:
            futures = [
                executor.submit(convert_region_range, input_path, output_dir, task)
                for task in region_tasks
            ]
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                    rx, rz = result["region"]
                    print(f"保存区域文件: r.{rx}.{rz}.mca ({result['seconds']:.2f}s, 保存 {result['save_seconds']:.2f}s)")
                except Exception as e:
                    print(f"转换区域时发生错误: {e}")

    total_seconds = time.perf_counter() - started
    total_layers = sum(result["layers"] for result in results)
    print(f"=========================转换全部完成！区域 {len(results)}/{len(region_tasks)}，层 {total_layers}，耗时 {total_seconds:.2f}s=========================")

if __name__ == "__main__":
    main()
