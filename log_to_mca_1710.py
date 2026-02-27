import os
import re

from anvil.legacy import LEGACY_ID_MAP
from nbt import nbt
import math
import zlib
from io import BytesIO
import anvil

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
    s = s.strip()
    return re.sub(r'^\[\d{2}:\d{2}:\d{2}\]\[.*?\]\s*', '', s).strip()

def extract_region_coords(filename):
    numbers = re.findall(r'-?\d+', filename)
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
        self.skylight = bytearray([0xFF] * 2048)  # Force MC to calculate SkyLight
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
            nbt.TAG_Byte(name='LightPopulated', value=0),
            nbt.TAG_Byte(name='TerrainPopulated', value=1),
            nbt.TAG_Byte(name='V', value=1),
            nbt.TAG_Long(name='InhabitedTime', value=0),
            nbt.TAG_List(name='Entities', type=nbt.TAG_Compound),
            nbt.TAG_List(name='TileEntities', type=nbt.TAG_Compound),
        ])

        heightmap_tag = nbt.TAG_Int_Array(name='HeightMap')
        # We start with 256 integers. Let Minecraft recalculate light 
        heightmap_tag.value = [0] * 256
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

block_cache = {}

def apply_blocks_to_region_1710(current_region, line, chunk_base_global_x, current_y, chunk_base_global_z):
    try:
        block_index = 0
        for segment in line.split('/'):
            if '-' not in segment:
                continue
            count_str, value = segment.split('-')
            count = int(count_str)

            if value not in block_cache:
                block_cache[value] = convert_block_1710(value)
            b_id, b_data = block_cache[value]

            for _ in range(count):
                if block_index >= 256:
                    break
                dx = block_index // 16
                dz = block_index % 16

                # Manual inline implementation of setting 1.7.10 chunk block
                global_x = chunk_base_global_x + dx
                global_z = chunk_base_global_z + dz
                y = current_y

                if global_x >= current_region.x * 512 and global_x < (current_region.x + 1) * 512:
                    if global_z >= current_region.z * 512 and global_z < (current_region.z + 1) * 512:
                        cx = global_x // 16
                        cz = global_z // 16
                        chunk_idx = (cz % 32) * 32 + (cx % 32)

                        if current_region.chunks[chunk_idx] is None:
                            current_region.chunks[chunk_idx] = EmptyChunk1710(cx, cz)

                        current_region.chunks[chunk_idx].set_block(b_id, b_data, global_x % 16, y, global_z % 16)

                block_index += 1
        return block_index >= 256
    except Exception as e:
        return False

def main():
    load_block_ids()

    if not os.path.exists("TriggerScript.log"):
        print("未找到 TriggerScript.log 文件！请确保已经在游戏内运行导出脚本。")
        return

    output_dir = "MCA_Output_1710"
    os.makedirs(output_dir, exist_ok=True)

    print("=========================开始转换日志为1.7.10 MCA格式=========================")

    with open("TriggerScript.log", "r", encoding="utf-8") as f:
        # 丢弃第一行
        f.readline()

        current_region = None
        region_rx = 0
        region_rz = 0

        chunk_bx = 0
        chunk_bz = 0
        current_y = 0
        inside_chunk = False

        line_count = 0

        for raw_line in f:
            line_count += 1
            if line_count % 50000 == 0:
                print(f"已处理 {line_count} 行日志...")

            line = clean_log_line(raw_line)
            if not line:
                continue

            # 检测是否是新文件头（Region分界线）
            if line.endswith(".r"):
                # 如果之前有打开的 Region，先保存它
                if current_region:
                    mca_name = f'r.{region_rx}.{region_rz}.mca'
                    save_path = os.path.join(output_dir, mca_name)
                    print(f"保存上一个区域文件(1.7.10格式): {save_path}")
                    try:
                        current_region.save(save_path)
                    except Exception as e:
                        print(f"保存文件 {save_path} 时发生错误: {e}")

                # 创建新 Region
                coords = extract_region_coords(line)
                if coords:
                    region_rx, region_rz = coords
                    print(f"检测到新区域开始: {line} -> 正在创建 r.{region_rx}.{region_rz}.mca")
                    # Usable anvil EmptyRegion 
                    current_region = anvil.EmptyRegion(region_rx, region_rz)
                inside_chunk = False
                continue

            # 区块空置结束符
            if line == "空":
                inside_chunk = False
                continue

            # 区块坐标头 "区0/0"
            if line.startswith("区"):
                chunk_coords = extract_chunk_coords(line)
                if chunk_coords:
                    chunk_bx, chunk_bz = chunk_coords
                    current_y = 0
                    inside_chunk = True
                continue

            # 层级数据
            if inside_chunk and current_region:
                if apply_blocks_to_region_1710(current_region, line, chunk_bx, current_y, chunk_bz):
                    current_y += 1

        # 文件末尾：保存最后一个打开的 Region
        if current_region:
            mca_name = f'r.{region_rx}.{region_rz}.mca'
            save_path = os.path.join(output_dir, mca_name)
            print(f"保存最后的区域文件(1.7.10格式): {save_path}")
            try:
                current_region.save(save_path)
            except Exception as e:
                print(f"保存文件 {save_path} 时发生错误: {e}")

    print("=========================转换全部完成！=========================")

if __name__ == "__main__":
    main()
