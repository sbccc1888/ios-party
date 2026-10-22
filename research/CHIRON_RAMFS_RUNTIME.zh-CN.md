# CHRAMFS 独立运行 fixture

主任务最终结果：云运行 `39782971298` 已在独立 RAMFS 内核中运行本 fixture，
取得新目录根卷挂载、嵌套文件完整只读映射、mask=127 和两次心跳。
根卷 114688 字节，子文件 51472 字节、跨 13 页，本次实际文件字节和为 90540。
独立内核的 POST 正反例、旧 N4KROOT 四项回归也通过。
原始记录在 `dist/foundation-39782971298/ramfs-runtime/`，
本地独立复核在同产物目录上一级的 `local-verification.json`。
下文是 agent 初次交付 fixture 时的范围与记录，后续运行结果不等于实机通过。

日期：2026-10-22。本扩展仅新增以下四个文件，不修改已有 RAMFS overlay、
正常 selftest、ABI probe 本体、通用 validator、主 CI、configs 或手机镜像：

- `ci/build_chiron_ramfs_probe.py`
- `scripts/chiron-ramfs-probe.S`
- `tests/test_ramfs_probe.py`
- 本文 `research/CHIRON_RAMFS_RUNTIME.zh-CN.md`

本轮交付是签名用户态程序与有界根卷的可构建输入。没有全量内核构建、
云作业、relink、QEMU、设备操作、下载或安装依赖、提交或推送。
主 agent 已另行报告原 D 探针在真实 QEMU 中七组通过、mask=127；
**该结果不属于这里的新 CHRAMFS fixture**。

## 构建与集成接口

```sh
python3 -B ci/build_chiron_ramfs_probe.py \
  --tree /path/to/normal-prepared-unixnu \
  --output /path/to/separate-ramfs-probe
```

本地无 prepared tree 时默认使用 D 的固定
`/private/tmp/chiron-console-source` ABI 参考源：

```sh
python3 -B ci/build_chiron_ramfs_probe.py \
  --output /private/tmp/chiron-ramfs-probe-local
```

Python 调用为 `build(output, tree=None, abi_source=None)`。
`--abi-source` 与 D 原接口一致，只覆盖其固定镜像来源，仍执行 D 的哈希检查。

构建器首先调用 `ci/build_chiron_abi_probe.build`，保持其默认源码指纹、
Mach/BSD/absolute-time dispatch 审计和正常 mockfs 精确哈希检查；
输入 `--tree` 是**未应用 RAMFS overlay 的正常 prepared tree**。
不将 overlay 树传给 `RAM.verify_mockfs`，不降低其严格性。
实际 kernel archive/relink/overlay 身份由主 agent 单独处理。

这一步在独立输出目录生成签名 child、child symbols、marker 与元数据。
随后立即移除中间 N4KROOT 的 `abi-probe-build.json`，避免后续 wrapper 失败
留下貌似通过的旧报告。使用同一 child，重新编译并签名 wrapper 主程序，
重建 trustcache、CHRAMFS root、主 addresses 和所有声明的 artifact 哈希。
失败重建不保留旧 RAMFS 成功报告；不覆盖已存在的普通 ABI 报告。
源码在构建前后比对，协调期间源码变化则拒绝发布最终报告。

正常 `tree/build/native-user-fixture`、其祖先和子目录被拒绝作为输出。
不要把普通 ABI fixture 目录当作本实验输出。已有本实验目录可显式重建。

输出主文件名与 D 相同：

| 文件 | 用途 |
| --- | --- |
| `launchd.macho` | 新 wrapper 的签名主程序；自有静态诊断 PID 1 |
| `child.macho` | D builder 生成的原独立签名 child |
| `root.img` | 新 CHRAMFS v1 根卷 |
| `trustcache.bundle` | 重算的新 main 与原 child 两个 cdhash |
| `addresses.json` | 新 main 的地址，保留通用观察符号 |
| `child-addresses.json` | 原 child 地址 |
| `probe-decoy.txt` | 根目录 `/probe.bin` 的不同文本内容 |
| `abi-probe-build.json` | 最终报告，兼容现有 result validator |

`.o` 与 `*-static.macho` 仍是底层 builder 的中间产物，不作为已资格输入。

## 真实路径与宏合同

wrapper 仅定义 `CHIRON_ABI_RAMFS=1` 后 include D 的
`chiron-abi-probe.S`，不复制或修改原探针的实现。

显式根目录树共 9 节点：6 个真实目录、3 个不同文件。

```text
/
  dev/
  sbin/
    launchd                      <- launchd.macho
  usr/
    lib/
      chiron/
        probe.bin                <- child.macho
  probe.bin                      <- probe-decoy.txt
```

目录均为 `0755, uid=0, gid=0`；main/child 均为 `0555`；
根目录文本为 `0444`。每个文件保留精确长度与独立 4K 对齐 extent。
根卷由生产宿主 `chiron_ramfs.build/parse/read_file` 构建和逐路径读回校验。
文本不是 Mach-O，长度与内容都显著不同于 child。因此旧两槽或错误名称别名
把嵌套路径解析到 `/probe.bin` 时，不能依赖恰好相同文件而假通过。

传入 wrapper 的定义来自本次签名完成后的 child 字节：

- `CHIRON_ABI_CHILD_SIZE = len(child)`
- `CHIRON_ABI_CHILD_MAP_SIZE = align4096(len(child))`
- `CHIRON_ABI_MARKER_OFFSET = marker_offset(child)`
- `CHIRON_ABI_FILE_SUM = sum(child) modulo 2^64`，按 uint64 常量传入

D 的宏分支将文件路径改为 `/usr/lib/chiron/probe.bin`；
在 mmap、头部和 marker 检查后，阶段 65 逐字节读取完整 child 的有效长度，
将每个无符号字节累加为 uint64，并与 FILE_SUM 比较。求和不包含根卷末页填充。
成功后继续原 munmap/close，`completed_stage` 仍为 64，结果 ABI 不变：
`_chiron_abi_results`、`<32Q`、256 字节、version=1、required_mask=127。
失败阶段 65 不发布 file_mapping bit；前五组及 receive timeout 对应 mask=95。

无 RAMFS 宏时 D 原程序路径与行为不变。builder 对宏、嵌套路径、
阶段 65 的必要源码合同做预检，并确认编译后的 main 包含嵌套路径。
缺失协作宏时显式失败，不生成伪 RAMFS 报告。

逐字节总和是有界读回探针，不是密码学认证；多字节变化可能抵消。
精确 root/child/main SHA-256 与签名哈希仍由独立产物绑定承担。

## 报告兼容性与资格标记

仍输出 `abi-probe-build.json`，直接继承 D 的 `result_abi`、
`abi_source_sha256`、dispatch 审计、traps、marker 与通用诊断字段，
因而可使用 `ci/validate_chiron_abi_probe.py`，无需分叉 validator。

最终报告包含：

- `root_format="CHRAMFS"`、`root_version=1`、`root_file_count=3`、
  `root_node_count=9`、精确 `root_paths` 与 `root_directories`。
- `file_byte_sum`、`file_byte_sum_algorithm`、
  `whole_file_readback_requested=true`、`whole_file_readback_failure_stage=65`。
- 新 main/child 的签名信息、所有七个输入 artifact 的精确长度和 SHA-256、
  wrapper 定义及相关源文件哈希。
- `whole_file_readback_runtime_verified=false`、`kernel_compiled=false`、
  `ramfs_mount_verified=false`、`file_pagein_verified=false`、
  `qemu_runtime_qualified=false`、`hardware_tested=false`。

`mockfs_source_verified=true` 只在调用时提供正常 prepared tree 且 D 验证通过时
出现，不表示新 RAMFS kernel 已编译、挂载或运行。报告额外的
`mockfs_source_role` 明确区分这个含义。

通用 validator 仅检查观察结果语义，不绑定 kernel/root/trustcache 成品；
主 agent 必须将本报告 artifact SHA-256 与实际 QEMU 使用的三个输入及
独立 RAMFS kernel 身份绑定后，才能发布运行资格。

## 宿主验证与范围

静态构建、签名、根卷和路径区分测试：

```sh
python3 -B -m unittest discover -s tests -p test_ramfs_probe.py -v
```

已有 Unicorn 位于 `/private/tmp/chiron-debug-python` 时可启用指令测试，
不下载或安装任何依赖：

```sh
PYTHONPATH=/private/tmp/chiron-debug-python \
  python3 -B -m unittest discover -s tests -p test_ramfs_probe.py -v
```

本轮实际执行上述含 Unicorn 的命令：**9 tests，OK，无 skip，4.099 秒**。
本地 CLI 也已实际执行成功，输出目录
`/private/tmp/chiron-ramfs-probe-local`，本次静态产物如下：

| 文件 | 精确字节数 | SHA-256 |
| --- | ---: | --- |
| `root.img` | 114688 | `6d6a415328eda9791556cefb1ca293a86f085e7dea382d2ca0fda513f16904bc` |
| `launchd.macho` | 51648 | `c89adbe8fd69e369273371e14eeb5def2c9496e834293f58f06cb7febf45cea6` |
| `child.macho` | 51472 | `6cc26758823857b349045a89caeb94b25f54af30f426c07d5ae0a32009054fcb` |

本次 `file_byte_sum=90363`，result ABI=256 字节。重建时应采用新报告中的
实际哈希和 sum，不硬编码本表数值，也不复用其他版本 child 的常量。
本地 CLI 无 `--tree`，所以仅标为 `local-reference`；
测试中的独立正常 prepared tree 路径另外实际验证通过。

测试直接执行编译后的 wrapper ARM64 指令，syscall 答复由明确的宿主 fixture
提供；文件内容来自生产宿主 CHRAMFS parser 按真实路径读回。检查：

- 签名/CodeDirectory 正确，损坏签名覆盖页被拒绝，trustcache 重算一致。
- 全部 artifact 哈希、新地址、结果 ABI、真实目录与三个文件内容一致。
- 未修改正常 prepared tree 仍通过 D 的源码与四文件 mockfs 哈希验证。
- wrapper 编译失败时，中间 N4KROOT 报告不残留为 RAMFS 成功报告。
- 正常指令路径完整执行，通用 validator 接受，mask=127、两次心跳。
- 映射内容偏移 `8192+19` 或最后一个有效字节翻转，均在阶段 65 失败；
  不发布 file_mapping bit、不输出 PROBE PASS。
- 宿主模拟错误路径别名，映射根目录文本，头部断言在阶段 62 失败。
  真实内核也可能在 open/mmap 更早拒绝错误对象，不能要求硬件固定失败阶段 62。

宿主将字节映射给 Unicorn 并不使用 XNU 的 device I/O、UBC 或 VM pager；
这些测试**不证明真实文件 mount/pagein/mmap 通过**。

## 仍需主 agent 完成

主 agent 首次真实 archive 编译发现 `struct chiron_ramfs_mount=16408` 字节，
超过 XNU `kalloc_type` 16 KiB 静态上限，正在将 nodes 拆为独立 typed allocation，
更新 overlay 身份及宿主回归。本扩展没有修改那些旧文件，也不将旧编译失败
隐藏为本 fixture 的成功。

后续需要：

1. 真实 XNU 头文件下的 archive 编译通过，精确记录修复后的 overlay 身份。
2. 独立实验内核 relink，保留正常实机通过内核与 fixture。
3. QEMU 使用新 main/root/trustcache，同步绑定三个 SHA-256 与新 kernel 身份，
   真实运行到通用 validator 验收。
4. 以 QEMU 结果证明新根卷实际挂载、嵌套文件映射和完整文件字节被访问；
   若需要更强覆盖，另加真实多文件交错读、权限、EOF/零尾和错误 I/O 验收。
5. 单独手机验证；本扩展没有设备操作。

本 root 只是两个自有小程序和短文本。CHRAMFS 当前卷接受上限 32 MiB，
现有手机载荷仍受 24 MiB 传输窗口与主 agent 核对的约 2.77 MB 空闲空间限制；
未修改 zero-tail 容量、地址或传输布局，也不宣称能承载 Apple 共享缓存。
