# A540 vs A740 差异清单（第二阶段）

参考实现是 `UniXNU/Adreno740`（SM8550/sheng）。本文档把 A540 的实机数据与它逐项对照，
结论是：**A740 的后端一行都不能直接复用。**

## 1. 固件格式：不兼容

`AdrenoFirmware.h` 的 `AdrenoFirmwarePlanGmu()` 按这个格式解析：

```c
struct { u32 address; u32 length; u32 type; u32 value; }  /* + length 字节载荷 */
```

它要求 `= le32(fw+0)` 是**块地址**。

A540 的 `a540_gpmu.fw2` 是 legacy fw2 容器：

```
+0x00 u32  header size (dwords) = 2933
+0x04 u32  block id             = 2   (GPMU_FIRMWARE_ID)
+0x08 ...  header: (id, value) 对
+0x2C ...  载荷
```

拿 A540 固件喂 `PlanGmu` 会得到：

```
[0] addr=0x00000b75(len=总长) len=2      type=8  val=1
[1] addr=0x00020000            len=589824  ← 文件只有 11736 字节，越界
```

**立即失败。** 必须为 A540 写 legacy fw2 解析器。

### A540 固件头实测解码

| 字段 | 值 |
|---|---|
| data[0] header size | 2933 dwords |
| data[1] block id | 2 (GPMU_FIRMWARE_ID) ✓ |
| HEADER_MAJOR | **3** |
| HEADER_MINOR | **9** |
| 载荷偏移 | data[11]（`data[2]+3`）|
| 载荷长度 | 2923 dwords（11692 字节）|
| GPMU_INST_RAM_SIZE | 4095 dwords，**2923 < 4095 ✓** |

权威解析器是 `LineageOS/android_kernel_xiaomi_msm8998` 的
`drivers/gpu/msm/adreno_a5xx.c: _read_fw2_block_header()`（行 1241）
和 `_load_gpmu_firmware()`（行 683）。

常量（`adreno_a5xx.h:215-228`）：

```c
#define GPMU_FIRMWARE_ID   2
#define GPMU_SEQUENCE_ID   3
#define GPMU_INST_RAM_SIZE 0xFFF
#define HEADER_MAJOR 1   /* 字段 id，不是版本号 */
#define HEADER_MINOR 2
#define HEADER_DATE  3
#define HEADER_TIME  4
#define MAX_HEADER_SIZE 10
```

注意 `HEADER_MAJOR/MINOR` 是**字段标识符**，不是版本值。版本值在它们后面那个 word。

## 2. 命令处理器：PFP+PM4 vs SQE

| 项 | A540 | A740 |
|---|---|---|
| PM4 固件 | `a530_pm4.fw` (19652) | — |
| PFP 固件 | `a530_pfp.fw` (16820) | — |
| SQE 固件 | — | `a740_sqe.fw` |

A540 是 Adreno 5xx 的双固件方案（PFP = Program Flow Processor 做分支/条件，
PM4 = 命令流解析）。A740 用单一 SQE。

**`kits/AdrenoPM4` 面向的是较新的包编码**，`a5xx_packets.h` 才是 A540 的包定义。
命令包 opcode 需要按 `adreno_a5xx_packets.h` 重新核对。

## 3. GMU 代际

| 项 | A540 | A740 |
|---|---|---|
| gpmu-version | **0x00030000，GMU 3.0** | 更新一代 |
| gpmu_major/minor (内核要求) | 3 / 0 | — |
| HFI 消息集 | 需按 `adreno_a5xx.c` 核对 | `AdrenoHfi.c` 面向 A7xx |

`AdrenoFirmware` 的 `AdrenoHfi.c` / `AdrenoHfiStartup.c` / `AdrenoHfiVotes.c`
是 A7xx 的 HFI 封装，A540 的 HFI 命令表在 `adreno_a5xx.c` 的 `_gpmu_*` 系列里。

## 4. 时钟控制器

| 项 | A540 | A740 |
|---|---|---|
| compatible | **`qcom,gpucc-8998-v2`** | SM8550 的 GPUCC |
| 基址 | 0x05065000 | — |
| 大小 | 0x9000 | — |
| 父时钟 | `xo_ao`, `gpll0` | — |

8 个 GPU 时钟：`core_clk iface_clk rbbmtimer_clk mem_clk mem_iface_clk isense_clk rbcpr_clk iref_clk`。

## 5. SMMU

| 项 | A540 | A740 |
|---|---|---|
| compatible | **`qcom,smmu-v2`** | Arm SMMU-500/700 变体 |
| 基址 | 0x05040000 (0x10000) | — |
| micro_mmu_control | **0x6000** | — |
| protect 窗口 | 0x40000000 大小 0x10000 | — |
| #global-interrupts | 0 | — |
| secure_align_mask | 0xfff | — |

`ArmSmmu4K` 是面向 SM8550 的 SMMU 实现，**A540 需要按 `qcom,smmu-v2`
（Qualcomm 自己的 v2 变体）重写**。

## 6. 电源

| 项 | 值 |
|---|---|
| 档位数 | 8 |
| 最高 | 710 MHz |
| 最低 | 27 MHz |
| initial-pwrlevel | **6 (257 MHz)** |
| max_power | 5448 |
| lm-limit | 6000 |
| gmem_size | 1 MB |
| num_protected_regs | 0x20 |

## 7. 内核确认的能力位

`adreno-gpulist.h` A540 条目：

```c
.features = ADRENO_PREEMPTION | ADRENO_64BIT |
            ADRENO_CONTENT_PROTECTION |
            ADRENO_GPMU | ADRENO_SPTP_PC,
```

`gpudev = &adreno_a5xx_gpudev`，`busy_mask = 0xFFFFFFFE`。

## 结论与实现路线

A540 需要一个独立的后端，不能复用 A740 的任何一层。建议顺序：

1. **legacy fw2 解析器**（本文档第 1 节已给出完整格式和常量）
2. **GPUCC 时钟驱动**（`qcom,gpucc-8998-v2`，8 个时钟）
3. **GMU 3.x 启动 + HFI**（按 `adreno_a5xx.c` 的 `_gpmu_*` 系列）
4. **SMMU v2**（Qualcomm 变体，micro_mmu_control 0x6000）
5. **PFP/PM4 加载 + CP 初始化**
6. **命令提交与 fence**

每一层都能用 Linux 内核的对应实现作为规范：
`drivers/gpu/msm/adreno_a5xx.c` / `a5xx_reg.h` / `a5xx_packets.h`。

## 参考源

| 用途 | 位置 |
|---|---|
| A5xx 驱动（权威）| `/root/hk/msm-kernel/drivers/gpu/msm/` |
| A740 参考实现 | `/root/hk/gpu-ref/Adreno740/` |
| 固件/HFI kit | `/root/hk/gpu-ref/AdrenoFirmware/` |
| PM4 kit | `/root/hk/gpu-ref/AdrenoPM4/` |
| A540 固件副本 | `E:\ios-sweat\a540-fw\` |
| 设备树 dump | `E:\ios-sweat\dt-dump.txt` |
