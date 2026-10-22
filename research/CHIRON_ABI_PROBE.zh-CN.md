# 独立静态 ARM64 系统接口探针

主任务追加记录：云运行 `39780009249`、源码提交 `6cd298b87635` 已在真实
XNU/QEMU 完成此默认探针，`required_mask=passed_mask=127`、
`failure_stage=0`、心跳 2 次，commpage 两个页 shift 均为 12。
该作业后续 RAMFS 编译失败；默认 ABI 探针的原始观察仍单独保存在
`dist/foundation-attempt-39780009249/qemu-abi/gdb-stop.json` 和
`abi-verification.json`，不记为整个基础任务作业成功。未取得实机资格。

后续运行 `39782971298` 已完成整组基础任务资格：
正常内核的默认 ABI 探针、新 RAMFS 实验内核的旧根卷四项自检，以及
CHRAMFS 嵌套文件映射探针均通过。两个 ABI 探针均为 mask=127、
failure=0、两次心跳；结果由原始调试结构、载荷哈希及 POST 记录独立复核。
参见 `dist/foundation-39782971298/local-verification.json`。
当前仍未验证 pthread/TLS 库、写入只读映射的拒绝、unmap 后访问的拒绝或实机运行。

该探针用于原 RAM 自检以外的接口诊断。它拥有独立的启动文件、结果 ABI、构建报告和验证器，不替换现有 `chiron-init.S` 或四项 selftest。主集成流程负责把它放入单独的 QEMU 资格，不放入原手机镜像。

## 构建和观察接口

```sh
python3 ci/build_chiron_abi_probe.py \
  --tree /path/to/prepared \
  --output /path/to/separate/abi-probe

python3 ci/validate_chiron_abi_probe.py \
  --observation /path/to/gdb-stop.json \
  --build-report /path/to/separate/abi-probe/abi-probe-build.json \
  --output /path/to/separate/verification.json
```

也可导入 `ci/validate_chiron_abi_probe.py`，调用 `validate(observed: dict, build_report: dict) -> dict`。验证失败抛出 `ValueError`；CLI 非零退出并删除旧 verification 文件，避免残留旧 PASS。

构建器复用 `ci/build_ram_selftest.py` 的 `build_program`、Mach-O 静态审计、SHA-256 CodeDirectory 检查、双文件根卷和 trustcache 接口，不修改这些函数原来的 ABI。产物：

| 文件 | 用途 |
| --- | --- |
| `launchd.macho` | 本探针，静态普通 ARM64，目标 iOS 17.0，`-march=armv8-a` |
| `child.macho` | 现有独立 child，作为只读文件映射对象，本探针不执行它 |
| `root.img` | `N4KROOT` v1 双文件布局，路径 `/sbin/launchd` 和 `/probe.bin` |
| `trustcache.bundle` | 两个不同的自签代码哈希 |
| `addresses.json` | 主程序全部导出符号，包括结果起止符号和观察器兼容符号 |
| `child-addresses.json` | child 符号 |
| `abi-probe-build.json` | `artifacts[filename].sha256/bytes`、`result_abi`、源码与来源审计 |

不使用 dyld、libSystem、pthread 或 TLS。入口首先执行 `SP &= ~15`。链接器入口使用 `LC_UNIXTHREAD`，签名槽为 4096 字节。构建器拒绝将指定 tree 的原 `build/native-user-fixture` 作为输出目录。

### 来源验证

指定 `--tree` 时，默认 ABI 来源**始终为该 tree 的 `.cache/xnu-work`**，不会尝试读取构建主机的私有 `/private/tmp/chiron-console-source`。它按本地 console 参考快照固定七个实际 ABI/策略定义文件的 SHA-256：

- `osfmk/kern/syscall_sw.c`
- `bsd/kern/syscalls.master`
- `osfmk/mach/message.h`
- `osfmk/mach/mach_traps.h`
- `osfmk/mach/arm/traps.h`
- `osfmk/arm/cpu_capabilities.h`
- `osfmk/ipc/ipc_policy.c`

`sleh.c` 允许有不同 trace overlay；独立核对 `handle_svc` 中负数 Mach 分派、absolute-time 特例、BSD 分派，以及 absolute-time handler 把 `mach_absolute_time()` 写回 `x0` 的关键函数片段。如果存在 `tree/build/chiron-source-overlay.json`，还要求 `sleh.c` 整文件哈希与其中 `after_sha256` 一致。报告记录当前文件哈希、关键片段哈希、overlay 报告哈希及绑定状态。指定 tree 同时复用现有 mockfs 四文件哈希检查。

不指定 tree 时可进行本地静态测试，默认参考 `/private/tmp/chiron-console-source`。`--abi-source <mirror>` 可显式指定字节相同的 ABI 定义镜像，仍需通过哈希与分派检查。报告通过 `abi_source_mode`、`abi_source_root`、`abi_source_sha256`、`dispatch_audit` 和 `mockfs_source_verified` 区分来源，不把本地参考构建写成 prepared-tree 资格。

## 执行内容

先完成兼容观察器所需的 `getpid=1`、`hw.pagesize=4096` 和控制台打开，再执行：

| bit | 名称 | 通过条件 |
| --- | --- | --- |
| 1 | `mach_port` | `task_self(-28)` 返回有效名字，`allocate(-16)` 创建 receive right，`insert_right(-21)` 插入 send right |
| 2 | `mach_message` | `mach_msg2(-47)` 发送及接收各成功一次，接收头、端口、大小、ID、8 字节 payload 均匹配 |
| 4 | `absolute_time` | `timebase_info(-89)` 返回非零 32 位比例；special trap `-3` 两次读数跨一次显式 1 秒 select 严格递增 |
| 8 | `commpage` | 固定 RO commpage `0xfffff4000` 的 `+0x25/+0x37` 字节均为 12 |
| 16 | `anonymous_protect` | 8192 字节匿名映射全零，逐字写入和读回；mprotect R 后读回，恢复 RW 后跨页写回，munmap 成功 |
| 32 | `file_mapping` | `/probe.bin` 只读私有映射成功，Mach-O magic/CPU/filetype 和独立 child marker 匹配，munmap/close 成功 |
| 64 | `receive_timeout_cleanup` | 空队列 receive 返回 `0x10004003`；`mod_refs(-19)` 依次释放插入的 send 和 receive right |

BSD syscall 按 carry 及返回值检查，Mach trap 按原始 `kern_return_t`/端口/数据检查，不把 carry 当作 Mach 成败契约。每个 bit 仅在对应调用及数据断言完成后设置。

iOS 17 目标不能依赖旧 `mach_msg_trap(-31)` 发送自定义消息：固定源码的 legacy policy 会拒绝。探针使用 `mach_msg2_trap(-47)`，send 选项为 `MACH_SEND_MSG | MACH_SEND_TIMEOUT | MACH64_SEND_MQ_CALL`，receive 为 `MACH_RCV_MSG | MACH_RCV_TIMEOUT`。正常发送和接收各设 100 ms 超时，空队列 receive 设 1 ms。无重试、无无限消息等待，接收缓冲区为 128 字节。

每组完成打印 `CHIRON ABI PASS ...`；所有项目完成后打印 `CHIRON ABI PROBE PASS`，随后两次实际控制台 write/1 秒 select 完成才发布 `_chiron_init_status=2`。`_chiron_heartbeats` 每次完整 write/select 后递增。

### 可选 RAMFS 全文件读回

RAMFS agent A 的独立构建可同时定义 `CHIRON_ABI_RAMFS` 和 `CHIRON_ABI_FILE_SUM`。前者将 `Lfile` 改为 `/usr/lib/chiron/probe.bin`；后者必须是本次实际映射文件所有字节之和，按 uint64 模加法计算。定义 RAMFS 宏而未定义 FILE_SUM 会在预处理时报错。

该分支沿用 `CHIRON_ABI_CHILD_SIZE`、`CHIRON_ABI_CHILD_MAP_SIZE` 和 `CHIRON_ABI_MARKER_OFFSET`，顺序为：打开文件（60）→只读映射（61）→Mach-O header 和 marker 验证（62）→逐字节读完恰好 `CHIRON_ABI_CHILD_SIZE` 字节并比较总和（65）→unmap（63）→close（64）→发布原有 file_mapping bit 32。循环逐字节加载整个文件，覆盖实际文件跨越的多个 4K 页，不读取文件末尾以外的映射填充。64 位期望总和使用一个 movz 和三个 movk，分别组装 0、16、32、48 位的 16 位片段。

求和失败保留阶段 65、status=-1 并到达 `_checkpoint`；不会发布 file_mapping bit。成功时仍以 completed_stage=64 结束，结果 ABI 保持 256 字节，没有新增结果字段或 bit。这个累加检查只证明实际文件读回与构建绑定总和一致，不声明密码学认证。默认未定义 `CHIRON_ABI_RAMFS` 时仍使用 `/probe.bin`，不编译阶段 65，保持原探针行为。现有 builder/test 文件不负责启用该宏；新增宏构建由 agent A 集成，真实 relink 和 QEMU 验证由主 agent 执行。

错误返回或数据断言失败会打印 `CHIRON ABI PROBE FAIL stage=0x... call=0x... return=0x...`，设置 `_chiron_init_status=-1` 并进入 `_checkpoint`。打印失败阶段时不会覆盖第一处失败的诊断字段。失败后不再推进测试；未完成资源释放由该次诊断启动结束处理。CPU 异常/内核 panic 等不能作为正常断言失败处理，需外层观察器记录。

## 结果 ABI v1

`_chiron_abi_results_end - _chiron_abi_results = 256`。格式 `<32Q`，每项小端 uint64，8 字节对齐；完整字段名也写入报告 `result_abi.fields`。原始负 trap 编号和错误状态按 uint64 补码保存。

| 字节偏移 | 字段 | 意义 |
| --- | --- | --- |
| 0 | `version` | 1 |
| 8 | `required_mask` | 127 |
| 16 | `passed_mask` | 逐组发布；完整通过为 127 |
| 24 | `failure_stage` | 运行中的阶段或首个失败阶段；成功检查点为 0 |
| 32 | `last_call` | 最后受跟踪的 syscall/trap 编号 |
| 40 | `last_return` | 该调用原始 x0 |
| 48 | `last_nzcv` | 该调用后 NZCV |
| 56 | `task_port` | task self 名字 |
| 64 | `receive_port` | 分配的端口名字，已完成释放时仍保留诊断值 |
| 72 | `message_id` | 从接收缓冲区读回的 ID `0x4348` |
| 80 | `message_payload` | 从接收缓冲区读回 `0x434849524f4e4142` |
| 88 | `empty_receive_result` | 空队列 receive 原始返回 `0x10004003` |
| 96 | `time_before` | 第一次 absolute-time |
| 104 | `time_after` | select 后第二次 absolute-time |
| 112 | `timebase_numer` | 从内核填充的 uint32 零扩展 |
| 120 | `timebase_denom` | 从内核填充的 uint32 零扩展 |
| 128 | `commpage_user_shift` | RO commpage 用户页 shift |
| 136 | `commpage_kernel_shift` | RO commpage 内核页 shift |
| 144 | `anon_base` | 已释放的匿名映射地址，保留诊断 |
| 152 | `file_base` | 已释放的文件映射地址，保留诊断 |
| 160 | `file_magic` | 实际映射读回的 `0xfeedfacf` |
| 168 | `console_fd` | 控制台打开结果 |
| 176 | `completed_stage` | 最后一组通过阶段，完整通过为 64 |
| 184 | `entry_sp` | 原始入口 SP |
| 192 | `aligned_sp` | 显式向下按 16 字节对齐后的 SP |
| 200 | `send_result` | send 原始返回 |
| 208 | `receive_result` | 正常 receive 原始返回 |
| 216 | `port_cleanup_result` | 最后 receive right 释放返回；两个释放调用均检查 |
| 224 | `file_length` | 构建绑定的 child 字节数；不是运行时 stat 结果 |
| 232 | `reserved0` | 0 |
| 240 | `reserved1` | 0 |
| 248 | `reserved2` | 0 |

兼容符号 `_results` 保持 `<4Q` 的 PID/sysctl返回/两次NZCV，`_user_page_size`、`_old_length`、`_chiron_heartbeats`、`_chiron_init_status` 各 8 字节。成功时分别要求 PID=1、sysctl返回=0、pagesize=4096、old_length=4、heartbeats>=2、status=2；失败 status 以有符号 64 位读取为 -1。

主要失败阶段：

| 阶段 | 内容 |
| --- | --- |
| 1 / 2 / 3 | console / PID / pagesize |
| 10 / 11 / 12 | task self / port allocate / insert |
| 20 / 21 / 22 | send / receive / header及payload断言 |
| 23 / 24 / 25 | 空队列超时 / send right释放 / receive right释放 |
| 30 / 31 / 32 / 33 | timebase / 首次计时 / select / 再次计时 |
| 40 | commpage shift断言 |
| 50 / 51 / 52 / 53 / 54 / 55 / 56 | 匿名map / 零页及写入 / R保护 / 读回 / RW保护 / 写回 / unmap |
| 60 / 61 / 62 / 63 / 64 | 文件打开 / mmap / 内容断言 / unmap / close |
| 65 | 仅 RAMFS 分支：marker 验证后、unmap 前的全文件逐字节累加比较 |
| 90 / 91 | 正常控制台write / 心跳select |

## 验证边界

本地测试命令使用已有 Unicorn，无下载或安装：

```sh
PYTHONPATH=/private/tmp/chiron-debug-python \
  python3 -m unittest discover -s tests -p test_abi_probe.py -v

python3 ci/build_chiron_abi_probe.py \
  --output /private/tmp/chiron-abi-probe-local
```

测试包含真实 Apple 工具链构建、签名与 root/trustcache 校验、prepared-tree 来源选择与 overlay 绑定，以及实际 ARM64 指令对合成 SVC 答案的控制流检查。包含未对齐入口 SP、BSD错误、Mach错误、错误消息头/payload、空队列错误返回、停滞时钟、零 timebase、错误 commpage、映射内容损坏、短写及失败记录检查。Mach fixture 故意设置 carry，验证探针没有混用 BSD 错误约定。

Unicorn 的 SVC 返回均为宿主 fixture，不是内核实现证明。validator 只检查捕获结果的格式、语义和兼容字段；主 agent 的独立 QEMU gate 负责真实 kernel/init/root/trustcache 哈希绑定、POST、CPU、计时器、pthread 内核注册和观察来源。

尚未由本任务验证：真实 QEMU/实机执行、dyld/Apple 用户态、pthread/TLS 库、跨进程 IPC、Mach 复杂消息/描述符、大规模队列、时钟校准精度、写入只读页必然触发保护异常、unmap 后访问必然异常。mprotect 项验证成功返回、R 状态读取和恢复 RW 后写入，不故意让 PID 1 触发故障；文件长度为构建绑定值。本地构建报告始终保留 `qemu_runtime_qualified=false`、`hardware_tested=false`、`pthread_tls_tested=false`。
