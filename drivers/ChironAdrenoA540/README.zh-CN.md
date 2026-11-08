# ChironAdrenoA540

A540 (chiron GPU) 后端。目前只有第一层：GMU 固件的 legacy fw2 容器解析。

## 为什么不能复用 Adreno740 的解析器

`UniXNU/AdrenoFirmware` 的 `AdrenoFirmwarePlanGmu()` 期望的块格式是

```c
struct { u32 address; u32 length; u32 type; u32 value; }
```

第一个 word 是**块地址**。

而 A540 的 `a540_gpmu.fw2` 第一个 word 是**块的 dword 总数**：

```
+0x00  u32 block_dwords    整块 dword 数（含载荷），必须小于文件的 dword 数
+0x04  u32 block_id        2 = GPMU firmware
+0x08  u32 header_dwords   后面字段 dword 数，不含自身；偶数、非零、≤ 10
+0x0c  { u32 tag; u32 value; } × (header_dwords / 2)
...    payload，起点 dword (header_dwords + 3)，长度 (block_dwords - header_dwords - 2)
```

把 A540 固件喂给 `PlanGmu` 会读到 `address=0x00000b75 len=2`，然后
`address=0x00020000 len=589824` —— 文件只有 11736 字节，第二个块就越界，解析失败。

## 权威参考

MSM8998 内核：

| 位置 | 内容 |
|---|---|
| `drivers/gpu/msm/adreno_a5xx.c:683` | `_load_gpmu_firmware()` 主流程 |
| `drivers/gpu/msm/adreno_a5xx.c:1241` | `_read_fw2_block_header()` 头部校验 |
| `drivers/gpu/msm/adreno_a5xx.h:215-228` | `GPMU_*` / `HEADER_*` 常量 |
| `drivers/gpu/msm/adreno-gpulist.h` | A540 条目（gpmu_major=3 等）|

载荷偏移和长度用的是内核自己的算式，不是推断：

```c
cmds     = data + data[2] + 3;
cmd_size = data[0] - data[2] - 2;
```

## 实测数据（a540_gpmu.fw2，11736 字节 / 2934 dwords）

| 字段 | 值 |
|---|---|
| block_dwords | 2933 |
| block_id | 2 (GPMU firmware) |
| header_dwords | 8 |
| tag 1 (HEADER_MAJOR) | **3** |
| tag 2 (HEADER_MINOR) | **9** |
| tag 3 (HEADER_DATE) | 132123407 |
| tag 4 (HEADER_TIME) | 1316879 |
| payload 起点 | dword 11 |
| payload 长度 | 2923 dwords（11692 字节）|
| GPMU_INST_RAM_SIZE | 4095 dwords，2923 < 4095 ✓ |
| payload[0] | 0x00005ff4（加载长度）|
| payload[1] | 0x0000018d（第一条指令）|

`HEADER_MAJOR/MINOR` 是**字段标签**，不是版本号。真正的版本是紧随其后的 value。

内核 A540 条目要求 `gpmu_major=3, gpmu_minor=0`；固件给 3/9。
内核的检查是 `if ((major > header[i+1]) && header[i+1])`，即只拒绝更新的主版本，
所以 9 > 0 不是问题。

## 测试

```sh
python3 tests/test_chiron_adreno_fw2.py    # fw2 容器解析
python3 tests/test_chiron_adreno_gpucc.py  # GPUCC 上电序列
```

测试用真实固件验证解析结果，再逐一验证 11 种畸形输入被拒绝：
block 尺寸越界、block id 未知、header 为零/奇数/超限/容不下、
文件过短、主版本过新，以及一个已知布局的合成镜像。

## GPUCC 与上电序列

寄存器偏移来自 `include/dt-bindings/clock/msm-clocks-hwio-8998.h`，
上电顺序来自 `drivers/clk/msm/clock-gpu-8998.c` 的 `enable_gfx_crc()`。

### 一个基址，不是两个

内核里有 `virt_base` 和 `virt_base_gfx` 两个指针，看起来像两个窗口。实际不是：

```
qcom,gpucc@5065000   compatible "qcom,gpucc-8998"   reg = <0x5065000 0x9000>
qcom,gfxcc@5065000   compatible "qcom,gfxcc-8998"   reg = <0x5065000 0x9000>
```

**同一地址、同一大小**，两个 driver 各 map 一份。所以 GDSCR（偏移 0x1004/0x1094）
和 GX 复位（0x1090）、GX 域控制（0x130）全都在这一个 0x9000 的块里。
本实现因此只有一个 `block` 指针。

### 频率表

`ftbl_gfx3d_clk_src_v2`（`clock-gpu-8998.c:168`）的 8 个档位与设备树读到的**完全一致**：

| core_hz | pll_hz |
|---|---|
| 180 MHz | 360 MHz |
| 257 MHz | 514 MHz |
| 342 MHz | 684 MHz |
| 414 MHz | 828 MHz |
| 515 MHz | 1030 MHz |
| 596 MHz | 1192 MHz |
| 670 MHz | 1340 MHz |
| 710 MHz | 1420 MHz |

每个 PLL 目标都是 core 的 2 倍，由 `gpu_pll0_pll_out_even` 后分频产生。
设备树的 `qcom,initial-pwrlevel=6` 是**从高到低**排的第 6 个，即 257 MHz，
在本表里是索引 1。

### 上电顺序（`GpuccPowerUp()`）

| # | 动作 | 延时 |
|---|---|---|
| 1 | 清 CX GDSCR 的 `SW_COLLAPSE` | 10 µs |
| 2 | 打开 graphics 电源轨 | — |
| 3 | 置位 GX 块复位（`GPU_GX_BCR` = 1）| 1 µs |
| 4 | 释放 GX 块复位（= 0）| — |
| 5 | 置位 GX 域复位（`GX_DOMAIN_MISC` bit4）| 1 µs |
| 6 | 释放 GX 域复位 | — |
| 7 | 释放 GMEM clamp（bit0）| — |
| 8 | 清 GX GDSCR 的 `SW_COLLAPSE` | 10 µs |
| 9 | 打开 `gfx3d` 时钟门 | — |

内核的 `enable_gfx_crc()` 之后还会跑完整的 CRC 校准再把一切关掉；这里只做到
GPU 已上电、有时钟为止，也就是驱动接触 GMU 之前需要的状态。

缺少电源轨回调会**返回 false**，而不是静默跳过——没有 vdd_gfx 时 GX 域起不来。

### 只有 5 个 CBCR 在 GPUCC 里

设备树的 8 个时钟名中，只有下列 5 个的闸门在 GPUCC 块内；
`mem_clk` / `mem_iface_clk` / `iref_clk` 来自 GCC/MMSS，不属于这个块。

```
gpucc_cxo_clk          0x01020
gpucc_rbbmtimer_clk    0x010D0
gpucc_gfx3d_isense_clk 0x01124
gpucc_rbcpr_clk        0x01054
gpucc_gfx3d_clk        0x01098
```

## 下一层

按 `research/CHIRON_GPU_A540_DELTA.zh-CN.md` 的顺序：

1. ~~legacy fw2 解析~~（完成）
2. ~~GPUCC 上电与时钟~~（完成）
3. GMU 3.x 启动 + HFI
4. SMMU v2（`qcom,smmu-v2`，micro_mmu_control 0x6000）
5. PFP/PM4 加载 + CP 初始化
6. 命令提交与 fence
