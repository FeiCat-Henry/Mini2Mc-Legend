# Mini World（MiniwAr）`.r` 区块格式完整解析 & 转 Minecraft `.mca`

> 本文所有结论都来自对 `libAppPlayJNI.so` 的反汇编 + 对存档 `w85624992381678` 全库 46 个 `.r` 文件、
> 16 008 个区块、83 400 个 section、约 2.29 亿个方块的实测统计，并且用"重新解包 → 逐方块比对"验证过。

---

## 0. 一句话结论

`.r` 就是一个**被轻度魔改过的 MCRegion 文件**：

```
[扇区0] 1024×u32 索引表（偏移被 XOR 混淆，低 24 位存偏移、高 8 位存扇区数）
[扇区1] 1024×u32 时间戳表（标准 Unix 时间，无混淆）
[扇区2+] 区块记录： [u32 压缩字节数][u32 标签][LZMA1 原始流 …] 按 4096 对齐
```

每个区块解压后是**带平铺位图的 FlatBuffer**：一张 chunk 表 → 一个 section 向量 → 每个 section
里两个大数组（4096×u16 方块 id、4096×u8 附加数据）。没有任何加密/加密层，也不需要联网。

---

## 1. 文件与外层坐标

| 项 | 值 |
|---|---|
| 文件名 | `x{a}z{b}.r`，`a`/`b` 可为负（如 `x-1z-6.r`） |
| 覆盖范围 | 32×32 个区块（和 MC Region 一样） |
| 区块数 | 最多 1024（实测单文件 2 ~ 765 块） |
| 扇区大小 | 4096 B |
| 文件头 | 2 个扇区（8192 B）：索引表 + 时间戳表 |
| 区块下标 | `index = ((z - regionZ) << 5) + (x - regionX)`，即 **x 为低位、z 为高位** —— 与 MC Anvil 完全一致（`ChunkRegionFile::xz2Index` @ `0x717410`） |
| 世界坐标 | 区块坐标 ×32 + 局部坐标，x/z 与 MC 同向；y 从 0 起向上 |

`ChunkRegionFile` 结构（`libAppPlayJNI.so`，Thumb，调用前 `addr & ~1`）：

```
+0x00  char path[128]
+0x08  int  dimension
+0x0c  int  regionX
+0x10  int  regionZ
+0x14  FILE*
+0x18  u32  timestamps[1024]      // 0x1000 B
+0x101c u32* key 数组指针          // 16 个混淆 key，运行时生成
+0x1020 int  writeMode
```

---

## 2. 容器层：索引表 + XOR 混淆

每个索引项 `e = u32le @ idx*4`：

```
raw      = e & 0x007FFFFF      // 低 23 位 = 扇区偏移
len      = (e >> 24) & 0xFF    // 高 8 位  = 该记录占用的扇区数
flag     = e & 0x00800000      // bit23：已被混淆（本存档全部为 1）
offset   = raw ^ KEYS[idx & 0xF]
```

- 混淆 key 共 16 个、21 位有效，按 `index % 16` 取用。
- key **不是**静态常量表：在 `.so` 里直接搜这 16 个 u32 常量 0 命中，也不是相邻拼接，只能在运行时
  生成/变形，所以逆向侧改用**纯数据拟合**（穷举每个槽位的 21 位取值，要求"该槽解码出的偏移全部落在
  该文件真实记录集合内且长度吻合"，跨 46 个文件投票），得到唯一最优解：

```
KEYS = [0x5d351c, 0x30f308, 0x524d19, 0x6cf2bc,
        0x04b262, 0x35e03b, 0x3a7c26, 0x4ba765,
        0x2762ed, 0x44f986, 0x6f7114, 0x6a4acf,
        0x7bb584, 0x72ca13, 0x22ad76, 0x178bbb]
```

- 验证：46 个文件逐一比对"索引表解出的偏移集合" vs "逐扇区扫描出的真实记录集合" →
  **0/46 有差异**，16 008 索引项 ↔ 16 008 记录，完全双射，无重复、无越界、无缺失。
- 注意 `len` 只是"占了几块扇区"，作者偶尔会多留一块（`len = ceil((size+8)/4096) + 1`），
  **不要用 len 反推 size**，一律读记录头里的 `u32 size`。

---

## 3. 压缩层：LZMA1 raw

记录头：

```
+0  u32 size      // 后面 payload 的字节数
+4  u32 tag
+8  payload[size]
```

`tag >> 28 == 2` 表示 LZMA，`tag & 0x0FFFFFFF` = 解压后长度（可用来核对）。payload 布局：

```
payload[0]      = props   // 本存档恒为 0x5D → lc=3, lp=0, pb=2
payload[1:5]    = u32 dictSize  // 16384
payload[5:]     = raw LZMA1 流（无 header、无长度字段、无 CRC）
```

Python 里 `lzma.LZMADecompressor(format=lzma.FORMAT_RAW,
filters=[{'id': lzma.FILTER_LZMA1, 'lc':3, 'lp':0, 'pb':2, 'dict_size':16384}])` 即可，
末尾差 1~4 字节属于正常（`CompressTool::decompress` @ `0x742388`，`compressBound` 会给余量）。

判据：`props < 225` 才是合法 LZMA props（不要用 `>= 45` 之类的上界判）。

---

## 4. FlatBuffer 层

解压后的 buffer 是一个 FlatBuffer（root uoffset 在偏移 0）。实测：

```
root table（vtable 长度 30，13 个槽）
  └─ 槽1 = sections 向量
                 └─ section table（7 个槽）
```

section table 的真实字段（按原始字节 dump 核对，`46_section_bytes.py`）：

| 槽 | 类型 | 含义 |
|---|---|---|
| 0 | u8 | **段号（segment index）0..15**。缺省（vtable 里写 0）时取默认 0 = 最底层段 |
| 1 | 128×u32 → i32 ×4096（= 8192 B） | **方块 id**：4096 个 u16（低 12 位是 id、高 4 位是状态，详见第 5 节） |
| 2 | uoffset → 4096×u8 | **附加数据**：逐方块 1 字节（水位/朝向/生长阶段…） |
| 3 | uoffset → 128×u32（512 B） | 位图/统计位（实心段才有） |
| 4 | uoffset → 128×u32（512 B） | 与槽3 内容相同（另一次统计，读取时可忽略） |
| 5 | u16 | 标量计数 A（样本：296 / 233 / 175 / 155 …） |
| 6 | u16 | 标量计数 B（多数与 A 相等，偶有不等，如 38 vs 41） |

> 槽 5/6 语义未定，但**与方块数据无关**（样本里 296 明显小于该段非空气数 4096），转换时可忽略。

**易踩的坑**：段号缺省时不要回退去读 `table + 0`，那是 vtable 的 soffset（常见 `0x12`=18），
会被误当成"段号 18"。早期"每个区块都有一个 18 段"的结论就是这个 bug，真实段号只有 0..15。

**体素索引**（段内局部坐标）：

```
i = (yLocal << 8) | (x << 4) | z          // 0..4095，x 在高位、z 在最低位
x = (i >> 4) & 0xF,  yLocal = (i >> 8) & 0xF,  z = i & 0xF
worldY = segment * 16 + yLocal
```

这个顺序和 Minecraft Anvil 的 `block_states.data` 完全一致，所以**转换时 4096 个 id 原样写入**，
不需要换位。这是已经核对过的一版（产物在游戏里世界朝向正确）。

### 4.1 缩略预览图为什么要另取一次样

同一份数据还有另一种读法 `(yLocal<<8)|(z<<4)|x`，它和上面的写法在 16×16 平面内**互为转置**。
用原顺序直接铺成图时，每个区块的图案是被转置的，拼起来会像"每个区块各自转了 90°、接缝错位"；
改用转置取样出图，图像就连续。两者互为镜像，所以：

| 用途 | 取样下标 | 说明 |
|---|---|---|
| `.mca` 转换 | `(y<<8)\|(x<<4)\|z` | 与 MC 一致，原样写；产物在游戏里朝向正确 |
| 缩略预览 PNG | `(y<<8)\|(z<<4)\|x` | 纯出图层，只为让区块之间连续 |

工具里对应 `vidx()`（转换）与 `pidx()`（出图），两个函数各自只在一处被调用。

判据（`_work/tools/60_axis_test.py`，可复跑）：拿相邻区块交界处的**整柱（256 层逐层）**比对，
转置取样一致率 97.9% / 97.2%，原样取样 95.3% / 91.5%；图像层看，原样铺图时区块边界处的
相邻像素差是内部的 4.26 倍（明显见缝），转置取样后 1.05 倍（看不出缝）。

> 遗留的可选项：把转换也改成转置取样，世界的区块接缝会更平滑（在起伏大的 region 上，
> 交界最高高度差从 4.2~9.5 块降到 0.6~0.9 块），但世界会变成当前这版的镜像。
> 目前按"世界朝向以游戏内实际观感为准"保留原样转换，需要切就说一声。

83 400 个 section 的 id 数组**恒为 4096 项**（全库遍历 0 异常）。

---

## 5. 方块 id 语义：低 12 位是 id，高 4 位是状态

存档里每个方块是 **u16**：

```
id    = value & 0x0FFF     // 0..4095，0 = 空气
state = value >> 12        // 4 个标志位，和方块类型无关
```

判定依据（同一份存档、只改截断规则，看命中 `tools/mc_block_map.json` 的比例）：

| 截断规则 | 命中率 |
|---|---|
| `value & 0x0FFF`（12 位） | **99.15%** |
| `value & 0x7FFF`（15 位） | 99.09% |
| 原值不截断 | 98.99% |

12 位胜出，且关键证据很硬：`32986 = 0x80DA` 截断后是 `218`（表里 oak_leaves）、`16387 = 0x4003`
截断后是 `3`（water）、`4099 = 0x1003` 也是 `3`。这些高位值在水/岩浆上分别出现了 8 种组合
（+0x1000、+0x2000、+0x3000、+0x4000、+0x5000、+0x6000、+0x7000、+0x8000），数量彼此接近，
说明它们是**同一批方块的 4 位状态**，不是"id 高位"。

### 5.1 权威映射表

`tools/mc_block_map.json` 就是转换的唯一依据（工具与编辑器都直接读写它，不再经过 txt）。
它由最初的 Lua 式表清洗而来，清洗结论如下：

- 275 条原始表项；**后出现的赋值覆盖前面的**（Lua 语义）：`4` flowing_water→water、
  `6` flowing_lava→lava、`1758` black_concrete→obsidian；
- `id > 4095` 的 50 条（`11883`、`150018`、`200006`、`390073` …）在 u16 的 12 位 id 空间里
  不可能出现，直接忽略；
- 225 条生效项；
- 11 个名字不是 MC 1.20.1 的真实方块名，换成最接近的合法方块（否则整段调色板会让
  游戏加载区块时报 Unknown block state）：

| 表里写的 | 换成 | 原因 |
|---|---|---|
| waterlily | lily_pad | 睡莲的正确 id |
| wheat_seeds | wheat | 那是物品，方块是 wheat |
| double_stone_slab | smooth_stone_slab | 老版双台阶 id 已废弃 |
| log | oak_log | 泛型名不存在 |
| bush | grass | 灌木，映射到矮草 |
| thatch | hay_block | 茅草 → 干草块 |
| pale_oak_log / pale_oak_fence | dark_oak_log / dark_oak_fence | 苍白橡木是 1.21.4 才有的 |
| apple / armor_stand / item_frame | dirt | 纯物品/实体名，MC 没有同名方块 |

- **表里没有的 id 一律落 `minecraft:dirt`（土块占位）**，按你的要求。

### 5.2 覆盖率实测（按方块数）

| 维度 | 方块总数 | 落土块占位 | 占比 | 最大的缺口 |
|---|---|---|---|---|
| `m0` 主世界 | 150 498 113 | 1 061 747 | 0.71% | 405(537K) / 115(210K) / 123(190K) / 400(92K) / 404(18K) |
| `m1` 地心 | 23 808 979 | 755 850 | 3.17% | 961(582K) / 407(136K) / 536(37K) |
| `m2` 萌眼星 | 56 405 651 | 55 257 615 | **97.96%** | **116(55.1M，整颗星的主体岩)** / 117(123K) / 258(8K) |
| 合计 | 230 712 743 | 57 075 212 | 24.74% | |

> `m2` 几乎全变土块，唯一原因就是它的主体岩 id **116 不在表里**。要修就在
> `mc_block_map.json` 的 `common` 里加一条 `"116": "minecraft:stone"`（或 `end_stone`/`netherrack` 等），
> 或用 `python tools/block_id_Editor.py --set 116 stone`，然后重跑转换即可，几分钟出结果。

### 5.3 形态统计与表的交叉验证

用 `49_shape_all.py` 统计了三个维度的 `hNb`（同 id 水平相邻数均值）、`top%`（该 id 有多少比例
是所在列的最高方块）、`vRun`（竖直连续长度均值）、`extra` 直方图。这些形态特征和你的表**完全吻合**，
反过来也证明表是对的、解析也是对的：

| id | 表里的名字 | 形态证据 |
|---|---|---|
| 1 | bedrock | 水平全连（hNb 2.75）、y=0..3 整层、m1 里 y0 与 y127 双壳 |
| 104 | stone | 1.2 亿块、hNb 3.57、全在地下、top% 0.1% |
| 101 | dirt | 紧贴草方块下方，hNb 2.98 |
| 100 | grass_block | top% 69.6%、vRun 1.00（只有表层一层） |
| 3 | water | y≈62 平面、top% 35%、extra=14/13/12（水位） |
| 5 / 6 | lava / lava（原 flowing_lava） | extra=0xF0（源方块），y≈5-7 与 y2..50 |
| 106 | sand | y≈61-62 紧贴地表，top% 7.4% |
| 107 | gravel | hNb 2.72、成团分布 |
| 200/201/202/203/254 | oak/spruce/birch/jungle/cherry_log | hNb≈0、vRun 2.5-2.8（1×1 竖直柱） |
| 218/219/220/221 | oak/spruce/birch/dark_oak_leaves | hNb 2.0-2.5、top% 30-50%（树冠） |
| 224 | grass | top% 88%、vRun 1.00、孤立单块 |
| 245 / 251 | lily_pad / bamboo | 水生/竖杆形态一致 |
| 401/402/406 | iron_ore / coal_ore / emerald_ore | hNb 1.1-1.7、孤立小块、随机深度 |
| 124 | netherrack | 只在 m1 出现，占该维度 85%、hNb 3.63 |

未收录但形态很明确、值得你补进表的：`405`（m0 里 537K，矿石状）、`115`（单层铺面 top% 100%，雪/结晶层）、
`123`（190K 石质变体）、`400`/`404`/`407`（同 401-406 族的矿石）、`961`（m1 里 582K，岩浆海边缘薄层）、
`117`（m2 顶端单层）、`112`（深部 y10 的平整层）。

---

## 6. 从 `.so` 侧确认的调用链

| 符号 | 地址 | 作用 |
|---|---|---|
| `ChunkRegionFile::ctor` | `0x717388` | 打开/创建 `.r`，读头表 |
| `xz2Index` / `index2XZ` | `0x717410` / `0x717424` | `((z-regZ)<<5)+(x-regX)` |
| `getChunkOffset` / `getChunkLen` | `0x7174fc` / `0x717534` | 读索引项、XOR 解混淆 |
| `makeChunkOffset` / `setChunkOffsetLen` | `0x717540` / `0x717588` | 写索引项、再混淆 |
| `loadChunk` / `saveChunk` | `0x7175c0` / `0x718e20` | 读/写单个区块记录 |
| `ChunkIOMgr::requireRegionFile` | `0x71935c` | 按 (dim, rx, rz) 管理打开的文件表 |
| `Chunk::loadFromBuffer` | `0x71be28` | 把解压后的 FlatBuffer 填进 Chunk |
| `Chunk::getBlock` | `0x714e00` | `cmp r2,#0xff; bhi` → 世界高 256、16 个 section 指针（`+0x5b8`） |
| `Ogre::CompressTool::decompress` | `0x742388` | LZMA 解压（`compress` @ `0x7422ac`） |
| `DefManager::loadVoxelPalette` | `0x511048` | 加载方块调色板定义 |

`Chunk` 布局：size `0x5FC`；`+0x5b8` 起 16 个 section 指针；`+0x590` 默认空气方块；
section `+0x18` 方块 id 数组、`+0x1c` 附加数组、`+0x2a`(u16) 非空计数、`+0x64+4i` 位图计数器。

---

## 7. 转成 Minecraft Java `.mca`

### 7.1 目标格式（Anvil / 1.18+）

```
[r.X.Z.mca]
 512 B 位置表：1024×u32 = (扇区偏移 24 位 << 8) | 扇区数
 512 B 时间戳表：1024×u32
 之后每个区块： [u32 长度][u8 压缩类型=2 zlib][zlib(NBT)]，按 4096 对齐
```

区块 NBT（`DataVersion = 3465`，即 1.20.1）关键字段：

```
DataVersion(int) xPos zPos yPos Status="minecraft:full"
sections: [ { Y(byte), block_states:{ palette:[{Name}], data:long[] },
             biomes:{ palette:["minecraft:plains"] } } ]
Heightmaps: { MOTION_BLOCKING: long[37], WORLD_SURFACE: long[37] }   // 9 bit × 256，7 值/long
block_entities: []   Structures:{starts:{},References:{}}   isLightOn(byte)=0
```

`block_states.data` 是 4096 个调色板下标按 `bits = max(4, ceil(log2(len(palette))))` 紧凑打包，
**每个 long 内不跨值**（1.16+ 规则）；调色板只有 1 项时可以整段省略 `data`。

### 7.2 坐标与高度

- 区块 (x,z) 与 MC 一一对应（同为 16×16×16 的 section、x/z 同向）。
- 单个 section 内部**原样写入**（4096 个 id 按顺序进 `block_states`，两侧顺序定义一致）；
  缩略预览图另有取样方式，见 §4.1。
- Heightmaps 的 256 个数用 `col = x*16 + z` 生成（和段内下标的解码方式保持一致）。
- `m0` → `region/`（y 0..255，16 个 section）；`m1` → `DIM-1/region/`（y 0..127，8 个 section）；
  `m2` → `DIM1/region/`。
- 空气段照样写出（单一 `minecraft:air` 调色板），保证 section 列表连续。

### 7.3 实测结果

```
46 个 .r → 46 个 .mca，16 008 个区块，230 712 743 个非空方块（约 2.3 亿）
回读校验（解 .mca → 逐方块与源 .r 对比，下标一一对应）：
  m0/x1z2.r  1 703 936 块，不一致 0   PASS
  m0/x0z-2.r 4 980 736 块，不一致 0   PASS
  m0/x-1z-6.r  131 072 块，不一致 0   PASS
  m1/x0z0.r  8 585 216 块，不一致 0   PASS
  m2/x1z-3.r 8 519 680 块，不一致 0   PASS
未知/未定名方块占比 24.74%（全部按你的要求落成土块；分维度明细与缺口 Top 见 5.2）
```

缩略预览图的轴向处理见 §4.1：转换保持 `(x<<4)|z` 原样写入（世界朝向已核对），
预览出图用 `(z<<4)|x` 取样，所以区块之间是连续的。这属于纯显示层处理，`.mca` 内容不含它。

俯视预览（`render_map.py` 用同一份映射表渲染，1 像素 = 1 方块）：

| 图 | 内容 |
|---|---|
| `docs/preview_m0.png` | 主世界：草/水/沙滩/雪山，南区能看到 mod 方块围出来的屋子 |
| `docs/preview_m1.png` | 地心：整片都是 y127 的基岩顶棚，只在地表露出岩石与岩浆 |
| `docs/preview_m2.png` | 萌眼星：主体岩 id 116 不在表里 → 整片落成土块 |

### 7.4 怎么进 MC

生成的只有 region 文件，没有 `level.dat`。做法：

1. 用目标版本（1.20.1）新建一个世界，退出；
2. 把 `out/world/region/*.mca` 覆盖进 `<存档>/region/`；
3. 需要地心/萌眼星的话，再把 `out/world/DIM-1/region/*.mca` → `<存档>/DIM-1/region/`、
   `out/world/DIM1/region/*.mca` → `<存档>/DIM1/region/`（目录不存在就新建）；
4. 进游戏 `/tp` 到对应坐标即可（区块坐标和 MC 一致，主世界大约在 x=-96..64, z=-96..48 之间）。

若目标版本不是 1.20.1，用 `--data-version` 换成对应值（1.20=3463，1.20.1=3465，1.20.2=3578，
1.21=3953，1.21.1=3955），或直接让 MC 自己升级存档（会提示版本转换）。

---

## 8. 用法

### 8.0 图形界面（一个文件、不用装任何东西）

```bash
python tools\MiniWorld_R_Tool.py
```

窗口里就五个按钮，顺序 ① → ③ → ② → ④ → ⑤：校验映射表、统计覆盖率、转换、生成预览图、回读校验，
路径与维度都在界面上选，日志和进度直接显示，预览图在右侧面板里。全部逻辑（读 .r、解 LZMA、
解析 FlatBuffer、写 NBT、写 .mca、回读比对、渲染 PNG）都在这一个文件里，不依赖别的脚本。
映射来源只有 `mc_block_map.json`；要改映射就用 `block_id_Editor.py`（同一个 JSON，读写都走它）。

界面有两种形态，程序自动挑：

- 这台机器的 Python 带 Tcl/Tk → 开**原生窗口**（tkinter）；
- 没有 Tcl/Tk（本机就是这种）→ 自动开**浏览器窗口**（本地 127.0.0.1:8123，同一个界面、同一套功能），
  关掉那个黑窗口就等于关程序。想装原生窗口版就装一个官方 Python（自带 tcl/tk）。

命令行自检（不开界面，跑一遍小样本转换+校验）：

```bash
python tools\MiniWorld_R_Tool.py --selftest
python tools\MiniWorld_R_Tool.py --selftest "D:\...\w85624992381678"
```

### 8.1 命令行脚本

```bash
# 0) 改映射表：直接编辑 tools/mc_block_map.json，或用编辑器改
python tools/block_id_Editor.py            # 有 Tk 开窗口，没有则进命令行（--list/--set/--del/--check）

# 1) 读取任意 .r
python tools/r_reader.py "D:\...\w85624992381678\m0\x1z2.r"

# 2) 全库转换（输出到 out/world/{region,DIM-1/region,DIM1/region}）
python tools/to_mca.py --save "D:\...\w85624992381678" --out "out\world"

# 3) 只转一个文件
python tools/to_mca.py --save ... --out ... --dims m0 --region x1z2

# 4) 回读校验（逐方块比对）
python tools/verify_mca.py --save ... --mca "out\world" --dim m0 --region x1z2
```

Python API：

```python
import sys; sys.path.insert(0, "tools")          # 在仓库根目录运行
from r_reader import RegionFile
rf = RegionFile("w.../m0/x0z0.r")
for idx in rf.chunk_indexes():
    ch = rf.read_chunk(idx)
    print(ch.chunk_xy(idx), ch.sections.keys())          # ({'band': Section})
    print(ch.block(0, 5, 0), ch.max_y())                 # 取某个世界坐标的 id
```

---

## 9. 尚未确定 / 风险点

1. **映射表是你提供的那份，不是逆向出来的**。`.rodata` 里 `BLOCK_AIR / BLOCK_BEDROCK / …` 按枚举
   顺序连续排列（起始 vaddr `0xde5274`），但既搜不到指向它的指针数组，`.rel.dyn` 里也没有对应的
   `R_ARM_RELATIVE` 重定位；存档内 `mods/.../behavior/block/*.json` 的 `pack_manifest.json` 写着
   `"encrypted": true`，正文加密不可读。所以"id 数字 → 方块名"的对应关系一律以 `mc_block_map.json`
   为准，表里没有的就按约定落土块。
2. section 槽 3/4 的 512 B 位图、槽 5/6 两个 u16 标量语义未定（不影响读取与转换）。
3. 高 4 位状态的确切含义（朝向/水位/生长阶段/"已实体化"）未确认；转换时只取低 12 位，状态位丢弃
   （水/岩浆的液面高度本来就不是问题，MC 会自己重算流体）。
4. `m1`/`m2` 到底对应 MC 的下界还是别的维度，只有 id 分布 + 世界高 128 的证据；`wterrtype.fb`、
   `wsize.fb` 是加密/混淆的 4 字节，没能解出。
5. `m2` 主体岩 `116` 不在表里，当前整颗星都是土块占位；补一条表项（`--set 116 stone`）再重跑
   转换即可修好（见 5.2）。
6. **写回路径只确认到函数级**：`saveChunk 0x718e20` / `saveChunkAndClearRegion 0x7189e8` 的
   "取旧记录长度 → 决定追加还是原地覆盖 → 更新索引项并重新 XOR → 更新时间戳表" 这套顺序是从
   索引/时间戳结构反推出来的，没有逐条指令读完。读方向（`loadChunk 0x7175c0`）是完整验证过的。
