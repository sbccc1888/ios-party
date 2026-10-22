# 自检镜像实机 SIGBUS 与入口栈修正

日期：2026-10-22。用户自行启动 `CT-39756701933-1-3B4D3DB21117` 后提供
`IMG_20261022_180358.jpg`。照片 SHA-256 为
`ac560254c807cb4bc90d5b8897f11c1dbe0cc6ef39d783a0de14b613ceb38024`。

## 直接观察

照片显示 PID 1 / launchd 退出，status=10、reason namespace=2、subcode=0xA，
随后 `initproc failed to start`。固定 XNU 的 `bsd/sys/reason.h` 定义
namespace 2 为 `OS_REASON_SIGNAL`，`bsd/sys/signal.h` 定义信号 10 为 SIGBUS。
panic caller `0xfffffff007a125b8` 在交付内核符号表中为
`_proc_prepareexit.cold.3`，是处理 init 退出的位置，不是原始用户故障 PC。

后续 core dump error 2 是转储创建失败，不是本照片证明的原始故障原因。
没有可见的完整用户 PC、SP、ESR 或有效 FAR，不能将 SIGBUS 直接称为
签名/AMFI 封控，也不能据此归因为 RAM 根卷损坏或 UFS 故障。

## 已修正的实现缺陷

旧程序 `_start` 不调整 Darwin 原始 argc/argv 入口栈就调用自检。
自检首条指令为 `stp x29,x30,[sp,#-96]!`，减 96 不会改变 SP 模 16 的余数。
既有真实 QEMU `preflight-captures/chiron-qemu-init/gdb-stop.json` 在 EL0
检查点记录 SP=`0x16fdffe08`、SCTLR=`0x3454593d`；按程序的 SP 保存/恢复路径，
该余数 8 会带入自检。固定 XNU `proc_reg.h` 的 SA0 为 bit 4，
`sleh.c:handle_sp_align` 将用户 SP 对齐异常转为
`EXC_BAD_ACCESS / EXC_ARM_SP_ALIGN (0x103)`，可继续变为 SIGBUS。

入口现在在 BL 前显式向下对齐 SP，保留原 argc/argv 数据，不关闭 SA0。
指令测试覆盖入口余数 0 和 8，并独立检查每次 SP 成对访存的对齐条件；
修复前失败、修复后通过。旧心跳程序没有 SP 访存，不能用它成功反驳此缺陷。
QEMU 曾执行成功也不能单独证明真机未命中此条件。

这是已确认的程序缺陷，且与首次自检输出前 SIGBUS 相符；
尚未取得该次手机故障的 PC/ESR/SP，不能写成实机根因已被最终确认。

## 持久异常通道

源码 overlay 扩展到固定 `bsd/uxkern/ux_exception.c`，并覆盖 SLEH 中
未解决的用户 abort、PC/SP alignment。专用快照保存 PID、PC、ESR、
原始 FAR、SP、SPSR、Mach code，以及转换后的信号与地址。
它与可恢复异常的最近快照分开，避免后续内核缺页覆盖原始用户上下文。

最终 panic 重新输出专用快照和最近异常，而不是依赖仍在屏幕上的旧文本。
SP/PC alignment 的 FAR 不作为有效数据地址解释，解码器据 EC/FnV 标明有效性。
64 KiB wire version 1 的旧字段偏移保持不变，使用原保留区的 extension version 1；
旧记录的 extension=0 仍可解码。

云端新增独立的 `chiron-init-sigbus.S` 资格程序，故意写自己的 RX 代码页。
它只用于 QEMU，要求实际记录 EL0 写保护异常、Mach code=2、PID 1 SIGBUS、
init 退出 panic，并在最终 panic 文本和屏幕保留精确 PC/ESR/FAR。
负向程序不放入手机 FAT，不替换正常自检程序，不注入手机启动参数。
进程退出后的调试只读取内核记录，不依赖已退出进程的兼容占位结果。

本地 44 项回归、11 个固定源码 overlay 及负向程序签名构建通过；
新云端与实机结果另记。设备刷写、启动和重启仍由用户操作。
