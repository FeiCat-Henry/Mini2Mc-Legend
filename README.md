# Mini2MC

继上一版的遗憾，我们破解并理解了r文件的格式，直接将r文件转换为mca，脱离游戏内部接口，转换效率极大幅提高。

把 **迷你世界（MiniwAr 引擎）** 的 `.r` 区块存档解析并转换成 **Minecraft Java 版** 能读的 `.mca`。

一份 2020 年的旧存档实测：46 个 region 文件 → 46 个 `.mca`，16,008 个区块、230,712,743 个非空方块，
逐块回读校验零差异。



> Convert MiniwAr (迷你世界) `.r` region saves into Minecraft Java Anvil `.mca`.
>
> 这是我们的联系方式，你可以通过这个找到我们。
>
> | QQ交流群   | QQ个人     |
> | ---------- | ---------- |
> | 1053150006 | 1278489415 |
> |            | 3857463390 |

## 它解决的三件事

| 关卡 | 原版工具读不动的点 | 本项目的处理 |
|---|---|---|
| 容器 | MCRegion 扇区表，但偏移被 XOR 混淆 | 16 个 key 反推（46 文件 / 16,008 条索引双射验证） |
| 压缩 | 不是 zlib，是 LZMA1 裸流 | `props 0x5d` + `dict 16384` + `raw`，末尾允许差 1~4 字节 |
| 区块 | 段结构是 FlatBuffer，不是 NBT | 走 vtable 取 section 向量，每段 7 个槽位 |



## 一句话教程

**先安装python及其运行库，再双击打开MiniWorld_R_Tool.py主程序**

> 通常，这是存档的位置  C:\Users\用户名\AppData\Roaming\miniworddata110\data\w地图ID

## 快速开始

```bash
pip install -r requirements.txt    # 只有 Pillow，且只有出预览图才用得到

# 0) 自检：拿一个存档里的 .r 样本，解码 → 转出 → 回读逐方块比对
python tools/MiniWorld_R_Tool.py --selftest /path/to/w85624992381678

# 1) 改/查映射表（唯一映射来源是 tools/mc_block_map.json，没有 txt 源表了）
python tools/block_id_Editor.py            # 窗口版；没有 Tk 时进命令行
python tools/block_id_Editor.py --check    # 或只检查一遍（条目数、超范围 id、不规范名字）
python tools/block_id_Editor.py --set 116 stone   # 补一条「id 116 → stone」

# 2) 全量转换
python tools/to_mca.py --save /path/to/w85624992381678 --out out/world

# 3) 回读校验（解 .mca 逐方块与源 .r 比对）
python tools/verify_mca.py --save /path/to/w85624992381678 --mca out/world --dim m0 --region x1z2

# 4) 渲染俯视预览（1 像素 = 1 方块）
python tools/render_map.py --save /path/to/w85624992381678 --dim m0 --out preview_m0.png
```

单文件 GUI（推荐从这里开始）：`python tools/MiniWorld_R_Tool.py`
有 Tk 开原生窗口，没有则自动起本地网页 `127.0.0.1:8123`；`--web` 可强制网页模式。

## 进游戏

生成的只有 region 文件，不含 `level.dat`：

1. 用 **1.20.1**（`DataVersion 3465`）新建一个世界，退出；
2. `out/world/region/*.mca` → 覆盖到 `<存档>/region/`；
3. `out/world/DIM-1/region/*.mca` → `<存档>/DIM-1/region/`（地心）；
4. `out/world/DIM1/region/*.mca` → `<存档>/DIM1/region/`（末地位置，实际是「萌眼星」）。

## 目录结构

```
tools/
  MiniWorld_R_Tool.py   # 单文件 GUI：校验映射表 / 转换 / 覆盖率 / 预览 / 回读校验
  r_reader.py           # .r 容器 + FlatBuffer 读取（RegionFile / Section / Chunk）
  to_mca.py             # 写 Anvil .mca（NBT 编码、palette、位打包、Heightmaps）
  verify_mca.py         # 回读校验
  render_map.py         # 俯视预览
  block_id_Editor.py    # 映射表编辑器（直接读写 mc_block_map.json）
  mc_block_map.json     # 唯一映射来源：225 项，id_mask 0xFFF，fallback minecraft:dirt
docs/
  r_format.md           # 格式全解（主文档，含每一步的判据）
  index.html            # 项目主页（GitHub Pages 用，单文件宣传页：8 段、内联样式与脚本）
  assets/               # 页面用图（等距村落 hero、同一村子的两种朝向对比）
  preview_m0/m1/m2.webp # 三个维度的俯视预览（页面用，png 版保留为原始导出）
```

## 已知限制

- **映射表没覆盖的方块落成土块**：全量 24.74% 走了占位；`m2` 的主体岩 id 116 不在表里，
  所以那颗星球整体是土块。你可以运行 block_id_Editor.py自己添加方块映射，保存后重新转换就可以。
- 目标版本固定 `1.20.1`（`DataVersion 3465`）；换 1.21 需复核个别方块名（如 `grass` → `short_grass`）。
- **单向**：`.r` 写回（`saveChunk`）只定位到符号与调用关系，没有回写测试。
- 只转方块，不含实体、方块实体、光照与生物群系细节。

## 文档

格式的每一个结论都附了判据（索引计数、双射验证、交界柱一致率、图像边界像素差），
能被复现、也能被推翻——见 [`docs/r_format.md`](docs/r_format.md)。

轴向这一段尤其值得看：读 `Chunk::getBlock` 证明不了段内 x/z 顺序（入口只取 `y>>4`，
打包值由调用方传入），最后是靠相邻区块交界处整柱 256 层逐层比对 +
村落围墙形状定的案。错取样时区块边界像素差是内部的 4.26 倍，改对后 1.05 倍。

## 许可

MIT。不含游戏本体的任何二进制（两个引擎 `.so`、资源包）和个人存档；
`mc_block_map.json` 只是 id→名字的映射数据，请按你自己的存档实际情况增补。

## 免责

本项目只读写你**自己**的存档，不修改游戏本体、不绕过任何在线校验。
请在合法前提下用于个人存档迁移。

## 一段故事

现在是2026年11月11日，我大一，现在我正坐在寝室里。去年的现在，我高三。那会，我想要把迷你世界里面的存档转换到mc里面，我联系到了一个miniworld-r-file-reader的作者，但他的逻辑只是钻游戏中的一个漏洞，基于游戏内置api，我和他把那个项目完善了，做出了Mini2MC-Legend，但我还是想要破解r文件，我对游戏进行了分析，先是排除了一大堆lua文件，最后定位到这两个so文件里，但这涉及到二进制反编译，我没办法了，随止。后来，今年2月，一个初中生，加入了我们的这个qq项目群里面，当时这个群还只有17个人，他当时有GPT5 无限token 的api，然后，他花了一个月把r文件破解了，发了视频，当时直接冲进b站全区前三了，各家社交媒体到处都在报道，我们的qq群，一下膨胀到了800多人了，但他迟迟不开源，还以此吊人胃口。我实在忍受不了，但也确实没法。时间一转，高考了，暑假，我再次尝试破解r文件，但还是不行。直到9月，agent开始大量爆发了，这东西非常强大，说句实在的，普通的豆包，都当作人工智障，但现在不一样，这是豆包Agent，可以操控电脑，上下文更长。9月，我在用它帮我开发一个游戏，我就见识到了它的强大。后来，我突然想到了，是不是也能用它，去完成我这个一年的心愿，但我忘了。直到今天，双十一，我又想起了，我抱着试着的心态，拿出了那两个尘封已久的so文件，给它发了信息，我其实没报多大希望的。没想到，一个小时过去，它做到了。ai发展得还是太快了，也就才半年，就强大了不知道多少。



我就做到这里了，有别的需求，后面的路要自己走了。
