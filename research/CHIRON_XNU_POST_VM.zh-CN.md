# 正式页表切换后的 XNU 诊断

日期：2026-10-22。

后续更新：`IMG_20261022_093457.jpg` 已显示 `17`。本页记录的诊断内核已返回原始 `arm_init`，但没有证明实机 PID 1，也没有给出后续故障原因。后续主线转为 [源码日志和异常记录](CHIRON_SOURCE_TRACE.zh-CN.md)，本页与本地插桩工具保留为历史资料。

## 实机结果与下一轮目标

用户提供 `IMG_20261022_092113.jpg`，可见：

```text
XNU STAGE 0000000000000011
```

按上一版固定检查点，原始 `arm_vm_init` 已经过内存参数导入、`early_random`、Mach-O 区段处理、`arm_vm_prot_init` 和 physmap 页表生成，来到正式 TTBR1 切换之前。照片不显示镜像名或哈希，不独立校验具体成品身份。

`11` 是旧版最后一个检查点，之后原始代码会启用正式内核映射、撤销 TTBR0 恒等映射。旧的 framebuffer 文本可能保留，因此它不证明卡死在该处，也不证明函数已经返回。

本轮目标是观察切换之后的原始代码，并在函数实际返回到 `arm_init` 时显示 `17`。尚无这一步的实机结果。

## 当前镜像

```text
dist/ram-boot-xnu-post-vm/chiron-kernel-post-vm.img
26,693,632 bytes
SHA-256 9b952ad267e7ec6faffe998ac9b5005f79be8a463b7ef543374aa29766c6f087
```

手机进入 fastboot 后临时启动：

```sh
fastboot boot /Users/mac/Documents/ChatGPT/ios_party/dist/ram-boot-xnu-post-vm/chiron-kernel-post-vm.img 2>&1 | tee /tmp/chiron-xnu-post-vm.log
```

记录完整屏幕，包含 `XNU STAGE`、`SCTLR`、`TCR` 及出现的异常字段。

编号均为十六进制；本版保留旧的 `00` 至 `11` 含义，追加：

| 编号 | 原始代码已到达的位置 |
| --- | --- |
| `12` | 正式 TTBR1 切换函数已返回，尚未撤销 TTBR0 恒等映射 |
| `13` | 原始无效 TTBR0 安装和 TLB 维护已返回 |
| `14` | 即将调用 `pmap_bootstrap` |
| `15` | `pmap_bootstrap` 和紧接的禁止抢占调用已返回 |
| `16` | `arm_vm_init` 的栈保护检查通过、被保存寄存器已恢复，即将恢复 SP 并返回 |
| `17` | 已回到原始 `arm_init` 的调用点之后 |
| `E1` | 早期 GOP 临时页表无法在预期空闲位置建立，主动停止 |
| `E2` | 后续绘图发现 bootstrap 根槽位或 GOP 描述符改变，主动停止 |

`17` 只证明本诊断版本的 `arm_vm_init` 返回，仍不代表真实 GIC/定时器、密码学 POST、PID 1 或 iOS 桌面已运行。任何最后可见编号都只是执行下界，不能独立证明故障恰好发生在下一条指令。

## 映射与可写记录

输入仍严格限定为同一三色交接、零尾部镜像，以及同一已检查源内核。UEFI、DTB、零尾部程序、init、ADT、trustcache、FAT 文件尺寸和容器布局保持此前构建的边界。

原有 7312 字节可执行填充中放置 6124 字节诊断代码。所有可变记录、保存区及临时栈改放到 `0xfffffff007aab110` 开始的 1440 字节中。该范围位于 `__DATA` 的文件支持区，处于所有非空 section 之外，内容必须全零，段初始权限必须为读写；工具同时检查 zerofill section 不得重叠。不占用声明的 BSS、per-CPU 结构或其他 section。

这是为固定成品选择的对齐填充，不是通用内核内存分配器。正式页表保护生效后，诊断器不向只读代码页写保存区或栈。

在 `12` 至 `17` 的检查点中：

1. 保存正常通用寄存器、SP、NZCV、DAIF，并使用独立临时栈；路径限定为单 CPU、用户进程尚未运行的早期初始化。
2. 在高虚拟地址中读取原始 bootstrap 根槽位，检查它与早期保留的描述符一致、页位于原 16 页保留区内，且五个 GOP 块描述符全部匹配。
3. 保存完整的原 TTBR0，在屏蔽中断的诊断窗口内短暂选用原 bootstrap TTBR0，并执行所需屏障和 TLB 失效。
4. 按此前 AT/PAR 检查绘图条件并显示编号。
5. 恢复完整原 TTBR0，再执行屏障和 TLB 失效，然后恢复正常寄存器并执行原被替换指令。

它不更改正式 TTBR1 页表，也不在正常启动指令执行时保留借用的 TTBR0。AT/PAR 检查失败时跳过绘图，仍执行恢复。根或块描述符不匹配时，记录 `E2` 并停止，不盲目使用被改变的映射；此时若当前没有可写的 GOP 映射，`E2` 可能只有 RAM 记录，屏幕保留更早编号。

该方法仍改变了检查点执行时的地址空间和 TLB 状态，因此本版是诊断实验，不能将其进展等同于未插桩内核已通过完整启动验证。

## 完整性与异常边界

工具检查原始输入哈希、原始文本 POST HMAC、每个被替换的指令、低地址异常向量、可执行填充和可写填充。替换指令限定为固定版本的非 PC 相对普通指令，执行后返回其后方。

和前两版分阶段内核一样，本版修改了原始文本，因此重新计算源密码学 POST 使用的文本 HMAC。实际 POST 实现及失败拒绝路径没有关闭；重新计算字段不是实机执行 POST 的证明，也不继承原始 QEMU PID 1 资格。

低地址异常记录器的范围仍只到 `0A` 恢复原生异常向量为止；它不会替代全部 C 初始化路径中的原生异常处理。没有异常文字不能解释成没有异常。

## 本地验证

34 项仓库回归测试和 45 个实际指令模拟场景通过，成品容器校验、FAT 文件读回、零尾部和哈希验证通过。

基础测试覆盖实际检查点的寄存器续接、输入条件和页表冲突拒绝、低地址合成异常、从原始入口执行到首次 SCTLR 写入之前的页表/TCR/TTBR/MAIR 生成。

后续场景额外建立共享的物理地址/KVA 内存视图，把代码页设为只读可执行、数据页设为读写，并检查：

- 每个 `12` 至 `17` 检查点正常绘图并返回原指令之后。
- 原 TTBR0（含高位字段）、TTBR1、TCR、PAR 及正常寄存器均恢复。
- 根槽位或块描述符改变时，在修改 TTBR0 前拒绝。
- AT 翻译失败、跳过绘图时，仍然恢复原 TTBR0。

这些后续场景人工建模 SCTLR 的 MMU 位及 AT/PAR 结果。它们验证实际指令控制流与寄存器恢复，不能证明真实硬件页表遍历、缓存、TLB 或手机上的 MMU 切换成功。原始 `arm_vm_init` 和 `pmap_bootstrap` 函数体、密码学 POST、PID 1 不在这些模拟中执行。

复现（输出目录必须不存在）：

```sh
python3 -m unittest discover -s tests -v
python3 tools/build_chiron_kernel_log.py \
  --source dist/ram-boot-handoff-stages/chiron-ram-init-handoff-stages.img \
  --output /tmp/chiron-post-vm-build \
  --post-vm
PYTHONPATH=/private/tmp/chiron-debug-python python3 tools/emulate_chiron_kernel_stages.py \
  --boot-directory /tmp/chiron-post-vm-build \
  --output /tmp/chiron-post-vm-validation
```

报告与预览位于 `dist/ram-boot-xnu-post-vm/kernel-log-report.json` 和 `dist/ram-boot-xnu-post-vm/validation/`。本轮只读 `fastboot devices` 没有枚举到设备，未自动重启或启动手机。
