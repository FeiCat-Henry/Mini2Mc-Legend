import os

# 获取当前目录下所有.r文件
def get_all_r_files():
    current_dir = os.getcwd()
    r_files = []
    # 遍历当前目录及其子目录
    for root, dirs, files in os.walk(current_dir):
        for file in files:
            if file.endswith('.r'):
                # 获取相对路径，只保留文件名
                r_files.append(file)
    return r_files

# 生成Lua表格式的字符串
def generate_table_string(file_list):
    # 排序文件列表，使输出更整齐
    file_list.sort()
    # 构建表字符串
    table_str = 'asd ={'
    for i, file_name in enumerate(file_list):
        table_str += f'"{file_name}"'
        # 除了最后一个元素外，其他元素后面都加逗号
        if i < len(file_list) - 1:
            table_str += ', '
    table_str += ', }'
    return table_str

# 主函数
def main():
    # 获取所有.r文件
    r_files = get_all_r_files()
    
    if not r_files:
        print("没有找到.r文件")
        return
    
    # 生成表字符串
    table_str = generate_table_string(r_files)
    
    # 输出到txt文件
    output_file = 'Map_asd.txt'
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(table_str)
    
    print(f"已成功将{len(r_files)}个.r文件的文件名输出到{output_file}")
    print("生成的表内容:")
    print(table_str)

if __name__ == "__main__":
    main()
