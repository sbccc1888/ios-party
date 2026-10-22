# Chiron 到 iOS 桌面：HackiOS / ios-sweet 复用评估

日期：2026-10-22（Asia/Shanghai）。本轮已转向复用，停止扩展自有 RAMFS 和一般自检。
交付范围是固定源码对照及可复现的源码/宿主验证入口，不是新的启动镜像。
没有下载 IPSW/Apple 系统镜像，没有在本机全量编译内核或运行 QEMU，没有操作手机。

## 1. 路线结论

**优先把 HackiOS 8796.121.2 + iPhone X iOS 16.5 / 20F66 作为独立的桌面验证候选，
先在云端复现其原组合，再做 chiron 的最小平台差异。保留当前 10063 实机成功路线，
作为平台证据和按模块回迁的后备。** 这是按源码耦合关系得出的工程选择，
不是“8796 已能在 MIX 2 启动”的结论。

原因是 HackiOS 已一起实现 APFS 只读根、可写 ramfs、stage0→init→Apple launchd、
选定 daemon 配置、entitlement/MAC 替代接口、IOSurface、IOMFB 和软件显示。
先把这些拆开迁到 10063，会同时引入内核代际接口迁移和用户态适配两个变量。
但整套 8796 仍须处理 **PAN、页大小策略、内存窗口、loader 平台识别和根卷传输**，
不能只添加设备名后就启动。

| 对比项 | A：8796 + 20F66 整套适配 chiron | B：必要源码迁入当前 10063 |
| --- | --- | --- |
| 保留的实现 | HackiOS 的用户态、文件系统、图形、启动配方作为一组 | 当前已实机成功的 4KiB、GIC/时钟、UEFI/DarwinBoot、日志、源密码学/pthread |
| 新增工作集中处 | Cortex-A53 级能力、chiron ADT/GOP/内存/MPIDR、UEFI 接口与大根卷 | APFS/ramfs 的 VFS 集成、miniamfi/MAC/AKS、exec 注入、图形和平台初始化接口 |
| 页策略 | 原组合是 4KiB 硬件页表与可能 16KiB 的 VM/用户 ABI，须单独资格 | 当前强制 native 4KiB VM/用户 ABI；不能假定所有 20F66 cache 直接符合 |
| 补丁实测 | 对固定原始 8796：`git apply --check` 成功 | 对固定原始 10063：29 个文件拒绝；对本地补丁后快照也失败 |
| 图形风险 | 可先保持与 20F66 配套的 selector 和注入库 | 用户 ABI 可保持 20F66，但内核 C++、注册、内存/端口桥需重新集成 |
| 到桌面的短路 | 云端原组合 → 较旧 CPU 条件 → chiron 平台层 → 同一用户态/软件显示 | 先迁文件系统及必要服务闭包，再接 Apple dyld/launchd/显示；不是只迁一个 FS |
| 采用条件 | 先取得原组合云端结果，再取得无 PAN、4KiB granule 条件下的真实运行证据 | A 的平台/loader 代价明显更大，或已有 10063 目标用户态闭包更适合时再采用 |

本轮的“补丁拒绝”只证明不能原样套用，不量化移植难度，也不证明某个模块无法迁移。
不会用强行忽略 hunk 失败、把 8796 `.a` 链入 10063、或复用另一版闭源 kext 来替代源码移植。

## 2. 固定输入与现有成果保护

已通过远端 `ls-remote` 和本地 Git 对象核对以下提交。代码分析使用这些对象，
而非浮动 main 或 README 的功能宣传：

| 简称 | 仓库 / 固定提交 |
| --- | --- |
| H | [hackios-xnu-kernel](https://github.com/oosexgang/hackios-xnu-kernel/tree/8e61187098f2c26c5daee8f9f72b6416aa64b9ab)，`8e61187098f2c26c5daee8f9f72b6416aa64b9ab` |
| U | [hackios-xnu-userland](https://github.com/oosexgang/hackios-xnu-userland/tree/906425b75e02b4f99eb5d7bb1ef7c9583472d772)，`906425b75e02b4f99eb5d7bb1ef7c9583472d772` |
| S | [ios-sweet](https://github.com/dipa-studio/ios-sweet/tree/067c9b3d463ecfbe37520683637ea2e6af31f3a4)，`067c9b3d463ecfbe37520683637ea2e6af31f3a4` |
| X16 | Apple XNU `xnu-8796.121.2`，`c54f35ca767986246321eb901baf8f5ff7923f6a`，由 H `xnu/apply.sh:16–18,36` 固定并检查 |
| X17 | Apple XNU `xnu-10063.141.1`，`d8b80295118ef25ac3a784134bcf95cd8e88109f`，当前 `ci/sources.json` 固定 |

H 的真实入口是 `docs/PORTING.md`、`xnu/README.md`、`xnu/redwood.patch`、
`xnu/files/`、`xnuloader/`、`common/`、`xnu-build.sh`。已实际阅读。
S 的 `AGENTS.md` 要求先读 `NOTES.md`；已阅读其中交接、dyld/cache/cryptex、
图形/USB 和静态 init SIGBUS 记录，并定位下文补丁。
S 的历史设备授权和写入操作只属于 sweet，不转移到 chiron。

当前项目分支仍为 `codex/chiron-source-trace`，HEAD 为
`e9ccfc7e7354076eb3fac8e0a763b49ddf732d22`。
额外保存了 `codex/preserved-before-hackios-reuse-20261022` 指向该 HEAD；
本轮开始时的 tracked/index 二进制 diff 及所有非忽略 untracked 文件归档在
`out/hackios-reuse-preserved/`。没有 stash、reset、清理或切换当前工作树。
已有 README、RUNTIME_STATUS、SIGBUS/自检文档及用户态/根卷审计的未提交成果保持原样。
`dist/` 内已有镜像与 `foundation-39782971298` 均未改动。

区分三个基线：实机成功记录是 `CT-39770335838-1-775F3F2E4009` 的静态 PID 1
与四项自检；`39782971298` 的新 RAMFS/ABI 是另一个云端 QEMU 资格；
本轮 HackiOS 复用检查又是独立源码/宿主结果。三者不能互相继承运行资格。

## 3. 4KiB 页表、VM 页与用户 ABI

**不能把 HackiOS 当作仅支持 16KiB，也不能把它当作当前 native 4KiB ABI 的等价实现。**

| 层次 | 核对到的具体代码 | 对 chiron 的含义 |
| --- | --- | --- |
| 硬件页表 | H BCM2837 配置没有新增 `__ARM_16K_PG__`；X16 `osfmk/arm64/proc_reg.h:1149–1154` 未定义该宏时 `ARM_PGSHIFT=12`。H patch `:2122–2138` 扩大 bootstrap 表到 20 个硬件页，注释明确针对 4K、最大 8GB 映射预算 | 存在 4KiB translation 路径；表容量修正可以复用，但不证明当前编译产物或全量内存访问已合格 |
| 内核 VM 页 | X16 `osfmk/arm/arm_init.c:395–426` 在 memSize >1GiB 时发布 `PAGE_SHIFT_CONST=PAGE_MAX_SHIFT`；H 对该文件的修改没有替换这个判断 | 大根卷时 VM 页可能为 16KiB；这与 TCR 的硬件 granule 是两个量 |
| 用户 map / commpage | X16 `bsd/kern/mach_loader.c:510–516` 的 64 位 map、`osfmk/arm/commpage/commpage.c:178–181` 的 64 位字段仍为 16KiB；H patch 没有改这两个文件 | 原组合保留这种逻辑页 ABI。不能仅看 TGran16 不支持就宣布用户态不可能运行，也不能仅看 4KiB bootstrap 就宣布 ABI 与当前一致 |
| 当前 native 4KiB | UniXNU `patches/xnu/native-4k-user-abi.patch` 分别改 arm_init、mach_loader、commpage、vm_shared_region；当前补丁后快照也逐项核对 | B 路线保留这些修正；A 若选择移入这些修正，须承认这是另一种 ABI 候选，重新检查 cache 和页入 |
| 签名页 | CodeDirectory 哈希粒度独立于上述页大小；S NOTES 记录所用 iOS16 文件为 4KiB 签名页，且 16KiB 内核的 multilevel 转换影响 entitlement 挂接，修正在 `0035-sweet-amfi-entitlements-platform-binaries.patch` | 这条记录指导检查，不能代替本轮缺失的 20F66 文件。当前可选 16K→4K 签名桥接也不能自动解决 AMFI 或 cache slide |
| cache slide | X16 `vm_shared_region.h:98` 的 `PAGE_SIZE_FOR_SR_SLIDE=4096`，v2/v3/v4 sanity check 对照这个值；H patch 未改 pager。X17 还有 v5/16KiB 路径 | 16KiB 段对齐本身不是拒绝理由；必须拿实际 cache 的 slide version/page_size 验证。当前 native4K 对 16K slide 的跨度问题见既有 4K 装载审计，不能只放宽 sanity check |

最短资格方法是先保持 H 原有页策略，在云端较旧 CPU 模型上记录实际
TCR.TG0/TG1、ARM_PGSHIFT、PAGE_SHIFT_CONST、task map、commpage，以及目标 dyld/cache
映射和缺页结果；如需改 native4K，单独形成候选，不能混入同一次“复现”。
本轮只确认源码条件，没有为混合逻辑页大小的所有路径授予运行资格。

chiron 探针记录 MMFR0=`0x1122`、TGran4=0、TGran16=0：
按现有项目记录，启动 CPU 可用 4KiB、不支持 16KiB granule。证据仅覆盖启动 CPU。
S `0004-sweet-machine-config.patch:249–250` 则显式启用 PAN 和 16K 硬件页，
其 shim、`tools/mkpayload.py` 与 `qemu-sweet.py` 也按 0x4000 布局；
**不能把 sweet 的内核和 shim 二进制直接用于 chiron。**

## 4. CPU、MPIDR、内存和加载器差异

| 条件 | HackiOS / ios-sweet 实际假设 | chiron 差异与最小处理 |
| --- | --- | --- |
| PAN | H patch `makedefs/MakeInc.def` 增加 `+pan`，`BCM2837.h` 强制 `__ARM_PAN_AVAILABLE__=1`；S 也启用 PAN | chiron MMFR1=0，当前配置明确关闭 PAN。A 必须同步移除该编译 feature 与配置宏，检查 copyin/out、异常入口，不能只把非法指令当成一般 panic |
| LSE / PAC / 其他 ISA | H 默认 QEMU `cortex-a76`，redwood 为 A55/A78；S 为 A55/A76。H 用户态使用普通 arm64 编译，但编译选项不证明 Apple 二进制最低 ISA | chiron ISAR0 Atomic 字段为 0；没有本轮 ISAR1/PAC 读数。以 armv8-a、无 PAN/LSE 的 CPU 条件验证内核；对 dyld/libSystem/cache 实物另查 arm64/arm64e、可达原子操作与 auth fixup。不能伪报能力位 |
| MPIDR | H loader `struct platform.core_shift` 用 `core << shift` 生成枚举；redwood shift=8，QEMU shift=0 | chiron 实测拓扑为 `[0,1,2,3,256,257,258,259]`，任一单一 shift 都无法表达全部 8 核。首轮只放启动核；后续将 loader 改为显式 MPIDR 表 |
| 内核 SMP | H `start.s` patch 用 Aff0+Aff1；`gic_v3_irq.c:152–158` 构造 SGI，`:398–412` 按 GICR_TYPER 寻找 affinity；`psci_smp.c:163–189` 用 cpu_phys_id 调 CPU_ON | 这些内核算法比 loader 的枚举更通用，可复用；仍需正确 ADT、GICR 范围、SMC、逻辑/物理 ID。S 0065/0078 的 SMP 不作为本轮前置 |
| GIC / timer | H redwood GICD=`0x17a00000`，GICR=`0x17a60000`；QEMU timer 与手机不同 | chiron GICD 相同但 GICR=`0x17b00000`，步长 `0x20000`；虚拟 timer INTID=19、19.2MHz、PSCI SMC，沿用本项目已有测量，不抄 redwood/S 的 GICR |
| 屏幕识别 | H `xnuloader/loader.c:532–568` 按 GOP fb_base 匹配平台，不匹配就走 QEMU ramfb | chiron GOP `0x9d400000`、1080×2160、rowBytes=4320、format=1；必须加入明确平台匹配及错误拒绝，避免误选 QEMU |
| CPU 渲染 | H MiniIOMFB 从 IOSurface 拷贝/缩放到 Boot_Video；S 也采用 CPU 软件显示 | 优先复用这条链路。chiron Adreno 540 不是 H 642L 或 S 618；GPU、Wi-Fi、声音不是首次桌面的前置 |
| 触摸 | H redwood Goodix SPI；S Goodix GENI 等 SM7150 驱动 | chiron 为 Synaptics DSX、I2C bus5/0x20、reset GPIO89、IRQ125，不能复用控制器地址和面板协议；可复用 HID/事件接口，真实可操作桌面仍需接入触摸 |

### 内存预算与大根卷

H loader `:26–35,2672–2741,2787–2824` 从 UEFI **一个**
EfiConventionalMemory 描述符选最大对齐窗口，32MiB 对齐，最多 6GiB，整段 AllocatePages。
resident 根卷置于窗口 `+128MiB`，上传上限为窗口大小减 128MiB 再减 768MiB；
执行前还检查 kernel、小 RAMDisk、resident 镜像和 ADT/top 不重叠。
`MAX_MEM_SIZE=6GB` 不是“所有 6GB 手机都能分配到 6GB”的保证。

当前 chiron ADT 候选窗口为 `0x100000000 + 1GiB`；DarwinBoot 的 WindowSize parser
可接受更大的数值，但实际仍需 AllocatePages 找到单一连续区。
Android 捕获的 bank 为低段 `0x40000000 + 3GiB` 和高段约 2.96GiB，
另有 firmware、ramoops、XPU 等保留区；现有 UEFI 照片是不完整内存图。
**不能把两 bank 相加当作可用连续根卷预算。**
把 H 所述约 2.5GB `system.apfs` 放进高 bank 后，还要有 wired kernel、
page tables、APFS cache、可写 var、daemon 和图形 surface 的余量；
是否够用须以实物镜像长度与最终内存图计算，不能从宣传大小给出通过结论。

更具体的源码预算：若仅用高 bank，且乐观地假定全段均为可分配内存，
`0xbd8c0000` 字节经 H 的32MiB向下对齐后窗口至多为 `0xbc000000`；
减去 resident 偏移 `0x08000000` 和内核运行预留 `0x30000000`，
**resident 上传上限只有 `0x84000000` = 2,214,592,512 字节 = 2.0625GiB**。
配方所述约2.5GB系统卷高于此上限；最终UEFI若再分割窗口，只会更小。
这不是声称实物20F66镜像已测得该大小，而是一个不能省略的候选淘汰条件。
要沿用大RAM根，必须证明存在更大的实际连续窗口或镜像已精简到预算内；
否则优先评估驻留UEFI的只读BlockI/O，不能继续靠扩大零尾部。

| 路径 | 多 GB 数据如何进入 / 被读取 | 当前不能直接继承的条件 |
| --- | --- | --- |
| 当前 Android boot v0 → FAT → DarwinBoot | 已有镜像只物理传约 24MiB，入口补到 48MiB；零尾部不携带额外有效数据；当前窗口 1GiB，root.img 被读入并复制到 owned window | 48MiB 非零传输曾失败，根因未完全定位；不能外推 bootloader 的多 GB 下载或放置能力。扩大镜像长度/补零不等于传输了 cache |
| H `xl ramdisk` + resident root | 小 UEFI/loader 先运行，之后 `xl:211–233` 通过自有 USB fastboot 协议上传系统卷；`loader.c:3657–3686` 分块复制，完成后才发布 resident_rd_size | 这是进入 loader 后的新 USB 服务，不是原 bootloader 已支持多 GB。依赖可用的 Qualcomm EFI USB device 协议、连续窗口及生命周期 |
| H UEFI Block I/O 服务 | `redwood_ufs.c` 通过共享请求页让另一核上的 resident loader 调 UEFI ReadBlocks；不直接编程 UFSHCI | 需要 UEFI 不退出、两侧核/缓存/共享页协议以及可读 Block I/O。当前 EBS 后的 ChironUFSHandoff 不能用这份服务直接替代；本轮不写分区 |
| S 内部 HFS+ 根 | `0028/0030/0034/0069` 从 UFSHCI 读取持久根；不把 cache 塞在 boot ramdisk | SM7150 地址、DMA/PHY、GPT 和持久写入布局不适用于 MSM8998；S 的 768MiB bootloader 上限不是 chiron 的实测上限 |
| 云端 QEMU | H `qemu/run-ios.sh` 用稀疏 RAM 文件把系统卷放在 0xFC000000，guest 默认7GiB、内核窗口约4.2GB；文件后端避免把镜像再读入 Python | 与手机实际连续区不同。QEMU 显示/启动成功不能认证手机的根卷容量或 USB/UFS 服务 |

### UEFI 契约

H `docs/PORTING.md` 建议移植 Mu-Silicium 并修改 boot shim / BootManager；
当前 chiron 已有 edk2-msm8998 固定版本实机路径，应优先评估在该固件里加载
HackiOS EFI 应用所需的 GOP、内存、USB 与 Block I/O 协议，缺什么再补，
不先重做整套 UEFI。
`common/` 配置传给打包器的 PAYLOAD_BASE/FD_BASE 也不是任意重定位开关：
`xnuloader/loader.c:18` 仍固定 PAYLOAD_BASE=`0xF0000000`，
`mkxnuimg.py`、shim、固件保留区及loader必须一致，不能只改设备conf文件。

尤其要读实现：H `xnuloader/loader.c` 开头仍写“exits boot services”，但
`:574` 起的实际约定为 **Boot services are never exited**，保留现场、内核返回与轮询服务；
`efi.h` 有 ExitBootServices 函数指针不代表调用过它。
当前 DarwinBoot 则确实在交接前 ExitBootServices。
不能让已 EBS 的内核继续调用 UEFI Block I/O，也不能保留 H 返回代码却不保留 UEFI
内存/栈/上下文。loader header 的 16/32MiB 对齐常量也不能直接用来判定 TCR granule。

## 5. 可复用代码清单

“直接复用”指保留并使用现有源码实现，而不是宣称跨版本不改即可编译运行。

| 功能 | 已定位实现 / 连接点 | 复用范围与须保留的边界 |
| --- | --- | --- |
| APFS reader | H `xnu/files/bsd/miscfs/apfs/{apfs_format.h,apfs_lib.[ch],apfs_deflate.c,apfs_zbitmap.c}` 及随附 libdeflate/libzbitmap | 格式解析、校验、目录、extent/hole、压缩读取可原样使用。支持未加密 live tree，不是 Apple APFS kext，也不提供完整 snapshot/encryption/认证实现 |
| APFS VFS | 同目录 `apfs_vfsops.c`、`apfs_vnops.c`、`apfs.h`；H patch 的 bsd/conf/files、config/MASTER、vfs_conf | root mount、目录/符号链接/xattr、UBC/pagein 已实现。`:737–805` 按 PAGE_MASK 检查、页尾清零。迁到10063要同时处理注册、vnode/UPL/锁与 redwood backend/内存统计依赖 |
| APFS 写入边界 | `apfs_write.[ch]`、`apfs_store.[ch]` | 有受限原地写既有文件/数据存储逻辑；挂载设备默认不提供 write，不等于通用可写 APFS。首次桌面沿用只读 system + RAM var |
| 可写 ramfs | H `bsd/miscfs/ramfs/ramfs.c`，H patch 注册项；`:1672–1709` 列出 VNOP，`:1731` 限额为 max_mem/4 的页数 | 已有目录、文件、hardlink/symlink、socket、xattr、mmap/pagein/pageout 和 read/write 缓存同步。不继续扩展自有 CHRAMFS；B 若采用应引入这份实现并隔离配置 |
| 镜像构建 | U `tools/apfs/{mkapfs.py,apfs.py,apfs-tool.c,build-tool.sh,check-image.sh}`；`ios165/{select-system.py,mkvarskel.py}` | 保留 manifest 的 mode/uid/gid/link 和压缩处理；与 H reader 配套。APFS block=4096 不等于 VM 页已为4096 |
| dyld / cryptex 布局 | U `ios165/fetch.sh`、`userland/build.sh:711–827` | 20F66、iPhone10,3/6、43个非 symbols cache 文件是配方中的具体约束；原 launchd 移为 launchd.ios，添加 OS/App cryptex。不能替换成另一 iOS 构建的 cache 而继续使用相同注入地址 |
| PID1 交接 | U `userland/src/stage0.c` → `src/init.c` → `rc.launchd` 的 `exec /sbin/launchd.ios`；`libmini/start.S` | stage0 挂 md1 到 /redwood，init 挂可写 var、配置注入和 jobs，再 exec 原 launchd。初始两个自有程序本身不等于 Apple PID1 成功 |
| 动态闭包验证 | U `src/hello_dyn.c`、`rc.ios`、`src/svc_test.c`、`src/surf_test.c` | 现成动态小程序/服务/图形接口检查可复用，不再另写一套一般 syscall fixture；必须实际走目标 dyld/cache 后才有对应资格 |
| daemon 适配 | U `src/inject.c`、`inject_sb.c`、`inject_media.c`、`mkjobs.py`、var 偏好；H kern_exec 的 exec_inject | 复用 CPU 渲染、输入/显示适配和服务裁剪。`inject.c:335,543` 有20F66 cache 固定地址及检查，绝非任意版本通用 shim |
| entitlement / MAC / AKS | H `libkern/amfi/miniamfi*`、`bsd/kern/kern_minimac.c`、`MiniAppleKeyStore.cpp`；S `0035/0036/0038/0050` | 这是研究启动的替代服务，包含允许策略、签名 entitlement 读取、ad-hoc platform 标志与 AKS 回答；不等于 Apple 信任链/SEP。当前10063“空 entitlement / 有权限拒绝”路径不能支撑这套 daemon 闭包 |
| IOSurface | H `iokit/Kernel/MiniIOSurface.[h,cpp]`，通过 GenericARMPE 初始化 | 有 per-task mapping、共享状态、跨进程 Mach port、create/lookup/lock/trap；`fillLockResult` 对已映射 surface 返回 `~0ULL`。保留 selector、结构体长度和生命周期 |
| IOMFB | H `MiniIOMobileFramebuffer.cpp` + loader ADT display 节点；`GenericARMPE.cpp:302–304` | IOMobileFramebuffer/AppleCLCD、交换、vsync async 通知、CPU 拷贝/缩放。GOP 文本 console 不提供这些用户客户端 |
| 软件显示参考 | S `0046`、`0056`、`0061`、`0067`、`0068`、`0079` | 0046提供IOSurface/IOMFB；0056节流present；0061逻辑显示尺寸；0067静默console；0068修正已映射lookup；0079电源请求。保留后续修正，不只抄0046 |
| HID | S `0058-sweet-hid-event-services.patch`（IOHIDFamily-1915.140.3服务/队列/客户端），`0060/0062/0063/0071` | 复用用户 ABI 和事件派发经验；chiron另接Synaptics/PMIC生产者，不能复制Goodix MMIO |
| 日志/异常 | H `osfmk/arm64/{loader_return.c,user_exception_trace.c}`、H patch 的 systrace/regtrace、`qemu/daemonlog.sh`、`xl log` | 可直接复用解码/筛选；内核返回需要完整驻留loader契约。QEMU日志有重放，事件按pid/时间去重，不能简单grep行数当启动数量 |
| S USB/持久日志 | `0043/0044/0047/0048/0049/0054/0066`，USB `0072–0077/0081`，`tools/{sweet-log.py,sweet-log-pull.sh,sweet-usbnet.sh}` | 0077在USB ECM上每100ms发UDP/5140，依赖0072–0076的DWC3/PHY/ifnet；地址须适配。0047会写userdata尾部，pull虽只读也依赖此前写入，本轮不接入/执行 |
| QEMU入口 | H `qemu/{run.sh,run-ios.sh,screendump.sh,touch.py,sym.py}`；S `tools/qemu-sweet.py` | H可复用UEFI、ramfb、输入、截图与日志；原run.sh固定A76，不能当无PAN/LSE验证。S为另一payload格式SWEETXNU且默认16K，不替代H的XNUP或当前UniXNU格式 |

H 源码到 10063 的具体依赖例子：`apfs_vfsops.c` 直接 include
`redwood_input.h`、`redwood_virtio_blk.h` 并调用 redwood backend/统计接口；
MiniIOSurface 调用 `IOUserClient::copyPortNameForObjectInTask` /
`copyObjectForPortNameInTask`，这两个 API 在固定 X17 `IOUserClient.h` 仍存在，
但存在声明不等于所有调用约定、内存管理或实例注册已合格。
这类连接点应逐模块用云端真实 XNU 编译参数检查，而不是重新实现 APFS 或表面桩函数。

## 6. ios-sweet 的已知坑与本项目应用

| 实际记录与补丁 | 对本次路线的直接作用 |
| --- | --- |
| S NOTES `:183–194` 附近：2.5GiB cache 不适合 boot ramdisk；HFS init 首行后 SIGBUS，定位到首个使用 SP 的 store；`tools/sweet-init.S:21–32` 已在入口对齐SP | 当前同类问题已有修复和记录，保留，不再把SIGBUS统称根卷错误或重复做一轮无关自检。H `libmini/start.S` 也保存原argc指针后对齐SP |
| NOTES `:164–174`：`ignition_level=0`连cache阶段也跳过；=1才找system根的cache，跳过Preboot/cryptex探测 | 复用真实启动参数与路径知识，不能只放 `/usr/lib/dyld`。S配方的cache文件数44与H20F66的43分别属于各自目标 |
| NOTES `:118–126`：libobjc-trampolines只在OS cryptex，低ignition下根路径缺失；`tools/mkiosroot.sh`做cryptex缺件合入 | 对照U配方逐项核对cache之外的库与App cryptex，不只复制dyld+主cache |
| `0035` entitlement、`0037` bindfs、`0038` AKS、`0040/0045` watchdog、`0050` Sandbox、`0052` rename swap、`0057` ext resolver、`0064` work interval、`0070` pid_hibernate | 桌面不是FS挂上即可：需保留服务权限、容器ACL、keybag、watchdog和前后台切换契约；优先复用现有补丁，按实际errno/异常决定是否迁入 |
| NOTES 图形ABI段：IOSurface sel13输出0x18、ABI尾字段；create/lookup输出0xa68；IOMFB显示发现依赖`/arm-io` display-subsystem和matching service | 云端先用现有surf_test核对per-task映射与跨进程传递，再看backboardd实际交换和vsync，避免把framebuffer刷色当iOS显示 |
| NOTES `0068`：lookup已映射surface应返回base=-1；`0070/0071`：后台切换/边缘触摸 | 首屏之后也需验收交互，保留这些后续修正；不能以一张Hello屏宣称可用桌面 |
| NOTES `:21` 的2026-10-18桌面记录 | 是上游sweet设备的记录；本轮没有其原始日志/照片，也不是chiron结果。只能作为复用线索，不移植为MIX2启动证据 |

## 7. 本轮已完成的可复现验证

入口：`research/hackios-reuse/verify_sources.py`；
固定源：同目录 `sources.lock.json`；
本轮结果：同目录 `verification-20261022.json`。
只需要 Python3、Git、宿主 C 编译器、zlib、OpenSSL 开发库（可由 pkg-config 定位）。
不会启动上游 fetch.sh、common/build.sh、xl、QEMU、fastboot 或 adb。

本轮命令：

```sh
python3 -B research/hackios-reuse/verify_sources.py \
  --cache /private/tmp/chiron-hackios-reuse \
  --current-xnu /private/tmp/chiron-console-source \
  --output out/hackios-reuse/verification.json
```

在新的本地或云端源码缓存复现：

```sh
python3 -B research/hackios-reuse/verify_sources.py --fetch \
  --output out/hackios-reuse/verification.json
```

`--fetch` 只拉五个固定源码仓库/提交，不取子模块、IPSW、SDK或完整内核构建依赖。
已有缓存要求HEAD精确匹配且干净，不覆盖工作树；
补丁检查使用临时index，不修改Apple源码。缺源/错提交/脏树/验证错误非零退出，
并写入本次失败JSON，不留下同一路径的旧通过结果冒充本轮通过。
`--current-xnu` 为可选的本地补丁后快照对照，只记录内容哈希，不把无.git快照认证为完整固定源。

实际验证内容：

1. 固定提交、关键文件的 Git 对象与磁盘字节一致，并记录SHA-256清单，含S全部现有patch。
2. H `redwood.patch` 对X16完整可应用；对X17记录29个拒绝文件及完整诊断。
   本地当前补丁后XNU快照另有拒绝诊断和5份页ABI相关源码哈希。
3. 直接编译上游`fmt_check.c`，其119个字段偏移、19个结构大小与Python reader一致。
4. 原有Python sparse reader 9例通过；`test_fetch.c` 包含真实`apfs_lib.c`，
   在宿主执行9例hole、非整块、跨extent读取，全部通过。
5. 用上游mkapfs建立4MiB自有APFS镜像，Python reader及真实C reader的宿主构建
   分别全量读回70001字节模式文件及125000字节压缩文件，与原字节一致。
   没有Apple二进制或内核VFS/pagein运行。

上游`run-tests.sh`的小型C读取用例在此固定版本没有把新增zbitmap和crypto依赖完整链接；
本入口显式链接随附`apfs_zbitmap.c`及OpenSSL，未改上游代码或伪造解码/哈希函数。
宿主构建不验证内核锁、UPL提交、EL0缺页、可执行映射或真实卷格式全覆盖；
Unicode、LZBITMAP实物与Apple完整根卷不在此次自有fixture范围内。

结果中的`source_checks_passed`、`host_checks_passed`为true；
`kernel_built`、`qemu_executed`、`hardware_observed`、
`apple_target_binaries_verified`、`desktop_boot_qualified`均为false。
另用独立空源码目录验证缺源入口确实退出1，并保持所有源码/宿主/启动通过标志为false。

## 8. 下一轮最短云端验证顺序

以下是待执行验收，不是本轮已有通过项。完整内核编译与QEMU均留在云端。

| 阶段 | 最小输入与动作 | 必须保存的结果 / 停止条件 |
| --- | --- | --- |
| C0 固定源 | 先运行本报告入口；独立候选目录，使用H/U固定提交，不覆盖当前10063树 | 源码Git/SHA清单、补丁诊断、宿主APFS读回。此阶段已完成 |
| C1 H原组合参考 | 云端按H `xnu/apply.sh`、依赖和`xnu-build.sh`构建8796；目标文件到位后，按U原配方生成20F66根及小卷，用H `qemu/run-ios.sh`原A76条件跑 | 每次绑定kernel/root/system/配置/日志/屏幕哈希。构建脚本make的导出检查可能非零，不能单靠已有kernel文件判断成功，须核对本轮链接产物、未解析符号和构建日志 |
| C2 chiron CPU准入 | 从C1派生无PAN、保守ISA候选，将云端QEMU命令CPU改A53级，保持单核并记录实际页策略；先原组合，再按证据决定是否改native4K ABI | 目标dyld/cache页入、动态hello进入main、退出；launchd接管PID1。A76通过而此步失败时先定位PAN/LSE/页映射，不扩展自有自检 |
| C3 软件图形 | 同一用户态及现成IOSurface/IOMFB/注入库，使用ramfb，复用surf_test与QEMU截图/触摸脚本 | 服务匹配、surface跨task数据一致、交换/vsync、backboardd和SpringBoard的PID/映像身份、连续非空屏幕、实际触摸/导航结果 |
| C4 chiron平台候选 | 仅在独立树移入真实GICR/timer/GOP/显式MPIDR/内存描述；优先保留现有edk2-msm8998，验证HackiOSEFI所需协议；先小APFS载荷和明确内存预算 | 云端编译/仿真日志及可审查产物；手机测试仍由用户执行，本轮不启动/重启/刷写 |
| C5 大根卷资格 | 取得最终UEFI内存图与实际系统卷长度；比较RAM上传可行性和驻留UEFI只读BlockI/O方案 | 非重叠且有运行余量的分配、传输长度与整卷哈希、实际文件/pagein读回。两者均不成立时明确阻塞，不把零填充、GPT读取或离线verified标志当存储通过 |

C1/C2里的Apple二进制缺失会阻塞“真实动态用户态/完整启动”验收，
不会阻塞目前已经完成的源码固定、补丁兼容、CPU/ABI差异定位和APFS复用验证。
待补输入是明确的20F66 dyld/launchd/cache/subcache及必要系统/cryptex文件清单和哈希，
并非要求本机先下载完整IPSW。S的20H392配方是另一个独立参考，不混用其库或固定地址。

MIX2桌面成功最终必须有匹配产物的**本机SpringBoard实际画面及输入响应**，
并有身份/日志证明链路经过Apple用户态、IOSurface和IOMFB。
README陈述、宿主工具通过、fastboot接受、静态PID1心跳、QEMU桌面或sweet照片，
均不能单独写成这个结论。
