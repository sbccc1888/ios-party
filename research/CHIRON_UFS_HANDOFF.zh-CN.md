# MSM8998 / Chiron UFS 板级交接：证据接口与源码缺口

日期：2026-10-22。任务 E 独立交付，主 agent 负责集成。未连接、查询或操作真实设备；没有执行 MMIO、启动 UFS 队列、修改控制器、写入存储、云构建或下载安装。

**当前结论：交接仍未合格，`verified=0`。** 已完成可复用的证据元数据核心、离线验证工具和源码审计。尚未实现可信板级生产者、供电/时钟/PHY 驱动、固件独占交接或 MSM8998 DMA 映射。本文不能作为实机 UFS 已接通的证明。

## 交付与边界

- `drivers/ChironUfsHandoff/HandoffEvidence.h`：C++14 freestanding 证据模型、资源绑定、启动/代次/阶段/时效检查、DMA 租约检查，以及始终不合格的旧 ABI 适配入口。
- `drivers/ChironUfsHandoff/tests/Core.cpp`：实际生产头文件的主机模型测试，同时用作 ARM64 编译入口。
- `tools/chiron_ufs_handoff.py`：仅处理已保存普通文件的源码清单、资源审计和 JSON 元数据检查。拒绝设备、FIFO、符号链接和重复 JSON 键。
- `tests/test_ufs_handoff.py`：13 项离线测试，包含 ASan/UBSan 主机执行、ARM64 freestanding 编译、逐项缺证据及错误启动身份/过期/错资源拒绝。
- `drivers/ChironUfsHandoff/source-audit.json`：本次本地文件哈希、相关源码行号、已采集 Android 资源、13 项未知事实和明确阻塞项。

既有 `drivers/ChironUfsReadonly/`、`ci/build_chiron_ufs_readonly.py` 和旧研究报告保持只读。本交付没有改 CI、启动流程、固件镜像或只读探针策略。头文件没有 MMIO、队列、UIC、SCSI、DMA/cache 操作入口；其初始化只复制已给出的普通内存对象。

## 已核对的本地来源

固定源声明是 `ci/sources.json` 的 edk2 `ace27110b9b9b6c591387d50cf748ec646402b4a` 和 UniXNU `7b52e7968c76453cdc18b5aa174fc368d5293b91`。本机完整快照在 `/private/tmp/ios-party-research/edk2`、`/private/tmp/ios-party-research/unixnu`，Linux 参考在 `research/upstream/linux-msm8998/` 及 `/private/tmp/ios-party-research/linux/`。

这些展开目录没有 `.git`；本工具记录实际读取文件的 SHA-256，**不把目录名、归档名前缀或哈希清单视为远端提交认证**。本地 `/private/tmp/ios-party-edk2.tar.gz` 的目录前缀为 `edk2-porting-edk2-msm8998-ace2711/`；所需 Common/edk2、edk2-platforms、simple-init 子模块内容未在该展开树中提供。

| 来源 | 本次实际读到的内容与限制 |
| --- | --- |
| `Platforms/Msm8998Binaries/chiron/dxe.inc:14` | Chiron 加载 common `UFSDxe.inf` |
| `Platforms/Msm8998Binaries/common/Binaries/UFSDxe/UFSDxe.inf:15` | `[Binaries.AARCH64]` 仅提供 DEPEX 和 `UFSDxe.efi`，不是 UFS C 源码 |
| 同目录 `UFSDxe.efi` | 94208 字节，SHA-256 `400e08d32f78cb9ebf4f5184ccf2e874ba75363b0d21b5c3e1b12d5f91a2a941`；本次仅保存文件身份，没有反汇编、运行或将其他版本 Qualcomm 开源实现当成此二进制的行为 |
| `drivers/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c:604` | `Root->Close(Root)` 在 EBS 前执行；关闭此文件句柄不能证明 UFSDxe 队列/事件/所有 BlockIo 使用者都已撤销 |
| 同文件 `:640` | 调用 `gBS->ExitBootServices(mImage, Key)`；成功后直接 `DarwinEnter`，未见 UFS 专用 drain、状态采集或 DMA 租约交接 |
| `Platforms/Msm8998FamilyPkg/Drivers/SynapticsRmi4Dxe/SynapticsTouchDxe.c:515` | 可读的退出回调关闭触摸 I2C 实例；`:535` 附近注册 EBS 事件。它不是 UFS 回调 |
| `Platforms/Msm8998ACPI/xiaomi/chiron/dsdt.asl:50` | UFS0 描述 `_CCA=0`、host `0x01da4000 / 0x14000`、INTID `0x129`；只是资源/策略描述，范围比运行时 DT 大 |
| 同文件 `:61017` 起 | PEP UFS 资源组出现 `VDD_UFS`、各 GCC clock 和 ICB 带宽资源；没有证明这些投票在本次 EBS 后被保留，也不是可直接使用的寄存器编程表 |
| `research/upstream/linux-msm8998/msm8998.dtsi:1753`、`:1796` | PHY `0x1da7000/0xda8`、host `0x1da4000/0x2500`，静态 host 8 时钟、1 lane |
| `research/upstream/linux-msm8998/chiron-msm8998-mtp.dtsi:61` | PHY L1/L2/L26，host GDSC/L20/L26/S4，host/PHY/ICE 启用 |

本地 Linux 参考只有 DTS/DTSI 子集，没有 `ufshcd.c`、`ufs-qcom.c`、QMP PHY 实现、MSM8998 GCC 驱动或 `msm-arm-smmu-8998.dtsi` 所包含的内容。因此不能从本次本地源码确定具体 clock CBCR/GDSC bit、PHY PCS 状态偏移、UFS stream/context 或 firmware exit 的寄存器操作；工具不猜测这些值，也未下载替代版本。

### EBS 回调是否关闭设备

**UFS 的答案仍是未知，不能声称“保持开启”或“必然关闭”。** 可读 DarwinBoot 没有显式 UFS 交接；可读触摸回调确实关闭 I2C；真正 UFSDxe 是二进制且没有本次运行中的 callback trace。这三项结论必须分别保留。

EBS 事件回调的执行本身不是 `ExitBootServices` 成功返回的证据。DarwinBoot 对 `EFI_INVALID_PARAMETER` 有重试路径；旧 callback 阶段记录和失败退出尝试不得被当成成功退出后的样本。未来需要对**实际装入镜像中的模块**确认回调注册和执行次序，并记录最后一次成功返回后的状态；其他 Qualcomm 驱动的同名函数不能替代这一证据。

## 资源绑定

工具复用旧工具的运行时 DT 解析，不解包不可信 tar 到磁盘，并额外核对 GCC/GDSC 范围、compatible 和 clock provider。原始采集 SHA-256 固定为 `08e76d50d5b513614203521be6f5eeaa40966e1e24a087ea582ee3fe6e9d7718`，还核对采集 manifest 的大小、返回码、哈希。

| 项目 | 新核心绑定 |
| --- | --- |
| host / PHY | `0x01da4000 / 0x2500`、`0x01da7000 / 0x0da8` |
| GCC / GDSC | `0x00100000 / 0xb0000`、`0x00175004 / 4` |
| SoC / revision | `292 / 0x00020001` |
| IRQ | 原始 SPI `265`、flags `0`、GIC INTID `297`，不把 flags 0 改写成已确认的触发方式 |
| lanes / host clocks | `2 / 9`，与采集一致，拒绝静态 DTS 的 1 lane/8 clocks |

ADT、DTS、ACPI 和 Android 采集只能约束资源。ACPI 的大范围、`_CCA`、缺失 `iommus`、时钟名称或 UEFI 曾成功读盘都不能成为交接资格。

## 每项实时证据的精确缺口

所有正向证据都必须来自同一启动、同一独占代次，在成功 EBS 返回后、进入探针前采集。后续若 reset、重映射、供电/时钟变化或所有权改变，必须使旧代次失效，即使还在时效窗口内也不能重用。

| 旧 Handoff 标志 | 新事实 | 必需可信板级证据及现缺口 |
| --- | --- | --- |
| `Power` | `power_retained` | 识别实际 GDSC、L20/L26/S4 及 PHY L1/L2/L26 的持有者，确认电源投票、相关互连/XPU 访问和安全 MMIO 条件在 EBS 后仍成立，并在探针期间持续持有。DT supply phandle 和 EBS 前状态不够；尚无 PMIC/RPM/GDSC 生产者 |
| `Clocks` | `clocks_retained` | 覆盖 9 个 host clock、PHY 的 ref/ref_aux 及共享父时钟/总线投票；需已审核的 MSM8998 provider 实现和 EBS 后实际 enable/status、有效 rate/parent、禁止后续 gate 的持有关系。尚无对应本地寄存器定义/驱动和运行证据 |
| `Link` | `phy_ready`、`link_active` | 确认 QMP v3 PHY 实际工作、lane 和 UniPro 状态、设备已 active 可读且无待执行唤醒/LinkStartup。UFSHCI HCE/HCS 是必要辅助，不足以单独证明 PHY、电源或长期访问安全；不发送 UIC/query/NOP 来“试一下” |
| `Exclusive` | `firmware_drained`、`submissions_blocked`、`interrupt_owned`、`firmware_dma_retired` | 在最后一次固件读盘后封闭所有提交者，记录无正在执行的 BlockIo/异步任务、事件回调/定时器及安全世界并发路径；可信地读回 transfer/task doorbell、错误/中断状态，并证明后续不再提交。IRQ 297 的注册/屏蔽/所有权也须明确，旧 UTRL/UTMRL 与数据 DMA 必须 retire 或继续保留。一次 doorbell=0、CPU IRQ mask 或关文件句柄不够 |
| `ExitBootServices` | `boot_services_exited` | 由成功返回分支生成本次 boot 身份和成功时间/代次，后续代码不调用 Boot Services/UFSDxe/BlockIo；callback 内样本不能冒充成功返回后的样本。现有跳转成功不提供完整 UFS 交接记录 |
| `DmaMapping` | `dma_route_known`、`dma_permissions`、`cache_contract`、`lease_retained` | 识别 MSM8998 当前 UFS 请求者 stream/context、所有翻译层或明确 bypass 依据、地址宽度及有效映射；证明本次专用 32 KiB 描述符/回应/数据缓冲既可被设备读也可被设备写，包含 cache 属性及 64 B 维护契约。未知 stream/context 保持未知，不套用 SM8550 的 `0x60` 或假设 VA=PA=IOVA。需要真实分配/映射 owner 和全启动生命周期持有对象；元数据地址/布尔声明不能证明它们 |

首次安全 MMIO 的前提必须在它之前建立。不能先读 HCS 再用读成功证明供电/时钟安全，也不能以软件超时代替总线不会卡死的前提。本任务不提供实际 MMIO reader；没有符合此前提的硬件条件。

## 可接入的内存接口

头文件 namespace 为 `chiron_ufs_handoff`，使用现有只读探针头文件中的 ABI 定义。

```cpp
Evidence begin(const Context &currentOwner);
Assessment assess(const Evidence &evidence, const Context &currentOwner);
chiron_ufs::Handoff unqualified(const Evidence &evidence);
```

`begin` 将 13 个事实全部设为 `Unknown`，不采集硬件。未来板级生产者可将审核后的样本写入 `Evidence.facts[]`；每个正向或失败事实都有完整 `Stamp { Identity { boot[16], generation }, ticks, phase }`，不能只改 packet 时间掩盖旧事实。

`Context` 必须由当前内核/loader 实际持有的 boot、单调时钟和 DMA owner 对象独立提供；不能从传入 Evidence 复制出来。boot ID 要在当前启动受信任路径建立且避免跨启动复用；本模块不生成 RNG、没有认证通道或安全世界证明。generation 要随任何影响交接的变化而递增。ticks 来自同一连续单调计数域，`maxAgeTicks` 由运行环境制定；墙钟、Unix 时间和不同 CPU/固件计数域不可直接混用。

校验包括精确资源、本次 boot/generation、`KernelPostEbs` 阶段、成功 EBS 的非零时间、未来/过期/早于 EBS 的时间，以及事实不能晚于封装它的 packet。结果区分 `Missing`、`Malformed`、`ResourceMismatch`、`WrongBoot`、`WrongPhase`、`Stale`、`InvalidLease`、`FailedObservation` 和 `MetadataConsistent`。

DMA `Lease` 包含 token、CPU VA、bus、完整长度、地址宽度、cache line、cached/read/write/retained 属性；与当前 owner 的独立租约逐项相同才通过。检查 4 KiB 对齐、至少 32768 B、整数溢出、设备地址宽度和证据/Context 不落入 DMA VA 范围。**这些检查只能拒绝错误元数据，不能证明真实页锁定、IOVA 连续性、SMMU 路由、物理别名或缓存一致性。**生产者仍需排除 Report/导出缓冲等别名和重叠，将结果复制到独立非 DMA 内存。

`Assessment` 的 `verified` 永远为 0，`hardwareExecutionAllowed` 永远为 false。即使主机模型的全部事实为 Observed，结果也只是 `MetadataConsistent`。`unqualified` 只给出旧 ABI 资源/缓冲视图，**始终 `verified=0`**；旧探针在入口拒绝。不存在 `--force-ready`、设置 Required 或自动调用探针的路径。

未来主 agent 需单独实现并审查真实生产者，再决定六个旧标志如何产生和消费。即便生产者补齐，进入探针之前也必须锁住所有权/资源代次，避免检查后状态变化。现有 DMA buffer 和 bus 映射从首次改队列开始必须保留到本次启动结束，包括成功、失败、超时；不可把租约对象或实际缓冲放栈上。新模块不负责分配、解除映射、停止、重试或回收。

## 工具用法

从仓库根目录运行，只读本地源/采集：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tools/chiron_ufs_handoff.py \
  --output drivers/ChironUfsHandoff/source-audit.json
```

完整快照位置变化时传 `--edk2 <本地目录>` 和 `--unixnu <本地目录>`，缺文件会标明 unavailable。不会联网取回缺件。仓库相对路径以外的 `/private/tmp` 来源只是本机审计输入，不是部署依赖。

验证已经导出的模型/候选证据：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tools/chiron_ufs_handoff.py \
  --evidence <已保存证据.json> --context <已保存上下文.json> \
  --output <普通报告文件.json>
```

JSON 结构由 `tests/test_ufs_handoff.py` 的 `model()` 展示；数字是整数，boot_id 为 16 字节非零小写 hex。该函数专用于主机模型，不代表硬件数据。facts 缺项或显式 `unknown` 会保留 unknown；`observed`、`failed` 都必须带合法 stamp。

结构错误返回退出码 2，并以 `status=rejected` 覆盖旧报告；缺事实/失败事实分别报告 `missing`/`failed_observation`。默认源码审计是 `blocked_no_trusted_board_producer`。退出码 0 仅表示报告已生成，不能用作就绪条件。**两个互相匹配的离线 JSON 仍是不可信声明，不能替代实时生产者，也不能生成任何可放行标志。**

## 验证结果

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_ufs_handoff.py -v
```

本次 13 项通过。覆盖 13 个事实各自缺失、未知、失败、不同 boot/generation、错误阶段、过旧/未来样本；15 个资源字段逐项篡改；EBS callback 和成功返回分离；DMA 权限、cache line、32 位越界、溢出、短缓冲、未持有生命周期和 token 不符；不可信输入与旧成功报告替换。

C++ 实际生产核心在主机 ASan/UBSan 下运行；全部资源字段篡改、证据与 DMA VA 重叠检查也执行。用其 `unqualified` 结果调用**既有** Probe 的主机模型，断言 `Unqualified`、所有 MMIO/cache/timer 回调计数为零、32768 B DMA 保持原字节。另以 `-target arm64-apple-darwin -ffreestanding -nostdinc++ -fno-exceptions -fno-rtti -Wall -Wextra -Werror` 编译核心成功。

没有编译/链接真实 XNU 包装层、没有生成新实机镜像、没有板级执行。这些离线结果不证明真实 UFS、缓存、DMA、IRQ 或链路就绪。

## 下一步必要输入

1. 对实际 UFSDxe/相关固件模块的退出行为建立可审计依据，补齐回调次序、请求封闭和旧 DMA 资源保留路径；若无法证明仍拒绝交接。
2. 补齐与实际 MSM8998 revision 匹配的 provider/PHY/SMMU 定义和可信访问前提，确定哪些状态可安全读取，哪些只能由现有所有者证明。
3. 由主 agent 在受控启动路径建立当前 boot/generation、成功 EBS 返回记录、独立 DMA lease owner 和永久持有对象，先让全 Unknown 路径明确拒绝。
4. 在后续**另行授权的实机实验**中，取得本表中的 EBS 后供电/时钟/PHY/link、固件 drain/exclusive/IRQ 和 DMA stream/context/映射证据。通过独立 RAM/已有诊断通道导出，不增加存储写入。
5. 只有可信生产者和真实证据经审核后，才考虑集成旧只读探针。当前没有任何 flag 或硬件条件可以直接放行。
