# A540 权威规格（来自内核 gpulist）

来源：`drivers/gpu/msm/adreno-gpulist.h:242-263`（A540 patchid=0）与 `:264-281`（ANY_ID）。

## 完整条目

```c
{
  .gpurev    = ADRENO_REV_A540,
  .core = 5, .major = 4, .minor = 0, .patchid = 0,
  .features  = ADRENO_PREEMPTION | ADRENO_64BIT |
               ADRENO_CONTENT_PROTECTION |
               ADRENO_GPMU | ADRENO_SPTP_PC,
  .pm4fw_name  = "a530_pm4.fw",
  .pfpfw_name  = "a530_pfp.fw",
  .zap_name    = "a540_zap",
  .gpudev      = &adreno_a5xx_gpudev,
  .gmem_size   = SZ_1M,
  .num_protected_regs = 0x20,
  .busy_mask   = 0xFFFFFFFE,
  .gpmufw_name = "a540_gpmu.fw2",
  .gpmu_major  = 3,
  .gpmu_minor  = 0,
  .gpmu_tsens  = 0x000C000D,
  .max_power   = 5448,
}
```

## 关键推论

**`ADRENO_64BIT`** — 确认 64 位 GPU 地址空间，微码基址寄存器的 LO/HI 双字写法是必要的。

**`gmem_size = 1 MiB`** — ZAP 范围与 ME RAM 核算的基准。之前移植完全没有这个数字。

**`num_protected_regs = 0x20`** — CP 保护寄存器共 32 个。

**`busy_mask = 0xFFFFFFFE`** — RBBM_STATUS 的 bit 0 不参与 busy 判定。

## 版本 3.0 vs 固件 3.9

| 来源 | major | minor |
|---|---|---|
| gpulist 期望 | 3 | 0 |
| `a540_gpmu.fw2` tag1/tag2 | 3 | 9 |

**不冲突。** `_read_fw2_block_header`（`adreno_a5xx.c:1241`）的比较是
"期望 > 固件"才报错，不是相等比较：

```c
case HEADER_MAJOR:
  if ((major > header[i + 1]) && header[i + 1]) {
    KGSL_CORE_ERR("GPMU major version mis-match %d, %d\n", ...);
    return -EINVAL;      // 仅当内置软件比固件旧
  }
  break;
case HEADER_MINOR:
  if (minor > header[i + 1])
    KGSL_CORE_ERR("GPMU minor version mis-match %d %d\n", ...);
  break;                 // 注意：没有 return
```

- `major: 3 > 3` → false → 通过
- `minor: 0 > 9` → false → 通过（连日志都不打）

`header[i+1] == 0` 的额外判断意味着**不带版本 tag 的固件也会被接受**。

## 固件头解析复核

```
file bytes     = 11736
block_dwords   = 2933      (data[0])
block_id       = 2         (data[1] = GPMU_FIRMWARE_ID)
header_dwords  = 8         (data[2])
cmd_size       = 2933 - 8 - 2 = 2923
cmds offset    = 8 + 3 = 11 dwords
```

内核的实际算法（`adreno_a5xx.c:724-729`）：

```c
if (data[2] > (data[0] - 2)) goto err;
cmds     = data + data[2] + 3;
cmd_size = data[0] - data[2] - 2;
```

**与本项目 `ChironAdrenoA540Fw2` 的实现一致。**
