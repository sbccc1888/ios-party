# HackiOS 8796 + 20F66 云端验证

本记录对应用户要求的 C1 原组合验证。云端使用 Cortex-A76 QEMU、单核、7GiB RAM；
不包含 chiron 无 PAN/LSE 条件或手机启动资格。当前工作区的
`codex/chiron-source-trace` 与原有未提交内容保持原样；云任务位于独立分支
`codex/hackios-8796-20f66`。

**最终结果：运行39894576642已通过，人工复核确认SpringBoard首页可见。**
370秒和680秒的1125×2436原始截图均有应用网格、Dock，状态栏和时钟持续变化。
原版dyld/cache、launchd PID1、init 36/36、IOSurface 80/80及持续IOMFB交换均有实际证据。
闭包保持固定8796＋20F66，另有一份1298字节的loader DMA等待补丁；不是未经修改的上游loader复现。

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
| [39888339946](https://github.com/to-mid/ios-party/actions/runs/39888339946) | `5fe0be57b51bb222692af98b0da6160bb7717136` | 源码内核/loader链接、目标身份、原配方用户态及两份APFS卷构建通过；卷内dyld/launchd读回通过。QEMU在内核执行前因缺少`efi-virtio.rom`退出1，没有截图或来宾运行里程碑 |
| [39890316014](https://github.com/to-mid/ios-party/actions/runs/39890316014) | `fbe62767fe35bd371501b44512e7053c95c97b85` | 已运行XNU、目标dyld/cache、原版launchd PID1及图形服务；36/36 init、80/80 IOSurface检查通过。1125×2436截图全黑，定位到loader的ramfb DMA状态处理；未取得桌面画面 |
| [39894576642](https://github.com/to-mid/ios-party/actions/runs/39894576642) | `e0722d9c3760349236de0a51f1a635298eff3853` | 全部云端步骤通过，原生framebuffer恢复；人工复核370秒、680秒截图为SpringBoard首页，完成720秒配置窗口 |

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
两轮独立获取产生完全相同的该清单，已保存为
`research/hackios-reuse/cloud-inputs-20F66.json`，不含Apple程序或镜像字节。
这解决了 C1 的目标文件获取与身份缺口，尚不证明代码签名页、slide 或运行兼容。

首轮 Linux 工具链资产的 SHA-256 为
`a72a7a577e2fbe2838b6b5e9c72034fa7d114af96f0e1d4b016f18730ce4056e`；
上游资产未另提供 digest，此值来源于首轮实际下载，后续运行强制匹配。

## 第三轮构建证据

原始构建包为`dist/hackios-reference-39888339946-build/`。
已在本机独立重算下载内核哈希、核对空的`kernel-undefined.txt`，
并核对APFS读回的dyld/launchd哈希与目标原件一致。

| 产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| 源码内核（普通arm64 Mach-O） | 15160872 | `0e5acecc038143e5ff7e71cc190a8cccd6461200df097d185248e2a569d7d4bd` |
| 本轮EFI loader | 68096 | `bf585c2c8762e1ac62009a2f8b476190aa032d2af6951d03f379e623309f9e9e` |
| 小APFS卷root.apfs | 4194304 | `cb4a241af55cdadefe42580c097f198807364ed555ce0f8a2f6ab375ef5c450f` |
| 系统APFS卷system.apfs | 2492219392 | `f781f6111ab3cd84a22dc2b385d2ceb51a706c16c913768e2ff3be26f2f65fa4` |

`stage0`、`init`、`hello_dyn`、`surf_test`、`libredwood.dylib`和
`libredwood-sb.dylib`均由本轮源码编译，哈希在`payloads.json`；
配方为`REST=1 SHARE=1`，没有启用GL或PREBUILT。
主`rc`保持原版`exec /sbin/launchd.ios`，demo job保持上游原始列表。

make原始退出码为2，完整日志保留：内核已在日志1710行链接，随后生成strip产物；
后续错误来自导出/符号集检查，本轮日志有222行`exported name not in import list`，
不是README所述217的简单转录。内核无未解析符号，但不据此认证任意外部kext ABI，
运行资格仍由实际QEMU结果决定。

第三轮`qemu-serial.log`只有
`qemu-system-aarch64: failed to find romfile "efi-virtio.rom"`；
`qemu-run.json`记载returncode=1、无输入/截图动作。
这是缩小apt依赖时漏掉`ipxe-qemu`的环境问题；修正为显式安装该包，
并在完整构建前检查QEMU能启动暂停的空虚拟机，不能将该环境检查记为XNU启动。

## 第四轮实际用户态与显示结果

原始记录为`dist/hackios-reference-39890316014/`。QEMU阶段为
2026-10-23 02:48:49–03:01:47 UTC，运行约778宿主秒；正常返回，日志保留到来宾约724秒。
本轮内核SHA-256为`6b862b18aa099297909a8fdf6fbb6d07124011c29f63fc939765bbf854934b23`，
系统APFS仍为上述`f781f611...`，小卷为
`9a2ef7eb49dd0981cb11802ef83f4b22045de8e44b73c379391d2cbb419385b6`。

- 实际`Hello from a dynamically linked program!`、返回7及父进程通过判断均出现。
- 自有init的36项检查为0失败；实际`surf_test`的80项检查为0失败。
- `ps`显示`1 ... /sbin/launchd.ios`；SpringBoard PID8、backboardd PID10，
  末尾`top:`仍显示这三个进程的实际CPU统计。
- IOMFB有持续交换和约9fps统计，约716来宾秒仍输出内存状态。
- 输入命令被QMP接受，但90–680宿主秒的8张1125×2436截图完全一致且全黑。
  此时不能报告桌面成功或输入产生了可见UI响应。

自动判定中另有一个匹配遗漏：SpringBoard真实路径为
`/System/Library/CoreServices/SpringBoard.app/`，`ps`末尾程序名被截断；
旧表达式只接受`/Applications/.../SpringBoard`完整名称。已修正路径匹配并接受
`top: ... SpringBoard[PID]`实际统计，同时要求至少两张原生尺寸非空画面，
避免30秒UEFI文字画面满足图形门槛。原始失败JSON保留。

黑屏的独立证据链：loader报告`ramfb: control 250018 after the request`后
仍发布旧GOP的800×600/`0x1fc7a0000`；内核IOMFB据此向800×600面板拷贝，
而QEMU截图已变为1125×2436，意味着显示设备已切到新的请求。
H `ramfb_phone_screen()`的DMA control为非volatile，仅在MMIO写后立刻检查一次，
非零即返回，因而没有发布新framebuffer。上游Linux
`drivers/firmware/qemu_fw_cfg.c` v6.8也使用READ_ONCE/屏障等待control完成。

最小修正为`ci/hackios/patches/0001-wait-for-ramfb-dma.patch`：
control按volatile读取，发布描述符前加屏障，最多等待1秒完成，再发布新SCREEN地址/尺寸。
不修改XNU、APFS、用户态或图形用户客户端。第五轮复用第四轮已校验的源码内核，
同时锁定其stripped/unstripped哈希及原构建来源，重新编译loader与用户态并重建卷；
这是复用本项目云端源码构建，不是回退到HackiOS上游prebuilt。

## 最终复核与产物

最终原始包为`dist/hackios-reference-39894576642/`，本机重算内核/loader/PNG哈希，
核对dyld/launchd的卷内读回哈希，并用当前完整判定函数回放原始日志与PNG，再次通过。
上一轮真实黑屏包在修正后的判定函数下仍被拒绝，没有改写其失败结果。

云端`verification.json`的`desktop_boot_qualified`刻意保持false，
因为自动像素检查不等于人工桌面识别；人工观察与其哈希绑定另记于
`local-verification.json`，其中桌面通过仅限`qemu_only=true`。
可提交的精简复核记录为`research/hackios-reuse/cloud-verification-20261023.json`。

| 最终对象 | SHA-256 / 观察 |
| --- | --- |
| 内核 | `6b862b18aa099297909a8fdf6fbb6d07124011c29f63fc939765bbf854934b23`，与第四轮相同，来自第四轮源码构建 |
| 系统APFS | `f781f6111ab3cd84a22dc2b385d2ceb51a706c16c913768e2ff3be26f2f65fa4`，与第四轮相同 |
| 小APFS | `586ebac488e5a7d1c5e07e85749c4274941be5e4f859427c0889209c4c8b2c98`，重建时motd改变；所记录六份程序/注入库与第四轮逐字节一致 |
| loader | `534efb4486b3758cc39a6e74a8ca01d7caf1149f46f59a7fb671f6ce0784f6bd`，68608字节 |
| loader补丁 | `c89f8a1aa81a95cd2d176acd02bd20ccc11eec10f00c2932c8e687aa6460c8e8` |
| 显示交接 | loader `1125×2436 @ 0x1fb9eb000`；IOMFB `1125×2436 on 1125×2436, rowBytes=4500` |
| QEMU | 正常返回0；765.8宿主秒；末尾仍有SpringBoard PID7、backboardd PID10、launchd.ios PID1的CPU统计 |
| 界面 | `screens/t0370.png`与`t0680.png`为可见首页，时间/时钟改变；黑色壁纸、Weather/Calendar小组件为空白 |

本轮init实际打印`memory 4192 MB, page size 16384, 1 cpu`，
APFS记录的单次pagein为32768字节、VM页16384字节。
这是H原组合发布的VM/用户页策略，不是chiron当前native4K ABI通过；
本轮没有独立取回TCR寄存器读数，不能以该打印替代硬件translation granule测量。

QMP操作均被接受，来宾virtio输入有记录；本轮没有逐项验收应用启动、完整导航、
网络、音频或安全服务，也没有MIX2手机操作。不能将此结果记为chiron CPU或实机通过。
启动前的`vgabios-ramfb.bin`缺失提示仍存在，但本轮可见桌面，未把该提示当作无画面的根因。

复现入口保留`.github/workflows/hackios-reference.yml`和`ci/hackios/`。
最终配置默认重新从固定源码编译完整内核；手动dispatch的`reuse_source_kernel=true`
才读取第四轮哈希锁定内核。复用仅限原artifact仍在保留期（7天）时，过期可使用默认全量路径，
不会悄悄回退到上游prebuilt。全量路径已在第四轮执行，精确哈希复用路径已在第五轮执行；
最后的可选入口调整没有改变本报告所绑定的`e0722d9c...`运行。
