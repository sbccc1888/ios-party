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
这只是静态线索，不能单凭 GUID 出现认定协议已在手机安装。
本会话没有连接 REA 工具或安装 REA CLI，未产生 REA Evidence；
上述结果来自普通文件检查。

同时读取 Project-Aloha/mu_aloha_platforms 的
`Platforms/QcomModulePkg/Include/Protocol/EFIUsbDevice.h`，
核对 HackiOS 固定源码 `xnuloader/efi.h`：
Revision、Start、Send、HandleEvent、AllocateTransferBuffer、
FreeTransferBuffer、Stop、AbortXfer、SetEndpointStallState、StartEx
排列一致，描述符集合和传输完成数据结构一致。
此头文件检查未锁定该辅助仓库提交，不能据此认证手机二进制 ABI；
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

HK2 源码提交：`d69c579069275f2985c93d9139546ec9499b9f55`。
云端打包运行：`39934940474`。构建及本地包校验结果在取回后补充。

手机的下一项验收是出现 `usb: started`，并由主机只读确认
`getvar product = xnuloader`、`getvar board = chiron`。
仅此两项不能证明 XNU 或 SpringBoard 已运行。
