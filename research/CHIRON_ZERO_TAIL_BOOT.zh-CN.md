# 完整 RAM 卷的零尾部传输

日期：2026-10-21。48 MiB 入口探针无可见输出，24 MiB 同版探针已显示有效交接地址。零尾部传输现已实机进入 UniXNU，DarwinBoot 读取内核并打印输入校验完成。用户进一步确认绿色横条，支持已退出 boot services 并进入交接汇编；尚未证明 XNU 或 PID 1 运行，也不宣称已查明 bootloader 内部根因。

## 新证据

用户报告 `dist/entry-live-48m/chiron-entry-probe-48m.img` 仍只显示 MI。`/tmp/chiron-entry-live-48m.log`：

```text
Sending 'boot.img' (50648 KB)                      OKAY [  1.144s]
Booting                                            OKAY [  0.001s]
Finished. Total time: 1.192s
```

直接比较 24/48 MiB 探针成品：

- 诊断 FD、编码后的 kernel payload、DTB、ramdisk 文件偏移一致。
- 前 24 MiB RAM 数据逐字节相同。
- 完整卷后面的 25,165,824 字节全部为零，非零字节计数为 0。

因此这组差异不需要用不同内核文件或后半段非零内容来解释。变化是传输/声明的长度以及关联元数据；仍缺 bootloader 内部的实际缓冲区地址和执行记录，不能直接将它定性为某个硬容量上限。

## 实现方式

`tools/build_chiron_zero_tail_boot.py`：

1. 仅接受原来固定 SHA-256 的 RAM 卷固件和逻辑卷，校验 FAT32 几何。
2. 确认逻辑卷恰好 48 MiB，截去的后 24 MiB 完全为零；出现任何非零字节即拒绝。
3. 传输原卷前 24 MiB，保持 FAT32 头、文件及原始逻辑卷身份。
4. 将一段入口代码加入原来未用的 Image 前缀，固件主体 `0x8000` 之后保持原字节。
5. 入口匹配实测地址、异常级别、关闭的 MMU/数据缓存、FDT 字段和 FAT32 基本几何后，才将零尾部写回 RAM，并更新 FDT 的 initrd end。
6. 恢复原 x0..x3 交接寄存器和栈，跳到原 PrePi 入口，继续原本的 UEFI/RAM 卷/XNU 加载路径。

完整逻辑卷 SHA-256 仍是：

```text
8be640543dec47458edf6cc8c8c4204a6857e2d5223e442b57704d9337c04a48
```

这不是将 FAT32 卷裁成一个不完整的 24 MiB 文件系统。主机端和指令模拟都要求“前 24 MiB + 补零”与原始整个 48 MiB 卷逐字节一致，原 XNU、自有 init、ADT、trustcache 文件保留不变。

## 入口约束

入口复用 `scripts/chiron-entry-probe.S` 的 `CHIRON_ZERO_TAIL` 编译模式，使用有界 FDT 解析器取得 end 属性的实际位置与 32/64 位长度，并按大端编码原位更新。

| 项目 | 仅接受的值 |
| --- | --- |
| Image 地址 | `0x40080000` |
| FDT 地址 | `0x43200000` |
| initrd 起点 | `0x43400000` |
| 传输后原 end | `0x44c00000` |
| 补零与更新后的 end | `0x46400000` |
| 需写零的范围 | `[0x44c00000,0x46400000)` |
| CPU 状态 | EL1 或 EL2，当前级 MMU 和数据缓存关闭 |

该补零范围包含于已采集的低地址 RAM bank，且不与采集到的 memblock/static reserved 区域相交。主机构建会再次检查 `research/chiron-live-profile.json`，遇到越界或保留区交集时拒绝。

这些是同机历史内存测量与此次 24 MiB 入口照片的约束，不是一个通用 UEFI 内存分配实现，也不是本次启动完整实时内存图。未知地址或 CPU 状态不会自动放宽条件，入口将显示 FAIL 并停住。

## 屏幕与失败码

- `EXPAND 0000000003000000`：入口已执行，正在检查或恢复逻辑卷。
- `READY 0000000003000000`：已补零并更新 FDT，即将交给原 UEFI 入口；不能据此宣布 UEFI 或 XNU 后续成功。
- `FAIL 10`：异常级别不支持，或 MMU/数据缓存开启。
- `FAIL 11`：Image/FDT 地址与实测约束不同。
- `FAIL 12`：initrd 的起止地址或 end 属性宽度不匹配。
- `FAIL 13`：FAT32 基本几何不匹配。
- `FAIL 1..5`：复用入口探针的 FDT 解析失败码。

FAIL 后同时显示 Image/FDT/initrd 地址、原始 CurrentEL 与 SCTLR，便于继续定位。地址解析失败时 initrd 显示零，不把未知值作为有效范围。

## 成品与验证

```sh
python3 tools/build_chiron_zero_tail_boot.py \
  --source dist/ram-boot/chiron-ram-init-experimental.img \
  --output dist/ram-boot-zero-tail
```

输出 `dist/ram-boot-zero-tail/chiron-ram-init-zero-tail.img`：

```text
SHA-256: 14c51d4478e7e5e7437f5d444f919b3ec23a337d10d6259dd185e08c9d28aaa9
Bytes:   26697728
```

传输包长度和 ramdisk 文件偏移与已经显示实机读数的 24 MiB 探针相同；boot 头的 ramdisk 长度为 24 MiB，入口成功后 FDT 和 RAM 中的逻辑卷恢复为 48 MiB。仅有这个长度与偏移相同，不代表 bootloader 的其他内部行为已测得。

本地验证：

- 18 项回归检查通过，包含非零尾部拒绝、错误 FAT 几何拒绝、内存越界/保留区拒绝、默认/补零两种模式汇编无重定位。
- 实际补零入口指令的 10 项 Unicorn 场景通过：EL1 的 32/64 位字段、EL2 的 64 位字段，成功恢复整个卷并保持其哈希；错误缓存、地址、长度、FAT 头、缺失字段均停住，未修改 RAM 卷或 FDT。
- 成功模拟返回至原 `Image+0x8000` PrePi 入口，x0..x3 与原 SP 恢复，固件主体不变。模拟在 PrePi 入口停止，没有执行后续 UEFI 或 XNU。
- 默认诊断模式额外执行 4 项共享解析器回归，32/64 位、高地址和缺失 initrd 均符合预期。
- boot SHA-1、原 DTB、传输前缀和重建后的整个卷均校验通过；构建拒绝改变已匹配的 ramdisk 文件偏移。
- 成功画面渲染检查见 `dist/ram-boot-zero-tail/validation/success-preview.png`，仅为模拟画面。

模拟报告：

```text
dist/ram-boot-zero-tail/validation/emulation-report.json
dist/ram-boot-zero-tail/validation/default-probe-regression.json
```

## 实机测试

```sh
fastboot boot /Users/mac/Documents/ChatGPT/ios_party/dist/ram-boot-zero-tail/chiron-ram-init-zero-tail.img 2>&1 | tee /tmp/chiron-zero-tail-fastboot.log
```

本版通过入口校验后会继续尝试原来的 UEFI 和 XNU 加载流程，不会像诊断探针那样主动停止。后续实机反馈如下；XNU 是否进入及自有 PID 1 是否出现心跳仍未验证。

## 实机到达 DarwinBoot 交接准备阶段

用户反馈“正常的”，提供 `IMG_20261021_161349.jpg`。照片清楚显示 UniXNU 的 `Chiron native 4K XNU / RAM init` 条目，以及 DarwinBoot 的 `inputs validated; exiting boot services`、`hand-off to the kernel`。按连续测试上下文关联本节补零镜像；照片本身不含启动镜像哈希。

`/tmp/chiron-zero-tail-fastboot.log`：

```text
Sending 'boot.img' (26072 KB)                      OKAY [  0.587s]
Booting                                            OKAY [  0.001s]
Finished. Total time: 0.619s
```

人工核对原照片局部后的字段：

| 字段 | 读数 |
| --- | --- |
| kernel VA | `0xfffffff007004000` |
| kernel PA | `0x40004000` |
| kernel entry PA | `0x403f9488` |
| boot args PA | `0x41540000` |
| window bytes | `0x40000000` |
| video | `0x9d400000`，1080×2160，row 4320，32 bpp |
| CurrentEL | 1 |
| SCTLR_EL1 | `0xc0180d` |
| TCR_EL1 | `0x280803518` |
| ID_AA64MMFR0_EL1 | `0x1122` |
| ID_AA64PFR0_EL1 | `0x1002222` |
| entry words | `140006f5 d503201f d503201f d503201f` |

这证明已越过原来 MI 无输出的阶段，UEFI/UniXNU 已执行并读到启动配置与内核，加载器完成了其输入校验。结合该成品及之前没有 RAM 卷时无启动项的对照，支持 RAM 传输、RAM 卷识别与配置/内核读取这条链已在实机工作；尚未取得运行时整卷哈希或独立的设备路径枚举。

核对固定 `UniXNU/UniXNU@7b52e7968c76453cdc18b5aa174fc368d5293b91`：

- `drivers/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c` 中，上述提示都在 `GetMemoryMap`/`ExitBootServices` 尝试循环之前打印。
- 仅当 `ExitBootServices` 成功，才调用 `DarwinEnter`。
- `Entry.S` 的绿色条在 `DarwinEnter` 内绘制，随后才清理 owned window 的缓存、关闭 MMU/缓存并分支到 XNU。

因此即使看到 `hand-off to the kernel`，也不能宣布 boot services 已成功退出。绿色条最多能证明进入了加载器的交接汇编，不证明 XNU 自身的第一条指令、初始化或 PID 1 已执行。这个条带与配置中的 `CONFIG_XNU_BOOT_PROGRESS=n` 是不同的标记，不应混为一谈。

照片未完整包含屏幕最上沿，无法可靠判定顶部绿色条是否出现；用户尚未说明此画面是否持续不变。当前仍需观察后续变化或补拍屏幕最上沿，再区分交接前、交接汇编和 XNU 后续阶段。静止的加载器画面本身也不等于内核必然挂死，尤其当前未启用内核 boot-progress/legacy-display 配置。

用户随后明确回复“有绿色的条”。按连续提问中所指的顶部横条解释，记录为 `ExitBootServices` 已成功返回并进入 `DarwinEnter`。该条绘制在整个缓存清理和 MMU 转换之前，仍不能证明已经完成这些操作或已执行 XNU。下一步的分阶段诊断见 [DarwinBoot 交接记录](CHIRON_DARWIN_HANDOFF.zh-CN.md)。
