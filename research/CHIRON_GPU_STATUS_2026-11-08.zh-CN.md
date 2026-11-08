# chiron A540 GPU 移植 — 完整状态（2026-11-08）

## 一句话

六层 A540 驱动已写完并通过单元测试，但没有一行在手机上跑过。
第 12 轮刷入探针后手机进入 UEFI 再也未返回，此后 14 轮全部阻塞在"需要按一次电源键"。

## 已完成且可验证

### 六层驱动 + 集成测试（`sbccc1888/ios-party` `codex/hackios-chiron`）

| 层 | 测试结果 | 关键内容 |
|---|---|---|
| fw2 解析 | ✅ all checks passed | legacy 容器，`data[0]` 是块大小非 header |
| GPUCC 上电 | ✅ all checks passed | 7 档频率（非 8），power level 编号方向相反 |
| GMU 握手 | ✅ all checks passed | `0xBABEFACE`，A540 走非 a530 分支 |
| SMMU v2 | ✅ all checks passed | ACTLR `0x70000000` 是 Qualcomm 唯一差异 |
| CP 微码 | ✅ all checks passed | 丢前 4 字节，`data[4]` 即 version |
| CP 环 | ✅ all checks passed | `RB_CNTL = 0x20C`，减半是四字单位 |
| 整链 | ✅ chain complete | 共享寄存器空间，层间无冲突 |

### 探针（`sbccc1888/Mu-Silicium` `chiron`）

7 个阶段，写 `oops` 分区：

```
stage 0a-0f   GPUCC 上电（唯一写入）
stage 1       GPUCC 寄存器
stage 2       GPU 状态（RBBM_STATUS 等）
stage 3       GMU
stage 4       CP 环
stage 5       SMMU
```

每步写盘后再继续，失败可定位。

## 硬件数据（实机采集，已交叉验证）

| 项 | 值 | 验证来源 |
|---|---|---|
| chipid | `0x05040001` | 设备树 |
| GPU 基址 | `0x05000000` (0x40000) | 设备树 + MemoryMapLib |
| SMMU 基址 | `0x05040000` (0x10000) | 同上 |
| GPUCC 基址 | `0x05065000` (0x9000) | 同上 |
| 频率档位 | 7 档，257–710 MHz | 设备树 + kgsl sysfs + 内核表 |
| GMU 版本 | 3.0 | 固件头 + 内核 gpulist |
| 固件 | `a540_gpmu.fw2` + `a530_pfp/pm4.fw` + `a540_zap` | `/vendor/firmware` 全部拉取 |

## 10 个已修正的错误（按发现顺序）

| # | 错误 | 后果 |
|---|---|---|
| 1 | `data[0]` 当 header size | 解析全失败 |
| 2 | GDSCR 地址在 GPU 块 | **返回错误值** |
| 3 | 频率表 8 档 | 180 MHz 不可用 |
| 4 | power level 方向 | **670 MHz 而非 257** |
| 5 | 关机而非重启 | 需人工干预 |
| 6 | GPU/SMMU 未映射 | **可能挂死** |
| 7 | 写 logfs FAT12 | **数据损坏** |
| 8 | 读不存在的寄存器 | **返回错误值** |
| 9 | 手写 GPT 解析 | 多一层出错机会 |
| 10 | 未上电就读 | **可能挂死** |

## 阻塞点

**手机从第 12 轮起需要按一次电源键。** 之后 14 轮全部无法验证。

主机侧已穷尽：`pnputil`、`Disable-PnpDevice` 全部因缺管理员权限失败；系统内无其他 USB 通路。

## 恢复后的第一步

```powershell
fastboot boot E:\ios-sweat\MuProbeC.img
```

然后等待：

1. **有输出** → 拍照或读 `adb shell "dd if=/dev/block/sda11 bs=4096 count=2048"`
2. **仍黑屏** → 说明问题不在 GPU，需要重新审视整个 UEFI 启动
3. **部分输出** → 日志会指出卡在哪个 stage

## 未完成

**软件层缺口：**
- SMMU 页表建立
- GMU 的 HFI 消息集
- ME 初始化包
- 命令提交与 fence

**实机侧全部未验证。**

## 诚实的评估

六层驱动的逻辑是扎实的：每一条都能对上内核源码，测试覆盖了边界与层间交互。但**它们是照着 Linux 内核写的，不是照着这台手机的硬件行为写的**。

第 2、4、8 类错误说明一件事：**只靠读源码，我能写出自洽但错误的东西。** 频率表的 power level 方向就是典型——单看表是对的，用起来会选错档位。

真机上验证一次的价值，超过再写三层。