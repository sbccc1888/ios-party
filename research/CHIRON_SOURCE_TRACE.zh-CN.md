# 固定基线的源码日志与异常记录

日期：2026-10-22。

## 状态

已取得的最新实机照片 `IMG_20261022_151546.jpg` 显示编号源码日志，最后可见 `PE_create_console() enter`；内存初始化与观察器原生映射已返回。照片不独立校验镜像哈希，尚无实机 PID 1 或完整 iOS 用户态的证据，也尚未确定失败原因。

最新运行 `39745321134` 已编译修正后的源码内核，通过控制台早期路径、真实 PID 1 两次心跳、POST 正反例、panic 文本和注入异常资格，完成打包及本地交付复核。旧运行 `39726305878` 的内核经 `39736014170` 打包，是上述实机照片对应版本。新的设备树 panic 路径尚待实机测试；此前设备故障原因没有被 QEMU 的受控测试自动确定。

## 已验证交付

```text
Kernel build ID: CT-39745321134-1-783cc7e68f24
Kernel source: 783cc7e68f24969bd38134ef4bdd28836dbc4b39
Packaging source: 783cc7e68f24969bd38134ef4bdd28836dbc4b39
Image: dist/source-trace-39745321134/CT-39745321134-1-783cc7e68f24.img
Bytes: 26,697,728
SHA-256: 4d24f9f96c021dba3984bae40d9db61bca287c4f22a4bdb7f59ab48a7477ef0a
Kernel SHA-256: 4fdd24494e423e52bf888bcc96e0f4036363caabd45d357496142b89778213f9
```

本地已独立检查 manifest 正确指向 `.img`、Android 容器头和 SHA-1、完整镜像哈希、重建 FAT 及四个载荷哈希、内核 POST HMAC、内核内构建 ID、启动菜单标题一致，且手机启动配置没有 `chiron-trace-test`。本地重新解码原始 console/normal 缓冲并确认控制台返回和两次心跳，报告为 `local-verification.json`。23 项主线门禁及云端完整资格通过；64 位显示字段修正后也重新通过宿主/ARM64 检查。此前已经跑过的 QEMU 资格未被假定继承，而是对当前内核重新取得。

手机进入 fastboot 后：

```sh
fastboot boot /Users/mac/Documents/ChatGPT/ios_party/dist/source-trace-39745321134/CT-39745321134-1-783cc7e68f24.img
```

预期是带 `CT-39745321134-1-783CC7E68F24` 标题的连续日志，而不是单个 `XNU STAGE` 编号。先观察 `panic DT region absent` 及 `PE_create_console returned`，再记录后续最后日志；红色 `PANIC CALLER` 表示记录了 panic 原因并停止。`EXCEPTION - MAY BE RECOVERABLE` 不独立代表失败；若之后出现两次 `CHIRON RAM init: PID 1, 4K ABI, heartbeat`，支持测试 init 完成两次定时等待。一条心跳本身不足以证明后续定时等待完成。

manifest、控制台早期/正常启动/受控 panic/真实数据异常的可读日志、原始记录、实际 QEMU 屏幕、内核符号、dSYM 和观察器源码位于 `dist/source-trace-39745321134/`。当前 `fastboot devices` 为空，没有自动启动或重启手机。

## 固定输入

基线声明位于 `diagnostics/baseline.json`，主要源码仍为：

- UniXNU `7b52e7968c76453cdc18b5aa174fc368d5293b91`。
- XNU `d8b80295118ef25ac3a784134bcf95cd8e88109f`。
- 原 `chiron_raminit_defconfig` 的单 CPU、4 KiB、RAM init、真实源密码学和 pthread 组合。
- 已验证的 EFI 与 RAM 卷固件产物，以及固定的零尾部传输汇编；不在本轮修改其固件主体。

`chiron_trace_defconfig` 相对当前 RAM 基线只改变观察器开关。十个修改目标在应用固定上游补丁后，必须匹配 `diagnostics/source-contexts.json` 的源文件哈希；否则拒绝 overlay。源码变化、编译器、归档输入、链接后的内核哈希和构建编号必须随产物保存。

本轮不对编译后的 XNU 改入口、替换指令或占用链接填充。普通构建流程仍进行源密码学 POST HMAC 的链接后封装，并验证实际正反例。

## 通道

`diagnostics/chiron-trace.*` 定义一个 64 KiB、版本化的内存记录区，位于正式可写内核数据段中，包含：

- 构建 ID：运行编号、重跑次数和源码提交前缀。
- 四个源码汇编阶段及其实际 PC。
- 带序号的阶段/初始化器日志，初始化器含函数和参数地址。
- 持续内核文本环形缓冲区。
- 最近一次异常的 PC、ESR、FAR、SP、SPSR、SCTLR 和 TCR。
- 独立的 panic 调用者、panic/异常次数，避免把旧异常当成本次 panic。
- 映射方式、实际绘制次数、绘制拒绝次数、重入和丢弃计数。

内存记录先于绘图；屏幕不可用时继续记录。环形文本和阶段表有明确容量，溢出会计数，不声称无限保留全部输出。日志只在本次启动的 RAM 内有效，不写 userdata，也不承诺重启后仍能取回。

早期汇编使用独立异常向量和紧急栈。MMU 开启后，早期向量改用 KVA，直到当前线程/CPU bootstrap 就绪后才恢复原生向量。原生同步异常入口记录实际保存状态；普通 SVC 系统调用不计为故障。可恢复异常记录后继续原处理，panic 在显示原因后停止，早期异常停止时明确标注。重入会保留独立字段并限制递归。

绘图最初使用经过检查的 bootstrap 映射，必要时在屏蔽中断的短窗口中借用该 TTBR0 并恢复；正常启动代码不在借用窗口内执行。`arm_vm_init` 完成后通过原生 `ml_io_map` 建立独立显示映射，随后不再借用启动映射。诊断显示最多使用 4 MiB、上部 768 行，减少早期 I/O 映射和绘图成本。正常控制台重置不会丢失内存记录。

## 源码接入点

- `start.s`：冷入口、bootstrap 页表完成、MMU 切换前后、早期异常向量。
- `arm_init.c`：首个 C 入口、CPU bootstrap、内存初始化返回、串口、控制台和平台初始化。
- `arm_vm_init.c`：保护页表、正式 TTBR1、TTBR0 撤销和 `pmap_bootstrap` 前后。
- `startup.c`：每个初始化器的进入与返回，保留真实函数地址。
- `sleh.c`：原生同步异常实际保存的寄存器。
- `debug.c`：所有常见 panic 包装器汇聚点，先捕获文本，再进入诊断停止状态。
- `serial_console.c`：同时接管缓冲式/非缓冲式写入，在原来的关闭开关和缓冲路径之前捕获真实控制台文本。
- 固定上游的 BootProgress 源码接口：内核文本、BSD/init/exec、IRQ 和退出事件。

日志中的初始化器地址配合对应构建的 `kernel-symbols.txt`、dSYM 和已修改源码定位。最后事件只代表执行下界；除非有异常、panic 或独立挂起证据，不把它直接称为故障位置。

## 云端资格

已有 `Chiron remote bring-up` 工作流增加 `kernel_mode=ram-trace`。其顺序是：

1. 轻量测试、固定源码/上下文检查。
2. 云端编译 observer、XNU、驱动、源密码学和最小 init；不在 Mac 本地编译完整内核。
3. 对同一个内核重新验证真实 POST、破坏 HMAC 的拒绝和真实 PID 1/页大小/定时等待。
4. 导出正常启动的实际 RAM 日志与 QEMU 屏幕。
5. 在 QEMU 中验证真实 POST panic 的文本、调用者及屏幕。
6. 仅通过测试 boot arg 请求对无效低地址 `0x1000` 的读取，验证真实数据异常 PC/ESR/FAR。
7. 通过后，把同一资格内核与匹配 init/ADT/trustcache 打入固定 RAM 传输，验证 FAT 读回和零尾部，再生成带编号镜像及 manifest。

打包拒绝把 `chiron-trace-test` 注入参数放进手机镜像。云端受控故障只用于验证通道，不是设备故障原因。产物含源码提交、依赖哈希、最终镜像哈希、验证报告、可读日志、实际屏幕和符号资料。

宿主测试覆盖环形缓冲、顺序、panic 参数不被消耗、异常字段、映射恢复、显示边界和重入保护。真正的 ARM64 入口、MMU、panic 和异常投递以云端 QEMU 资格为准；真机结论仍须单独记录。

主线门禁为 `python3 ci/test_source_pipeline.py`，覆盖源码观察器、配置、RAM 资格与实际使用的传输/FAT 组件。历史的固件卷重压缩和指令插入工具不参与新流水线，其独立测试保留。首次云尝试 `39719697817` 在这些历史 LZMA 测试上失败，未进入内核编译，也没有生成镜像。

运行 `39722298776` 已编译新源码内核，并通过原 POST 正反例和 PID 1 两次心跳资格。新增故障测试被上游调试器的 `_panic` 提前停点拦住，未执行到日志捕获函数，因此未生成上机镜像。仅对显式选择 `_chiron_trace_fault_checkpoint` 的受控测试取消这两个提前停点；普通测试保留原来的 panic 防护。

该轮实际屏幕还揭示 `/dev/console` 的缓冲写入可能在进入原有字符钩子前被关闭开关丢弃。源码通道因此补齐缓冲入口，并要求资格运行的日志中出现两次真实心跳文本。`proc_prepareexit` 只标注为生命周期钩子，不把进入该函数直接称为进程已经退出；可恢复异常也不使用 panic 的红色显示。

运行 `39724613586` 已通过前述源码通道资格并完成云端打包。缓冲控制台补齐后的最终版本仍需重新执行同一流水线，额外检查心跳文本，以及数据异常确实来自 `_chiron_trace_test` 和显式注入事件，而不是启动中正常的可恢复缺页。

运行 `39726305878` 已通过上述完整资格：控制台含两次真实心跳、panic 文本和调用者均捕获，注入数据异常定位到 `_chiron_trace_test+0x44`、FAR=`0x1000`。本地交付检查发现打包脚本变量复用使 manifest 错指报告文件；已补回归测试，后续只复用该次已验证内核在云端重打包。manifest 分别记录内核源码/运行编号和打包源码/运行编号，不能混为一项。

## 边界

目前只面向固定的单 CPU 早期启动。尚不承诺 SMP、EL3/固件异常、硬件总线完全挂死或损坏栈的所有情况。若没有异常且 CPU 不再执行，内存记录只能保留最后经过的事件；不能据此编造故障原因，可能仍需要独立硬件调试或已验证的看门狗。

这条路线的通过标准是每轮保存可解码的经过记录、真实异常/失败文本和地址，不是继续增加单个编号，也不是宣称 iOS 已启动。

## 控制台路径修正与扩展记录

用户照片 `IMG_20261022_151546.jpg` 首次显示编号 `CT-39726305878-1-BFAAE879457F` 的源码日志，最后可见 `0x6ad PE_create_console() enter`。正式页表、`pmap_bootstrap`、`arm_vm_init`、观察器原生映射与 `serial_init` 均已返回；尚无可见异常原因和实机 PID 1。下部旧 UEFI 菜单只是 framebuffer 中保留的内容，不能据此认定控制权已返回 UEFI。

从已交付镜像原样提取内核后，`llvm-objdump --disassemble-symbols=_PE_create_console` 确认该函数包含：

```text
mov  w0, #0xc000
movk w0, #0x3c0f, lsl #16
mov  w1, #0x4000
bl   _ml_io_map_wcomb
...
ldr  w8, [x0]
...
bl   _bzero
```

这与固定源码的 BCM2837 panic 区域宏相符：`0x3c0fc000`、16 KiB。当前 RAM 配置未启用已有的设备树路径，因此编译进了外来板级默认值。这是明确的配置缺陷，尚无实机 PC/总线证据证明它就是本次停滞根因。

`chiron_raminit_defconfig` 和 `chiron_trace_defconfig` 同时启用 `CONFIG_XNU_DT_PANIC_REGION=y`，保持两种运行模式的非观察器配置一致。固定上游的现有实现校验 `pram/reg` 和 `/chosen/embedded-panic-log-size`；缺少区域即返回。当前项目及 QEMU ADT 都没有 `pram`，不会以替代地址进行映射或清零。此改动不改 POST、进程签名或 entitlement 路径。

新增三个按固定源哈希校验的 overlay 目标：

- `pexpert/arm/pe_init.c`：panic 检查入口/返回、采用的区域来源、校验失败原因、读取/清零，以及模式选择。
- `osfmk/console/video_console.c`：输入几何与长度/偏移、映射决策、映射参数/返回、旧映射退役、锁/几何/VT100 初始化、清屏和控制台重绑。
- `osfmk/arm/io_map.c`：早期虚拟地址分配及 `pmap_map_bd` 前后，保留原映射语义。

`boot_progress_bind` 保留观察器独立映射，记录正常控制台的真实地址及几何，并重绘历史记录。宿主测试验证外部绑定不会覆盖观察器映射、几何或边界。全部十个改动文件随成品保存。

新增 `_chiron_trace_console_checkpoint`，只在源码中的 `PE_create_console` 返回后执行。云端对相同内核在该处采集原生 QEMU 屏幕与原始记录，按执行顺序验证设备树路径、缺省区域跳过、framebuffer 映射和控制台返回，并拒绝固定板级区域日志及 panic。其余正常 PID 1、POST 正反例、panic 和注入异常资格全部保留。此检查只验证 QEMU，不能取代实机结果。
