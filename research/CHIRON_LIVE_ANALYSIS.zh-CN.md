# MIX 2 的 ADB 实机检查结果

后续更新：UEFI 探针已读取启动 CPU 的原始特性，确认支持 4 KiB、不支持 16 KiB；见 [探针照片记录](CHIRON_UEFI_PROBE.zh-CN.md)。本页保留此前 Android/ADB 阶段的采集边界与结论。

采集日期：2026-10-20，Asia/Shanghai。用户指定的无线 ADB 设备已确认是 chiron，su 返回 `uid=0(root)`。本轮只执行读取：没有刷写、重启、修改系统设置、加载内核模块或写硬件寄存器。

已生成结构化板级资料：[`chiron-live-profile.json`](chiron-live-profile.json)。该文件是测量结果，**不是可直接构建的 UniXNU board.toml，也不是上机验证通过的内存布局**。

## 实测结论

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 设备 | Mi MIX 2 / chiron，用户确认 6 GB + 128 GB | ADB 属性、用户说明 |
| SoC | MSM8998，SoC ID 292 / `0x124`，修订 2.1 | soc sysfs、运行时 FDT |
| 系统 | Android 15，安全补丁 2026-08-01 | 系统属性 |
| 内核 | Linux 4.4.302-perf+，2026-10-01 构建 | `uname`、`/proc/config.gz` |
| 当前内核页大小 | 4096 字节 | `getconf PAGE_SIZE`、运行时内核配置 |
| CPU | 8 核；0–3 的 CPU part 为 `0x801`，4–7 为 `0x800`，implementer 均为 `0x51` | `/proc/cpuinfo` |
| CPU affinity | `0,1,2,3,0x100,0x101,0x102,0x103` | 运行时 FDT |
| PSCI | SMC | 运行时 FDT |
| 触控 | **Synaptics**，I²C bus 5、地址 `0x20` | 实际 driver symlink、input 设备 |
| 触控驱动 | `synaptics_dsi_force`，input 名为 `synaptics_dsx` | sysfs、`/proc/bus/input/devices` |
| 触控 firmware build ID | 2676394 | 驱动只读 sysfs 属性 |
| 面板 | **JDI NT35596S video** 配置 | 启动 cmdline、panel-info |
| 屏幕 | 1080 × 2160，60 Hz 模式，32 bpp | fb0 sysfs |
| Android framebuffer stride | **4352 字节 / 1088 像素** | fb0 `stride` |
| framebuffer virtual_size | 1080 × 4320 | fb0 sysfs；是虚拟高度，不是实体屏幕高度 |
| UFS | 型号 `KLUDG4U1EA-B0C1`；sda 对应 SCSI `0:0:0:0` | block sysfs |
| UFS 逻辑/物理块大小 | 均为 4096 字节 | block sysfs |
| userdata | `/dev/block/sda17` | by-name symlink、GPT、sysfs |

本机 FocalTech `5-0038` 节点虽然存在，但没有实际 driver symlink。`uinput-goodix` 是 virtual input 节点，不能据它推断主触控是 Goodix。本机触控移植应优先实现 Synaptics 协议，而非 sweet 的 GT9896。

Synaptics 的具体 S3330/S3331/TD4322 子型号尚未从本轮只读接口确定，不因 family 或固件 build ID 猜测型号。

当前 `uname` 的版本、构建时间与用户提供镜像相符，但本轮没有读取整个 boot 分区做哈希比对，所以不声称两份 boot 全字节一致。

## 实际 RAM 布局

运行时 FDT 的 `/memory/reg` 已由固件填入两个 bank。以下均采用半开区间，即右端不包含：

| Bank | 起点 | 终点 | 长度 |
| --- | --- | --- | --- |
| 0 | `0x40000000` | `0x100000000` | `0xc0000000` |
| 1 | `0x100000000` | `0x1bd8c0000` | `0xbd8c0000` |

`MemTotal` 为 5,859,212 KiB。RAM bank 描述不能直接当作全部可交给 XNU 的内存，需要继续排除固件、外设、引导器和 payload 保留区。

本机 `/proc/iomem` 即使通过 su 读取也显示零地址；没有修改相关设置。通过只读 memblock debugfs 得到了真实区间：

| memblock memory 起点 | 终点（不含） |
| --- | --- |
| `0x40000000` | `0x85800000` |
| `0x88f00000` | `0x8ab00000` |
| `0x95700000` | `0xb0000000` |
| `0xb0200000` | `0x1bd8c0000` |

`memblock/reserved` 也已保存。它包含本次 Android 启动占用的内核、DT 和其他分配，不能原样用作未来 UEFI 的最终内存图。

### 初始候选窗口

为最小 init 阶段选出的候选是：

```text
base = 0x100000000
size = 0x40000000       # 1 GiB
end  = 0x140000000      # exclusive
```

脚本已验证该窗口在当前 RAM bank 与 memblock memory 内，而且不与捕获的 memblock reserved 相交。**这只通过当前 Android 内存图的静态检查。**

上机前仍要核对 UEFI 最终内存图、payload 放置与存活期、早期物理地址访问；目前 `qualified_for_boot_image=false`。没有据此生成或运行启动镜像。

`0xb0000000–0xb0200000` 仍是 ramoops 保留区，排除了直接照搬 sweet 低地址起点的做法。

## splash 与 stride 的修正

这是本轮解决的两个重要差异：

1. 镜像里节点名是 `splash_region@9d600000`，静态 `reg` 原为 `0x9d600000 + 0x2400000`。运行时**节点名未变，reg 已改成 `0x9d400000 + 0x2400000`**。因此此前 UEFI 与静态 DTS 的地址差异，不能靠节点名决定。
2. Android fb0 的 32 bpp stride 是 4352 字节，而不是 `1080 * 4 = 4320`。移植显示拷贝必须按实际 stride 逐行处理。

运行时面板选择器：

```text
1:dsi:0:qcom,mdss_dsi_jdi_fhd_nt35596s_video:config2:1:none:cfg:single_dsi
```

以上 stride 是 **Android 当前模式** 的值；未来通过 UEFI 启动时，仍应以 GOP 的 `PixelsPerScanLine` 为准。splash 保留区不等于已证明的当前扫描输出地址，不能据此直接写 framebuffer。

## userdata 的准确几何

只读取 sda 开头 24 KiB 元数据；没有读取 userdata 内容。GPT 主表头 CRC 和分区表 CRC 均通过，且 userdata 位置、长度与 sysfs 独立一致。

```toml
# Measured GPT geometry only; not authorization to format or flash.
first_lba = 0x1c000
last_lba = 0x1c257fa
lba_bytes = 4096
```

- userdata 长度：120,418,447,360 字节。
- sysfs 的 `start=917504` 和 `size=235192280` 使用 512 字节扇区计数。
- 换成 4096 字节 GPT LBA 后，起点是 114688，而不是 917504。
- sda 的 GPT 为 32 个 128 字节 entry；本轮只验证主 GPT，没有读取和校验备份 GPT。
- boot 位于 `/dev/block/sde40`。当前 by-name 中没有 `dtbo` 和 `vbmeta` 这两个路径，不应为了套新平台流程而假定它们存在。

## 仍未知的项目

### 16 KiB / 裸 CPU 特性

当前内核没有 `cpu*/regs/identification/` 接口。运行时配置还关闭了 `/proc/kcore`、`/dev/mem` 和 `/dev/kmem`；本轮没有使用内核注入、模块或寄存器写入来绕过这些限制。

旁边内核源码 `arch/arm64/kernel/cpuinfo.c` 在 `__cpuinfo_store_cpu()` 中明确对读取结果做：

```c
info->reg_id_aa64mmfr0 = read_cpuid(SYS_ID_AA64MMFR0_EL1) &
                        (~MMFR0_EL1_16KGRAN_MASK);
```

因此即使后续读到这个缓存，也不能把其 16 KiB 标志当作原始硬件值。现在的结论仍为 **16 KiB 硬件支持未确定**；PAN/LSE 的原始 CPU 寄存器也未读到。可继续采用不依赖它们的 4 KiB 内核基线，不把 Linux 的配置选择当成硬件能力证明。

### bootloader 接受临时启动镜像

系统属性报告 `flash.locked=1`、`verifiedbootstate=green`、`vbmeta.device_state=locked`，但 `/proc/cmdline` 的原始参数含 `androidboot.verifiedbootstate=orange`。两组报告存在矛盾。

不能仅靠这些属性断言已锁定或可直接临时启动。当前没有重启到 fastboot，也没有执行解锁、上锁或启动测试。

## 验证与下一步

已完成的交叉检查：

- 解析后的 FDT 与实时 sysfs device-tree tar 中的 RAM、splash、GIC 属性逐字节一致。
- GPT 主表头 CRC、entry CRC、磁盘容量与 sysfs 检查通过。
- userdata 的 GPT 字节偏移和长度与 sysfs 的 512 字节扇区计数一致。
- 触控的 driver symlink 与 input sysfs 路径一致；面板 cmdline 与 panel-info 一致。

下一步可据 `chiron-live-profile.json` 编写 chiron 的 ADT 生成器与平台配置，采用 4 KiB、单核、JDI 软件显示和 RAM init 的最小目标。先完成 MSM8998 EDK2 与 UniXNU 的构建接入，再核对引导期内存并测试；本轮不涉及 userdata 写入。

原始采集与逐文件哈希清单保存在 Git 忽略目录：

```text
research/artifacts/chiron-live-20261020/
research/artifacts/chiron-live-details-20261020/
```

其中可能含设备标识与调试日志。公开报告和 profile 只提取移植所需参数，不把原始日志纳入 Git。
