# 首次 UEFI 探针照片记录

日期：2026-10-20。依据用户提供的 `IMG_20261020_204848.jpg` 原图，旋转并裁剪后人工转录。照片 SHA-256：

```text
0ad25265fca3f71287f7c05ca4f06a61293236df3d2dd8e4692bed008aeff6c6
```

原照片留在用户 Downloads 中，不上传到 Git。照片中探针文字叠在 UniXNU 菜单上，存在重影，但以下关键字段清楚。

## 寄存器

| 字段 | 读数 |
| --- | --- |
| CurrentEL | `0x4`，EL1 |
| MIDR_EL1 | `0x51af8014` |
| MPIDR_EL1 | `0x80000000` |
| ID_AA64MMFR0_EL1 | `0x1122` |
| ID_AA64MMFR1_EL1 | `0x0` |
| ID_AA64ISAR0_EL1 | `0x11120` |
| CNTFRQ_EL0 | 19,200,000 Hz |
| TGran4 | `0` |
| TGran16 | `0` |

这是启动 CPU 在 EL1 可见的原始寄存器，未通过 Linux 的 cpuinfo 缓存；未读取另一簇 CPU。

寄存器编码中 TGran4=0 表示支持 4 KiB，TGran16=0 表示不支持 16 KiB。这确认当前启动 CPU 必须采用 4 KiB 路线，不能原样使用 sweet 的 16 KiB 启动路径。

MMFR1 的 PAN 字段、ISAR0 的 Atomic 字段也为零，支持现有不依赖 PAN/LSE 的保守构建选择。该结论不扩展为另一簇 CPU 的实测结果。

编码交叉核对：现有 MSM8998 内核源码 `arch/arm64/include/asm/sysreg.h` 中 `ID_AA64MMFR0_TGRAN4_SUPPORTED=0`、`ID_AA64MMFR0_TGRAN16_NI=0`，PAN/Atomic 字段均位于 bit 20 起。

## GOP

| 字段 | 读数 |
| --- | --- |
| 分辨率 | 1080 × 2160 |
| PixelsPerScanLine / pitch | 1080 像素 |
| PixelFormat | `1` |
| FrameBufferBase | `0x9d400000` |
| FrameBufferSize | 9,331,200 字节 |
| 本模式每行字节数 | 4320 |

**UEFI 的 4320 字节行距与之前 Android 的 4352 字节行距不同。**加载器应使用本次 GOP 的真实参数，不能将 Android 值硬编码到 UEFI/XNU 视频交接。

## 内存与运行边界

探针报告共有 19 个 conventional memory 区域，只显示前 12 行；这不是完整内存图。照片中高地址区域包含 `0x100000000` 起点，但不据此将固定地址的 1 GiB 窗口标记为最终合格。

接下来的加载器必须从当时的 UEFI 内存图成功分配实际 owned memory，并验证 payload 的地址与存活期。

照片明确显示 `Chiron read-only probe`、寄存器输出和 `Press a key to return`，因此探针确实执行到了等待按键阶段；同时可见 UniXNU 图形菜单。红字 `no boot entry found` 对应当前没有可用的启动项/回退加载器，不能据此将探针执行判作失败。当前固件没有 XNU payload，尚无 XNU 内核入口或用户态运行的证据。
