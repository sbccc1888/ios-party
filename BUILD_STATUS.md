# 首轮远程构建结果

日期：2026-10-20。私有仓库：`to-mid/ios-party`，分支：`codex/chiron-bringup`。

已完成板级配置、单核 ADT、定时器中断适配、远程编译、固件集成和本地独立校验。构建阶段未操作手机；随后首次临时启动的状态见文末。尚未证明 XNU 或 iOS 用户态在 MIX 2 上运行。

## 成功作业

| 产物 | GitHub Actions 运行编号 / 成功作业 | 源码提交 |
| --- | --- | --- |
| XNU 源内核、ArmCpu 和 ArmDTPlatform 静态库 | `39416204280` / `kernel` | `ebe864a1220c02eca3629403258f1957906c4830` |
| UniXNU、DarwinBoot、ChironProbe 等 AArch64 EFI | `39417919101` / `efi` | `a28307a414b917bda8e603ef8645a17d8071f593` |
| 最终整合固件及 boot v0 容器 | `39421481238` / `firmware` | `fd29081cca1d2160de06071d14095c85f1233f48` |

最终固件作业复用了已通过的 EFI。已下载驱动的源文件哈希与当前 ArmCpu 覆盖、固定版本的 ArmDTPlatform 一致。

## 本地成品

| 路径 | 大小 | 用途 |
| --- | --- | --- |
| `dist/kernel/kernel.source.unstripped` | 21,021,808 字节 | 普通 arm64 Mach-O 源内核；未预链接 |
| `dist/kernel/drivers/` | 两个静态库约 440 KB | 已编译，尚未链接进启动内核 |
| `dist/kernel/kernel.source.adt` | 约 10 KB | 单核 chiron ADT |
| `dist/efi/ChironProbe.efi` | 11,776 字节 | 原始 CPU 特性、GOP、内存图探针 |
| `dist/efi/DarwinBoot.efi` | 25,088 字节 | XNU 加载器 |
| `dist/efi/UniXNU.efi` | 90,624 字节 | 启动管理器 |
| `dist/firmware/chiron-unixnu.fd` | 6,291,456 字节 | 含已验证 EFI 的固件卷 |
| `dist/firmware/chiron-uefi-experimental.img` | 1,527,808 字节 | 实验性 boot v0 UEFI 容器；不含 XNU payload |

`dist/` 约 29 MiB，包含校验报告；本轮结束时整个工作区约 57 MiB。大日志保存在远端产物中，编译树仅在临时 runner 上生成并随作业回收，本机没有新增完整构建树。

## 已通过的校验

- 5 项轻量回归检查：实测 GIC/定时器资源、单核/SMC、易失 NVRAM、错误架构拒绝、固件头长度约束。
- 源内核的普通 arm64 Mach-O 类型，以及各 EFI 的 AArch64 PE32+ 架构与 UEFI subsystem。
- 成品 SHA-256、boot v0 的 legacy SHA-1、4096 字节容器对齐。
- ARM64 Image 头与完整固件卷均为 6 MiB。
- 固件携带的 DTB 为 MSM8998 v2.1：`msm-id=<0x124 0x20001>`、`board-id=<0x20 0>`。
- 本地 EFI 文件的哈希与固件集成报告中的输入哈希一致。

关键 SHA-256：

```text
kernel.source.unstripped
c51a6dea018eb514a286681a561e9aa7b72616a1b9785724bf863cf13e98dd28

chiron-unixnu.fd
7513f28ad5e8a44f750d775c2175b206a9a569e1691a239d82a5037cc27d09d1

chiron-uefi-experimental.img
6dc0b1effe4adf13ce4c821b4a906112ad14e00e252934ae09339d9e3b7acc7b
```

完整信息分别在 `dist/kernel/artifact-report.json`、`dist/efi/artifact-report.json`、`dist/firmware/firmware-report.json`、`dist/firmware/integration-report.json` 和 `dist/firmware/container-inspection/inspection.json`。

## 当前边界与下一步

源内核仍使用 bootstrap 配置，平台、CPU、密码学和 pthread 等外部组件需要后续链接/初始化，随后才能做 RAM init 心跳验证。当前固件容器只提供 UEFI、管理器和探针，不能据本轮编译结果声称 XNU 已能启动。

UEFI 探针现已成功执行，启动核心的 4 KiB 能力与 GOP 参数已记录。下一步补齐并链接平台、CPU、密码学、pthread 和最小 RAM 根环境，整理加载器所需的内核入口与负载，验证 XNU 日志和最小 init 心跳。加载器仍需从当时的 UEFI 内存图分配真实 owned memory。

固件移除了 SimpleInit GUI 和 UAS 存储应用入口，但保留了 vendor DXE 驱动；仍没有全链路无写入的运行证明。原始设备采集与所有成品均被 Git 忽略。

## 首次临时启动

2026-10-20，用户报告进入 fastboot，USB 实测 product 为 `chiron`，bootloader 和 critical 均报告已解锁，最大传输大小为 536,870,912 字节。

对上述 SHA-256 的实验镜像执行了一次 `fastboot boot`：

```text
Sending 'boot.img' (1492 KB)  OKAY [0.037s]
Booting                     OKAY [0.001s]
Finished. Total time: 0.040s
```

之后 fastboot 设备列表为空。随后用户报告成功进入 UEFI，并提供了探针照片：可见 EL1 寄存器输出和等待按键，确认 ChironProbe 已执行。启动 CPU 的 TGran4=0、TGran16=0；GOP 为 1080×2160、pitch=1080、base=`0x9d400000`。详细转录见 [UEFI 探针观察](research/CHIRON_UEFI_PROBE.zh-CN.md)。

仍未确认 XNU 运行。未执行分区刷写或擦除命令。原始命令输出位于 `research/artifacts/chiron-firstboot-20261020/fastboot-boot.log`。
