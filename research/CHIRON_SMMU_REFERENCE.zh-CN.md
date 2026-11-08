# A540 SMMU 参考实现：ArmSmmu4K

## 关键结论

**`UniXNU/ArmSmmu4K` 是 SMMUv2 驱动，直接适用于 A540。**

证据来自 `ArmSmmu4KProgram.h` 的头注释：

```
/* Byte offsets are relative to one real, owned SMMUv2 MMIO mapping. */
```

以及 API 形态：

- `ArmSmmu4KProgramDisabledContext(io, bank, config, poll_budget, result)`
- `ArmSmmu4KObserveOwnedContext(...)`
- `ArmSmmu4KSynchronizeOwnedContext(...)`
- `ArmSmmu4KSyncContextTlb(io, context_offset, asid, all, poll_budget, result)`

## 之前的状态：kit 全是空目录

六个 kit 目录在克隆时均未初始化 submodule：

| kit | 之前文件数 | 现在 |
|---|---|---|
| AdrenoFirmware | 0 | 16 |
| AdrenoPM4 | 0 | 19 |
| ArmSmmu4K | 0 | 28 |
| AirNir | 0 | 0 |
| VerifiedRoot | 0 | 0 |
| XnuPrelink | 0 | 0 |

`git submodule status` 每一项都有前导 `-`，即"已注册未初始化"。

**所以此前所有"参照 A740 实现"的说法，实际上参照的是文档和内核源码，不是可读的实现。**

## ArmSmmu4K 覆盖范围

3425 行，测试齐全：

| 文件 | 职责 |
|---|---|
| `ArmSmmu4KProgram.c` | 上下文编程（SCTLR/TTBR/ASID/TLBSYNC）|
| `ArmSmmu4KContext.c` | 上下文镜像与配置 |
| `ArmSmmu4KTableMemory.c` | 4K 页表内存 |
| `ArmSmmu4KStreams.c` | stream ID 匹配 |
| `ArmSmmu4KMmio.c` | MMIO 抽象 |
| `ArmSmmu4KLive.c` | 运行时更新 |
| `ArmSmmu4KAudit.c` | 一致性审计 |

测试：`test_tables` / `test_permissions` / `test_streams` / `test_context` /
`test_program` / `test_alias` / `test_live`

## 对 A540 移植的影响

**SMMU 层不需要写，需要接线。**

原计划把它当作"需要实现并在硬件上测试"的开源问题。实际上它是一个集成问题：
把 ArmSmmu4K 接到 GPU 的 stream ID 和上下文配置上。

出错面小得多，因为：
- SCTLR/TTBR/ASID/TLBSYNC 的时序已被测试覆盖
- 表格式是 ARM 标准 4K，不是 Qualcomm 私有
- ACTLR 的处理有明确的责任划分（调用者负责平台要求）

## 仍需确认的

1. A540 的 stream ID 具体值（需设备树或固件）
2. `CB_ACTLR = 0x70000000` 是否成立（探针 stage 5 会给出答案）
3. 上下文 bank 编号的分配
4. `ArmSmmu4KConfig` 的字段与 A540 参数的映射

## 复现

```bash
cd /root/hk/unixnu-src
git submodule update --init kits/ArmSmmu4K
git submodule update --init kits/AdrenoFirmware
git submodule update --init kits/AdrenoPM4
```
