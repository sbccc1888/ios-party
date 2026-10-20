# 用户提供的 MIX 2 boot.img 分析

日期：2026-10-20。设备规格由用户确认：MIX 2 / chiron，6 GB 内存、128 GB 存储。

本文记录最初的静态镜像分析，依据镜像字节、提取的 DTB 和旁边构建资料；该阶段未连接手机或执行镜像。后续实机结果单独记录。

后续已按用户授权完成 ADB 实机检查，见 [运行时报告](CHIRON_LIVE_ANALYSIS.zh-CN.md)。以下保留静态镜像分析的原始结论；实际 SoC、RAM、触控和 splash 的运行时补充以该报告为准。

## 输入与工具

用户提供的文件：

```text
KernelSU-Next-33129-chiron-crDroidAndroid-15.0-20260805-boot.img
```

- 文件长度：16,449,536 字节。
- SHA-256：`4b86144c109b8a5bd219f52bd87f5f69652fe0559c82b9368ddbcf01ead9cf94`。
- 附近的 `artifacts/Image.gz-dtb` 与该镜像的 kernel payload SHA-256 一致：`044978c54a33b36f60a21701bbac72d0be7f5b0c62492a6a60e1a3f933ed5904`。
- 工具：`tools/inspect_chiron_boot.py`，仅用 Python 标准库离线解析 boot v0、gzip、ARM64 Image 头、FDT 结构，提取 DTB。它不运行输入代码。
- 当前没有可用的 REA MCP/CLI 固件分析工具；结果来自上述离线解析，不是 REA Evidence，也没有反编译驱动二进制。

## 已确认的镜像格式

| 项目 | 解析值 |
| --- | --- |
| Android boot header | **v0** |
| boot 打包页大小 | 4096 字节 |
| 头部 OS 版本字段 | 15.0.0 |
| 头部安全补丁年月字段 | 2026-08 |
| kernel payload | 14,919,384 字节，gzip 内核后附 3 个 DTB |
| ramdisk | 1,523,682 字节 |
| second payload | 0 字节 |
| kernel payload 文件偏移 | `0x1000` |
| ramdisk 文件偏移 | 14,925,824 |
| 头部 kernel_addr | `0x8000` |
| 头部 ramdisk_addr | `0x01000000` |
| 头部 tags_addr | `0x100` |
| 解压内核字节数 | 41,887,752 |
| ARM64 Image text_offset | `0x80000` |
| ARM64 Image image_size | 47,394,816 |
| ARM64 Image flags | `0xa`，编码的内核页大小为 4096 |
| 适配镜像的 legacy payload SHA-1 | 与镜像头 ID 匹配 |

boot 页大小是文件打包对齐；内核页大小由 ARM64 Image flags 独立确认。两者都为 4096，不应把它们当成同一个字段。`text_offset`、boot 头中的地址和文件偏移也分别属于不同语义，不能相互替代，或直接当成可用物理内存窗口。

内核 banner 为 `Linux version 4.4.302-perf+`，内含 2026-10-01 的构建时间。头部 OS 版本是元数据，不足以证明该文件正在用户手机上运行。

头部 cmdline：

```text
androidboot.console=ttyMSM0 androidboot.hardware=qcom msm_rtb.filter=0x37 ehci-hcd.park=3 service_locator.enable=1 swiotlb=2048 loop.max_part=7
```

## 3 个 DTB

| 提取文件 | MSM 修订 | `qcom,msm-id` | `qcom,board-id` | 长度 |
| --- | --- | --- | --- | --- |
| `dtb-00.dtb` | v2.1 | `0x124, 0x20001` | `0x20, 0` | 404,667 |
| `dtb-01.dtb` | v1 | `0x124, 0` | `0x20, 0` | 397,658 |
| `dtb-02.dtb` | v2 | `0x124, 0x20000` | `0x20, 0` | 404,663 |

不能默认第一个就是本机实际选中的 DTB，需要由 bootloader 的选择结果或运行时 DT 确认。

与相邻目录的 crDroid 参考 `boot.img` 比较：

- 两个镜像包含相同的 3 个 DTB；按内容 SHA-256 配对后，逐字节一致。
- 参考镜像的顺序为 v1、v2、v2.1；KernelSU-Next 镜像为 v2.1、v1、v2。
- ramdisk 内容 SHA-256 一致，均为 `df18b36f5eba1f8db21588155a7f5b0d7f8d6835af26e03db65e459bf0c584bc`。
- 参考镜像不匹配本工具计算的 legacy SHA-1；本工具只记录该差异，不能据此推断它损坏或无法启动。用户提供的 KernelSU-Next 镜像通过该项校验。

## 从附带 DTB 确认的资源

三份 DTB 在下列项目上一致：

| 项目 | DTB 内容 |
| --- | --- |
| CPU reg / affinity | `0,1,2,3,0x100,0x101,0x102,0x103` |
| PSCI | `arm,psci-1.0`，`method=smc` |
| GIC | GICv3；GICD `0x17a00000 + 0x10000`，GICR `0x17b00000 + 0x100000` |
| timer | 19,200,000 Hz；虚拟 timer 条目为 PPI 3，即 INTID 19 |
| UFS | `0x01da4000 + 0x2500` |
| 触控 I²C | BLSP1 QUP5，`0x0c179000 + 0x600` |
| Synaptics 候选 | `synaptics,dsx-i2c-force`，地址 `0x20` |
| FocalTech 候选 | `ft,ft5x46_i2c`，地址 `0x38`，固件名含 `ft8716` |
| 触控 GPIO | reset 89，IRQ 125 |
| splash 保留区 | `0x9d600000 + 0x02400000` |
| ramoops 保留区 | `0xb0000000 + 0x00200000`，`no-map` |
| `/memory/reg` | 全部为零值占位 |

v2.1 DTB 中有 3 个匹配 1080 × 2160 的面板配置：JDI NT35596S、JDI NT35596S Pro、EBBG FT8716；均声明 `dsi_video_mode`、60 Hz。它们是镜像中的候选配置，不能由此确定本机面板与触控供应商。

### 对内存规划的直接影响

sweet 的低地址模式从 `0xb0000000` 开始。这份 chiron DTB 却将同一地址起的 2 MiB 标为 ramoops 的 `no-map` 保留区。**必须重新选择 XNU/payload 地址，不能照抄 sweet 的低地址配置。**

用户确认的 6 GB 只提供容量信息。静态 DTB 的 `/memory/reg` 是零值占位，因此仍缺启动后由固件填入的实际 RAM bank。不能把“6 GB”写成一个连续的 XNU 内存窗口，也不能直接照搬 public UEFI 或 sweet 的高地址 RAM 数值。

128 GB 也不能给出 userdata 的 LBA、分区长度、UFS LUN 和块大小，这些需要实际 GPT 与设备数据。

### 对屏幕和触控的影响

splash 的真实镜像值支持前次报告中 `0x9d600000` 保留区的观察，但它仍不等于显示控制器当前扫描输出地址。后续需要读取 GOP 或 MDSS 的实际 framebuffer 配置。

静态 DTB 同时包含两类触控节点，只能证明镜像具备两类配置。需要读取运行时驱动绑定，才能选定 Synaptics 或 FocalTech 的实现。

## 对旁边构建资料的交叉核对

读取 `artifacts/kernel.config`：

```text
CONFIG_ARM64_4K_PAGES=y
# CONFIG_ARM64_16K_PAGES is not set
# CONFIG_ARM64_64K_PAGES is not set
# CONFIG_ARM64_PAN is not set
# CONFIG_ARM64_LSE_ATOMICS is not set
CONFIG_KSU=y
CONFIG_KSU_KPROBES_HOOK=y
```

该配置 SHA-256 为 `38c52b9b162178d9253d9af2eb732b0f06793ea5083eb99a56ecc50cf3fba292`。其中 4 KiB 与镜像 flags 一致；PAN/LSE 是旁边构建资料的设置，本轮没有通过反汇编或运行探测确认完整产物与 CPU 能力。

`repack_legacy_boot.py` 使用 v0 头部，替换 kernel payload 并保留 ramdisk，与镜像结构及比较结果一致。原构建脚本按 `Image.gz + DTB 集合` 生成 kernel payload。

## 更新后的移植决策

1. 可以确定用 **chiron 的 boot v0 + appended DTB 结构**作为此适配基线的打包参考，不再以 sheng/sweet 的示例头部作默认值；最终仍需设备启动验证。
2. 第一版保持单核、4 KiB、ARMv8-A 基线，不依赖未确认的 PAN/LSE；继续以 UniXNU 4 KiB 路线为主线。
3. 可复用提取出的 DTB 作为平台资源输入与后续比较基准，保留三种 SoC 修订的区别。
4. CPU 的 16 KiB 支持仍未知；Linux 选择 4 KiB 并不能证明 CPU 不支持其他 granule。
5. 生成能上机的探针前，仍应取得本机运行时 RAM bank、实际 SoC 修订与 framebuffer 配置。触控绑定、GPT 与存储写入属于后续步骤。

## 本地输出

已提取的镜像资料位于：

```text
research/artifacts/chiron-ksu-next-verified/inspection.json
research/artifacts/chiron-ksu-next-verified/dtb-00.dtb
research/artifacts/chiron-ksu-next-verified/dtb-01.dtb
research/artifacts/chiron-ksu-next-verified/dtb-02.dtb
research/artifacts/chiron-crdroid-reference-verified/inspection.json
```

`research/artifacts/` 已加入 Git 忽略规则。原输入文件保持不变；分析脚本拒绝覆盖已有输出目录。
