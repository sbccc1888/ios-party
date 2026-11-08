# chiron A540 GPU 硬件输入（实机采集，2026-11-08）

全部数据来自手机 `/sys/firmware/devicetree/base`，KernelSU root 下读取。
不做推断，只记录读到的字节和解码结果。

## 身份

| 项 | 值 | 来源属性 |
|---|---|---|
| chipid | **0x05040001** | `qcom,chipid` = `05 04 00 01` |
| GPU | **Adreno A540, revision 1** | 注意：项目文档写的 `0x05040000` 是 rev 0，实机是 rev 1 |
| GMU 版本 | **0x00030000 (GMU 3.0)** | `qcom,gpmu-version` |
| GPU id | 0 | `qcom,id` |
| bus width | 32 | `qcom,bus-width` |

## 地址映射

| 区域 | 基址 | 大小 | 节点 |
|---|---|---|---|
| GPU 控制寄存器 | **0x05000000** | 0x40000 | `qcom,kgsl-3d0@5000000` |
| SMMU | **0x05040000** | 0x10000 | `arm,smmu-kgsl@5040000` |
| GPUCC（时钟控制器）| **0x05065000** | 0x9000 | `qcom,gpucc@5065000` |
| QDSS STM | 0x0161c000 | 0x40000 | `qcom,gpu-qdss-stm` |
| qtimer | 0x017921000 | 0x1000 | `qcom,gpu-qtimer` |

## 中断

| 用途 | SPI | 类型 |
|---|---|---|
| kgsl_3d0_irq | **300** (`0x12c`) | level |
| SMMU global | 329, 330 | level |
| SMMU context | 331 | level |

## 固件

| 文件 | 大小 | SHA256（实机拉取）|
|---|---|---|
| `a540_gpmu.fw2` | 11736 | `57d7653c4dcc1130ac2c31512ec7fbb6348613d4bcd99b9b6221e3f9ee52f535` |
| `a540_zap.mdt` | 6684 | `af255cceb96a309f8ee31430b0c8849c7d06dbe338f3d6e7424365d567783717` |
| `a540_zap.b00` | 148 | `5302ecf8978c825bdc9d8455a828a4ac1e1d762cf09ff821f9f506fb89a549e5` |
| `a540_zap.b01` | 6536 | `d6436b7661b2d78f4a9f614aad91078a5814e723e2e00e1828c6ca7d50cad8e5` |
| `a540_zap.b02` | 4916 | `997933766e93f7692329f397808f8c23128a58b565d6305e48608cfce711e5d7` |
| `a540_zap.elf` | 17204 | `bddc06814c76158f6bd014c665aa870f0976e2eda6614d92e15bc4982d24fc91` |
| `a530_pfp.fw` | 16820 | `cc4f2b1cd6b3a9401592e2498ec43b87ba926a23848fa231f417ab08df9c47ba` |
| `a530_pm4.fw` | 19652 | `a02c9be11787472d4ab0173ed8552afbde1f31b1e81bcd87adf1fcea97b6115f` |

**重要差异：A540 复用 A530 的 PFP/PM4 命令处理器固件**，不是 A740 的 `a740_sqe.fw`。
文档里提到 `a540_zap` 与 `a540_gpmu.fw2`，与实机一致。

ZAP 由 `qcom,kgsl-hyp` 节点加载：`pas-id = 13 (0xd)`，`firmware-name = a540_zap`，
`compatible = qcom,pil-tz-generic`。

## 电源档位（8 级，big-endian u32）

| 档位 | gpu-freq | bus-freq | bus-min | bus-max |
|---|---|---|---|---|
| 0 | 710 MHz (0x2a51bd80) | 192 | 192 | 192 |
| 1 | 670 MHz (0x27ef6380) | 192 | 176 | 192 |
| 2 | 596 MHz (0x23863d00) | 176 | 144 | 192 |
| 3 | 515 MHz (0x1eb246c0) | 176 | 144 | 192 |
| 4 | 414 MHz (0x18ad2380) | 144 | 128 | 176 |
| 5 | 342 MHz (0x14628180) | 128 | 80 | 144 |
| 6 | 257 MHz (0x0f518240) | 80 | 48 | 128 |
| 7 | 27 MHz (0x019bfcc0) | 0 | 0 | 0 |

`qcom,initial-pwrlevel = 6`（257 MHz 起步）。

## 时钟（8 个）

```
core_clk  iface_clk  rbbmtimer_clk  mem_clk  mem_iface_clk  isense_clk  rbcpr_clk  iref_clk
```

`gpucc` 节点的父时钟是 `xo_ao` 和 `gpll0`，`compatible = qcom,gpucc-8998-v2`
（MSM8998 专用，与 SM8550 的 GPUCC 不同）。

## 供电

| 名称 | phandle |
|---|---|
| vddcx | 0xd7 |
| vdd | 0x1a9 |
| vdd_dig (gpucc) | 0x5a |

`regulator-names = "vddcx", "vdd"`。

## SMMU 细节

| 项 | 值 |
|---|---|
| compatible | `qcom,smmu-v2` |
| micro-mmu-control | **0x6000** |
| protect 区间 | **0x40000000，大小 0x10000** |
| secure_align_mask | 0xfff |
| #global-interrupts | 0 |
| TCU 时钟 | gcc_gpu_cfg_ahb_clk, gcc_bimc_gfx_clk, gcc_gpu_bimc_gfx_clk |
| attach-impl-defs | 有 20 组寄存器对（见 dt-dump.txt 原始字节）|
| hyp_secure_alloc | 存在 |
| retention | 存在 |

`kgsl-iommu` 节点的 `compatible = qcom,kgsl-smmu-v2`，与 SMMU 共用同一寄存器区间。

## 其他参数

| 属性 | 值 | 含义 |
|---|---|---|
| base-leakage-coefficient | 0x22 | CPR 泄漏系数 |
| gpu-efuse-leakage | v0=0x00070130, v1=0x00000018 | eFuse 泄漏值 |
| highest-bank-bit | 15 | 最高 bank |
| idle-timeout | 80 ms | |
| l2pc-cpu-mask | 0xf0 | |
| lm-limit | 0x1770 (6000) | |
| max-power | 0x1548 (5448) | |
| snapshot-size | 0x100000 (1 MB) | |
| gpmu-tsens | 12, 13 | 温度传感器 ID |
| tsens-name | tsens_tz_sensor12 | |
| coresight-atid | 3 | |
| no-nap | 存在 | 禁用 nap |
| gpu-quirk-lmloadkill-disable | 存在 | |
| bus-control | 存在 | |
| isense-clk-on-level | 1 | |

## 与 A740 的关键差异（下一步要展开）

1. **命令处理器固件不同**：A540 用 `a530_pfp.fw` + `a530_pm4.fw`；A740 用 `a740_sqe.fw`。
   PFP/PM4 是 Adreno 5xx 时代的双固件方案，SQE 是 6xx/7xx 的单固件方案。
2. **GMU 版本**：A540 是 GMU 3.0；A740 是更新一代。HFI 消息集需要核对。
3. **GPUCC**：`qcom,gpucc-8998-v2`，不是 SM8550 的。
4. **SMMU**：`qcom,smmu-v2`，不是 SM8550 的 ArmSmmu500/700 变体。
5. **chipid**：0x05040001 vs A740 的 0x07040000 系列。

## 固件文件位置

实机拉取的副本在 Windows `E:\ios-sweat\a540-fw\`，含全部 8 个文件。
