# Chiron 最小用户态自检

## 状态与边界

2026-10-22：步骤 2 的源码、独立签名 Mach-O 构建、容器及指令级测试已实现，云运行 `39756701933` 已通过真实 XNU/QEMU 执行及完整交付核验。
既有 MIX 2 实机证据是旧版 init 的 PID 1 / 4 KiB / 多轮心跳；
新增自检已在 `IMG_20261022_180358.jpg` 中出现 PID 1 SIGBUS，尚无实机通过证据。
入口栈对齐修正和下一轮观测见 [SIGBUS 排查](CHIRON_SIGBUS_DEBUG.zh-CN.md)。
本文件不把宿主机模拟的系统调用响应算作 XNU 运行通过。
父端负责云端内核构建、QEMU 资格测试、ADT 和最终打包；设备由用户操作。

普通编译 `scripts/chiron-init.S` 保持原心跳行为。
自检构建显式启用 `CHIRON_RAM_SELFTEST=1`，并包含
`scripts/chiron-selftest-main.S`。成功条件仍包含真实 `getpid()==1`、
`sysctl(HW_PAGESIZE)==4096`、控制台完整写入和两轮 `select` 定时等待。
`_results`、`_user_page_size`、`_old_length`、`_chiron_heartbeats`、
`_chiron_init_status` 和 `_checkpoint` 保留兼容性。

## 固定源码依据

项目 `ci/sources.json` 固定 UniXNU
`7b52e7968c76453cdc18b5aa174fc368d5293b91` 与 XNU
`d8b80295118ef25ac3a784134bcf95cd8e88109f`（xnu-10063.141.1）。
本次读取 `/private/tmp/ios-party-research/unixnu` 和
`/private/tmp/chiron-console-source`，后者含已应用的固定 mockfs 补丁。
这些目录是源码快照，没有 `.git`，不能用本地 git HEAD 再次证明其来源；
构建入口另对实际使用的四个 mockfs 文件做内容哈希核对。

具体复用：

- `scripts/build-native-user-fixture.py` 的静态 ARM64/iOS 17、
  `vtool`、4096 字节 ad-hoc CodeDirectory 和 version 2 trustcache 格式。
- `patches/xnu/mockfs-test-file.patch` 已有的双文件 `N4KROOT` 容器，
  `MOCKFS_TEST_FILE`、`backing_offset`、只读 open/close 和 vnode 页入路径。
- 固定 XNU `bsd/kern/syscalls.master` 的
  exit=1、read=3、write=4、open=5、close=6、wait4=7、getpid=20、
  getppid=39、munmap=73、select=93、pread=153、mmap=197、
  sysctl=202、posix_spawn=244。
- 固定 XNU `bsd/kern/kern_exec.c:3548` 的 `posix_spawn` 接受空 `adesc`；
  `kern_exec.c:3866` 起在未设 `POSIX_SPAWN_SETEXEC` 时创建新进程，
  执行独立映像；返回时通过用户指针写入 32 位 child PID。
  使用这一路径可避免依赖 fork 子分支的双返回约定。
- `bsd/sys/mman.h` 的 `PROT_READ|PROT_WRITE=3`、
  `MAP_ANON|MAP_PRIVATE=0x1002`。

参考快照 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| 上游 build-native-user-fixture.py | `8321a8a32424d75e49f34cb9c08aa56127ceeb0a200e126d3b0acde70e235ffc` |
| 上游 mockfs-test-file.patch | `4a1ef86e0bdca7b633f59885961be1939574f8ba9814d1d75ebbfd0a7a8bd919` |
| 固定 syscalls.master | `4bc39fcc0aa08f40e367064b947d586937d181ce6dd688683cefbdbe4650fa46` |
| 固定 kern_exec.c | `4ffa4e99be554cf1adde9a117eb444564a9cbd304f1756a3a3988f766d295143` |
| 固定 mman.h | `d7a740faf74c857f60e4ec188c89159ed82375ec56d662bd3624e15a6cdc6644` |

## 构建入口

在仓库根目录执行，构建只涉及两个用户程序，不编译内核：

```sh
python3 ci/build_ram_selftest.py --tree /path/to/prepared/unixnu
```

默认输出 `<tree>/build/native-user-fixture`。也支持
`--output /path/to/output`；同时给 `--tree` 仍做源码核验。
仅给 `--output` 适用于本地用户程序构建，此时
`mockfs_source_verified=false`，不能据此宣布所用内核已包含匹配的 mockfs。
要求现有 Xcode iPhoneOS SDK、`xcrun`、`codesign`，不自动下载工具。

`--tree` 要求准备好的 `.cache/xnu-work/bsd/miscfs/mockfs` 的四个文件
与 `MOCKFS_HASHES` 完全一致，变更或缺失均拒绝构建。
固定上游已有必要扩展，本次无需新增源码 overlay，
也无需改变 mockfs 的路径规则。父端继续使用其现有
`XNU_SOURCE_MOCKFS=1` 内核。

产物：

| 文件 | 用途 |
| --- | --- |
| `launchd.macho` | 签名 PID 1 自检主程序，继续供进程哈希和符号观察使用 |
| `child.macho` | 不同源码、不同 Mach-O、独立签名的子程序 |
| `root.img` | 实际 initrd，内含主程序和子程序 |
| `trustcache.bundle` | 按 cdhash 排序的两项 version 2 module |
| `addresses.json` | PID 1 的绝对符号地址 |
| `child-addresses.json` | 独立子程序入口和标记地址 |
| `selftest-build.json` | 源码/产物哈希、容器、签名、ABI 与未验证边界 |

目录还保留 `.o` 与未签名的 `*-static.macho` 便于审查。
脚本先签 child，解析其 `__TEXT,__st_marker` 的真实文件偏移，
再把偏移和签名后文件长度作为编译常量绑定到 PID 1。
对两个最终映像执行 `codesign --verify --strict`，
审计静态 ARM64/iOS 17 load commands，并逐个核对 SHA-256
CodeDirectory 的 4096 字节代码槽，最后生成 trustcache 和根镜像。
报告不会把签名构建标记为 QEMU 或实机通过。

## 真实检查与证据

1. 文件：实际 `open("/probe.bin")`、`read` 32 字节 Mach-O 头，
   核对魔数、ARM64 CPU 与可执行类型；`pread` 核对独立文件内完整标记；
   在签名后文件精确末尾 `pread` 要求返回 EOF；close 必须成功。
2. 内存：`mmap(NULL,8192,3,0x1002,-1,0)`，
   要求页对齐并检查全部 1024 个 64 位字初始为零；
   全量写入 `byte_offset XOR 0x5197` 并逐字读回，
   跨越 4096 字节边界，随后实际 `munmap` 成功。
3. 独立 exec：`posix_spawn` 执行 `/probe.bin`，
   传入有效 argv/envp 和空属性描述符，要求返回成功及 PID>1。
   `scripts/chiron-selftest-child.S` 是另一份编译输入，
   入口 `_chiron_selftest_child_entry` 在 child 映像；
   它实际检查 getpid()>1、getppid()==1，另开控制台写日志，
   close 后以 37 退出。其失败退出码是 111。
   父进程没有能够退出 37 的 fork 分支，也不在 spawn 返回时发布 exec PASS。
4. 回收：第一次 `wait4(child_pid,...,0,NULL)` 必须返回同一 PID，
   原始 status 必须等于 `37<<8`，才发布独立 exec PASS；
   第二次 `wait4(child_pid,...,WNOHANG,NULL)` 必须失败且 errno=ECHILD，
   才发布回收 PASS。

运行成功应依次出现：

```text
CHIRON SELFTEST PASS file read/marker/EOF
CHIRON SELFTEST PASS mmap zero/RW/munmap 8192
CHIRON SELFTEST START independent child
CHIRON SELFTEST CHILD PASS pid>1 ppid=1 exit=37
CHIRON SELFTEST PASS independent exec exit=37
CHIRON SELFTEST PASS wait/reap ECHILD
CHIRON RAM init: PID 1, 4K ABI, heartbeat SELFTEST PASS
CHIRON RAM init: PID 1, 4K ABI, heartbeat SELFTEST PASS
```

CHILD 日志与 START 的相对顺序受调度影响；START 不表示 exec PASS。
屏幕文字是辅助观察，最终门禁必须读取结构化结果、绑定产物哈希。
自检失败后的心跳持续附带 `SELFTEST FAIL`，成功时根据完整通过位和零失败阶段
附带 `SELFTEST PASS`；仅有基础心跳不能被误记为新增自检通过。

## 结构化 ABI

`_chiron_selftest_results` 为 little-endian `<8Q`，固定 64 字节：

| 字节偏移 | 字段 | 成功值 |
| --- | --- | --- |
| 0 | version | 1 |
| 8 | required_mask | 15 |
| 16 | passed_mask | 15 |
| 24 | failure_stage | 0 |
| 32 | child_pid | >1 |
| 40 | wait_pid | ==child_pid |
| 48 | wait_status | `37<<8` / `0x2500` |
| 56 | reserved | 0 |

通过位：file=1、mmap=2、independent_exec=4、wait_reap=8。
独立 exec 位在核对第一次 wait 后置位，回收位在第二次 wait 返回
ECHILD 后置位；bit 4 依赖独立子映像执行成功。

`failure_stage` 执行期间表示当前步骤，全部成功后才清零；
因此不能把执行中的非零值与最终失败混淆。
`_chiron_init_status=2` 只在所有自检成功且完成两轮控制台写/等待后设置，
此后持续心跳。自检失败输出十六进制阶段、syscall 和原始返回值，
保留失败阶段并使 init status=-1；恢复原心跳循环但始终不设置 status=2。
自检失败路径不抵达成功 `_checkpoint`，可另设断点
`_chiron_selftest_failure_checkpoint` 捕获失败现场。
原基础检查/控制台/定时等待失败仍使用既有 `_checkpoint` 失败停留逻辑，
父端必须核对 status，不能仅凭抵达该符号验收。

`_chiron_selftest_diagnostics` 为 `<5Q`，固定 40 字节：

| 偏移 | 含义 |
| --- | --- |
| 0 | 最近一次被自检记录的 syscall number |
| 8 | 最近一次原始 x0 返回值 |
| 16 | 返回后 NZCV，C 位为 bit 29 |
| 24 | 从实际文件标记读出的首 8 字节 |
| 32 | mmap 返回的地址 |

比较、内容或短读失败时，errno 不一定有意义，按阶段解释原始返回值。
失败日志的 best-effort write 不覆盖上述诊断值。
成功日志也会更新最近 syscall，因此最终诊断通常为 write，
不能要求成功时最近 syscall 仍是返回 ECHILD 的 wait4。

| 阶段 | 检查 |
| --- | --- |
| 10 | open 子文件 |
| 11 | read 与 Mach-O 头核对 |
| 12 | pread 标记与逐字节核对 |
| 13 | 精确文件尾 EOF |
| 14 | close 子文件 |
| 20 | mmap 返回与页对齐 |
| 21 | 全映射初始零值 |
| 22 | 全映射写入/读回 |
| 23 | munmap |
| 30 | posix_spawn 与 child PID |
| 31 | wait PID 与原始退出 status |
| 32 | 再次 wait 返回 ECHILD |
| 40 | 自检日志完整写入 |

## 父端集成和验收

父端增加观察范围：
`_chiron_selftest_results:64`、`_chiron_selftest_diagnostics:40`，
并保留原来的五组兼容结果。
QEMU 复用上游已有接口：

```text
--native-user
--native-user-image <output>/launchd.macho
--native-user-ramdisk <output>/root.img
--trust-cache <output>/trustcache.bundle
```

实际 initrd 必须是同一个 `root.img`，不能继续传原来的纯
`launchd.macho`。`root.img` 的头是 `<8sII4Q`：
magic=`N4KROOT\0`、version=1、count=2、launch offset=4096，
child offset 为主程序末尾向上对齐至 4096；
长度均为精确文件长度，尾部补零到 4096。
现有 ADT 的 `/chosen/memory-map/RAMDisk` 接受整个根镜像，
`TrustCache` 接受含两个自有程序的 bundle，不增加存储设备节点。

父端应同时要求：

- 原内核 POST/拒绝测试、MMU/EL0/4K、CPU/GIC/timer、pthread 门禁通过。
- init status=2、完成的 heartbeats>=2，64 字节自检字段严格匹配上述 ABI。
- 观察中的 `offline_root_sha256` 等于最终传输的根镜像哈希。
- 根镜像两段分别等于被哈希的主/子映像，构建报告、符号表与签名产物一致。
- 文件和子程序负向测试不可变成全部 PASS；至少保留本地故障注入测试，
  条件允许时再增加真实 QEMU 缺文件/错误子程序镜像的拒绝观察。

FAT 及引导菜单的 `initrd` 改接 `Payloads/root.img`，
`launchd.macho` 保留用于符号和证据审计。只有真实 QEMU 自检
全部通过后才能将根镜像打包为下一版手机镜像。
本实现没有触发构建、发布或任何设备操作。

父端后续整合的运行 `39756701933` 已观察到 `_chiron_selftest_results`：
`(1,15,15,0,2,2,9472,0)`，即四项通过、独立子进程 PID 2、
等待同一 PID 并取得退出码 37。正常原始文本含四项 PASS 和两次
`heartbeat SELFTEST PASS`，并通过原 POST 正反例、panic/注入异常资格。
本地复核从镜像重建 FAT、提取根卷并核对主/子映像、重新解码原始记录，
全部通过，实际 QEMU 屏幕也已查看。

候选为 `dist/source-trace-39756701933/CT-39756701933-1-3b4d3db21117.img`，
SHA-256 为 `722d3732ae10b02fa855d2ef948e3b43555c82058c374626a6b6d95567cb44b1`。
`selftest-build.json` 中的 `qemu_runtime_qualified=false` 记录的是用户程序构建时刻；
后续真实运行资格在 `manifest.json` 的 `qemu_source_trace_proof.user_selftest` 及
`qemu-logs/verification.json` 中，不篡改原始构建报告。

## 本地验证

```sh
python3 -m unittest discover -s tests -p test_ram_selftest.py -v
```

若已有 Unicorn，通过其安装目录设置 `PYTHONPATH` 可启用指令测试。
本机已存在 `/private/tmp/chiron-debug-python`，本次使用：

```sh
PYTHONPATH=/private/tmp/chiron-debug-python python3 -m unittest discover -s tests -p test_ram_selftest.py -v
```

8 项测试覆盖容器/信任缓存边界、实际签名构建及代码页篡改拒绝、
兼容模式汇编、独立 child 指令执行与两轮心跳、
文件/映射/子程序/退出回收故障拒绝和定时等待失败拒绝。
指令模拟会加载两份最终签名 Mach-O，执行 AArch64 指令，
但以显式宿主机 fixture 回答 SVC；不会证明 XNU 的文件系统、调度器、
签名策略或时钟已实际通过。

真实限制：根镜像是固定的两个自有文件，没有普通目录创建、
文件写入或通用根文件系统；匿名映射仅验证 8192 字节，
独立程序仅验证一次 spawn/exit/reap，没有 libc、dyld 或 launchd 服务管理。
第一次 wait 为阻塞等待，若内核/child 挂起，会停在阶段 31，
需要父端 QEMU 超时和已有内核诊断定位；不伪装成 PASS。
失败后可能保留最多一个打开文件或 8192 字节映射，仅做一次自检，
不会循环创建子进程或持续泄漏资源。
