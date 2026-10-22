# Chiron XNU 原生 UFS 独立只读诊断

日期：2026-10-22。范围：MSM8998 v2.1 / Chiron，现有 4 KiB 单核 RAM init 心跳基线之外的独立诊断选项。

当前交付包含可执行的 UFSHCI 单槽传输核心、XNU 入口源码、单翻译单元归档构建器、资源核对工具和离线故障注入测试。**尚未编译 XNU 入口归档、未链接到内核、未运行真实控制器；目前明确拒绝硬件执行。**本地只做驱动核心编译和主机测试，没有完整内核编译、云构建、设备命令、提交或推送。

## 父端最小集成接口

独立构建调用：

```sh
python3 ci/build_chiron_ufs_readonly.py --tree <已完成bootstrap编译的tree> --output <独立目录>
```

产物为 `libChironUfsReadonly.a`、`ChironUfsReadonly.o`、`compile.log`、`driver-report.json`。构建器复用现有 `build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOCPU.cpo.json` 的真实编译参数，只编译新增驱动，不执行内核构建。先验证固定 NativeUfs 源码的 SHA-256；允许父端既有 `prepare_runtime.py` 对构建脚本的适配。普通 ARM64 Mach-O 和 `_chiron_ufs_readonly_probe` 定义均需检查通过才生成报告。

独立镜像的链接参数增加 `-Wl,-force_load,<绝对路径>/libChironUfsReadonly.a`。入口声明位于 `drivers/ChironUfsReadonly/ChironUfsReadonly.h`：

```cpp
extern "C" int chiron_ufs_readonly_probe(
    const chiron_ufs::Handoff *, chiron_ufs::Report *);
```

由父端板级代码在 IOKit 可映射设备内存、时钟可读、交接已验证之后显式调用一次。没有自动 personality、存储设备发布或文件系统挂载。该归档无需启用 `CONFIG_XNU_NATIVE_UFS`；应继续保持原配置为 `n`，防止引入上游 SM8550 驱动。链接归档本身也不会执行入口。独立启动参数必须恰好解析为 `chiron-ufs-readonly=1`；不带此参数返回 `Disabled`，不碰 MMIO。现有 ram-trace 和下一版用户态自检镜像均无需改动。

调用方必须零初始化并保留独立的 `Report`；它不能与 DMA 缓冲重叠。`Handoff` 包含 ABI 1、下表资源，以及调用方专有的 32 KiB、4 KiB 对齐、普通 cached 内存 `dma`、对应连续设备总线地址 `dmaBus` 与长度 `dmaSize`。它不是仅有 CPU 物理地址的分配结果：必须证明 UFS 在当前 SMMU/互连状态下能够访问这段总线地址，包括读描述符和写回应/数据。CPU VA 和设备地址允许不同；不假设恒等映射。

`Handoff.verified` 的六项必须来自当前启动的实际板级资格实现，不能根据 Android DT、启动参数、地址、ADT 布尔字段或离线测试直接置位：

| 标志 | 板级资格义务 |
| --- | --- |
| `Power` | UFS GDSC、各电源轨在 ExitBootServices 后保持有效，相关 MMIO 可安全访问 |
| `Clocks` | 总线、接口、UniPro、参考及 lane 时钟有效，无访问时关钟风险 |
| `Link` | PHY 和链路可用，设备已处于可读状态，不需要探针执行唤醒或 LinkStartup |
| `DmaMapping` | 当前 UFS stream/context、地址宽度、DMA 映射、缓存属性及生命周期已验证 |
| `Exclusive` | 固件不再提交请求，XNU 独占控制器，无 IRQ/并发命令或 DMA 使用者 |
| `ExitBootServices` | UEFI Boot Services 已退出，后续路径没有调用 UFSDxe/BlockIo |

缺任何一项返回 `Unqualified`，在 MMIO 映射及 DMA 修改之前退出。资源不符返回 `ResourceMismatch`。XNU 包装层还检查 ADT 和 `CTR_EL0.DminLine == 4`（64 字节维护粒度），否则拒绝。**本仓库当前没有能满足这些条件的板级交接生产者，不能以填满标志位代替实现。**现阶段可用空 handoff 验证拒绝分支；该分支不证明 UFS 硬件就绪。

## 必需 ADT 字段

路径 `/arm-io/ufs-ro`。所有整数按本项目 ADT 生成器使用的小端编码；范围是特意命名的绝对物理地址，不使用普通 `arm-io/reg` 的相对地址语义。

| 属性 | 精确内容 |
| --- | --- |
| `compatible` | ASCII `chiron,ufs-readonly-v1`，含末尾 NUL |
| `host-physical-range` | 两个 LE64：`0x01da4000, 0x2500` |
| `phy-physical-range` | 两个 LE64：`0x01da7000, 0x0da8` |
| `qcom,msm-id` | 两个 LE32：`292, 0x00020001` |
| `lanes-per-direction` | 一个 LE32：`2` |

ADT 只绑定资源，不能证明硬件交接；没有任何 `*-ready=1` 属性可以放行。探针不访问 PHY、GCC、GDSC 或 SMMU 的寄存器。IRQ 只记录为资源，传输通过轮询完成，不注册中断。

## 已采集资源及上游差异

`research/artifacts/chiron-live-20261020/runtime-tree.tar` 的 SHA-256 为 `08e76d50d5b513614203521be6f5eeaa40966e1e24a087ea582ee3fe6e9d7718`，与同目录 capture manifest 一致。资源工具直接读取 tar 属性，不将不可信归档解压到文件系统。

| 资源 | 采集结果 |
| --- | --- |
| Host | `/soc/ufshc@1da4000`，`qcom,ufshc`，`0x01da4000 / 0x2500`，status `ok` |
| PHY | `/soc/ufsphy@1da7000`，`qcom,ufs-phy-qmp-v3`，`0x01da7000 / 0xda8` |
| IRQ | DT `<0 265 0>`，SPI 265 / GIC INTID 297；此处保留原始 flags 0 |
| GCC | `/soc/qcom,gcc@100000`，`qcom,gcc-8998-v2`，`0x00100000 / 0xb0000` |
| GDSC | `/soc/qcom,gdsc@175004`，`qcom,gdsc`，`0x00175004 / 4` |
| Host supplies | GDSC、RPM L20、RPM L26、RPM S4 |
| Reset | GCC phandle `0x3c`，specifier `6`；未解释成寄存器地址 |
| PHY supplies | L1、L2、L26（板级 DTS）；运行时 PHY 属性另有对应 phandle |
| Lanes / clocks | 2 条 lane，9 个 host clock specifier |
| DMA | Host 无 `iommus` 属性；不能据此认定无 SMMU 或已 bypass |

采集的 9 个时钟名称依次为 `core_clk`、`bus_aggr_clk`、`iface_clk`、`core_clk_unipro`、`core_clk_ice`、`ref_clk`、`tx_lane0_sync_clk`、`rx_lane0_sync_clk`、`rx_lane1_sync_clk`。`tools/chiron_ufs_readonly.py` 解析 provider phandle 并输出原始 specifier；这些旧 Qualcomm ID 不能直接当寄存器偏移使用。

本地 `research/upstream/linux-msm8998/msm8998.dtsi` 的同一 host 只有 1 条 lane、8 个时钟；`chiron-msm8998-mtp.dtsi` 启用 host/PHY/ICE 并绑定电源。运行时资源与静态 DTS 的差异已经验证，适配优先保留运行时证据。被屏蔽为全零的 `iomem.txt` 不是 MMIO 地址来源。

固定 UniXNU 为 `ci/sources.json` 的 `7b52e7968c76453cdc18b5aa174fc368d5293b91`。读取的本地快照没有 `.git`，因此 `upstream.json` 同时保存实际读取文件的哈希；父端现有固定源获取流程仍负责提交来源验证。

上游 `drivers/NativeUfs/NativeUfs.cpp` 匹配 `qcom,sm8550-ufshc`，固定 sheng host `0x01d84000`，并在 `GccUfs.h`、`SmmuTakeover.h` 使用 SM8550 板级资源。`hostBringUp()` 包含 SMMU 接管、设备 START STOP UNIT 唤醒，上游 `RootCommands.h` 还包含持久化 WRITE(10)/SYNCHRONIZE CACHE 路径。这些不能用于 Chiron。

保留的通用来源为 `RootTransportLayout.h`、`RootCommands.h`、`TransferPoll.h` 的描述符布局、应答校验、单槽轮询和超时隔离；本地重新实现独立白名单，没有编译或包含上游的写入代码。上游 `native-aes-personality.patch` 会在 `XNU_NATIVE_UFS` 时注册 `NativeUfs` platform/root personality，构建脚本 force-load `build/native-ufs/libNativeUfs.a`；本适配不复用这条自动启用路径。

UEFI UFSDxe 仅为固件阶段驱动。现有 UEFI 曾读过存储不等于上述交接已成立，本适配没有调用任何 UEFI 存储服务。

## 只读协议和超时边界

SCSI 入口按 opcode、LUN、CDB 长度、每个保留字节、数据方向、传输长度逐项核对；未知 CDB 一律拒绝，且拒绝发生在 MMIO/cache/DMA 操作之前。

| 白名单 | 限定 |
| --- | --- |
| TEST UNIT READY `00` | 6 字节全零 CDB，无数据 |
| REQUEST SENSE `03` | 固定 96 字节输入，不接受 descriptor/附加参数 |
| INQUIRY `12` | 标准 36 字节输入，EVPD/page 均为 0 |
| READ CAPACITY(10) `25` | 固定 8 字节输入 |
| READ CAPACITY(16) `9e/10` | 固定 32 字节输入，其他 service action 拒绝 |
| REPORT LUNS `a0` | LUN 0，固定 80 字节输入，最多 8 个普通 LUN |
| READ(10) `28` | 仅 LUN 0、4096 字节块、LBA 0–5，长度与块数完全相等 |

数据方向只有无数据和 device-to-host。WRITE、FORMAT、UNMAP、MODE SELECT、SANITIZE、START STOP UNIT、SYNCHRONIZE CACHE、安全/RPMB 命令、well-known LUN 均不可达。无任意 UPIU/query/task-management 提交 API；内部只有 NOP OUT 与受检 SCSI COMMAND。没有读原文件系统、挂载、分区发布或写存储的路径。

在设备交接已证明后，传输按顺序进行：检查 host ready/无 outstanding transfer/task/无 fatal；屏蔽 host IRQ；停止 UTRL；更换 UTRL base；启动 UTRL；NOP；REPORT LUNS；每个报告 LUN 的 INQUIRY 和容量；LUN 0 容量匹配后一次读取已采集的六块。

上述 IE、UTRL run/stop、base、completion W1C 和 doorbell 写入是**控制器运行状态写入**，不是存储数据写入；仍有硬件运行影响，故受全部交接条件约束。不会复位 host、启动链路、写 UIC/PHY/时钟/电源/SMMU、唤醒设备或尝试命令重发。设备未就绪时直接停止并报告 SCSI/sense。

停止队列最多 100 ms，单条命令最多 1 s；均另有固定轮询次数上限，即时钟冻结也会返回。零 doorbell 且 OCS 仍为初始 15 不算完成。检查 OCS、UPIU type/tag/LUN、EHS、长度、sense 与 residual，传输错误即禁止后续提交。最多 8 LUN 的整次探针也具有有限命令数。轮询超时无法中断一次已经卡死的总线读，因此**MMIO 可访问的电源/时钟资格不能用软件超时替代**。

从首次修改队列开始，DMA 缓冲及其 bus 映射必须保留到整次启动结束；成功也必须保留，因为控制器仍引用新队列。超时后不取消、不 reset、不释放或重新使用；XNU 包装层不提供 stop/unload/retry。`Report.quarantined` 表示必须保留 DMA，`queueTouched` 表示已修改队列。控制器映射由包装层永久保留；DMA/IOVA 的持有对象仍归父端板级代码负责，不能用栈内存或临时描述符。

## 已知块验收

Android 已采集 `sda-lun.txt` 为 LUN 0、4096 字节 logical/physical block、236109824 个 512 字节 sysfs sector，换算 29513728 个 4096 字节块。它是与现有 GPT/路径对照得到的 LUN 0 预期，探针仍需重新枚举核实，不假设所有 LUN 容量相同。

`sda-primary-gpt.bin` 为 24576 字节，SHA-256 为 `03e9e4ec8e6b971911b3c39bdb714f1401cf5541ea179502f42d806900d5bfe5`。探针检查：

- LUN 0 last LBA `29513727`、block bytes `4096`。
- 主 GPT revision `0x10000`、header size `92`、current LBA 1、backup LBA `29513727`。
- entries LBA 2、32 项、每项 128 字节。
- header CRC `2578770494`、entries CRC `1352948940`，以及重新计算的两项 CRC。

成功后六块原始数据仍位于 `Handoff.dma + chiron_ufs::Data`，长 `chiron_ufs::DataMax`；父端可以复制到独立 RAM 证据缓冲，经现有诊断通道导出。不能为导出证据而增加原机存储写入。驱动核心核对的是 GPT header/entries，不声称 MBR/padding/备用 GPT 已验证。

资源工具支持对**已经导出的普通文件**逐字节比较完整六块：

```sh
python3 tools/chiron_ufs_readonly.py --output <本地报告.json>
python3 tools/chiron_ufs_readonly.py --compare-gpt <保存的六块.bin> --output <对照报告.json>
```

拒绝设备文件；报告始终保留 `hardware_execution_allowed=false`。文件内容吻合不认证其来源，不自动成为实机证明。

## 验证与剩余阻塞

```sh
python3 -m unittest discover -s tests -p test_ufs_readonly.py -v
```

当前 8 项测试通过：真实采集资源/三类资源篡改、已知 GPT 与破坏数据对照、普通文件限制、编译记录重写、freestanding ARM64 核心编译、独立只读策略测试，以及带 ASan/UBSan 的实际生产传输核心故障注入。传输模型覆盖 18 种情况：成功、READ CAPACITY(16) 回退、transfer/task busy、前置/执行中 fatal、doorbell 超时、冻结时钟、零 DB/陈旧 OCS、错误 UPIU type/tag/LUN、SCSI sense、短容量回应、错误几何、破坏 GPT、队列停止超时、base 写回失败。

`Transport.cpp` 的 `--policy-only` 模式不打开 GPT 文件，只使用空 fixture 和全零 DMA 缓冲验证白名单、逐项缺资格拒绝、错误资源拒绝及未启动传输时的命令拒绝。断言 MMIO read/write、doorbell、cache 回调次数均为零，并逐字节检查 DMA 未修改；该模式在载入真实 GPT 和进入 18 种传输模型之前明确返回，不制造 GPT 成功证据。新增 `test_host_policy_without_capture` 不设 capture skip，Linux CI 即使没有私有资源也必须编译并执行这一部分。

真实 GPT 的 18 种传输测试仍由独立测试调用 `<saved-gpt.bin>` 模式；缺失私有 capture 时，仅相关传输和资源测试明确 skip，不用伪造数据替代成功。策略测试可单独运行：

```sh
python3 -m unittest discover -s tests -p test_ufs_readonly.py -k test_host_policy_without_capture -v
```

ARM64 编译只实例化生产协议核心；主机模型替代 MMIO/cache/时钟，不证明 XNU API 链接、DMA coherency、MSM8998 或实机资格。本机没有 bootstrap `IOCPU.cpo.json` 和内核头闭包，XNU 包装层归档需由父端云构建验证，当前不能标为已编译成功。

仍缺的实证：

1. 当前 ExitBootServices 后 UFS power/clock/PHY/link 状态与安全 MMIO 访问条件。
2. MSM8998 UFS 的真实 DMA stream/context 和当前固件 SMMU 状态；缺 `iommus` 不是 bypass 证明，不能套用 SM8550 stream `0x60` 或上下文。
3. 父端提供专用 DMA 分配、有效 bus 映射及整次启动生命周期持有。
4. 固件请求清空、host 独占及无中断竞争的交接证据。
5. XNU 单驱动归档云编译/链接结果；然后才是用户操作设备得到的枚举、容量及 GPT 原始数据。

本交付没有实现完整冷启动的 GCC/PMIC/PHY/SMMU 驱动，不能声称 NativeUFS 已接通。它提供的实际可接入路径是：父端验证交接并传入 `Handoff` 后，使用 XNU 自己的 MMIO/cache/timer 运行通用 UFSHCI 请求；条件不成立时在入口拒绝。
