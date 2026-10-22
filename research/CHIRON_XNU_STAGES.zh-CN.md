# XNU 早期启动与内存初始化诊断

日期：2026-10-21。

2026-10-22 更新：`IMG_20261022_092113.jpg` 已实机显示 `11`，本页的内存初始化检查点已推进到正式 TTBR1 切换之前。切换后继续显示的新诊断镜像见 [页表切换后记录](CHIRON_XNU_POST_VM.zh-CN.md)。

## 最新实机观察

`IMG_20261021_213506.jpg` 显示：

```text
XNU STAGE 000000000000000C
SCTLR     0000000034D4593D
TCR       00000022A51A251A
```

`0C` 表示已进入原始 `arm_vm_init`，因而原始启动汇编、MMU 开启和 `arm_init` 的前段已在手机上执行。尚未确认 `arm_vm_init` 返回、真实 GIC/定时器初始化或 PID 1 心跳。照片没有镜像文件名和哈希；这里记录检查点含义，不把它当作字节级成品身份校验。

最后显示的编号仅证明经过该位置。当前代码没有在 `arm_vm_init` 返回后继续画标记，而且正式页表切换会移除低地址 GOP 映射，所以不能据此宣布“卡死在 arm_vm_init”。

## 内存初始化测试镜像

```text
dist/ram-boot-xnu-vm/chiron-kernel-vm-stages.img
26,693,632 bytes
SHA-256 fcbf300de64b5c54dac9f65bb9e46391566758becfa8200febc57592a2b17397
```

本版沿用 12 点版本的路径，增加五个 `arm_vm_init` 内部检查点。2026-10-22 的照片已显示 `11`；这确认到达页表切换前，不能证明切换之后的执行情况。

手机进入 fastboot 后，临时启动：

```sh
fastboot boot /Users/mac/Documents/ChatGPT/ios_party/dist/ram-boot-xnu-vm/chiron-kernel-vm-stages.img 2>&1 | tee /tmp/chiron-xnu-vm-stages.log
```

记录完整屏幕，尤其是 `XNU STAGE`、`SCTLR`、`TCR` 和可能出现的 `EARLY EXCEPTION / ESR / ELR / FAR`。编号按十六进制显示，`10` 和 `11` 分别是第 16、17 点。

| 编号 | 经过的位置 |
| --- | --- |
| `00` | 诊断入口校验和临时栈建立完成，即将转入原始入口 |
| `01` | 原始 `start_first_cpu`，即将写 OSLAR |
| `02` | OSLAR 写入返回 |
| `03` | 低地址 VBAR 已设置，即将读取内存参数 |
| `04` | 原始启动代码已设置两个启动栈指针 |
| `05` | bootstrap 页表清零完成，正在读取 Mach-O 头 |
| `06` | 原始恒等映射和内核虚拟映射页表已生成 |
| `07` | TCR 写入返回 |
| `08` | TTBR0、TTBR1、MAIR 写入返回 |
| `09` | 即将切换 MMU；暂时保留低地址诊断异常向量 |
| `0A` | MMU/cache 的 SCTLR 写入和 ISB 已返回；恢复原生异常向量 |
| `0B` | 原始 `arm_init` 函数入口 |
| `0C` | 原始 `arm_vm_init` 函数入口 |
| `0D` | 已导入内存参数，正在进行范围检查 |
| `0E` | `arm_vm_init` 中的 `early_random` 已返回 |
| `0F` | Mach-O 区段与页大小处理之后，尚未调用 `arm_vm_prot_init` |
| `10` | `arm_vm_prot_init` 返回，新保护页表尚未启用 |
| `11` | physmap 页表已生成，即将切换正式内核 TTBR1 |
| `E1` | 临时 GOP 页表无法在预期空闲位置建立，主动停止 |

若停留 `11`，只能确认到达正式页表切换之前。切换后原映射消失，屏幕可以保留 `11` 而内核继续执行。本版不在正式页表生效后使用这些探针，也没有证明内存初始化返回、GIC、定时器或 PID 1。

## 诊断改动与完整性

输入限定为已验证的三色交接、零尾部镜像：

```text
17d0c5b0e09daa37916f9c20129dcd89cd558aca0e3ed6257c54fc914214f6a5
```

源码内核限定为：

```text
812eb05da0c93556674489ba706868fc2b5208fd66b4a74cd27f282cb4f34de2
```

工具先检查原内核的 POST HMAC，再检查每个被替换的指令和 16 个低地址异常向量。未知内核、非预期指令、非空可执行填充或不能容纳程序时拒绝生成。

程序占用 `__TEXT_EXEC` 原有的 7312 字节零填充中的 6412 字节；更新 Mach-O 入口，并在 17 处用分支进入检查点，随后执行原指令并返回。第 `09` 点有意将原生高虚拟地址 VBAR 的写入延迟到第 `0A` 点，避免 MMU 尚未开启时异常跳向高地址。

探针使用独立临时栈，保存通用寄存器、原 SP、NZCV 和 DAIF。它只在单核、线程尚未开始运行的路径内短暂借用 TPIDR_EL0；在原始启动代码清零该寄存器之前恢复传入值，此后恢复为零。它不适用于后续线程或中断处理程序的通用插桩。

为了显示 MMU 开启后的信息，第 `06` 点从原始 16 页 bootstrap 页表保留区中选择下一张空页，检查根槽位、页对齐、边界和整页为空，然后为 GOP 建立五个 2 MiB、设备内存、仅特权读写且不可执行的恒等映射。诊断器不向正式内核页表增加映射；所有共享诊断数据写入均发生在正式页表生效之前。

本版修改了原内核 `__text`，因此它与此前仅改入口的 witness 版本不同：必须为插桩后的文本重新计算源密码学 POST 使用的 HMAC。实际 POST 实现和拒绝路径没有关闭；重新计算 HMAC 只保证该字段与新的文本一致，不代表实机已经执行 POST，也不把历史 QEMU 资格自动转移给新内核。

UEFI/三色交接代码、零尾部恢复程序、DTB、init、ADT、trustcache 和 FAT 分配保持原字节或原布局。只替换等长 kernel/config 文件，并重算 boot v0 的 legacy SHA-1；24 MiB 后仍全零。

## 异常记录范围

低地址的 16 个异常槽位改为记录 ESR、ELR、FAR、SPSR、异常发生时 SP、SCTLR 和最后一个阶段编号，显示后主动停止，不尝试继续故障指令。

该记录器从诊断入口设置低地址 VBAR 开始，持续到 `0A` 恢复原生异常向量。它不覆盖全部 C 初始化路径，不保证捕获路由到更高异常级的故障，也不能把没有异常文字解释为没有发生异常。

绘图在 MMU 开启后先通过 AT/PAR 检查 GOP 范围端点的可写映射，并恢复原 PAR_EL1；无法确认时跳过绘图。本版检查点全部在正式 TTBR 切换之前。

## 验证与复现

本轮 32 项仓库回归测试及 30 项实际指令模拟场景通过，boot 容器和等长 FAT 文件读回校验通过。以下为复现方式。

轻量测试：

```sh
python3 -m unittest discover -s tests -v
```

指令模拟使用本机已有的 Unicorn：

```sh
PYTHONPATH=/private/tmp/chiron-debug-python python3 tools/emulate_chiron_kernel_stages.py \
  --boot-directory dist/ram-boot-xnu-vm \
  --output /tmp/chiron-xnu-vm-validation
```

模拟覆盖每个实际检查点的原指令续接及寄存器、SP、NZCV、DAIF、TPIDR_EL0 保持；覆盖 SP_EL0、输入配置拒绝、根槽位冲突、非空/不对齐/越界页拒绝，以及三类合成低向量异常的字段和可见文本。还从实际诊断入口执行原始启动页表生成、TCR/TTBR/MAIR 设置，在原始 SCTLR 写入之前停止，并解析生成的页表检查代码、`arm_init` 和 GOP 映射。

该模拟不执行 MMU 开启，不模拟手机真实缓存和异常投递，也没有执行 `arm_init` / `arm_vm_init` 的函数体、密码学 POST 或 PID 1。十二点版本的 `0C` 来自实机照片，不来自这些模拟。

复现最新成品（输出目录必须不存在）：

```sh
python3 tools/build_chiron_kernel_log.py \
  --source dist/ram-boot-handoff-stages/chiron-ram-init-handoff-stages.img \
  --output /tmp/chiron-xnu-vm-build \
  --vm-stages
```

原十二点版使用 `--stages`。上游 `CONFIG_XNU_BOOT_PROGRESS` 仍为关闭：固定版本的完整观察器依赖 UFS/owner-root/Virtio 配置，不能只翻转开关用于当前最小 RAM 内核。

构建报告：`dist/ram-boot-xnu-vm/kernel-log-report.json`。模拟报告和预览：`dist/ram-boot-xnu-vm/validation/`。

启动汇编与内存初始化源码核对的是 Apple XNU 固定提交 `d8b80295118ef25ac3a784134bcf95cd8e88109f` 的 `osfmk/arm64/start.s`、`osfmk/arm64/arm_vm_init.c`，并结合当前内核的符号和实际指令核对。没有用其他设备的运行结果代替本机观察。
