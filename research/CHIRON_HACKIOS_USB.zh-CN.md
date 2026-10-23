# MIX2 HK2：复用 Qualcomm 原生 USB

## 2026-10-23 实机故障与边界

用户照片 `IMG_20261023_173316.jpg` 显示 HK1 已加载
19,338,576 字节载荷至 `0xf0000000`，loader 已取得
1080×2160 GOP，但返回：

```text
Existing EDK2 Fastboot transport unavailable: Not Found
USB transport unavailable; kernel not started
HK1 FAIL START LOADER
```

这是内核执行前的故障。已有云端 A53 桌面资格不能代替本次手机 USB、
内核或桌面运行验证。

## 接口差异

固定固件源码为 `edk2-porting/edk2-msm8998`
`ace27110b9b9b6c591387d50cf748ec646402b4a`。
其 chiron FDF 同时包含厂商 `UsbDeviceDxe` 和 EDK2
`FastbootTransportUsbDxe`，但两者不是同一种 USB Device ABI。

| 对象 | GUID |
| --- | --- |
| EDK2 Fastboot transport | `74bd9fe0-8902-11e3-b9d3-f72238fc9a31` |
| EDK2 USB Device | `021bd2ca-51d2-11e3-8e56-b75417c70b44` |
| Qualcomm USB Device | `d9d9ce48-44b8-4f49-8e3e-2a3b927dc6c1` |
| 厂商 UsbDeviceDxe 固件文件 | `3299a266-15f0-4346-8318-716336736d3e` |

核对 `tianocore/edk2` 固定提交
`e7aac7fc137e247edad22f7ee53b9a1fba227397` 的
`EmbeddedPkg/Drivers/AndroidFastbootTransportUsbDxe/FastbootTransportUsb.c`：
入口先查找 EDK2 USB Device 协议，成功后才安装 Fastboot transport。
因此仅显式启动这个模块，不能自动适配 Qualcomm 的不同协议。

对本地固定固件来源的厂商 `UsbDeviceDxe.efi` 作普通字节查找，
Qualcomm GUID 位于文件偏移 57504；未找到上述 EDK2 USB Device GUID。
该文件为 65,536 字节，SHA-256 为
`2e6050adb56cae921f133e772bb495077d8529781005a76de1b8a0b7aa967480`。
这只是静态线索，不能单凭 GUID 出现认定协议已在手机安装。
本会话没有连接 REA 工具或安装 REA CLI，未产生 REA Evidence；
上述结果来自普通文件检查。

同时读取 Project-Aloha/mu_aloha_platforms 的
`Platforms/QcomModulePkg/Include/Protocol/EFIUsbDevice.h`，
固定至提交 `99c155dfc4c00525a2af7d9f72f8bf3d8eddf280`，
头文件 SHA-256 为
`3dbe2ad2fad2ac4e76882c4870fb9a7e3a7a54309e0fbac089ec6c3cc3d3b146`，
核对 HackiOS 固定源码 `xnuloader/efi.h`：
Revision、Start、Send、HandleEvent、AllocateTransferBuffer、
FreeTransferBuffer、Stop、AbortXfer、SetEndpointStallState、StartEx
排列一致，描述符集合和传输完成数据结构一致。
此辅助头文件不能单独认证手机二进制 ABI；
运行时会记录实际 Revision 和 StartEx 返回状态。

## 本次改动

- HK2 入口先查询 Qualcomm USB Device 协议；仅在缺失时从固件卷显式
  加载并启动 `UsbDeviceDxe`，再次查询接口，并显示每一步状态。
- 移除 HK1 自建 Fastboot 代理，复用 HackiOS 的 Qualcomm USB 初始化事件、
  StartEx、专用 DMA 传输缓冲区和轮询收发路径。
- 保留 chiron 单核限制、`getvar:board` 和驻留根卷容量检查。
- 保留 HK1 图片及清单；新候选使用独立 HK2 文件名和清单。
- 另修复遗留工具测试发现的 URL 凭据脱敏遗漏：含敏感查询参数的 URL
  在命令参数分支中仍须清理 userinfo。

## 验证

本地已将新补丁应用于固定上游 loader 副本，确认上游 `usb_wait`、
`usb_rx`、`usb_tx` 字节保持一致，初始化事件与 DMA 缓冲区分配仍在。
这不模拟 USB 控制器，也不授予硬件资格。

HK2 首轮提交：`d69c579069275f2985c93d9139546ec9499b9f55`。
运行 `39934940474` 在入口编译时因缺少 PI 类型定义失败，未生成候选。
补充 `PiDxe.h` 后提交为 `060456ac82257fcc0ec5226bcdbf78a2f8404910`，
第二轮打包运行 `39935222049`，入口编译已通过。
本轮全部步骤成功，已取回并完成本地独立复核。

| HK2 对象 | 字节数 / SHA-256 |
| --- | --- |
| 启动镜像 | 26,697,728 / `80fa2a3c4fe521506596cfee70254d6f1fc7c2fbaa505ecf309d67d66d631dce` |
| EFI 入口 | 13,312 / `09919e1236b796450814dfa9eda9b22868e6ae8bbcff96fccedcd6be771e3f00` |
| 原生 USB loader | 69,632 / `2ea1c84490fbfb9fb8bacae85b3e69d8d5b1b0da692e8a2431056780394349b1` |
| loader 源码 | `82bcf25474ea6a539a4bd0f5551c2978c98659650381fbe02037ba513666e086` |
| 原系统卷 | 2,492,219,392 / `f781f6111ab3cd84a22dc2b385d2ceb51a706c16c913768e2ff3be26f2f65fa4` |

本机重新计算 legacy Android boot SHA-1 和镜像 SHA-256，
逐字节比较 HK1 与 HK2 的固件、末尾 DTB 和 HackiOS 载荷；
重新读取 RAM FAT 中的入口、loader、payload 和 HK2 菜单标题。
另从固定上游源码应用 DMA 补丁、chiron overlay 和本次 USB 补丁，
得到与云端记录完全一致的 loader 源码哈希。
现有系统卷长度和完整 SHA-256 已重新校验。

交付目录为 `dist/mix2-hackios/`：
`MIX2-HackiOS-HK2.img`、`manifest-hk2.json`、
`local-verification-hk2.json`、`START-HK2.zh-CN.md`。
本地清单只将镜像名改为交付名，并补充云运行/提交与原始清单哈希；
原始云产物保留在 `dist/mix2-hackios-hk2-build/`。
精简校验记录另存 `research/hackios-reuse/phone-usb-hk2-20261023.json`。
构建日志为 `out/chiron-port/package-hk2-39935222049.log`。

遗留 URL 凭据脱敏修复后，现有 48 项 HackiOS 工具测试全部通过。
本会话未执行手机启动、上传、刷写或重启，硬件资格保持 false。

手机的下一项验收是出现 `usb: started`，并由主机只读确认
`getvar product = xnuloader`、`getvar board = chiron`。
仅此两项不能证明 XNU 或 SpringBoard 已运行。

## HK2 实机结果与 HK3

用户照片 `IMG_20261023_180213.jpg` 确认：

```text
HK2: Qualcomm USB protocol: Success
usb: Qualcomm protocol revision 10000001
usb: start failed 800000000000000e
USB transport unavailable; kernel not started
```

上层 Qualcomm 协议存在，但 StartEx 返回 `EFI_NOT_FOUND`。
这推翻了“只找到上层协议即可开始枚举”的假设；仍未执行 XNU。
仅凭该状态不能区分底层 USB Function IO 查找失败和后续控制器操作失败。

对照 Qualcomm 参考源码镜像 `Rivko/android-firmware-qti-sdm670`
固定提交 `20bb8ae36c93fc16bbadda0e0a83f930c0c8a271`：

- `boot_images/QcomPkg/Drivers/UsbDeviceDxe/UsbDeviceDxe.c`：
  入口直接发布上层协议；StartEx 才调用 UsbDeviceOpenProtocols 查找
  USB Function IO，之后依次 StopController、StartController 和配置端点。
- `boot_images/QcomPkg/Drivers/UsbConfigDxe/UsbConfig.c`：
  初始化事件通过无返回值回调启动外设控制器，调用者不能取得内部失败状态。
- `boot_images/QcomPkg/Include/Protocol/EFIUsbInitProtocol.h`：
  提供带状态返回的 QueryCoreInfo 和 StartController；
  QueryCoreInfo 的 CoreNum 为 NULL 时查询默认 USB 端口。
- `boot_images/QcomPkg/Drivers/UsbInitDxe/UsbInitDxe.c`：
  USB Init 转调 USB Config，负责模式枚举转换。
- `boot_images/QcomPkg/SDM670Pkg/Library/UsbConfigLib/UsbConfigLib.c`：
  参考实现创建外设控制器句柄后调用 ConnectController。
- `boot_images/QcomPkg/Drivers/UsbfnDwc3Dxe/UsbfnDwc3.c`：
  DWC3 入口发布 Driver Binding，绑定控制器后才发布 USB Function IO。

这些文件是参考实现，并非声称已反编译并逐指令认证 MIX2 厂商二进制。
本地 MSM8998 固定厂商 UsbInitDxe.efi 在偏移 20576 可找到 USB Init
协议 GUID `2da15ac7-4909-4c4c-b8c3-a742195ea46e`，
UsbDeviceDxe 与 UsbfnDwc3Dxe 中均能找到标准 USB Function IO GUID
`32d2963a-fe5d-4f30-b633-6e5dc55803cc`。GUID 静态出现不等于实机已安装协议。

HK3 的改动：

- 分别检查 Qualcomm USB Device 和底层 USB Function IO。
- 底层已存在时复用现有控制器，不重复初始化。
- 底层缺失时取得 USB Init，并核对固定 DWC3 驱动的 Driver Binding；
  若厂商驱动尚未加载，只从原固件卷按固定文件 GUID 加载。
- 通过 Loaded Image 的固件文件路径识别已加载模块，避免重复加载同一驱动。
- 查询默认端口及支持模式，显式调用 StartController 的 peripheral 模式；
  显示返回状态，并再次检查 USB Function IO，失败时停在入口。
- chiron loader 跳过旧初始化事件，防止显式启动后再次重入；
  原生 StartEx、DMA 缓冲区、轮询收发、载荷和内核保持原有逻辑。

固定上游源码应用检查通过；原生 usb_wait、usb_rx、usb_tx 字节保持一致，
loader 身份、系统卷容量检查保留；现有 48 项 HackiOS 工具测试全部通过。
HK3 云端运行 `39937869418` 全部通过，源码提交
`2ef68f2e39270cf02f3028434914857ebca1c559`。
本轮只编译 EFI 入口及 loader 并重新打包，未重新编译内核或生成系统卷；
本地交付复核通过，仍须手机运行验证。

| HK3 对象 | 字节数 / SHA-256 |
| --- | --- |
| 启动镜像 | 26,697,728 / `24193a719c9cb1e9e322fa419816d65f2d1ed8dc25b588d3818705ce1aaeb477` |
| EFI 入口 | 15,360 / `41ba81b50a54782bd3135dbbbac2c9b1617082ded74415e71cc2bdd45bb934a3` |
| 原生 USB loader | 69,632 / `da23c4afc123858996562ed84291bcb2aa0abcaa20ed34aed7aa09fbad5ecec0` |
| loader 源码 | `43f524f0f450d3eac5798cc3fcb11f7151419f8fa83a069a56e8e1f03a204704` |

本机重新核对容器 SHA-256 和 legacy SHA-1，逐字节比较 HK2/HK3 的
固件、末尾 DTB 和 HackiOS 载荷；重新读取 RAM FAT 中的新入口、
loader、payload 和 HK3 标题。入口包含 USB Init 和 USB Function IO GUID。
从固定上游源码重放全部 loader 补丁，得到与云端完全一致的源码哈希。
HK3 系统卷清单与 HK2 一致，本轮未重复计算大卷哈希；
实际上传工具仍会检查系统卷完整哈希。

交付文件为 `dist/mix2-hackios/MIX2-HackiOS-HK3.img`、
`manifest-hk3.json`、`local-verification-hk3.json`、`START-HK3.zh-CN.md`。
原始云产物位于 `dist/mix2-hackios-hk3-build/`，构建日志位于
`out/chiron-port/package-hk3-39937869418.log`。
精简复核记录为 `research/hackios-reuse/phone-usb-hk3-20261023.json`。
本轮未执行手机启动、上传、刷写或重启；HK3 硬件资格保持 false。

## HK3 实机结果与 HK4

用户照片 `IMG_20261023_182618.jpg` 确认：

```text
USB Function IO before init: Not Found
USB Init protocol: Success
resident UsbfnDwc3Dxe: Success
DWC3 driver binding: Success
query default USB core: Success
USB core 0 supported modes 3
start peripheral controller: Success
USB Function IO after init: Not Found
```

入口停在 PREPARE QUALCOMM USB，载荷和 XNU 尚未执行。
现在可以排除上层协议缺失、USB Init 缺失、DWC3 驱动未加载等情况，
但仍不能把 StartController 返回成功当成底层驱动已绑定。

本轮用普通 LLVM 工具检查固定 MSM8998 厂商二进制，未运行 REA：

| 二进制 | 原始 SHA-256 |
| --- | --- |
| UsbConfigDxe.efi | `c81450d139adbb7bc1744a2dc21388e35dac7876913224d04b1c298c4f386d6f` |
| UsbfnDwc3Dxe.efi | `c54d0ea4b67bccfe2508549bd54e3197f1d2918b0376f0ea059fbb9ce3929f3e` |

LLVM 首次读取时拒绝厂商 PE 的 185 字节 debug directory。
`research/hackios-reuse/prepare_pe_analysis.py` 只在新的离线分析副本中
清零该目录的八字节引用；核对其余全部字节未变。
分析副本始终位于 `out/chiron-port/usb-analysis/`，不作为启动或打包输入。

UsbConfigDxe 协议表位于 RVA `0x11140`，StartController 包装入口为
`0x1a04`，转调实现 `0x4a24`。实现的 `0x4b8c–0x4b9c`
比较当前模式和目标模式；相等时返回成功，绕过后续
`0x4c50` 的外设 ConnectController 调用。
这证实了“重复启动成功但未重试连接”的静态路径，
不等于已观察到手机本次一定走了该分支。

DWC3 的 Driver Binding 表位于 `0x1c140`，Supported 为 `0x1548`。
实际指令读取 USB Config 的 CoreNum/ModeType 偏移 `0x88/0x8c`，
并要求目标为 SS device 模式 `4`。HK4 对这两个布局偏移增加编译期检查。
原始哈希、分析副本哈希、代码位置和观察边界保存在
`research/hackios-reuse/usb-binding-static-20261023.json`。

HK4 在 StartController 返回成功而 USB Function IO 仍缺失时：

- 枚举实际 USB Config 控制器，显示 revision、core 和当前模式。
- 仅对默认 core 的 SS peripheral 控制器调用已加载 DWC3 的 Supported。
- 支持时通过 UEFI ConnectController 优先连接该 DWC3 驱动，
  关闭递归连接，并在同一控制器上检查 USB Function IO。
- 失败时打印 Supported/ConnectController 状态及 BY_DRIVER 协议所有者，
  不移除其他驱动或直接修改私有模式状态。

HK4 源码提交为 `41cdd7cd4825d166050698bb165f40edf81a8059`。
只修改 EFI 入口及菜单版本，沿用 HK3 loader、载荷、内核和系统卷。

HK4 云端运行 `39940010772` 全部通过，本地独立复核通过：

| HK4 对象 | 字节数 / SHA-256 |
| --- | --- |
| 启动镜像 | 26,697,728 / `1c1ececd1b5214d3a0098b942513a335a2b0cc632d2d418fd845629caeda5c5a` |
| EFI 入口 | 16,384 / `f369d7e775d00094afc04b0995fe0c9e354a67c7dfedc9b61daf4b480f2dd5fa` |
| 重新链接的 loader | 69,632 / `46b5c55254d29a2e301542a4f74bed803caa78a9948cfa72b2bb3197a7910c2f` |

容器 SHA-256 和 legacy SHA-1、入口/loader/payload 的 FAT 读回通过。
固件、末尾 DTB 和载荷与 HK3 逐字节一致，系统卷清单相同。
loader 源码哈希与 HK3 相同；重新链接产生的二进制只在 COFF
时间戳字段有差异，将两份时间戳在内存中清零后其余字节完全一致。
这种归一化仅用于比较，没有修改交付的 loader。

交付位于 `dist/mix2-hackios/`：
`MIX2-HackiOS-HK4.img`、`manifest-hk4.json`、
`local-verification-hk4.json`、`START-HK4.zh-CN.md`。
原始包保留在 `dist/mix2-hackios-hk4-build/`，日志为
`out/chiron-port/package-hk4-39940010772.log`。
精简复核记录为 `research/hackios-reuse/phone-usb-hk4-20261023.json`。
本轮未操作手机，HK4 的实际 USB 枚举、内核执行和桌面仍待实机验证。

## HK4 实机结果与 HK5

用户照片 `IMG_20261023_184637.jpg` 确认请求启动外设后，实际配置仍为：

```text
USB cfg 0 revision 10003 core 1 mode 10000
USB cfg 1 revision 10003 core 0 mode 1
matching peripheral controllers: 0
```

core 0 的实际模式 `1` 是 XHCI host，`0x10000` 是无效模式。
HK4 没有找到 mode 4 的外设控制器，所以未调用 DWC3 Supported。
载荷、loader 和 XNU 仍未启动。

固定原厂 UsbConfigDxe 的 `0x4b2c–0x4b88` 会在静态配置启用时，
将调用者要求的模式替换为静态模式，再执行模式相等判断。
因此请求 peripheral 后仍返回成功且实际处于 host，符合该强制策略路径。
本机尚未直接读取运行中私有静态标志，不能把代码分支存在当作分支追踪。

另一个公开 API ConfigUsb 的 wrapper 为 RVA `0x1514`，实现为 `0x3990`。
它直接按传入的模式初始化原厂 USB core interface：
`0x3acc` 比较 mode 4，`0x3ae0` 调用 InitDevice；
该路径不执行上述 StartController 的静态模式替换。
原厂 DWC3 的 Start 本身就通过这个公开 API 配置控制器。

HK5 在确认只有默认端口的 host 配置、没有可绑定外设句柄时：

- 核对原厂 USB Config 模块文件 GUID、内存镜像尺寸 `0x13000`、
  revision `0x10003` 及三项公开回调的 RVA，拒绝不匹配的实例。
- 将完整公开 USB Config 表复制到 Boot Services 内存，
  保留原厂回调及尾部 VBUS 方法，只对新副本设置默认 core 和 mode 4。
  完整结构大小 `0xa0` 和 core/mode 偏移都有编译期检查。
- 经厂商 USB Init 的 StopController 停止当前 host 栈。
  停止失败时释放尚未注册的副本，并停在失败画面。
- 注册本次启动专用的 USB Config 句柄，让原厂 DWC3 执行
  Supported/ConnectController，并在同一新句柄检查 USB Function IO。
- 注册后的公开表保持驻留。副本所有回调均指向原厂常驻模块，
  因此入口返回时不会留下指向入口代码或栈的悬空回调。

此方案没有改变原厂私有静态策略字段，也没有写 NVRAM；
不修改原固件或内核指令。实际硬件角色切换仍待手机验证。

新增 `tests/test_chiron_usb_peripheral.py`：
直接提取生产代码中的 CreatePeripheralController 函数及协议类型，
使用宿主 C 编译器和 EFI doubles 运行 12 个情景。
3 项测试通过，覆盖身份不符、分配失败、停止 host 失败、注册失败、
驱动支持/连接失败、返回成功但底层协议缺失，以及真正取得协议的路径。
还核对停止 host 修改原对象时副本仍保持外设模式，
注册后的表不会被提前释放。这些检查不是 USB 硬件模拟或实机资格。

HK5 源码提交 `824d1768e84a7f3e8ff0990c8738435103192d42`，
云端运行 `39942820704` 已全部通过，本地独立复核通过。

| HK5 对象 | 字节数 / SHA-256 |
| --- | --- |
| 启动镜像 | 26,697,728 / `0c1a5835d1a696baef3f71c1e070316cb8fa3bb8517de90c4ce57df6f18bf7ac` |
| EFI 入口 | 17,920 / `379c28b883f2091d99a10fe90f366c0c50841c670041a34cbe8219b79352a083` |
| 重新链接的 loader | 69,632 / `b18cb9242eb4ff5f042595573e6cb71859c85035ae937cb5629827efb9ebb932` |

容器 SHA-256、legacy SHA-1 和新入口的 FAT 读回通过。
固件、末尾 DTB、载荷与 HK4 逐字节一致，系统卷清单相同。
loader 源码哈希相同，二进制仍仅在 COFF 时间戳有差异。
新入口包含 host 停止、固定 API 身份核对和专用外设句柄的阶段输出。

交付目录为 `dist/mix2-hackios/`：
`MIX2-HackiOS-HK5.img`、`manifest-hk5.json`、
`local-verification-hk5.json`、`START-HK5.zh-CN.md`。
原始包为 `dist/mix2-hackios-hk5-build/`，构建日志为
`out/chiron-port/package-hk5-39942820704.log`。
精简复核记录为 `research/hackios-reuse/phone-usb-hk5-20261023.json`。
本轮未操作手机，实际 USB 枚举和后续 XNU 执行仍待用户测试。
