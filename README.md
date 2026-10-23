# ios-party

小米 MIX 2（chiron / MSM8998）的实验性 XNU / iOS 移植源码。
当前默认分支 `codex/hackios-chiron` 保存 HackiOS 8796 路线；
`codex/chiron-source-trace` 保留此前 10063 基线。
这是开发项目，尚不是可日用的 iOS ROM。

本仓库不提供 Apple 原版系统文件；公开工作流不会上传 Apple 系统卷。
原始贡献的许可为 [BSD-2-Clause-Patent](LICENSE)，上游源码和补丁保留各自许可，
见 [第三方与发布范围说明](NOTICE.md)。

基于 UniXNU 与 MSM8998 EDK2 的设备移植工程。目标设备为小米 MIX 2 6 GB / 128 GB，实测 SoC 修订 2.1、Synaptics 触控、JDI video 面板。

2026-10-22，实机照片 `IMG_20261022_162407.jpg` 已显示构建 `CT-39745321134-1-783CC7E68F24` 和多轮 `CHIRON RAM init: PID 1, 4K ABI, heartbeat`，确认 MIX 2 上的最小 4 KiB XNU 与自有用户态 PID 1 已运行。尚未生成完整 iOS 用户态或可日常使用的系统。早期 CPU 页大小能力与 GOP 参数见 [UEFI 探针记录](research/CHIRON_UEFI_PROBE.zh-CN.md)。

此前已在 Cortex-A53 QEMU 中观察到自有 PID 1、4 KiB ABI 和两次真实心跳，并通过实机 `17` 标记确认原始 `arm_vm_init` 返回。当前实机多轮心跳进一步支持 PID/页大小检查、控制台写入和多轮定时等待返回；单张照片不证明长期稳定性，也不单独确定此前停滞的根因。

后续主线改为固定源码基线上的统一日志：入口与内存初始化记录、持续内核文本、EL1 异常 PC/ESR/FAR 和 panic 原因先进入版本化内存记录，再输出到屏幕。云端 `ram-trace` 模式统一编译、验证、编号和打包，不再对编译后的 XNU 追加指令补丁。以前的本地插桩工具保留为历史诊断资料。

2026-10-22 的 `IMG_20261022_151546.jpg` 已显示该源码日志，最后可见 `PE_create_console() enter`；`arm_vm_init`、独立原生 framebuffer 映射和串口初始化均已返回，尚无实机 PID 1。后续核对旧成品指令发现控制台路径仍读取 BCM2837 固定 panic 区域 `0x3c0fc000`；RAM 运行配置已改为仅使用经校验的设备树区域，新增控制台内部日志和 QEMU 早期资格。不能把此配置问题直接认定为照片中的停滞原因。

修正后的云构建 `39745321134` 已通过控制台路径、POST 正反例、PID 1 两次心跳及真实 panic/数据异常资格，并完成打包及本地原始记录/载荷哈希复核。实机心跳对应镜像为 `dist/source-trace-39745321134/CT-39745321134-1-783cc7e68f24.img`，照片证据与完整边界见 [源码可观测性](research/CHIRON_SOURCE_TRACE.zh-CN.md)。

设备操作由用户执行：刷写、临时启动和重启均不由助手操作；助手负责源码、构建、产物校验和日志分析。

下一阶段已并行实现：[用户态自检](research/CHIRON_USER_SELFTEST.zh-CN.md) 已在真实 XNU/QEMU 完成文件读取、两页匿名映射、独立子程序执行及退出回收，候选镜像为 `dist/source-trace-39756701933/CT-39756701933-1-3b4d3db21117.img`，仍待实机观察 `SELFTEST PASS`。[UFS 只读探针](research/CHIRON_UFS_READONLY.zh-CN.md) 已通过宿主测试和独立 XNU 归档编译；未链接、未执行硬件，仍缺退出 UEFI 后的电源/时钟、链路、DMA/SMMU 及独占交接资格。

首轮内核、驱动库、EFI 和整合固件的远程编译均已通过。成品位置、哈希和验证边界见 [构建记录](BUILD_STATUS.md)。

## 构建方式

GitHub Actions 工作流 `Chiron remote bring-up` 在源码推送时仅跑轻量验证；真正的编译任务仅手动触发：

- Linux runner 编译 AArch64 UniXNU、DarwinBoot 和 MSM8998 固件。
- macOS runner 编译 4 KiB XNU 源内核与平台驱动静态库。
- Mac 本地只运行轻量测试、生成 ADT 和下载必要产物。
- 不下载完整 IPSW，不生成 userdata 镜像，不执行 ADB/fastboot。
- 每个任务设有超时，产物保留 7 天；不缓存完整编译树。

固件集成重跑可填写 `efi_run_id`，复用同仓库此前成功生成的 `chiron-efi` 产物；该情况下跳过 EFI 重编译。更新 EFI 源码后应留空，重新编译。

`kernel_mode=ram-init` 增加源密码学、pthread、平台/CPU 与 mockfs 链接，并运行 QEMU POST 正反例和自有 PID 1 心跳测试。该模式需填写可用的 `efi_run_id`；其通过标准是 `runtime-test.json` 的真实运行观察，不能以编译成功代替。源密码学使用上游固定的第三方评价快照，来源信息保留在 `ci/sources.json` 和构建报告中，不声明厂商认证。

源码诊断配置关闭依赖原始 AppleImage4/AMFI 的完整 trust-cache runtime；自有 init 仍进行构建时 ad-hoc 签名检查，但不声明 Apple 信任链或完整 iOS 安全模型。完整系统路径需要另行接入真实组件。

仅在显式的 `XNU_DIAGNOSTIC_EMPTY_ENTITLEMENTS` 配置中，AMFI 缺失且签名不含 entitlement 上下文、XML/DER blob、entitlement special slot 时，空集合的加速操作返回成功；带 entitlement 的签名返回不支持。该诊断分支保留正常签名结构和页哈希路径，不能作为完整 AMFI 的替代品。

同一诊断模式下，AMFI 缺失时的 IOKit entitlement 查询返回空值；它不授予任何 entitlement。存在 AMFI 时仍执行原来的查询实现。

新增 RAM 负载通道通过 bootloader 的 `linux,initrd-start/end` 交接只读 FAT32 卷，早期保留 payload、固件卷和 ramoops，并通过 GUID HOB 传给 `ChironRamdisk`。该路径正在验证；它不需要写 userdata 或新增磁盘分区。

`ram_init_run_id` 与 `firmware_run_id` 同时填写时，独立打包任务会校验运行观察与输入哈希、创建 48 MiB RAM 卷、读回校验文件，再生成 `chiron-ram-init-experimental.img`。固件输入必须是包含新 RAM 卷驱动的构建，不能使用最初的纯探针固件。该镜像仍需独立实机验证。

本地验证：

```sh
python3 -m unittest discover -s tests -v
python3 scripts/make-chiron-adt.py --output out/chiron/chiron.adt
```

## 当前产物的边界

`configs/chiron_defconfig` 是第一轮源代码 bootstrap 配置。CPU、平台和密码学等外部库尚未链接进内核；原始内核不能直接作为 DarwinBoot 的完整 XNU payload 使用。

CI 另行编译 ArmDTPlatform 与 ArmCpu 静态库。其中 ArmCpu 已改为从 ADT 的 `virtual-timer-intid` 读取本机虚拟定时器中断号 19，保留上游 QEMU 的默认值 27。

`ChironProbe.efi` 是无存储访问的应用级探针，显示原始 CPU 特性、GOP 和 UEFI 内存图。它不切换页表、不退出 boot services；固件侧键入口指向该探针，默认启动仍由 UniXNU registrar 注册。整个固件仍含 vendor 驱动，不能据此声称全链路无写入。

ADT 使用真实 chiron 身份、单核、SMC、GICv3 和易失 NVRAM，不包含存储节点和伪造的 Apple 设备身份。它向 DarwinBoot 请求 1 GiB 实际 UEFI 分配，而不把 Android 的候选地址硬编码为可用窗口。

旧 MSM8998 固件与新加载器分别用各自固定的 EDK II 基线编译，再把 EFI 二进制嵌入固件。编译成功仍需验证 UEFI 协议、最终内存图与入口行为；原平台含未审计的 vendor 驱动，编译产物不能因此称为无写入的诊断镜像。

## 目录

| 路径 | 用途 |
| --- | --- |
| `board.toml` | 实测板级参数与当前构建阶段 |
| `research/chiron-live-profile.json` | 去除设备标识的运行时测量结果 |
| `scripts/make-chiron-adt.py` | 最小 Apple Device Tree 生成器 |
| `drivers/ArmCpu/` | 上游 BSD 驱动及 chiron 定时器适配 |
| `ci/sources.json` | 固定的上游源码提交 |
| `ci/` | 远程构建、集成与产物校验 |
| `tests/` | ADT 资源、NVRAM 和产物架构检查 |
| `tools/` | boot 镜像解析、ADB 只读采集和 FDT/GPT 校验 |
| `research/` | 移植评估、静态镜像分析及实机观察记录 |

大文件、运行时原始采集、设备标识、Apple 镜像与本地构建目录均不进入 Git。上游驱动的许可保存在 `drivers/ArmCpu/LICENSE`。

## 研究记录

- [移植评估](research/MIX2_PORTING.zh-CN.md)
- [boot.img 静态分析](research/CHIRON_BOOT_ANALYSIS.zh-CN.md)
- [ADB 实机采集分析](research/CHIRON_LIVE_ANALYSIS.zh-CN.md)
- [UEFI 探针照片转录](research/CHIRON_UEFI_PROBE.zh-CN.md)

本地 `research/upstream/` 仅作为参考快照，不上传；上游入口和对应版本记录在移植评估中。重新采集 Android 数据时，将输出保存在 `research/artifacts/`，该目录被 Git 忽略。
