# RAM 启动入口覆盖排查

日期：2026-10-21。已复现一个条件性固件入口缺陷，但完整负载的补丁对照实机仍停留 MI，没有解决本次故障。当前正在准备直接显示交接地址的入口探针，尚未确认手机失败根因，也未证明手机执行了 XNU。

## 已知实机反馈

| 固件与负载 | 用户反馈 | 已取得的终端证据 |
| --- | --- | --- |
| 旧版探针，无 ramdisk | 可进入探针 | 首次启动记录 |
| 新版固件，无 ramdisk | 可进入探针 | 用户照片 |
| 旧版固件，4 KiB 原卷前缀 | 正常 | Sending/Booting 均 OKAY |
| 旧版固件，24 MiB 原卷前缀 | 正常 | Sending/Booting 均 OKAY |
| 新版固件，完整 48 MiB 卷 | 停留 MI | 无对应完整启动日志 |
| 旧版固件，完整 48 MiB 卷 | 无 UEFI | 无对应完整启动日志 |
| 旧版未压缩固件，完整 48 MiB 卷 | 无 UEFI | 无对应完整启动日志 |

36 MiB 前缀镜像已生成，但没有其启动日志或用户结果。前缀对照不挂载文件系统，不能作为 RAM 卷驱动或 XNU 已工作的证据。

24 MiB 日志 `/tmp/chiron-24m-fastboot.log`：

```text
Sending 'boot.img' (26068 KB)                      OKAY [  0.585s]
Booting                                            OKAY [  0.001s]
Finished. Total time: 0.613s
```

## 成品中确认的入口指令

固定上游 `edk2-porting/edk2-msm8998@ace27110b9b9b6c591387d50cf748ec646402b4a`：

`Platforms/Msm8998FamilyPkg/Library/ArmPlatformLib/AArch64/ArmPlatformHelper.S`

`ArmPlatformPeiBootAction` 在执行整个 FD 的复制前，先将 FDT 指针写到固定地址：

```asm
mov x10, #0x83300000
str x0, [x10]
...
ldp x2, x3, [x4], #16
stp x2, x3, [x6], #16
subs x5, x5, #16
b.ne loop
ret
```

本地 LLVM 核对了旧版 FD 文件偏移 `0xfe64` 的 MOV/STR 指令，以及新版 FD 的同一序列（偏移 `0x101f4`）。不是仅凭源码推断产物包含这段逻辑。

如果本次 FD 源地址范围包含 `0x83300000`，该 STR 会先改掉源 FD 内的 8 字节，再把损坏内容复制到链接地址 `0xd0000000`。这里的“如果”尚缺手机实际加载地址验证。容量变化可能改变加载布局，但不能从现有反馈推导出一个已测得的 bootloader 容量上限。

REA 工具未接入且 CLI 不可用；上述成品核对来自 LLVM，下面的执行观察来自 Unicorn，不是 REA Evidence。未为此修改客户端注册或安装其他反编译引擎。

## 指令执行复现

`tools/emulate_chiron_fdt_store.py` 使用 Unicorn 2.1.4 执行两版成品里实际的 ARM64 helper 指令与原有链接地址常量。模型仅映射 RAM，不模拟完整手机。

| 合成 FD 源地址 | 原版复制结果 | 补丁复制结果 |
| --- | --- | --- |
| `0x80080000`，不覆盖固定 store | 与输入完全一致 | 与输入完全一致 |
| `0x83200000`，覆盖固定 store | FD 偏移 `0x100000` 有 8 字节损坏 | 与输入完全一致 |

四种情况均返回到重定位后的调用地址，FDT 指针保存值正确。旧版和新版分别执行上述四项，共 8 项模拟结果均符合预期。

原始报告：

- `dist/boot-fdt-fix-legacy-raw/emulation-report.json`
- `dist/ram-boot-fdt-fix/emulation-report.json`

这些地址是用于测试边界情况的合成输入；没有将其声明为手机失败时的实际地址，也没有模拟 DXE、XNU 或 iOS。

## 修复与对照

源码修复在 `ci/integrate_firmware.py`：将固定 FDT 指针写入移到完整 FD 复制之后，未来编译会应用该改动。已用固定上游源码实际验证替换上下文。

`tools/patch_chiron_fdt_store.py` 为现有两份固定哈希的 FD 创建等效指令对照，不接受未知版本：

- 原先提前执行的 STR 替换为 NOP。
- 复制后的 RET 改为跳到 FD 头部预留区 `0x40`。
- 该处执行原 STR 并 RET，保持原来重定位后的返回地址。

工具验证 ARM64 头、唯一指令序列、外层 FV 头校验和、SEC FFS 类型及固定数据校验标记。它拒绝修改内容校验开启或布局不匹配的 FFS 文件；本次修改不改变 FV/FFS 头。

两个输出均保留原 ramdisk 字节、原 DTB、加载地址字段、整个镜像长度和 ramdisk 文件偏移，并重算、独立检查 boot SHA-1。未压缩对照的 kernel_size 也完全不变；完整新版 gzip 镜像的 kernel_size 增加 12 字节，仍在原来的打包页数内。

| 对照镜像 | SHA-256 | 长度 |
| --- | --- | --- |
| `dist/boot-fdt-fix-legacy-raw/chiron-fdt-after-copy.img` | `b26f8b08791d2964999e52f7a75dad27c711333ba1d478708c922f3490ea5746` | 57,053,184 |
| `dist/ram-boot-fdt-fix/chiron-fdt-after-copy.img` | `47d5d521edf32ee1a7d43df8315e4bee0105fb61be28d0efa79da957637bf948` | 51,863,552 |

新版补丁镜像保留完整 48 MiB FAT32 卷与原 XNU/init 文件，后续实机失败结果见下文。旧版未压缩对照保留作隔离验证，它没有 RAM 卷驱动，不能启动其中的 XNU。

```sh
fastboot boot /Users/mac/Documents/ChatGPT/ios_party/dist/ram-boot-fdt-fix/chiron-fdt-after-copy.img 2>&1 | tee /tmp/chiron-fdt-after-copy-fastboot.log
```

若进入探针，需要区分看到 `RAM payload base=... bytes=50331648`、RAM 文件系统可读取、实际跳入 XNU 和 PID 1 心跳这几个不同里程碑。若仍无 UEFI，应继续取得实际入口/FDT/initrd 地址或早期执行标记，不能宣布此修复解决了手机问题。

## 本地验证

- 14 项回归测试通过，包括源码保存顺序、指令修改范围、分支目标、错误指纹和布局拒绝。
- 两版实际 ARM64 入口指令共 8 项 Unicorn 模拟结果通过。
- 两个补丁镜像均完成完整性与容器布局检查。
- 原始成品未覆盖；没有执行手机启动、刷写或重启。

Unicorn 仅安装到 `/tmp/chiron-debug-python`，可用以下命令复核新版模拟：

```sh
PYTHONPATH=/tmp/chiron-debug-python python3 tools/emulate_chiron_fdt_store.py \
  --source-firmware dist/ram-boot-fdt-fix/source-firmware.fd \
  --patched-firmware dist/ram-boot-fdt-fix/firmware.fd \
  --output /tmp/chiron-fdt-emulation-recheck.json
```

## 补丁实机失败反馈

用户随后提供 `IMG_20261021_145551.jpg`：画面仍为 MI 和 Unlocked，未出现 UEFI 或 XNU 输出。`/tmp/chiron-fdt-after-copy-fastboot.log`：

```text
Sending 'boot.img' (50648 KB)                      OKAY [  1.117s]
Booting                                            OKAY [  0.001s]
Finished. Total time: 1.181s
```

该结果证明补丁镜像已被 fastboot 接受，但不能证明已执行到固件或内核入口。上文模拟复现的是一个条件性缺陷，不是本次手机根因的证明；延后保存指针尚未解决实机启动，后续不将其描述为已验证修复。

## 直接采集入口地址

`scripts/chiron-entry-probe.S` 在 ARM64 Image 头之后、原 PrePi 之前运行。它保留原始头的 `adr x1, .`，将后续分支导向预留区，使用该 Image 预留区内的栈。探针只绘制字段、解析有界 FDT，最后停止；不执行原 UEFI，不挂载 RAM 卷，也不进入 XNU。

字段全部以十六进制显示：

| 字段 | 含义 |
| --- | --- |
| `ENTRY` | 入口与第一段绘制代码执行标记，值为 1 |
| `IMAGE` | 由 Image 起始指令取得的本次 Image 实际地址 |
| `FDT` | 入口寄存器 x0 中的设备树地址 |
| `INIT`、`END` | 从本次 FDT `/chosen` 读取的 initrd 起止地址 |
| `SIZE` | 解析成功时的 initrd 字节数，否则为 0 |
| `STATE` | 0：解析成功；1：指针/头无效；2：结构无效；3：无 initrd 字段；4：地址字段长度不支持；5：范围无效 |
| `OVER` | 位 1：固定 store 位于 Image 内；位 2：位于 initrd 内；位 4：FDT 指针位于 Image 内；位 8：位于 initrd 内 |

探针在测得的显示保留区内尝试 `0x9d400000`、`0x9d600000` 与 4320、4352 两种行距组合，分开纵向位置绘制四份读数，减少依赖单一未知 scanout 的影响。这仍不证明 bootloader 正在扫描其中一个组合；看不到文字时不能直接判定未跳入探针。

为沿用先前成功对照的 gzip 格式，当前优先提供：

| 镜像 | SHA-256 |
| --- | --- |
| `dist/entry-live-24m/chiron-entry-probe-24m.img` | `3f7e86c8d1f8219a1852596cf334d3ab55ecc1126125b7bc3540e2d28269a84a` |
| `dist/entry-live-48m/chiron-entry-probe-48m.img` | `f8c7a182e2567c1239473cc057185a01a0052c60609144d9deb808c8297c1faa` |

两者使用相同诊断 FD（SHA-256 `ccd9fae76dd479e2b8108dd7c064254a0847a33c66af35877f401937a3e26025`），只在 ramdisk 前缀长度和对应元数据上不同。增加探针导致 gzip kernel_size 增长 1590 字节，跨过一个打包页；ramdisk 文件偏移比旧版 gzip 对照多 4096 字节。因此读数属于诊断镜像，不能直接冒充原始失败镜像的地址。原始 24 MiB 成功的是未插入探针的旧版 gzip 镜像，尚未声称本探针 24 MiB 实机可用。

先测试 24 MiB 探针，确认 `ENTRY` 和其余字段可见，再比较 48 MiB：

```sh
fastboot boot /Users/mac/Documents/ChatGPT/ios_party/dist/entry-live-24m/chiron-entry-probe-24m.img 2>&1 | tee /tmp/chiron-entry-live-24m.log
```

```sh
fastboot boot /Users/mac/Documents/ChatGPT/ios_party/dist/entry-live-48m/chiron-entry-probe-48m.img 2>&1 | tee /tmp/chiron-entry-live-48m.log
```

停在这些字段上是探针设计行为，不表示正在等待 iOS 启动。

验证记录：

- 汇编目标为基础 AArch64，检查 ELF 对象无未解析重定位，代码与栈均限于原本未使用的 Image 前缀。
- 固件主体（偏移 `0x8000` 后）保持原字节，原 DTB 与对应 RAM 前缀哈希均校验通过，boot SHA-1 正确。
- 本轮完整回归 15 项通过；最终显示布局变更后入口探针定向测试通过。
- `tools/emulate_chiron_entry_probe.py` 执行实际探针指令：32/64 位地址、超过 4 GiB 的地址、覆盖标记、缺失字段、chosen 外字段忽略、损坏头/结构、无效字段长度/范围，共 10 项均通过。
- 四个绘制候选均检查为非空，并渲染检查 `dist/entry-probe-v2-validation/display-preview.png`；这只是模拟画面，不是手机运行照片。
- 原始字节、原镜像及失败日志均保留，没有自动操作手机。

## 24 MiB 入口探针实机读数

用户提供 `IMG_20261021_152528.jpg`。将原照片顺时针旋转 90 度并裁取清晰读数区域后，人工转录如下；处理后的本地查看文件为 `/tmp/chiron-entry-24m-readings.png`，原照片未修改。

| 字段 | 读数 |
| --- | --- |
| `ENTRY` | `0x1` |
| `IMAGE` | `0x40080000` |
| `FDT` | `0x43200000` |
| `INIT` | `0x43400000` |
| `END` | `0x44c00000` |
| `SIZE` | `0x01800000`，24 MiB |
| `STATE` | `0x0` |
| `OVER` | `0x0` |

对应 `/tmp/chiron-entry-live-24m.log`：

```text
Sending 'boot.img' (26072 KB)                      OKAY [  0.585s]
Booting                                            OKAY [  0.001s]
Finished. Total time: 0.612s
```

该照片首次直接确认诊断入口在手机上执行，并成功解析本次 FDT 的 initrd 字段。24 MiB 诊断镜像的 Image 范围为 `[0x40080000,0x40680000)`，FDT 指针及 initrd 起止地址分离，固定 `0x83300000` 不位于 Image 或 initrd 范围内。因此此前固定 store 覆盖机制没有在这个 24 MiB 诊断样本中命中，不能把它认定为本次故障根因。

`OVER=0` 仅代表代码列出的四项“地址点落入范围”检查没有命中；不是完整内存图或所有区间均无覆盖的证明。`STATE=0` 证明字段解析和范围顺序正确，不证明 RAM 文件系统或内核可以运行。

画面中部分复制候选为斜线，清晰候选足以读取全部字段；斜线本身不作为 XNU/GPU 故障证据。这一探针有意停止，不尝试 UEFI 或 XNU。同版 48 MiB 探针的后续结果见下一节。

## 48 MiB 无输出与后续传输绕过

用户随后报告同版 48 MiB 探针只剩 MI；已取得发送和 Booting 均 OKAY 的日志，总用时 1.192 秒。未取得该样本的入口地址，不能直接将“没有文字”解释为从未跳入探针。

本地逐字节比较发现：两份探针的 FD/kernel payload、DTB、前 24 MiB RAM 前缀和 ramdisk 文件偏移一致，而额外的 24 MiB 全部为零。这支持下一步以缩短物理传输、入口后补零的方式绕过大负载差异；不再依赖裁剪出一个无法挂载的半卷，也不把这个现象直接宣称为 bootloader 的确定容量上限。

已实现并验证恢复完整 48 MiB 逻辑卷、再返回原 UEFI 入口的实验镜像；尚待实机反馈。实现、地址约束、原卷哈希保持、18 项回归与指令模拟见 [零尾部传输记录](CHIRON_ZERO_TAIL_BOOT.zh-CN.md)。
