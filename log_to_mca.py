import anvil
import os
import re

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

block_cache = {}

def apply_blocks_to_region(current_region, line, chunk_bx, current_y, chunk_bz):
    try:
        block_index = 0
        for segment in line.split('/'):
            if '-' not in segment:
                continue
            count_str, value = segment.split('-')
            count = int(count_str)

            if value not in block_cache:
                mc_block = convert_block_id(value)
                block_cache[value] = anvil.Block('minecraft', mc_block)
            anvil_block = block_cache[value]

            for _ in range(count):
                if block_index >= 256:
                    break
                dx = block_index // 16
                dz = block_index % 16
                try:
                    current_region.set_block(
                        anvil_block,
                        chunk_bx + dx,
                        current_y,
                        chunk_bz + dz
                    )
                except Exception:
                    pass
                block_index += 1
        return block_index == 256
    except Exception:
        return False

def main():
    load_block_ids()

    if not os.path.exists("TriggerScript.log"):
        print("未找到 TriggerScript.log 文件！请确保已经在游戏内运行导出脚本。")
        return

    output_dir = "MCA_Output"
    os.makedirs(output_dir, exist_ok=True)

    print("=========================开始直接转换日志为MCA=========================")

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
                    print(f"保存上一个区域文件: {save_path}")
                    try:
                        current_region.save(save_path)
                    except Exception as e:
                        print(f"保存文件 {save_path} 时发生错误: {e}")

                # 创建新 Region
                coords = extract_region_coords(line)
                if coords:
                    region_rx, region_rz = coords
                    print(f"检测到新区域开始: {line} -> 正在创建 r.{region_rx}.{region_rz}.mca")
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
                if apply_blocks_to_region(current_region, line, chunk_bx, current_y, chunk_bz):
                    current_y += 1

        # 文件末尾：保存最后一个打开的 Region
        if current_region:
            mca_name = f'r.{region_rx}.{region_rz}.mca'
            save_path = os.path.join(output_dir, mca_name)
            print(f"保存最后的区域文件: {save_path}")
            try:
                current_region.save(save_path)
            except Exception as e:
                print(f"保存文件 {save_path} 时发生错误: {e}")

    print("=========================转换全部完成！=========================")

if __name__ == "__main__":
    main()
