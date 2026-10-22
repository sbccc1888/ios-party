# RAM init 阶段

本阶段目标已在 Cortex-A53 QEMU 中通过：单核、4 KiB XNU 启动自有静态 PID 1，完成真实的 PID/页大小检查、控制台写入和两次定时等待。2026-10-22，用户照片 `IMG_20261022_093457.jpg` 已显示 `XNU STAGE 17`，确认诊断内核中的原始 `arm_vm_init` 返回。尚无实机 PID 1 心跳、完整 iOS 用户态或明确故障原因的证据。后续停止零散二进制插桩，转向 [固定基线源码日志](research/CHIRON_SOURCE_TRACE.zh-CN.md)；既有诊断结果保留在本文。

## 已有观察

- 源密码学 529 个 C 文件、pthread 与 mockfs 已编译，运行内核已链接。
- QEMU 中真实 UEFI → UniXNU → DarwinBoot → 4 KiB XNU 路径已建立。
- 源密码学默认 POST/PRNG 初始化与单字节 HMAC 破坏拒绝测试已通过。
- Cortex-A53 模型中已观察到 ArmDTPlatform、CPU、GIC、pthread 初始化，以及定时器中断。
- 只读 RAM 卷驱动和旧平台早期保留逻辑已经编译成固件，运行编号 `39532407683`。整合镜像首次实机尝试停留在 MI 标志，卡点未定。

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

## 首次 RAM 镜像实机尝试

2026-10-21，用户报告执行上述镜像的 `fastboot boot` 后一直显示 MI 标志。现场只读查询中 fastboot 和 ADB 均未枚举到设备，未取得串口、UEFI 或 XNU 日志。成品 SHA-256 仍与上述记录一致。此现象不能证明 XNU 已执行，也不足以区分 bootloader、早期 UEFI、RAM 卷驱动或显示交接的问题。

`tools/make_chiron_firmware_control.py` 可从该成品生成同版固件的无 RAM 卷对照镜像，检查输入/输出 boot SHA-1，并强制保证 kernel payload（含固件与附加 DTB）逐字节不变。它只改变 ramdisk 及对应容器元数据；去掉 initrd 也会改变 bootloader 放置与早期保留路径，因此对照启动成功不能直接定位为 RAM 块驱动问题。

```sh
python3 tools/make_chiron_firmware_control.py \
  --source dist/ram-boot/chiron-ram-init-experimental.img \
  --output dist/boot-controls
```

输出 `dist/boot-controls/chiron-same-firmware-no-ram.img` 是诊断对照，不含 XNU/init 的 RAM 传输卷。

### 无 RAM 卷对照的照片反馈

2026-10-21，用户在上述对照测试指引后提供 `IMG_20261021_130716.jpg`。照片明确显示 `Chiron read-only probe` 和 `Press a key to return`，确认 UEFI 应用已执行。按连续测试上下文将其解释为无 RAM 卷对照的反馈；照片本身不显示启动文件名或哈希。

- `CurrentEL=4`，`MIDR=0x51af8014`，`MMFR0=0x1122`，`MMFR1=0`，`ISAR0=0x11120`，`CNTFRQ=19200000`。
- `TGran4(raw)=0`、`TGran16(raw)=0`，与首次探针一致。
- `RAM payload not reserved`：源码含义为未发现大小匹配的 payload GUID HOB；无 initrd 的对照中这是预期结果，不能解释成完整 RAM 镜像已经尝试保留且失败。
- GOP 为 1080×2160，pitch=1080，format=1，base=`0x9d400000`，bytes=9331200。
- conventional memory 共 15 个区域，照片仅显示前 12 个；不是完整内存图。

该观察未证明 XNU 执行。完整 RAM 镜像停留 MI，而同版无 RAM 卷对照进入探针，将调查重点缩小到 initrd 放置以及固件处理 RAM 卷后才执行的路径，尚不能确定具体失败阶段。

### 旧固件加相同 RAM 卷的对照

第二个对照复用 2026-10-20 已成功执行探针的旧固件，保留完整 RAM 镜像的 48 MiB ramdisk 字节。工具要求两个输入均通过 boot 校验、DTB 相同且相关加载参数一致，输出后独立检查固件、ramdisk 和 boot SHA-1。

```sh
python3 tools/make_chiron_firmware_control.py \
  --source dist/ram-boot/chiron-ram-init-experimental.img \
  --firmware-source dist/firmware/chiron-uefi-experimental.img \
  --output dist/boot-controls-legacy
```

输出 `dist/boot-controls-legacy/chiron-legacy-firmware-same-ram.img` 的 SHA-256 为 `d2193780d4c1f0b1c360dfacdd1a9d927b9ef563595037b6c4855a87be8566c7`。用户反馈该对照也不能进入 UEFI。旧固件没有新增 RAM 卷驱动，因此无法仅用该驱动解释两版固件的失败；仍需检查非空/大 initrd 加载、内存放置及早期公共路径。该反馈未独立确认具体卡点，也没有证明全部早期 UEFI 代码均未执行。

### 未压缩固件对照

本地原 Android 运行时树的 `/chosen/linux,initrd-start/end` 为 `0x43400000`、`0x43573fe2`，与 boot 头编码的 `ramdisk_addr=0x01000000` 不同。该实测只适用于当时 Android 启动，不能当作本次 fastboot 加载地址的证明，也不能据此硬编码新的空闲内存区。

参考公开 LK 源码 `msm8916-mainline/lk2nd` 的 `app/aboot/aboot.c`，`cmd_boot` 在 gzip 分支把解压缓冲区放在 `target_get_scratch_address() + image_actual + page_size`，随后先搬移 ramdisk、再搬移 kernel。该顺序提供“大 initrd 与 gzip 解压缓冲区交互”的可检验假设；它不是手机所用 bootloader 的已验证源码或实测覆盖证据。

为单独测试该路径，生成旧版固件的未压缩 ARM64 Image + 原 DTB + 原 48 MiB RAM 卷：

```sh
python3 tools/make_chiron_firmware_control.py \
  --source dist/ram-boot/chiron-ram-init-experimental.img \
  --firmware-source dist/firmware/chiron-uefi-experimental.img \
  --uncompressed-firmware \
  --output dist/boot-controls-legacy-raw
```

输出 `dist/boot-controls-legacy-raw/chiron-legacy-raw-firmware-same-ram.img`，57,053,184 字节，SHA-256 为 `a455f8550bbb7807e788b770f2680d75f34f3bba3b0ca30158b640d336a329d6`。输入固件内容、DTB、ramdisk 的哈希与对应源输入一致，boot SHA-1 通过；本地 11 项回归检查通过，包括原始 Image 边界拒绝和两种编码的负载保真。

用户随后反馈该未压缩对照也没有出现 UEFI。改变固件编码也会改变 kernel_size、容器后续偏移和加载器路径；当前结果不支持把绕过 gzip 当作已验证修复，也不能仅凭这一结果排除 gzip 版本另有解压问题。旧固件仍不会挂载 RAM 卷或启动其中的 XNU。

### 4 KiB 非空 ramdisk 对照

截至本轮反馈，已知结果如下；失败项均为用户反馈没有 UEFI 界面，缺少对应的完整 fastboot 输出和早期启动日志。

| 对照 | RAM 大小 | 可见结果 |
| --- | --- | --- |
| 旧版探针固件 | 0 | 曾成功进入探针 |
| 新版固件，无 RAM 卷 | 0 | 提供探针照片 |
| 新版固件，完整 RAM 卷 | 48 MiB | 停留 MI |
| 旧版固件，完整 RAM 卷 | 48 MiB | 无 UEFI |
| 旧版固件，未压缩 Image，完整 RAM 卷 | 48 MiB | 无 UEFI |
| 旧版固件，原 RAM 卷前缀 | 4 KiB | 用户反馈能正常启动；fastboot 发送/启动均 OKAY |

下一对照保持旧版 gzip 固件与 DTB 不变，只携带原 RAM 卷的前 4096 字节：

```sh
python3 tools/make_chiron_firmware_control.py \
  --source dist/ram-boot/chiron-ram-init-experimental.img \
  --firmware-source dist/firmware/chiron-uefi-experimental.img \
  --ramdisk-prefix-bytes 4096 \
  --output dist/boot-controls-tiny
```

输出 `dist/boot-controls-tiny/chiron-legacy-ram-4k.img`，1,531,904 字节，SHA-256 为 `0e2efd188275eccbcacdfb7f445b9ea9d480f10769382aedb676ce68400bedae`。固件 payload 哈希与旧版输入一致，boot SHA-1 通过。该前缀不是可挂载 FAT 卷，也不包含完整 XNU 负载；工具仅允许将前缀对照配合不含 RAM 卷驱动的旧固件使用。检查包括前缀字节保真、不对齐长度拒绝，以及缺少旧版固件时拒绝生成；本地 11 项回归测试通过。

用户反馈该 4 KiB 对照能正常启动。已读取 `/tmp/chiron-4k-fastboot.log`，实际终端输出为：

```text
Sending 'boot.img' (1496 KB)                       OKAY [  0.033s]
Booting                                            OKAY [  0.001s]
Finished. Total time: 0.036s
```

该结果说明非空 ramdisk 并不必然导致失败。完整卷与小前缀还同时存在后续内容、长度及相应内存布局差异，尚不能认定某个 bootloader 容量上限。测试命令同时采集终端输出：

```sh
fastboot boot /Users/mac/Documents/ChatGPT/ios_party/dist/boot-controls-tiny/chiron-legacy-ram-4k.img 2>&1 | tee /tmp/chiron-4k-fastboot.log
```

### 24 MiB 前缀对照

4 KiB 成功与 48 MiB 失败之间，先以 24 MiB 前缀做中点对照，保持同一旧版 gzip 固件、DTB、命令行和加载地址字段。仅 ramdisk 前缀长度、boot 头长度字段及关联校验值改变；没有修改固件逻辑或选定新的物理地址。

```sh
python3 tools/make_chiron_firmware_control.py \
  --source dist/ram-boot/chiron-ram-init-experimental.img \
  --firmware-source dist/firmware/chiron-uefi-experimental.img \
  --ramdisk-prefix-bytes 0x1800000 \
  --output dist/boot-controls-24m
```

输出 `dist/boot-controls-24m/chiron-legacy-ram-24576k.img`，SHA-256 为 `a302759743b182e358800dd1a2669fc8702014f36cef3c62cc36665b6d1cbce4`。已独立检查固件/DTB 字节保持和 ramdisk 前缀哈希，并通过 boot SHA-1 校验。用户反馈也能正常启动，日志确认发送和 Booting 均为 OKAY，总用时 0.613 秒。它仍是不能挂载或启动 XNU 的截断卷，不能将此结果视为 RAM 卷驱动通过。

```sh
fastboot boot /Users/mac/Documents/ChatGPT/ios_party/dist/boot-controls-24m/chiron-legacy-ram-24576k.img 2>&1 | tee /tmp/chiron-24m-fastboot.log
```

## 入口覆盖缺陷的复现与补丁

后续在两版成品的 ARM64 入口确认：固件复制前会先向固定 `0x83300000` 写入 FDT 指针。Unicorn 执行实际指令后复现了一个条件性缺陷：若该地址落入源 FD 内，复制结果有 8 字节损坏；将保存指针延后至复制结束即可消除该模拟破坏。手机本次失败的实际加载地址仍未知，因此尚不能确认它就是本次故障根因。

源码集成已调整保存顺序，并生成保留完整 RAM 卷、相同容器总长度及 ramdisk 偏移的补丁对照。新版完整负载为 `dist/ram-boot-fdt-fix/chiron-fdt-after-copy.img`，实机反馈见下文。14 项回归与两版共 8 项指令模拟通过。成品哈希、修改位置、模拟报告和验证边界详见 [入口覆盖排查](research/CHIRON_RAM_BOOT_DEBUG.zh-CN.md)。

用户随后实测补丁版仍停在 MI（照片 `IMG_20261021_145551.jpg`）；已取得发送/Booting 均为 OKAY 的日志。该改动没有解决手机启动，不能将模拟缺陷等同为实机已确认根因。

后续已生成在 UEFI 之前显示 `IMAGE/FDT/INIT/END/SIZE/STATE/OVER` 的入口探针：`dist/entry-live-24m/chiron-entry-probe-24m.img` 与 48 MiB 对照。它们会主动停在诊断读数，不尝试 UEFI 或 XNU。代码、容器检查及 10 项指令模拟通过，尚待实机验证。先用 24 MiB 验证读数可见，再比较完整 48 MiB；详细限制与命令见上述排查记录。

24 MiB 入口探针现已取得实机照片 `IMG_20261021_152528.jpg`：`IMAGE=0x40080000`、`FDT=0x43200000`、`INIT=0x43400000`、`END=0x44c00000`、`SIZE=0x1800000`、`STATE=0`、`OVER=0`。对应 fastboot 发送/启动均为 OKAY。此次诊断样本未命中探针列出的固定 store/FDT 地址覆盖；不能以此证明所有内存无冲突、RAM 卷已挂载或 XNU 已运行。48 MiB 入口探针后续反馈见下文。

## 零尾部传输实验

48 MiB 同版入口探针随后实测仍只有 MI，日志确认发送和 Booting 均 OKAY。比较两份成品确认：新增的后 24 MiB 全是零，诊断程序与前缀字节相同。因此已实现“仅传前 24 MiB，入口后补零并更新 FDT”的实验路径，保持原完整 48 MiB FAT32 卷和 XNU/init 文件字节不变，再继续原 UEFI 启动。

成品：`dist/ram-boot-zero-tail/chiron-ram-init-zero-tail.img`，26,697,728 字节，SHA-256 为 `14c51d4478e7e5e7437f5d444f919b3ec23a337d10d6259dd185e08c9d28aaa9`。地址严格限制为实测 Image/FDT/initrd 范围，要求当前 MMU/数据缓存关闭；条件不符则显示 FAIL 并停止，不进行补零。

18 项回归、10 项补零指令模拟以及 4 项默认诊断模式回归通过。模拟验证整卷哈希与原卷一致、FDT end 更新正确、启动参数和 SP 恢复，并到达原 PrePi 入口；模拟没有执行后续 UEFI/XNU。

用户后续提供 `IMG_20261021_161349.jpg` 并反馈正常：实机进入 UniXNU，显示 `Chiron native 4K XNU / RAM init`，DarwinBoot 打印 `inputs validated; exiting boot services` 和 `hand-off to the kernel`。内核 PA=`0x40004000`，entry=`0x403f9488`，args=`0x41540000`，video=`0x9d400000` / 1080×2160 / row 4320。该观察支持已打通 RAM 卷识别与内核读取，尚不能认定 XNU 已执行。

源码核对确认上述提示在 `ExitBootServices` 之前；退出成功后交接汇编绘制的绿色条才是下一阶段标记，而且它仍不等于 XNU 本身执行。照片没有完整显示屏幕最上沿，需后续观察确认。bootloader 原始大负载失败机制仍未查明。详细字段、约束和测试命令见 [零尾部传输记录](research/CHIRON_ZERO_TAIL_BOOT.zh-CN.md)。

## DarwinBoot 分阶段交接

用户随后确认有绿色条；依固定源码，将进度记为 `ExitBootServices` 成功且已进入 `DarwinEnter`，仍不宣称已跳入 XNU。原版只画一次绿色，因此不能据此判定缓存清理、MMU 切换或内核启动具体哪一步停止。

已生成仅改变 DarwinBoot 交接函数的诊断镜像 `dist/ram-boot-handoff-stages/chiron-ram-init-handoff-stages.img`。绿/蓝/红三条带分别标记交接开始、缓存清理后、MMU/TLB 转换后。原 XNU/init、RAM 卷和零尾部入口保持不变，且缓存范围和 MMU 操作保留原语义。

23 项回归与 4 项缩小窗口的交接指令模拟通过；模拟不包含手机真实页表/缓存，也未执行 XNU。本段为交接镜像生成时的记录，后续实机观察见下文。完整校验、哈希和判读规则见 [DarwinBoot 交接诊断](research/CHIRON_DARWIN_HANDOFF.zh-CN.md)。

## 原始 XNU 启动与内存初始化入口

用户后续提供 `IMG_20261021_210529.jpg`，显示绿/蓝/红三带和白色 `KERNEL ENTRY` 信息。它支持已进入内核文件内的诊断入口，不足以证明原始 XNU 初始化成功。

随后生成 12 个检查点及低地址异常记录的内核诊断镜像。用户提供 `IMG_20261021_213506.jpg`，明确显示 `XNU STAGE 000000000000000C`，TCR 为 `0x22a51a251a`，SCTLR 照片读数为 `0x34d4593d`。按固定检查点，已到达原始 `arm_vm_init` 函数入口，因此已确认原始 XNU 启动代码、MMU 开启和 `arm_init` 执行。照片不显示启动文件名或哈希，不能独立确认具体镜像字节。

当前诊断只在启动页表中临时映射 GOP；原始 `arm_vm_init` 后续会切换正式页表并撤销低地址映射。最后保留的 `0C` 既不能证明函数内发生故障，也不能证明已经返回。

为继续区分路径，新增内存参数处理、`early_random` 返回、`arm_vm_prot_init` 前后及正式 TTBR1 切换前的 `0D` 至 `11` 检查点。成品为 `dist/ram-boot-xnu-vm/chiron-kernel-vm-stages.img`。2026-10-22，用户提供 `IMG_20261022_092113.jpg`，明确显示 `XNU STAGE 0000000000000011`，确认已到达正式 TTBR1 切换前；照片不能证明后续切换成功或失败，也不独立校验具体镜像哈希。变更范围、验证和阶段表见 [XNU 分阶段记录](research/CHIRON_XNU_STAGES.zh-CN.md)。

## 正式页表切换后的诊断

针对 `11` 之后的观测盲区，新增 `--post-vm` 诊断版本：共享记录及临时栈移到原始 `__DATA` 的未使用零填充，避免正式页表将代码区保护为只读后发生诊断器自身写故障。后续检查点在绘图期间短暂切换至经校验的 bootstrap TTBR0，绘图结束恢复原 TTBR0，并保留 TTBR1、TCR、PAR 和正常寄存器续接。正常启动指令不在该借用窗口内执行。

新增 `12` 至 `17` 检查点，分别覆盖正式 TTBR1 生效、TTBR0 撤销低地址映射后、`pmap_bootstrap` 前后、函数尾声和返回原始 `arm_init` 调用者。用户随后提供 `IMG_20261022_093457.jpg`，显示 `17`，确认该诊断内核中的函数已返回；照片不独立校验具体成品哈希，也不能据此判断后续执行是否失败。

成品为 `dist/ram-boot-xnu-post-vm/chiron-kernel-post-vm.img`，SHA-256 为 `9b952ad267e7ec6faffe998ac9b5005f79be8a463b7ef543374aa29766c6f087`。34 项回归及 45 项指令模拟场景通过。晚期测试把代码页设为只读、数据区设为可写，并验证 TTBR0 恢复及失败路径；SCTLR/AT 在这些测试中人工建模，不证明手机真实 MMU、缓存或硬件表遍历成功。随后已收到上述 `17` 照片反馈，但照片不独立确认具体哈希。完整边界与命令见 [页表切换后记录](research/CHIRON_XNU_POST_VM.zh-CN.md)。

## 源码日志路线

用户指出最后标记不能替代故障原因、异常通道接入过晚，以及反复二进制插桩的验证成本。当前以固定 UniXNU/XNU 源码、原 RAM-init 配置和已工作的 RAM 传输为基线，新增 `ram-trace` 云构建模式。新内核只通过源码 overlay 和正常链接生成；旧指令插桩不再作为迭代主线。

通道覆盖汇编入口和 MMU 过渡、初始化回调进入/返回、控制台、原生 EL1 异常和统一 panic 入口。记录含构建 ID、事件序号、函数地址、独立的最近异常寄存器及 panic 调用者。暂时失去显示时继续保留 RAM 日志；原生映射建立后继续显示。诊断 panic 在输出后停止，避免正常 panic 处理重启或覆盖画面。

云端在原 POST 正反例和真实 PID 1 心跳资格之外，还必须验证真实 panic 文本及注入数据异常的 PC/ESR/FAR，采集原生 QEMU 屏幕、符号和 dSYM，全部通过后才能编号打包。当前仍未取得设备的明确故障原因；受控 QEMU 错误不等于手机的故障。后续构建状态见 [源码可观测性](research/CHIRON_SOURCE_TRACE.zh-CN.md)。

源码编译及资格运行 `39726305878` 已通过：真实控制台两次心跳、POST 正反例、原生 framebuffer 输出、真实 panic 原因/调用者，以及来自 `_chiron_trace_test+0x44` 的数据异常（ESR=`0x96000005`、FAR=`0x1000`）。源内核 SHA-256 为 `e0df2b09d270318c6190d4b1f12469a4147e85d5b0b544775870f0c318e853a5`。

修正 manifest 文件名引用后，仅复用上述已验证内核进行云端打包，运行 `39736014170` 已通过；本地独立验证了 `.img` 后缀/ANDROID 头、完整镜像 SHA、FAT 中的内核 SHA、构建 ID 和不存在注入参数。成品 `dist/source-trace-39736014170/CT-39726305878-1-bfaae879457f.img`，26,697,728 字节，SHA-256 为 `234ed79e9e5d29a79af24e4110f5fa513a6c8f8056aafb203a557ef0069a7ce0`。未检测到 fastboot 设备，未启动手机；真实设备的 PID 1 和故障原因仍未确认。

### 源码日志的首轮实机反馈与控制台路径

2026-10-22，用户提供 `IMG_20261022_151546.jpg`，屏幕构建 ID 与上述交付相符。照片显示正式 TTBR0 撤销、`pmap_bootstrap`、`arm_vm_init`、独立原生 framebuffer 映射及 `serial_init` 均已完成，最后可见序号 `0x6ad`、`PE_create_console() enter`。没有可见 panic/异常原因或 PID 1；最后日志不能单独证明停滞发生的位置。照片不独立确认镜像哈希。

源码及从旧成品 FAT 中提取的原始内核反汇编均确认：`PE_create_console` 中的旧 panic 检查映射 BCM2837 的 `0x3c0fc000 + 0x4000`，读取头部并可能调用 `bzero`。它不是本项目已验证的 Chiron panic 区域；即使该路径在 QEMU 可返回，也不能证明实机访问有效。两个 RAM 运行配置现启用已有 `XNU_DT_PANIC_REGION` 校验路径；当前 ADT 没有 `pram`，因此应跳过访问，不虚构物理地址。

源码 overlay 扩展到 `pe_init.c`、`video_console.c` 和早期 `io_map`，记录 panic 检查、显示参数、映射地址/大小/属性、图形或文本模式、控制台锁/几何/清屏及返回。保留观察器自身的原生映射，控制台重绑时记录真实参数并重绘。新 QEMU 检查点必须实际观察设备树缺省跳过、映射返回和 `PE_create_console` 返回，再继续沿用 POST、两次心跳和 panic/数据异常资格。23 项本地主线测试及干净固定源码的 10 个 overlay 校验通过；云端编译、打包及新实机结论另记。
