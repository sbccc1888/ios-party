# RAM init 阶段

本阶段目标已在 Cortex-A53 QEMU 中通过：单核、4 KiB XNU 启动自有静态 PID 1，完成真实的 PID/页大小检查、控制台写入和两次定时等待。手机上尚未测试本阶段内核。

## 已有观察

- 源密码学 529 个 C 文件、pthread 与 mockfs 已编译，运行内核已链接。
- QEMU 中真实 UEFI → UniXNU → DarwinBoot → 4 KiB XNU 路径已建立。
- 源密码学默认 POST/PRNG 初始化与单字节 HMAC 破坏拒绝测试已通过。
- Cortex-A53 模型中已观察到 ArmDTPlatform、CPU、GIC、pthread 初始化，以及定时器中断。
- 只读 RAM 卷驱动和旧平台早期保留逻辑已经编译成固件，运行编号 `39532407683`。该固件尚未实机测试。

运行编号 `39539539318` 已实际观察到：

- PID = 1，`HW_PAGESIZE` = 4096，sysctl 成功返回且实际输出长度为 4。
- 自有 init 心跳 2 次，定时器中断计数 28。
- 默认密码学 POST/PRNG 初始化通过，HMAC 破坏被拒绝。
- pthread 已注册，CPU/GIC 已工作。
- 初始和活动 TCR 均为 `0x22a51a251a`，4 KiB granule，40-bit boot PA。

这是模拟器中的自有最小用户进程，不等于 iOS 用户态或完整 Apple 安全模型已建立。

## 当前诊断配置

配置为 `configs/chiron_raminit_defconfig`。完整 trust-cache runtime 依赖原始 AppleImage4/AMFI，本模式不启用它，也不声明 Apple 信任链。

自有 init 仍进行构建时 ad-hoc 签名和严格校验。源码诊断只对实际为空的 entitlement 集合处理其空加速操作；带 entitlement 的程序拒绝，AMFI 缺失时 IOKit 查询返回无 entitlement。存在 AMFI 时保留原查询路径。真实密码学 POST 不受这些分支影响。

受限空集合处理和无权限返回路径已覆盖实际入口。最新通过结果记录于 `dist/ram-init/runtime-test.json`，原始 QEMU 观察记录于 `dist/ram-init/qemu-observation.json`。回归检查验证带 entitlement 时拒绝及输入哈希改变时拒绝打包。

## RAM 启动传输

已实现 bootloader initrd → 早期 reserved HOB → 只读 EFI Block I/O → FAT32 → UniXNU 配置的通道。

- `linux,initrd-start/end` 从原始 bootloader FDT 读取。
- payload、实际固件范围与 ramoops 在早期保留。
- RAM 块设备拒绝写入；这不代表其他 vendor DXE 驱动获得了无写入证明。
- 打包任务需要通过的 PID 1 运行观察和匹配的内核/init/ADT 哈希。
- 传输卷为 48 MiB，临时启动镜像不涉及 userdata 格式化或磁盘分区修改。

整合运行编号 `39540284319` 已通过：输入哈希、FAT 文件读回、只读 fsck 和 boot 容器检查均成功。输出为 `chiron-ram-init-experimental.img`，内含 48 MiB RAM 卷。该镜像尚无实机运行证明，不宣称已在 MIX 2 启动 XNU。

本地成品：`dist/ram-boot/chiron-ram-init-experimental.img`，51,863,552 字节。

```text
SHA-256
8be4c51f7a9105010e1500ec16a02b95bc8f9dba31251270ae03623f09b46961
```

本地独立检查确认：镜像哈希与报告一致、RAM 卷哈希与读回报告一致、boot v0 的 legacy SHA-1 正确、ARM64 Image 头覆盖整个 6 MiB 固件卷。最终仍需实机临时启动测试；检查时 USB fastboot 设备列表为空，未自动重启手机。
