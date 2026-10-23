# HackiOS 8796 + 20F66 云端验证

本记录对应用户要求的 C1 原组合验证。云端使用 Cortex-A76 QEMU、单核、7GiB RAM；
不包含 chiron 无 PAN/LSE 条件或手机启动资格。当前工作区的
`codex/chiron-source-trace` 与原有未提交内容保持原样；云任务位于独立分支
`codex/hackios-8796-20f66`。

## 输入与入口

- HackiOS kernel：`8e61187098f2c26c5daee8f9f72b6416aa64b9ab`。
- HackiOS userland：`906425b75e02b4f99eb5d7bb1ef7c9583472d772`。
- Apple XNU：`xnu-8796.121.2` / `c54f35ca767986246321eb901baf8f5ff7923f6a`。
- 目标：iPhone10,3 / iPhone10,6，iOS 16.5 / 20F66。
- 工作流：`.github/workflows/hackios-reference.yml`。
- 实现：`ci/hackios/cloud.py`、`build-kernel.sh`、`dependencies.lock.json`。

所有完整内核编译、Apple 系统文件下载、镜像生成及 QEMU 都限制在 Linux Actions。
本机只保存脚本、哈希、诊断日志和后续自建内核/截图，不下载 IPSW 或完整根卷。
内核不允许回退到上游 prebuilt；系统卷构建后另用 APFS C reader 读回 dyld/launchd
与已校验原件比较。`verification.json` 不从非空截图推导桌面成功。

## 已完成的云端观察

| 运行 | 提交 | 实际结果 |
| --- | --- | --- |
| [39793797768](https://github.com/to-mid/ios-party/actions/runs/39793797768) | `81695594ef2b135255d98677c61a77d66d78f45b` | 20F66 获取与身份/ZIP完整性检查完成；8796 源码补丁已应用；上游依赖脚本在 `exporthdrs` 阶段失败。未链接内核，未生成 APFS 系统卷，未运行 QEMU |
| [39884525307](https://github.com/to-mid/ios-party/actions/runs/39884525307) | `6ce83cad006b7bac4967d64e6182a5774bf9b85b` | 内层诊断确认 SETUP 宿主工具的 `hostcompat.h:44` 缺少 `uuid/uuid.h`。未链接内核或运行QEMU；20F66文件再次通过校验 |

首轮开始于 2026-10-22 14:13 UTC，结束于 14:45 UTC。
第二轮为 2026-10-23 01:32–01:39 UTC。
首轮外层日志没有保存 `xnu-deps/exporthdrs.log`；第二轮补充保存后定位为
Linux依赖缺少uuid开发头，修正是在工作流安装`uuid-dev`，没有改内核/用户态实现。
第二轮apt安装缩小到无GUI推荐依赖，并切换镜像，安装阶段由约28分钟降至约2分钟。

首轮原始产物保存在 `dist/hackios-reference-39793797768/`；
完整 Actions 文本日志为 `out/hackios-cloud/run-39793797768.log`。

## 20F66 实物身份已确认

数据来自首轮云端实际下载/解包，非 README 说明：

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| BuildManifest.plist | 449650 | `7dd041dc7ac05f2e1f041013024f9062b8166d916c708ff8b46aca5e089e091f` |
| OS DMG | 4728546953 | `64354ba3475d3c34d6fc531f75585a7b24af0fdb7a3a8a2bdf95c7f351a3f290` |
| OS cryptex DMG | 2726297600 | `2279bf443856fcb6a3f00b1fa06a32ba02a66fdf6ab6b12019462c7498aafdd3` |
| App cryptex DMG | 10485760 | `6b9d1f09e9b181cd4524df4cebe33be33000cb14bfd06ab4deb50590507a04e5` |
| `/usr/lib/dyld` | 922912 | `a453aa234fc9bd440dc4e55bd2e18f9a7eb2bc3fd7ef319d8714a664bc55bf5c` |
| `/sbin/launchd` | 525088 | `4c61512d19b9a55a6ddad27e23b88fc5008fbf77d0c0b5e6bdd7c7d90cef3e06` |

BuildManifest 的 ProductVersion/BuildVersion 分别为 16.5/20F66，支持上述两款身份；
三份 DMG 均出现在 manifest 中，并通过 IPSW 中央目录的长度与 CRC-32 对照。
dyld 与 launchd 均为 cputype `0x100000c`、subtype 0 的普通 arm64 Mach-O。
主 cache magic 为 `dyld_v1   arm64`；43 个非 symbols 运行时 cache 文件共
2,722,512,896 字节（约 2.536GiB），逐文件哈希在原始 `apple-inputs.json`。
这解决了 C1 的目标文件获取与身份缺口，尚不证明代码签名页、slide 或运行兼容。

首轮 Linux 工具链资产的 SHA-256 为
`a72a7a577e2fbe2838b6b5e9c72034fa7d114af96f0e1d4b016f18730ce4056e`；
上游资产未另提供 digest，此值来源于首轮实际下载，后续运行强制匹配。

## 待验收

下一步依次为：取得明确的内核构建诊断并完成源码链接；生成原配方两份 APFS 卷；
运行目标 dyld/cache、动态 hello 返回7、原版 launchd PID1、IOSurface 跨进程检查；
采集 backboardd/SpringBoard、IOMFB swap 和多时点屏幕/输入证据。

只有取得相应实际观察才更新通过项。当前没有 QEMU 运行、桌面或 MIX2 启动成功结论。
