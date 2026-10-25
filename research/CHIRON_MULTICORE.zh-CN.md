# chiron 多核启动

本文记录让 MIX 2（chiron / MSM8998）不只跑在启动核上所需的条件，以及在实机上观察到的现象。范围仅限「内核把从核拉起来」这一步，不涉及用户态。

## 现状

- 上游首轮移植把内核固定在启动核：`apply_chiron_usb.py` 里 `more_cores = 0`，`chiron-profile.json` 里 `cpu_count = 1`。
- 这是规避，不是设备限制。MSM8998 用 PSCI `CPU_ON` 拉起从核，内核侧已有完整实现（`osfmk/arm64/psci_smp.c`、`GenericARMPE::run()`）。
- `ci/hackios/apply_chiron_multicore.py` 撤销上述固定，让 `cpus=N` 真正生效。

## 必须同时成立的条件

### 1. 虚拟定时器是 PPI 19

```c
/* osfmk/arm64/gic_v3_irq.c，改之前 */
#define GIC_INTID_VTIMER        27      /* EL1 virtual timer PPI */
```

27 是 Apple 与 QEMU 的编号。MSM8998 是 **19**。

编号错误的后果不是「定时器不走」，而是**从核在 `cpu_init()` 里停下**：从核进入内核后要靠时钟中断推进，向量号对不上就永远等不到，最终由 panic 路径的 `DebuggerXCallEnter()` 向其余核发 `SIGPdebug`，屏幕上出现成串的：

```text
cpu_signal failed in DebuggerXCallEnter
```

改法（`apply_chiron.py` 已在做）：loader 把编号写进设备树，内核读取而不是编译期定死。

```c
dt_prop_u32(&w, "virtual-timer-intid",
    plat->virtual_timer_intid != 0 ? plat->virtual_timer_intid : 27);
```

```c
if (SecureDTGetProperty(entry, "virtual-timer-intid", &tp, &tp_size) == kSuccess) {
    gic_vtimer_intid = *(uint32_t const *)tp;
    if (gic_vtimer_intid < 16 || gic_vtimer_intid > 31) panic("gic: timer is not a PPI");
}
```

### 2. `cpus=` 要传到内核

loader 自身默认只给一个处理器，多核必须由 payload 的命令行显式要求：

```text
-v cpus=7 loader_return_after=45
```

### 3. 已排除的两个错误方向

| 曾经怀疑 | 实测结论 |
| --- | --- |
| GOP 行距不一致导致每行错位 | 实测 `PixelsPerScanLine = 1080 = width`，`stride = 4320 = 1080×4`，**不存在错位** |
| `excepstack_top` 未分配 | `cpu_data_alloc(FALSE)` 在从核启动前已调用，字段非零 |

## 编解码：panic 的那几行日志

```text
UEFI EXCEPTION type=0 elr=... esr=96000006 far=10F000000
```

`ESR = 0x96000006` 解码为 `EC = 0x25`（Data abort）+ `ISS = 0x6`（Translation fault, level 2, 读），即访问了一个未映射地址。这条来自 **loader 自己的**异常钩子（`install_exception_hook()`），不是内核 panic，出现它说明内核根本没被启动。

## 尚未完成

- 修好 `virtual-timer-intid = 19` 之后，7 个核能否全部走完 `cpu_init()` 并进入调度，仍待实机确认。
- 从核稳定启动后，`psci_smp_stop_others()` 的关核路径也要在同一次运行里验证（返回 bootloader 时只应剩启动核）。

## 与上游的分歧

上游选择单核并已在实机上跑出 PID 1。本分支保留多核，因为：

- 从核问题是可诊断的（定时器编号已定位），不是设备缺陷；
- 用户态（launchd、CoreAnimation）在多核下行为才接近真机；
- 单核路径可以随时用 `cpus=1` 复现，不丢失上游已验证的能力。
