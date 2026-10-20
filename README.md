# MIX 2 / chiron XNU bring-up

基于 UniXNU 与 MSM8998 EDK2 的设备移植工程。目标设备为小米 MIX 2 6 GB / 128 GB，实测 SoC 修订 2.1、Synaptics 触控、JDI video 面板。

当前阶段是远程编译和输入验证。尚未证明 XNU 在 MIX 2 上启动，也没有生成完整 iOS 用户态或可日常使用的系统。

首轮内核、驱动库、EFI 和整合固件的远程编译均已通过。成品位置、哈希和验证边界见 [构建记录](BUILD_STATUS.md)。

## 构建方式

GitHub Actions 工作流 `Chiron remote bring-up` 在源码推送时仅跑轻量验证；真正的编译任务仅手动触发：

- Linux runner 编译 AArch64 UniXNU、DarwinBoot 和 MSM8998 固件。
- macOS runner 编译 4 KiB XNU 源内核与平台驱动静态库。
- Mac 本地只运行轻量测试、生成 ADT 和下载必要产物。
- 不下载完整 IPSW，不生成 userdata 镜像，不执行 ADB/fastboot。
- 每个任务设有超时，产物保留 7 天；不缓存完整编译树。

固件集成重跑可填写 `efi_run_id`，复用同仓库此前成功生成的 `chiron-efi` 产物；该情况下跳过 EFI 重编译。更新 EFI 源码后应留空，重新编译。

本地验证：

```sh
python3 -m unittest discover -s tests -v
python3 scripts/make-chiron-adt.py --output out/chiron/chiron.adt
```

## 当前产物的边界

`configs/chiron_defconfig` 是第一轮源代码 bootstrap 配置。CPU、平台和密码学等外部库尚未链接进内核；原始内核不能直接作为 DarwinBoot 的完整 XNU payload 使用。

CI 另行编译 ArmDTPlatform 与 ArmCpu 静态库。其中 ArmCpu 已改为从 ADT 的 `virtual-timer-intid` 读取本机虚拟定时器中断号 19，保留上游 QEMU 的默认值 27。

`ChironProbe.efi` 是无存储访问的应用级探针，显示原始 CPU 特性、GOP 和 UEFI 内存图。它不切换页表、不退出 boot services；固件侧键入口指向该探针，默认启动仍由 UniXNU registrar 注册。整个固件仍含 vendor 驱动，不能据此声称全链路无写入。

ADT 使用真实 chiron 身份、单核、SMC、GICv3 和易失 NVRAM，不包含存储节点和伪造的 Apple 设备身份。它向 DarwinBoot 请求 1 GiB 实际 UEFI 分配，而不把 Android 的候选地址硬编码为可用窗口。

旧 MSM8998 固件与新加载器分别用各自固定的 EDK II 基线编译，再把 EFI 二进制嵌入固件。编译成功仍需验证 UEFI 协议、最终内存图与入口行为；原平台含未审计的 vendor 驱动，编译产物不能因此称为无写入的诊断镜像。

## 目录

| 路径 | 用途 |
| --- | --- |
| `board.toml` | 实测板级参数与当前构建阶段 |
| `research/chiron-live-profile.json` | 去除设备标识的运行时测量结果 |
| `scripts/make-chiron-adt.py` | 最小 Apple Device Tree 生成器 |
| `drivers/ArmCpu/` | 上游 BSD 驱动及 chiron 定时器适配 |
| `ci/sources.json` | 固定的上游源码提交 |
| `ci/` | 远程构建、集成与产物校验 |
| `tests/` | ADT 资源、NVRAM 和产物架构检查 |

大文件、运行时原始采集、设备标识、Apple 镜像与本地构建目录均不进入 Git。上游驱动的许可保存在 `drivers/ArmCpu/LICENSE`。
