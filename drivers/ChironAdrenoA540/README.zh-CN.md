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
python3 tests/test_chiron_adreno_gmu.py    # GMU 启动握手
python3 tests/test_chiron_adreno_smmu.py   # SMMU v2 context bank
python3 tests/test_chiron_adreno_cpsw.py   # CP 微码分割
python3 tests/test_chiron_adreno_cpstart.py # CP 环与 ME 启动
python3 tests/test_chiron_adreno_bringup.py # 整链（共享寄存器模型）
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

## GMU 启动握手

来自 `adreno_a5xx.c:780` 的 `a5xx_gpmu_start()`，寄存器索引来自 `a5xx_reg.h:768-784`。

### 索引是字偏移，不是字节偏移

`a5xx_reg.h` 里的值是 **word offset**：

```c
static inline void kgsl_regwrite(struct kgsl_device *device,
                                 unsigned int offsetwords, ...)
```

`adreno_regwrite()` 里还有 `BUG_ON(offsetwords * sizeof(uint32_t) >= device->reg_len)`。
所以字节偏移 = 索引 × 4，本实现由 `GmuRegister()` 一次性完成这个换算。

| 名称 | 字索引 | 字节偏移 |
|---|---|---|
| `GPMU_INST_RAM_BASE` | 0x8800 | 0x22000 |
| `GPMU_DATA_RAM_BASE` | 0x9800 | 0x26000 |
| `GPMU_WFI_CONFIG` | 0xA8C1 | 0x2A304 |
| `GPMU_RBBM_INTR_INFO` | 0xA8D6 | 0x2A358 |
| `GPMU_CM3_SYSRESET` | 0xA8D8 | 0x2A360 |
| `GPMU_GENERAL_0` | 0xA8E0 | 0x2A380 |
| `GPMU_GENERAL_1` | 0xA8E1 | 0x2A384 |

### 握手

```
1. （仅 a530）写 GPMU_WFI_CONFIG = 0x00004014
2. 写 GPMU_CM3_SYSRESET = 0        ← 释放 CM3 核心
3. 轮询 GPMU_GENERAL_0 直到 == 0xBABEFACE（5000 次，每次 1µs）
4. （非 a530）要求 GPMU_GENERAL_1 == 0
```

`0xBABEFACE` 是固件写入通用寄存器的"我活着"标记。硬件团队估计初始化约 3000 周期，
所以内核轮询 5000 次、每次 1µs 后判定超时。

**A540 走非 a530 分支**——内核的条件写的是 `adreno_is_a530()`，看起来像所有 a5xx 都成立，
实际不是。A540 因此**不写** WFI_CONFIG，且**必须**检查 GENERAL_1。

### 返回值

| 结果 | 含义 |
|---|---|
| `Ok` | 固件启动成功 |
| `NoRegisters` | 缺少必需的读/写回调 |
| `FirmwareTimeout` | GENERAL_0 始终不是 0xBABEFACE |
| `FirmwareError` | GENERAL_0 对了但 GENERAL_1 非零（错误码通过 `*error_value` 返回）|

## SMMU v2

`qcom,smmu-v2` 在 chiron 上位于 `0x05040000`，大小 `0x10000`。

### 它其实就是标准 SMMU-500

两个驱动的寄存器偏移逐个对照，完全一致：

| 寄存器 | `kgsl_iommu.c` | `arm-smmu.c` |
|---|---|---|
| SCTLR | 0x00 | 0x00 |
| TTBR0 | 0x20 | 0x20 |
| CONTEXTIDR | 0x34 | 0x34 |
| FSR | 0x58 | 0x58 |
| FAR | 0x60 | 0x60 |

**唯一的 Qualcomm 差异**是 `arm-smmu.c:1665` 的这段：

```c
if (smmu->model == SMMU_MODEL_QCOM_V2) {
    reg = ACTLR_QCOM_ISH << ... | ACTLR_QCOM_OSH << ... | ACTLR_QCOM_NSH << ...;
    writel_relaxed(reg, cb_base + ARM_SMMU_CB_ACTLR);
}
```

`ACTLR_QCOM_{NSH,ISH,OSH}` 在 shift 30/29/28 各置 bit 1，所以写的是 **`0x70000000`**。
标准 SMMU-500 不会写这个寄存器，qcom,smmu-v2 必须写，否则 GPU 事务的排序太弱。

### Context bank 布局

```
基址 = 0x8000 + (index << 12)     每个 bank 占 0x1000
```

### 编程顺序（`SmmuProgramContext()`）

| 顺序 | 寄存器 | 偏移 | 说明 |
|---|---|---|---|
| 1 | TTBR0 | 0x20/0x24 | 低/高两个 32 位写 |
| 2 | TTBR1 | 0x28/0x2C | |
| 3 | TTBCR | 0x30 | T0SZ / IRGN0 / ORGN0 / EAE |
| 4 | TTBCR2 | 0x10 | SMMU-500 独有 |
| 5 | MAIR0/1 | 0x38/0x3C | |
| 6 | CONTEXTIDR | 0x34 | |
| 7 | **ACTLR** | 0x04 | **Qualcomm 专用 `0x70000000`** |
| 8 | SCTLR | 0x00 | **最后写**，写到这时前面的都被读过 |

SCTLR 基础值 `CFIE | EAE_SBOP | M | S1_ASIDPNE`，然后：

- `stall_on_fault` → 加 `CFCFG`（出错时暂停 GPU）
- 否则 → 加 `HUPCF`（后续事务不受挂起错误影响）

两者**互斥**。完整值实测为 `0x240010c1`：

```
bit0  M          1
bit6  CFIE       1
bit7  CFCFG      1
bit12 S1_ASIDPNE 1
bit26 TRE        1  ┐ EAE_SBOP
bit29 AFE        1  ┘
```

### 一个易错的位域

ASID 在 TTBR0 的 bit[55:48]。**右移 32 位后落在高字的 bit[23:16]**，即 ASID 7 写成
`0x00070000`，**不是 `0x00000007`**。写成后者会把 ASID 放进 bit[39:32]，那是错的字段。

## CP 微码：几乎没有格式

`adreno_a5xx.c:2534` 的 `_load_firmware()` 做四件事：

```c
kgsl_allocate_global(device, ucode, fw->size - 4, KGSL_MEMFLAGS_GPUREADONLY, 0, "ucode");
memcpy(ucode->hostptr, &fw->data[4], fw->size - 4);
*ucode_size   = (fw->size - 4) / sizeof(uint32_t);
*ucode_version = *(unsigned int *)&fw->data[4];
```

**丢掉前 4 字节，从 `+4` 起整块拷贝。** 没有容器、没有分段头、没有校验和。

| 文件 | 大小 | +4 | +8 |
|---|---|---|---|
| `a530_pfp.fw` | 16820 | `0x005ff112` | `0x00000fec` (4012) |
| `a530_pm4.fw` | 19652 | `0x005ff066` | `0x000012b0` (4784) |

`+4` 那个 dword 同时就是 `ucode_version`。`+8` 看起来像指令数，但内核从不读它，
所以它只是载荷的第一个字，**不要当成独立长度字段**。

## CP 启动

`adreno_a5xx.c:2483` 的 `a5xx_rb_start()`，寄存器索引在 `a5xx_reg.h:60-91`，同样是字偏移。

| 顺序 | 寄存器 | 字索引 | 内容 |
|---|---|---|---|
| 1 | `CP_RB_RPTR_ADDR` | 0x804/0x805 | 硬件写读指针的位置（64 位）|
| 2 | `CP_RB_CNTL` | 0x802 | 环大小 / 块大小（log2）|
| 3 | `CP_RB_BASE` | 0x800/0x801 | 环在 GPU 地址空间的位置（64 位）|
| 4 | — | — | 加载 PFP / PM4 到 ME RAM |
| 5 | `CP_ME_CNTL` | 0x832 | **写 0 清 ME_HALT，微引擎开跑** |

### `CP_RB_CNTL` 的推导

```c
#define A5XX_CP_RB_CNTL_DEFAULT (((ilog2(4) << 8) & 0x1F00) | \
                                 (ilog2(KGSL_RB_DWORDS >> 1) & 0x3F))
```

`KGSL_RB_SIZE = 32 KiB`（`adreno_ringbuffer.h:29`）→ `KGSL_RB_DWORDS = 8192`。

- `ilog2(4) = 2` → `2 << 8 = 0x200`（块大小字段）
- `ilog2(8192 >> 1) = ilog2(4096) = 12` → `0x0C`（大小字段）

**结果 `0x20C`。** 内核注释说明了为什么减半：硬件要的是**四字**单位的 log2，不是双字。

测试在 1024/2048/4096/8192/16384 五个尺寸上验证了大小字段跟着变，块字段恒为 `0x200`。

## 整链集成测试

单层测试各自用私有模型，查不出层与层之间的问题。`tests/Bringup.cpp` 用**一份共享的
寄存器空间**把所有层串起来跑：

```
GPUCC 上电  →  SMMU context bank  →  GMU 握手  →  CP 环  →  微码  →  放开 ME
```

模型按**绝对地址**索引，所以 GPUCC（0x5065000）、SMMU（0x5040000）、GPU（0x5000000）
三个窗口像真机一样互不重叠。哪一层踩到别人的寄存器，就会立刻暴露。

顺序取自 `adreno_a5xx.c`：

| 步骤 | 内核位置 |
|---|---|
| GMU 握手 | `a5xx_gpmu_init` → `a5xx_gpmu_start` (2203) |
| CP 环 | `a5xx_rb_start` (2483) |
| 微码加载 | `a5xx_rb_start` 内的 `a5xx_microcode_load` (2509) |
| 放开 ME | `CP_ME_CNTL = 0` (2514) |

集成测试额外验证两件事：

1. **CP 环与 GMU 寄存器不重叠**：CP 在字 0x800+，GMU 在字 0xA800+，
   `CP_RB_BASE*4 + 0x1000 <= GPMU_INST_RAM_BASE*4` 成立。
2. **`CP_ME_CNTL` 是最后写的**：在环的 BASE 之后才放开微引擎，
   否则引擎会在环描述符还没写完时就去读。

## 下一层

按 `research/CHIRON_GPU_A540_DELTA.zh-CN.md` 的顺序：

1. ~~legacy fw2 解析~~（完成）
2. ~~GPUCC 上电与时钟~~（完成）
3. ~~GMU 启动握手~~（完成；HFI 消息集待做）
4. ~~SMMU v2 context bank 编程~~（完成；页表建立待做）
5. ~~CP 微码分割 + 环编程~~（完成；ME 初始化包待做）
6. 命令提交与 fence
