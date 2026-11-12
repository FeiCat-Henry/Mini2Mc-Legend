#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mini2MC 映射表编辑器  ·  mc_block_map.json
=========================================================
直接读写 mc_block_map.json（唯一映射来源），不再有 txt 源表。

JSON 结构：
  {
    "_readme":   [说明文字],
    "id_mask":   4095,                     // 方块值低 12 位是 id
    "fallback":  "minecraft:dirt",         // 表里没有的 id 落成什么（土块占位）
    "dims":      {"m0": {}, "m1": {}, "m2": {}},
    "common":    {"16": "minecraft:grass", ...},   // 全局 id → MC 方块名
    "notes":     {"16": "草方块", ...}             // 可选，只是注释，转换时不用
  }
dims 里的键是维度名，值是「id → 方块名」的覆盖表，优先于 common（一般是空的）。

用法：
  python block_id_Editor.py                  // 有 Tk 开窗口，没有就进命令行
  python block_id_Editor.py --list           // 打印全部条目
  python block_id_Editor.py --set 116 stone  // 新增/修改（名字不用写 minecraft:）
  python block_id_Editor.py --set 116 stone "草方块"
  python block_id_Editor.py --del 116
  python block_id_Editor.py --check          // 只检查、不写
  python block_id_Editor.py --file D:\\x\\mc_block_map.json   // 指定文件

文件位置：默认按「当前目录 → 本文件所在目录 → 项目根目录」找 mc_block_map.json。
每次写盘前会把旧文件备份成 mc_block_map.json.bak（只留一份）。
"""
import copy
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FILENAME = 'mc_block_map.json'
PREFIX = 'minecraft:'


# ============================================================ 文件定位 / 读写
def find_file(name=FILENAME):
    for c in (os.path.join(os.getcwd(), name), os.path.join(HERE, name), os.path.join(ROOT, name)):
        if os.path.exists(c):
            return os.path.abspath(c)
    return os.path.abspath(os.path.join(os.getcwd(), name))


def load(path):
    if not os.path.isfile(path):
        raise SystemExit('找不到映射表：%s' % path)
    with io.open(path, 'r', encoding='utf-8') as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise SystemExit('映射表不是一个 JSON 对象：%s' % path)
    doc.setdefault('id_mask', 4095)
    doc.setdefault('fallback', PREFIX + 'dirt')
    if not isinstance(doc.get('common'), dict):
        doc['common'] = {}
    if not isinstance(doc.get('notes'), dict):
        doc['notes'] = {}
    if not isinstance(doc.get('dims'), dict):
        doc['dims'] = {}
    return doc


def save(path, doc):
    """写盘：先备份旧文件，再原子替换，保持中文不转义、键顺序稳定"""
    d = {'_readme': doc.get('_readme', []),
         'id_mask': int(doc.get('id_mask', 4095)),
         'fallback': str(doc.get('fallback', PREFIX + 'dirt')),
         'dims': doc.get('dims', {}),
         'common': {str(k): v for k, v in sorted(
             ((int(k), v) for k, v in doc['common'].items()), key=lambda kv: kv[0])}}
    notes = {str(k): v for k, v in sorted(
        ((int(k), v) for k, v in doc.get('notes', {}).items() if str(k).isdigit()),
        key=lambda kv: kv[0])}
    if notes:
        d['notes'] = notes
    # 顶层的其它自定义键原样带上（notes 已在上面按有无决定是否写）
    for k, v in doc.items():
        if k != 'notes' and k not in d:
            d[k] = v
    if os.path.isfile(path):
        shutil.copyfile(path, path + '.bak')
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, path)
    return path


# ============================================================ 名字规范化 / 校验
def bare(name):
    s = str(name).strip()
    if s.lower().startswith(PREFIX):
        s = s[len(PREFIX):]
    return s


def norm(name):
    """统一成 JSON 里的值：minecraft:小写_下划线"""
    s = bare(name).lower().replace(' ', '_')
    return PREFIX + s


def check(doc):
    """返回 (list of 问题, 统计 dict)；不写文件"""
    mask = int(doc.get('id_mask', 4095))
    common = doc['common']
    issues = []
    ids = []
    for k, v in common.items():
        ks = str(k).strip()
        if not ks.isdigit():
            issues.append('键不是数字：%r → %r' % (k, v))
            continue
        i = int(ks)
        ids.append(i)
        if i > mask:
            issues.append('id %d 超出 id_mask=0x%X，转换时会被忽略' % (i, mask))
        if not isinstance(v, str) or not v.strip():
            issues.append('id %d 的名字是空的' % i)
            continue
        s = bare(v)
        if not s:
            issues.append('id %d 的名字只有前缀' % i)
        elif ' ' in s or s.upper() == s and s.isalpha():
            issues.append('id %d 的名字不规范（有大写或空格，转换时会自动规范）: %s' % (i, v))
    dup = len(common) - len(set(str(k) for k in common))
    if dup:
        issues.append('有 %d 个重复键（JSON 里后者覆盖前者）' % dup)
    stats = {'count': len(common), 'ids': (min(ids), max(ids)) if ids else (None, None),
             'mask': mask, 'fallback': doc.get('fallback')}
    return issues, stats


def upsert(doc, ident, name, note=''):
    mask = int(doc.get('id_mask', 4095))
    if not str(ident).strip().isdigit():
        raise SystemExit('数字 ID 必须是纯数字：%r' % ident)
    i = int(str(ident).strip())
    if i > mask:
        raise SystemExit('id %d 超过 id_mask=0x%X，转换时会被忽略（上限 %d）' % (i, mask, mask))
    nm = norm(name)
    if len(bare(nm)) < 2:
        raise SystemExit('方块名不像话：%r' % name)
    key = str(i)
    old = doc['common'].get(key)
    doc['common'][key] = nm
    if note.strip():
        doc.setdefault('notes', {})[key] = note.strip()
    return key, old, nm


# ============================================================ 图形界面
def run_gui(path):
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    class Editor(object):
        def __init__(self, root, path):
            self.root = root
            self.path = path
            self.doc = load(path)
            root.title('Mini2MC 映射表编辑器 — mc_block_map.json')
            root.geometry('860x620')
            self.v_file = tk.StringVar(value=path)
            self.v_id = tk.StringVar()
            self.v_name = tk.StringVar()
            self.v_note = tk.StringVar()
            self.v_find = tk.StringVar()
            self.v_status = tk.StringVar(value='就绪')
            self._build()
            self.refresh()

        # ---------- 布局
        def _build(self):
            head = ttk.Frame(self.root, padding=(12, 10, 12, 4))
            head.pack(fill='x')
            ttk.Label(head, text='映射表', width=6).pack(side='left')
            ttk.Entry(head, textvariable=self.v_file, state='readonly').pack(
                side='left', fill='x', expand=True)
            ttk.Button(head, text='打开…', width=7, command=self.on_open).pack(side='left', padx=(6, 0))
            ttk.Button(head, text='重载', width=6, command=self.on_reload).pack(side='left', padx=(4, 0))

            box = ttk.LabelFrame(self.root, text='新增 / 修改一条', padding=10)
            box.pack(fill='x', padx=12)
            ttk.Label(box, text='数字 ID').grid(row=0, column=0, sticky='w')
            ttk.Entry(box, textvariable=self.v_id, width=10).grid(row=0, column=1, padx=(4, 12))
            ttk.Label(box, text='MC 方块名').grid(row=0, column=2, sticky='w')
            ttk.Entry(box, textvariable=self.v_name, width=28).grid(row=0, column=3, padx=(4, 12))
            ttk.Label(box, text='注释(可选)').grid(row=0, column=4, sticky='w')
            ttk.Entry(box, textvariable=self.v_note, width=18).grid(row=0, column=5, padx=(4, 8))
            ttk.Button(box, text='写入', width=8, command=self.on_upsert).grid(row=0, column=6)
            ttk.Label(box, text='名字不用写 minecraft:，大小写和空格会被自动规范',
                      foreground='#666').grid(row=1, column=0, columnspan=7, sticky='w', pady=(6, 0))

            bar = ttk.Frame(self.root, padding=(12, 8, 12, 4))
            bar.pack(fill='x')
            ttk.Button(bar, text='保存到文件', command=self.on_save).pack(side='left')
            ttk.Button(bar, text='删除选中', command=self.on_delete).pack(side='left', padx=(6, 0))
            ttk.Button(bar, text='检查', command=self.on_check).pack(side='left', padx=(6, 0))
            ttk.Label(bar, text='  搜索').pack(side='left')
            e = ttk.Entry(bar, textvariable=self.v_find, width=22)
            e.pack(side='left', padx=(4, 0))
            e.bind('<KeyRelease>', lambda ev: self.refresh())

            # 表格
            wrap = ttk.Frame(self.root, padding=(12, 0, 12, 6))
            wrap.pack(fill='both', expand=True)
            cols = ('id', 'name', 'note')
            self.tree = ttk.Treeview(wrap, columns=cols, show='headings', selectmode='browse')
            for c, t, w in (('id', 'ID', 90), ('name', '方块名', 420), ('note', '注释', 300)):
                self.tree.heading(c, text=t)
                self.tree.column(c, width=w, anchor='w')
            sb = ttk.Scrollbar(wrap, orient='vertical', command=self.tree.yview)
            self.tree.configure(yscrollcommand=sb.set)
            self.tree.pack(side='left', fill='both', expand=True)
            sb.pack(side='right', fill='y')
            self.tree.bind('<<TreeviewSelect>>', self.on_pick)

            ttk.Label(self.root, textvariable=self.v_status, relief='sunken',
                      anchor='w', padding=(8, 3)).pack(side='bottom', fill='x')

        # ---------- 数据
        def refresh(self, keep_focus=False):
            kw = self.v_find.get().strip().lower()
            self.tree.delete(*self.tree.get_children())
            notes = self.doc.get('notes', {})
            n = 0
            for k, v in sorted(((int(a), b) for a, b in self.doc['common'].items()
                                if str(a).isdigit()), key=lambda kv: kv[0]):
                name = bare(v)
                note = notes.get(str(k), '')
                if kw and kw not in ('%d' % k) and kw not in name.lower() and kw not in note.lower():
                    continue
                self.tree.insert('', 'end', values=(k, name, note))
                n += 1
            ids = [int(a) for a in self.doc['common'] if str(a).isdigit()]
            rng = '%s..%s' % (min(ids), max(ids)) if ids else '空'
            self.v_status.set('条目 %d（显示 %d），id 范围 %s；fallback=%s；id_mask=0x%X'
                              % (len(self.doc['common']), n, rng,
                                 self.doc.get('fallback'), int(self.doc.get('id_mask', 4095))))

        def on_pick(self, _ev=None):
            sel = self.tree.selection()
            if not sel:
                return
            vals = self.tree.item(sel[0], 'values')
            self.v_id.set(vals[0])
            self.v_name.set(vals[1])
            self.v_note.set(vals[2])

        def on_upsert(self):
            try:
                key, old, nm = upsert(self.doc, self.v_id.get(), self.v_name.get(), self.v_note.get())
            except SystemExit as e:
                messagebox.showwarning('不能写入', str(e))
                return
            self.refresh()
            self.v_name.set(bare(nm))
            self.v_status.set(('已改 id %s：%s → %s' % (key, bare(old), bare(nm))) if old
                              else '已加 id %s → %s' % (key, bare(nm)))

        def on_delete(self):
            sel = self.tree.selection()
            if not sel:
                messagebox.showinfo('提示', '先在表格里选一条')
                return
            k = self.tree.item(sel[0], 'values')[0]
            if not messagebox.askyesno('确认删除', '删除 id %s？点「保存到文件」后才真正生效。' % k):
                return
            self.doc['common'].pop(str(k), None)
            self.doc.get('notes', {}).pop(str(k), None)
            self.refresh()
            self.v_status.set('已删 id %s（未保存）' % k)

        def on_check(self):
            issues, stats = check(self.doc)
            if issues:
                messagebox.showwarning('检查结果',
                                       '共 %d 条，id 范围 %s..%s\n\n' % (stats['count'], stats['ids'][0],
                                                                    stats['ids'][1])
                                       + '\n'.join('- ' + s for s in issues[:20]))
            else:
                messagebox.showinfo('检查结果',
                                    '共 %d 条，id 范围 %s..%s，没有发现问题。'
                                    % (stats['count'], stats['ids'][0], stats['ids'][1]))

        def on_save(self):
            try:
                save(self.path, self.doc)
            except Exception as e:
                messagebox.showerror('保存失败', str(e))
                return
            self.refresh()
            messagebox.showinfo('已保存', '写入 %s\n旧文件备份为 %s.bak' % (self.path, FILENAME))
            self.v_status.set('已保存 %d 条' % len(self.doc['common']))

        def on_reload(self):
            try:
                self.doc = load(self.path)
            except SystemExit as e:
                messagebox.showerror('读取失败', str(e))
                return
            self.refresh()
            self.v_status.set('已重新载入')

        def on_open(self):
            p = filedialog.askopenfilename(initialdir=os.path.dirname(self.path) or ROOT,
                                           filetypes=[('映射表 JSON', '*.json'), ('全部', '*.*')])
            if not p:
                return
            try:
                self.doc = load(p)
            except SystemExit as e:
                messagebox.showerror('读取失败', str(e))
                return
            self.path = p
            self.v_file.set(p)
            self.refresh()
            self.v_status.set('已打开 %s' % p)

    root = tk.Tk()
    Editor(root, path)
    root.mainloop()


# ============================================================ 命令行
HELP = """命令：
  list [关键字]      列出条目（可按 id 或名字过滤）
  set <id> <名字> [注释]   新增/修改（名字不用写 minecraft:）
  del <id>           删除
  check              检查一遍（不写盘）
  save               写盘（自动备份 .bak）
  path               显示当前文件
  quit               退出（未保存的改动会提醒）
"""


def cmd_list(doc, kw=''):
    kw = (kw or '').strip().lower()
    notes = doc.get('notes', {})
    for k, v in sorted(((int(a), b) for a, b in doc['common'].items() if str(a).isdigit()),
                       key=lambda kv: kv[0]):
        nm = bare(v)
        note = notes.get(str(k), '')
        if kw and kw not in ('%d' % k) and kw not in nm.lower() and kw not in note.lower():
            continue
        print('%5d  %-34s %s' % (k, nm, note))


def cmd_check(doc):
    issues, stats = check(doc)
    print('条目 %d，id 范围 %s..%s，id_mask=0x%X，fallback=%s'
          % (stats['count'], stats['ids'][0], stats['ids'][1], stats['mask'], stats['fallback']))
    if not issues:
        print('没有发现问题。')
    for s in issues[:60]:
        print('- ' + s)
    if len(issues) > 60:
        print('… 另有 %d 条' % (len(issues) - 60))
    return len(issues)


def repl(path):
    doc = load(path)
    dirty = False
    print('映射表编辑器（命令行模式，输入 help 看命令）')
    print('当前文件：%s（%d 条）' % (path, len(doc['common'])))
    while True:
        try:
            line = input('map> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            line = 'quit'
        if not line:
            continue
        cmd, _, rest = line.partition(' ')
        cmd = cmd.lower()
        rest = rest.strip()
        if cmd in ('quit', 'exit', 'q'):
            if dirty and input('有未保存的改动，先保存吗？[y/N] ').strip().lower() in ('y', 'yes'):
                save(path, doc)
                print('已保存 %s' % path)
            print('再见')
            return
        elif cmd == 'help':
            print(HELP)
        elif cmd == 'path':
            print(path)
        elif cmd == 'list':
            cmd_list(doc, rest)
        elif cmd == 'check':
            cmd_check(doc)
        elif cmd == 'save':
            save(path, doc)
            dirty = False
            print('已保存 %s（旧文件备份 .bak）' % path)
        elif cmd == 'set':
            parts = rest.split(None, 2)
            if len(parts) < 2:
                print('用法：set <id> <名字> [注释]')
                continue
            try:
                key, old, nm = upsert(doc, parts[0], parts[1], parts[2] if len(parts) > 2 else '')
            except SystemExit as e:
                print(e)
                continue
            dirty = True
            print(('改 id %s：%s → %s' % (key, bare(old), bare(nm))) if old
                  else '加 id %s → %s' % (key, bare(nm)))
        elif cmd == 'del':
            if not rest.isdigit():
                print('用法：del <id>')
                continue
            k = str(int(rest))
            if k not in doc['common']:
                print('没有 id %s' % k)
                continue
            doc['common'].pop(k)
            doc.get('notes', {}).pop(k, None)
            dirty = True
            print('已删 id %s（记得 save）' % k)
        else:
            print('不认识的命令：%s（输入 help）' % cmd)


def main(argv):
    path = find_file()
    positional = []
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == '--file' and i + 1 < len(argv):
            path = os.path.abspath(argv[i + 1])
            i += 2
            continue
        if a.startswith('--file='):
            path = os.path.abspath(a.split('=', 1)[1])
            i += 1
            continue
        if a.startswith('-'):
            positional.append(a)
            i += 1
            continue
        positional.append(a)
        i += 1
    flags = [a for a in positional if a.startswith('-')]
    words = [a for a in positional if not a.startswith('-')]

    doc = load(path)

    if '--list' in flags or '-l' in flags:
        print('# %s' % path)
        cmd_list(doc, words[0] if words else '')
        print('# 共 %d 条' % len(doc['common']))
        return 0
    if '--check' in flags or '-c' in flags:
        print('# %s' % path)
        return 0 if cmd_check(doc) == 0 else 1
    if '--set' in flags:
        if len(words) < 2:
            print('用法：--set <id> <名字> [注释]')
            return 2
        try:
            key, old, nm = upsert(doc, words[0], words[1], words[2] if len(words) > 2 else '')
        except SystemExit as e:
            print(e)
            return 2
        save(path, doc)
        print(('改 id %s：%s → %s' % (key, bare(old), bare(nm))) if old
              else '加 id %s → %s' % (key, bare(nm)))
        print('已写入 %s（旧文件备份 .bak）' % path)
        return 0
    if '--del' in flags:
        if not words:
            print('用法：--del <id>')
            return 2
        key = str(int(words[0]))
        if key not in doc['common']:
            print('没有 id %s' % key)
            return 1
        doc['common'].pop(key)
        doc.get('notes', {}).pop(key, None)
        save(path, doc)
        print('已删 id %s，写入 %s' % (key, path))
        return 0

    # 没给动作：有 Tk 开窗口，否则进命令行
    if tk_ok():
        run_gui(path)
        return 0
    print('这个 Python 没有可用的 Tk（缺 Tcl/Tk），改用命令行模式。')
    repl(path)
    return 0


def tk_ok():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        r.update_idletasks()
        r.destroy()
        return True
    except Exception:
        return False


if __name__ == '__main__':
    sys.exit(main(sys.argv))
