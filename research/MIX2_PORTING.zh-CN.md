# 小米 MIX 2（chiron）运行 XNU / iOS 的移植评估

后续实机更新：UEFI 和只读探针已启动，启动 CPU 的原始 TGran16=0，确认不能直接沿用 sweet 的 16 KiB 启动路径；见 [UEFI 探针记录](CHIRON_UEFI_PROBE.zh-CN.md)。以下保留各次研究阶段的观察与条件。

资料核对日期：2026-10-20。本页汇总初始源码评估和后续实机补充。初始评估阶段未启动设备；当前已验证到 UEFI/探针，详细进度以构建记录和探针报告为准，尚未验证 XNU 或 iOS 用户态启动。

## 用户设备与镜像补充

用户已确认规格为 **6 GB + 128 GB**，并提供此前适配的 KernelSU-Next / crDroid boot.img。已完成静态提取，详见 [boot.img 分析](CHIRON_BOOT_ANALYSIS.zh-CN.md)。

- 该镜像实际为 **boot header v0**，gzip 内核后附 3 份 MSM8998 DTB；ARM64 Image 头独立确认 Linux 使用 4 KiB 页。
- 提取的 DTB 确认 GICR 为 `0x17b00000`、UFS 为 `0x01da4000`、触控总线为 QUP5。
- `0xb0000000 + 0x200000` 是 ramoops 保留区，与 sweet 的低地址窗口起点冲突，不能照抄。
- 三份静态 DTB 的 `/memory/reg` 均为零值占位。6 GB 容量已知，但实际 RAM bank、固件选择的 DTB 与触控绑定仍需要运行时数据。
- 这份镜像的打包格式优先作为本机后续诊断镜像的参考。前述 UEFI 示例使用 v1、sweet 使用 v2，都不等于此镜像的实际 v0 格式。

### ADB 实机补充

随后已按用户授权通过无线 ADB 和 su 只读检查本机，详见 [实机检查结果](CHIRON_LIVE_ANALYSIS.zh-CN.md) 和 [结构化测量资料](chiron-live-profile.json)：

- 已确认 SoC v2.1、Synaptics `5-0020` 触控，以及 JDI NT35596S video 面板。
- 实测 fb0 stride 为 4352 字节。运行时 splash `reg` 为 `0x9d400000 + 0x2400000`，与镜像中的静态值不同。
- 实际 RAM banks 为 `[0x40000000,0x100000000)`、`[0x100000000,0x1bd8c0000)`，并已读取 memblock 保留区。
- userdata 的 4 KiB LBA 为 `0x1c000–0x1c257fa`，GPT 主表及 sysfs 交叉校验通过。
- 16 KiB 原始能力仍未读取；当前内核源码会屏蔽缓存的 16 KiB 标志。本轮没有重启或刷写。

## 结论

MIX 2 有值得继续的基础：`edk2-msm8998` 明确列出 chiron，并有专用 DSC、DTB 和二进制驱动目录；Linux 设备树也公开了板级资源。但是 UEFI 支持不等于 XNU 或 iOS 已支持。

建议先做 chiron 的硬件能力与启动探针，再选择内核：

- 默认工程主线：MSM8998 UEFI → UniXNU → DarwinBoot → 4 KiB XNU。复用 UniXNU 的构建、ADT、平台专家与驱动框架，新增 chiron 板级支持。
- 如果实机确认支持 16 KiB 页，而且目标是尽早出现 iOS 16 桌面，可以评估移植 ios-sweet 的直接启动路径。
- ios-sweet 最有价值的参考包括用户态启动、HID、软件 framebuffer 和调试补丁；它与 UniXNU 的 XNU 版本、页大小、文件系统路径不同，不能把两套补丁直接混用。

这是从源码推导的建议，没有实机启动日志前，不承诺能到 SpringBoard，也不承诺完整手机功能。

## 已阅读的项目

| 项目 | 此次读取版本 | 实际提供的内容 |
| --- | --- | --- |
| UniXNU/UniXNU | `main` 归档前缀 `7b52e79` | 构建驱动、UEFI 启动管理器、DarwinBoot、通用 XNU 驱动、移植文档 |
| unixnu.com | 当日 `/porting/` 页面，并核对仓库 `docs/` | UEFI、设备仓库、ADT、分阶段 bring-up 文档 |
| dipa-studio/ios-sweet | `main` 归档前缀 `11da58b` | Redmi Note 10 Pro / SM7150 的直接启动 shim、XNU 补丁、iOS 用户态准备工具 |
| edk2-porting/edk2-msm8998 | `new` 归档前缀 `ace2711` | 已有 chiron 的 UEFI 板级支持 |
| LineageOS/android_kernel_xiaomi_msm8998 | `lineage-22.2`；目录树对象 `7231883a368604f90806e167890e00f515a9927c` | MSM8998、chiron、BLSP、regulator、GPU 设备树 |

用户提到的 sweet-ios，在此次检索中对应的是 `dipa-studio/ios-sweet`，不是之前尝试的 `xuhongsr/sweet-ios`。

### 上游状态如何解读

- UniXNU 当前设备列表公开列出 sheng，即小米平板 6S Pro / SM8550；这不是 MIX 2 的支持声明。
- sheng 页面报告原版 iPadOS 用户态到达 SpringBoard、锁屏、主屏幕和设置；桌面由 CPU 渲染，GPU 未启用。
- ios-sweet README 报告 iOS 16.7.16 / iPhone X 用户态已到桌面，使用 XNU 8796、16 KiB 页、HFS+ 根文件系统和 CPU 渲染。
- ios-sweet 的 `NOTES.md` 在 2026-10-18 的记录比 README 更新：记载返回主屏幕手势可用、USB CDC-ECM 链路可 ping，以及 8 核补丁。该记录也明确说互联网访问尚未打通，部分新内核尚未实机启动。
- 上述为项目作者报告，本次没有独立复现。不要把 README 中的 6 核、导航不可用，或“USB 可 ping”直接当作完整当前功能矩阵。

## 第一关：CPU 能力和页大小

**必须先读实机寄存器，不能只依据“ARM64”选择现成内核。**

先在低风险探针中记录：

- `CurrentEL`、`MIDR_EL1`、`MPIDR_EL1`。
- `ID_AA64MMFR0_EL1`，特别是 `TGran4`、`TGran16`、物理地址宽度。
- `ID_AA64MMFR1_EL1` 的 PAN 能力。
- `ID_AA64ISAR0_EL1` 的原子指令能力。
- `CNTFRQ_EL0`、GIC 系统寄存器接口，以及固件允许的 PSCI 调用。

ios-sweet 的 MMU 和内核使用 16 KiB 页；其 shim 已有输出 `tg16` 的探针。不能用 sweet 手机的探测结果替代 MIX 2。若 MIX 2 不支持 16 KiB，sweet 现有内核和 MMU 路径就不能直接启动，应使用 UniXNU 的 4 KiB 路线，或另行完整移植 sweet 的 4 KiB 内核与用户态内存映射支持。

MSM8998 UEFI 源码明确写有“不支持 LSE”的注释。为 chiron 构建的 shim、内核和辅助原生代码，应按实测能力选择 ARMv8-A 目标，避免无条件 LSE、PAN 或其他较新 ISA 指令。用户态及共享缓存的指令需求也要验证，不能假定仅改内核编译参数就解决。

`Msm8998FamilyPkg.dsc.inc` 同时存在 `-march=armv8-a+lse -moutline-atomics` 和构建问题注释；不要把它当成经过验证的通用编译模板。是否生成可在本机运行的回退代码，需要检查产物。

## 已确认的板级差异

以下地址是公开源码中的值，不是对用户手中手机的实测。板级修订、内存容量、固件与运行时设备树可能改变有效配置。

| 项目 | sweet | chiron 源码 | 移植影响 |
| --- | --- | --- | --- |
| SoC | SM7150 | MSM8998 | 需要独立设备配置 |
| GIC | GICv3 | GICv3 | 控制器逻辑可参考，资源和权限必须重验 |
| GICD | `0x17a00000` | `0x17a00000` | 地址相同不代表全部配置相同 |
| GICR | `0x17a60000` | `0x17b00000` | shim 和 ADT 都要修改 |
| CPU affinity | `0,0x100,...,0x700` | `0,1,2,3,0x100,0x101,0x102,0x103` | 不能沿用 sweet 的 `core << 8` |
| 定时器 | 19.2 MHz，虚拟定时器 INTID 19 | DT 同样给出 19.2 MHz、虚拟 PPI 3 | 验证真实 IRQ 到达后再启调度 |
| UFS host | `0x01d84000` | `0x01da4000`，长度 `0x2500` | 改资源之外，还要核实链路、DMA、时钟、LUN |
| 实体屏幕 | 1080 × 2400 | chiron DSC：1080 × 2160 | 重做 stride、拷贝、缩放及触控坐标 |
| 触控总线 | GENI SE7 | BLSP1 QUP5，`0x0c179000` | 重写总线底层 |
| 触控 IC | Goodix GT9896 | Synaptics 或 FocalTech 配置 | 不能沿用 Goodix 报文解析 |
| 电源 | RPMh，PM6150 系列 | RPM 通道，PM8998 相关资源 | 不能沿用 RPMh 寄存器操作 |
| GPU | 无硬件渲染驱动 | A540 固件、`chipid=0x05040000` | 首轮使用 CPU 渲染 |

### 内存和 framebuffer 是两个需要特别核对的地方

1. sweet 的高地址 RAM 窗口为 `0x100000000 + 0xfcc00000`。MSM8998 UEFI 列有不同的 4/6/8 GB 内存布局，不能把 sweet 的窗口直接赋给 XNU。需要从实机 FDT、reserved-memory、UEFI 内存图和保护区域中选择有效窗口，并排除 payload、页表、日志与 framebuffer。
2. MSM8998 UEFI 配置把 framebuffer 放在 `0x9d400000`；此次读取的 Linux 基础 DTS 则有 splash 保留区 `0x9d600000 + 0x02400000`。保留区本身也不一定等于扫描输出的起始地址。必须读取最终合并 DT 和实际 GOP/显示配置，不应挑其中一个值直接写屏幕。

### 触控并不是一种固定芯片

chiron 板级 DTS 在 `i2c_5` 下同时描述：

- `synaptics,dsx-i2c-force`，普通 I²C 地址 `0x20`；配置项含 S3330、S3331、TD4322。
- `ft,ft5x46_i2c`，I²C 地址 `0x38`。
- 两类都涉及 reset GPIO 89、IRQ GPIO 125，以及 PM8998 和显示相关供电。

应先从 Android 的驱动绑定、输入设备和日志确认本机是哪类，再实现对应协议。不要只因 DTS 中同时存在两个节点，就认为两种控制器都在本机上。

## 两条实现路线

### A：UniXNU 作为工程主线

目标链路：

```text
MIX 2 原厂 bootloader
  -> chiron 专用 MSM8998 UEFI
  -> UniXNU + DarwinBoot
  -> chiron ADT + 4 KiB XNU
  -> RAM 中最小 init
  -> 原生 UFS / 根文件系统
  -> 匹配的 iOS/iPadOS 用户态
  -> 软件显示 + HID
```

主要工作：

1. 评估现有 MSM8998 EDK2 平台是否能构建、装载 UniXNU 和 DarwinBoot，检查 GOP、文件系统、Block I/O、按键与 `ExitBootServices`。
2. UniXNU 构建驱动默认要求 Mu-Silicium 的配置布局。现有 `edk2-msm8998` 是另一套目录和打包流程：要么做构建适配层，要么将 chiron 平台迁入 Mu-Silicium，不能只把 `--uefi` 指向旧仓库就认为兼容。
3. 新建 `UniXNU-chiron`：`board.toml`、`configs/chiron_defconfig`、ADT 生成器、设备驱动和必要脚本。寄存器、内存、PSCI 与分区几何来自 chiron。
4. 初始内核只启用平台专家、单核 CPU、GIC、timer、framebuffer、RAM root。GPU、原生触控、SMP、存储写入逐步加入。
5. 后续再评估通用 NativeUFS 对 MSM8998 的兼容性，补齐 PM8998、RPM、时钟、复位、SMMU 和显示所需的板级支持。
6. 使用匹配该 XNU 版本的 IPSW、内核缓存、预链接组件和身份数据。sheng 的 UUID、LBA、硬编码地址、PMGR 和 trust cache pin 都不能照搬。

UEFI 的 UFSDxe 负责启动阶段；DarwinBoot 退出 boot services 后，XNU 仍要有自己的存储驱动。UEFI 菜单按键同样不能代替 iOS 内核 HID。

### B：移植 ios-sweet 的直接启动路径

前提：实机支持其所需的 16 KiB 页，并完成指令集核对。

可复用的方向：

- Mach-O payload、ADT 编码、最小用户进程与 QEMU 验证工具。
- XNU 平台专家、CPU/GIC/PSCI 的实现思路。
- HFS+ 根文件系统、iOS 16 用户态准备流程。
- IOSurface / IOMobileFramebuffer 服务和软件渲染接口。
- HID 事件服务以及从内核日志定位 SpringBoard 问题的方法。

必须替换或重新验证：

- shim 的 GICR、CPU affinity、framebuffer、重启寄存器、RAM 窗口、UFS 地址。
- Android boot image 格式与加载参数；sweet 脚本固定 v2，而现有 chiron UEFI 构建使用 v1。最终按本机原厂镜像和 bootloader 验证，不能把某个版本当作全机型要求。
- GENI I²C、GT9896、RPMh、PM6150、SPMI 仲裁器配置。
- HFS+ 根分区定位、日志尾部位置、逻辑块大小。
- 显示逻辑尺寸和触控映射；可保留 iPhone X 逻辑画布，但必须针对 1080 × 2160 正确缩放或裁剪，并同步坐标变换。

不要为得到 v1 镜像就只修改 `--header_version`；旧格式的 DTB 放置、偏移、头部与压缩要求也需要一起匹配。

### 为什么不能直接拼两套补丁

UniXNU 此次 `feeds.toml` 固定 XNU `10063.141.1` / Darwin 23.6.0，当前路线采用 4 KiB 页和预链接 APFS 等组件；sweet 是 XNU 8796 / iOS 16 代际、16 KiB 页和内建 HFS+。驱动二进制 ABI、内核 API、boot args、页大小策略、IOKit 用户客户端接口及预链接组件都有匹配要求。

应先选定一套完整的内核和用户态基线，再逐项移植源码。不能把 sweet 的内核直接当作 UniXNU 的现成 payload，也不能把 sheng 的闭源组件或地址用于 chiron 而跳过验证。

## 实际执行顺序与通过标准

| 阶段 | 做什么 | 通过标准 |
| --- | --- | --- |
| 0 | 收集原厂 boot、运行时 DT、分区表、触控绑定及内存规格 | 能解释所有候选地址和保留区，保留设备恢复资料 |
| 1 | 仅启动诊断镜像，读取 CPU、内存、GIC、timer 信息 | 页大小和指令能力明确；异常有可读记录 |
| 2 | 选择 UEFI + UniXNU，或适配后的直接 shim | 屏幕输出可靠；镜像、DTB、payload 位置一致 |
| 3 | XNU 单核、GIC、timer、RAM root、最小 init | 实机看到 init 心跳，而不只是“内核没 panic” |
| 4 | UFS 只读识别与 GPT 校验 | inquiry、容量、块大小、LUN、GPT 内容与备份一致 |
| 5 | 独立实验根镜像、launchd | 根文件系统与匹配用户态运行，能取回日志 |
| 6 | 软件 framebuffer、IOSurface、SpringBoard | 锁屏/桌面可见，显示刷新稳定 |
| 7 | QUP、触控、电源键、音量键 | 触控坐标及边缘手势正确，按键可锁屏唤醒 |
| 8 | SMP、USB、功耗及其他外设 | 每项功能均有实机日志与可重复验证 |

QEMU 适合验证内核、payload 和最小 init，不能验证 Qualcomm XPU、真实触控供电、DSI 刷新或固件残留状态。

不把 GPU 放入第一阶段：UniXNU 的 NativeAdreno 源码包含 A7xx/GMU 路径，chiron DTS 是 A540；目前 sheng 文档也明确桌面由 CPU 渲染、GPU 未启用。不能因“都是 Adreno”就认为已有可直接用于 iOS 的 A540 图形栈。

## 本轮下一步需要的实机材料

- 已获得：6 GB + 128 GB、适配 boot.img、实际系统版本、运行时 FDT、sysfs device-tree、内核配置和日志、memblock、触控绑定和面板信息。
- 已获得：各 UFS 磁盘块大小、分区 sysfs、sda 主 GPT 及 userdata 几何。当前没有 `dtbo`、`vbmeta` 的 by-name 路径。
- 待验证：原始 CPU 特性、UEFI 接管后的最终内存图和 GOP、bootloader 是否接受所构建的临时镜像。启动状态属性与 cmdline 有矛盾，不能只依据属性作判断。

已有测量资料可用于编写真实的 chiron 平台输入；未来 boot.img 仍须独立验证，不能把 Android 内存图或 sheng 参数直接当作已经合格的 UEFI/XNU 配置。

## 存储与恢复边界

`fastboot boot` 只是临时装载入口，本身不能保证被装载的软件不写存储。MSM8998 UEFI README 明确警告 UFS 数据可能被清空；本次没有审计其全部二进制组件，不能把现成镜像称为无写入探针。

最初应使用不初始化 UFS 的诊断镜像与 RAM 中的 init。对 UFS 的协议访问先只读验证；根文件系统写入和持久化日志放到后续阶段。不要提前使用 sweet 的 userdata 刷写命令或日志分区尾偏移。

## 参考材料

本机 `upstream/` 保存了本轮直接读取的少量原文件，保留原有版权与许可文字，供核对引用；该参考快照目录不上传 Git。下面列出其对应原路径，在线读取时请结合前文的上游仓库与版本定位。完整构建输入由 `ci/sources.json` 和上游固定引用获取，不依赖这些本地快照。

| 本地快照 | 上游原路径 |
| --- | --- |
| `upstream/ios-sweet/README.md`、`NOTES.md` | 同名路径 |
| `upstream/ios-sweet/mkadt.py` | `tools/mkadt.py` |
| `upstream/ios-sweet/shim.c`、`build.sh` | `shim/shim.c`、`shim/build.sh` |
| `upstream/UniXNU/uefi-firmware.md` | `docs/porting/uefi-firmware.md` |
| `upstream/UniXNU/device-repository.md` | `docs/porting/device-repository.md` |
| `upstream/UniXNU/four-k-page-tables.md` | `docs/xnu/four-k-page-tables.md` |
| `upstream/UniXNU/overview.md` | `docs/drivers/overview.md` |
| `upstream/UniXNU/sheng.md`、`feeds.toml` | `docs/devices/sheng.md`、`feeds.toml` |
| `upstream/edk2-msm8998/README.md` | `README.md` |
| `upstream/edk2-msm8998/chiron.dsc` | `Platforms/Msm8998Pkg/Device/xiaomi/chiron/chiron.dsc` |
| `upstream/edk2-msm8998/QcomPkg.dsc.inc` | `Silicon/QC/Msm8998/QcomPkg/QcomPkg.dsc.inc` |
| `upstream/edk2-msm8998/Msm8998FamilyPkg.dsc.inc` | `Platforms/Msm8998FamilyPkg/Msm8998FamilyPkg.dsc.inc` |
| `upstream/edk2-msm8998/PlatformMemoryMapLib.c` | `Silicon/QC/Msm8998/QcomPkg/Library/PlatformMemoryMapLib/PlatformMemoryMapLib.c` |
| `upstream/linux-msm8998/msm8998*.dtsi` | `arch/arm/boot/dts/qcom/` 下同名文件 |
| `upstream/linux-msm8998/chiron-msm8998-mtp.dtsi` | `arch/arm/boot/dts/qcom/xiaomi/chiron-msm8998-mtp.dtsi` |
| `upstream/linux-msm8998/chiron-msm8998-v2.1-mtp.dts` | `arch/arm/boot/dts/qcom/chiron-msm8998-v2.1-mtp.dts` |

上游入口：

```text
https://github.com/UniXNU/UniXNU
https://unixnu.com/porting/
https://github.com/dipa-studio/ios-sweet
https://github.com/edk2-porting/edk2-msm8998/tree/new
https://github.com/LineageOS/android_kernel_xiaomi_msm8998/tree/lineage-22.2
```
