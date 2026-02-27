import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import os
import re

class BlockIDManager:
    def __init__(self, root):
        self.root = root
        self.root.title("Mini2MC Legend 方块映射表编辑器")
        self.root.geometry("800x600")
        
        # 文件路径
        self.file_path = "block_id_data.txt"
        
        # 存储数据的字典
        self.block_id_map = {}
        # 存储所有行的原始数据（包括注释和重复项）
        self.all_lines = []
        
        # 创建UI
        self.create_widgets()
        
        # 加载文件数据
        self.load_data()
        
    def create_widgets(self):
        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 输入区域
        input_frame = ttk.LabelFrame(main_frame, text="添加新的Block ID", padding="10")
        input_frame.pack(fill=tk.X, pady=5)
        
        # 数字ID输入
        ttk.Label(input_frame, text="迷你世界数字ID:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.id_entry = ttk.Entry(input_frame, width=20)
        self.id_entry.grid(row=0, column=1, padx=5, pady=5)
        
        # 单词ID输入
        ttk.Label(input_frame, text="MC单词ID:").grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)
        self.name_entry = ttk.Entry(input_frame, width=30)
        self.name_entry.grid(row=0, column=3, padx=5, pady=5)
        
        # 注释输入
        ttk.Label(input_frame, text="注释(可选):").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        self.comment_entry = ttk.Entry(input_frame, width=50)
        self.comment_entry.grid(row=1, column=1, columnspan=3, padx=5, pady=5, sticky=tk.W)
        
        # 添加按钮
        self.add_button = ttk.Button(input_frame, text="添加", command=self.add_block_id)
        self.add_button.grid(row=0, column=4, rowspan=2, padx=10, pady=5, sticky=tk.NS)
        
        # 保存按钮
        self.save_button = ttk.Button(input_frame, text="保存文件", command=self.save_data)
        self.save_button.grid(row=0, column=5, rowspan=2, padx=10, pady=5, sticky=tk.NS)
        
        # 排序按钮
        self.sort_button = ttk.Button(input_frame, text="按ID排序", command=self.sort_and_display)
        self.sort_button.grid(row=0, column=6, rowspan=2, padx=10, pady=5, sticky=tk.NS)
        
        # 显示区域
        display_frame = ttk.LabelFrame(main_frame, text="Block ID列表", padding="10")
        display_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 创建滚动文本框
        self.text_area = scrolledtext.ScrolledText(display_frame, wrap=tk.WORD, width=90, height=25)
        self.text_area.pack(fill=tk.BOTH, expand=True)
        
        # 统计信息
        self.status_var = tk.StringVar()
        self.status_var.set("就绪")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
    def load_data(self):
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as file:
                    self.all_lines = file.readlines()
                    
                # 解析数据到字典
                self.block_id_map = {}
                for line in self.all_lines:
                    # 尝试匹配 block_id_map["数字id"]="单词id" 格式
                    match = re.search(r'block_id_map\["(\d+)"\]="([^"]+)"', line.strip())
                    if match:
                        block_id = match.group(1)
                        block_name = match.group(2)
                        self.block_id_map[block_id] = block_name
                
                # 显示数据
                self.display_data()
                self.status_var.set(f"已加载 {len(self.all_lines)} 行数据，包含 {len(self.block_id_map)} 个唯一ID")
            else:
                messagebox.showinfo("提示", "文件不存在，将创建新文件")
        except Exception as e:
            messagebox.showerror("错误", f"加载文件时出错: {str(e)}")
    
    def display_data(self):
        self.text_area.delete(1.0, tk.END)
        for line in self.all_lines:
            self.text_area.insert(tk.END, line)
    
    def add_block_id(self):
        block_id = self.id_entry.get().strip()
        block_name = self.name_entry.get().strip()
        comment = self.comment_entry.get().strip()
        
        if not block_id or not block_name:
            messagebox.showwarning("警告", "数字ID和单词ID都不能为空")
            return
        
        # 检查ID是否为数字
        if not block_id.isdigit():
            messagebox.showwarning("警告", "数字ID必须是纯数字")
            return
        
        # 创建新条目
        new_line = f'    block_id_map["{block_id}"]="{block_name}"'
        if comment:
            new_line += f"                  # {comment}"
        new_line += "\n"
        
        # 检查是否已存在相同ID
        if block_id in self.block_id_map:
            existing_line_index = None
            for i, line in enumerate(self.all_lines):
                if f'block_id_map["{block_id}"]' in line:
                    existing_line_index = i
                    break
            
            if existing_line_index is not None:
                current_value = self.block_id_map[block_id]
                response = messagebox.askyesno(
                    "确认替换", 
                    f"ID {block_id} 已存在，当前值为: {current_value}\n位于第 {existing_line_index + 1} 行\n是否替换？"
                )
                if not response:
                    return
                
                # 执行替换操作
                self.all_lines[existing_line_index] = new_line
                self.status_var.set(f"已替换ID {block_id} 的值为 {block_name}")
        else:
            # 添加新条目
            self.all_lines.append(new_line)
            self.status_var.set(f"已添加ID {block_id} -> {block_name}")
        
        # 更新映射字典
        self.block_id_map[block_id] = block_name
        
        # 排序并显示
        self.sort_and_display()
        
        # 清空输入框
        self.id_entry.delete(0, tk.END)
        self.name_entry.delete(0, tk.END)
        self.comment_entry.delete(0, tk.END)
    
    def sort_and_display(self):
        try:
            # 解析所有有效行（不包含注释行）
            valid_lines = []
            comment_lines = []
            
            for line in self.all_lines:
                if line.strip().startswith('#') or not line.strip():
                    comment_lines.append(line)  # 保存注释行和空行
                else:
                    # 尝试提取ID进行排序
                    match = re.search(r'block_id_map\["(\d+)"\]', line)
                    if match:
                        block_id = int(match.group(1))
                        valid_lines.append((block_id, line))
                    else:
                        comment_lines.append(line)  # 无法识别的行作为注释行处理
            
            # 按ID排序
            valid_lines.sort(key=lambda x: x[0])
            
            # 重新组合所有行
            self.all_lines = [line for _, line in valid_lines] + comment_lines
            
            # 显示排序后的数据
            self.display_data()
            
            self.status_var.set(f"已按ID排序，共 {len(self.all_lines)} 行数据")
        except Exception as e:
            messagebox.showerror("错误", f"排序时出错: {str(e)}")
    
    def save_data(self):
        try:
            with open(self.file_path, 'w', encoding='utf-8') as file:
                file.writelines(self.all_lines)
            
            messagebox.showinfo("成功", f"数据已保存到 {self.file_path}")
            self.status_var.set(f"已保存 {len(self.all_lines)} 行数据")
        except Exception as e:
            messagebox.showerror("错误", f"保存文件时出错: {str(e)}")

if __name__ == "__main__":
    root = tk.Tk()
    app = BlockIDManager(root)
    root.mainloop()
