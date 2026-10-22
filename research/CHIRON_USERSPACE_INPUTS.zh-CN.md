# Chiron 目标用户态离线准入工具

日期：2026-10-22。工具：`tools/audit_chiron_userspace.py`；测试：
`tests/test_userspace_audit.py`。此工具只读用户明确提供的目录或 JSON 清单，
使用 Python 标准库，不运行目标、不下载系统文件、不安装依赖、不操作设备。
它不会选择某个 iOS 版本。当前没有提供原版目标文件，不能写兼容通过。

## 给主 agent 的调用接口

在项目根目录运行，不提供目标即得到可集成的缺件报告：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tools/audit_chiron_userspace.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/audit_chiron_userspace.py --manifest research/userspace-inputs/empty.example.json
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_userspace_audit.py -v
```

前两条预期退出 **1**，stdout 是单个 JSON，`admission.eligible=false`、
`compatibility_verified=false`；`missing_product_version`、
`missing_build_version`、`no_target_inputs` 指明缺件。不能将退出 1 改为兼容通过。

真实输入的命令格式：

```sh
python3 tools/audit_chiron_userspace.py --manifest /explicit/inputs.json
python3 tools/audit_chiron_userspace.py --root /explicit/root --product-version "$PRODUCT_VERSION" --build-version "$BUILD_VERSION"
```

变量必须来自真实目标资料；不是建议某个发行版。可以同时提供清单与 `--root`，
此时清单内文件路径相对 root，否则相对清单所在目录。CLI 的版本值必须与清单
一致，并与角色为 `system_version` 的 `SystemVersion.plist` 中
`ProductVersion` / `ProductBuildVersion` 比对。`identity_verified` 只代表这个
字符串交叉比对，**不代表文件来源、原版身份或整个文件集属于同一构建已认证**。

工具只向 stdout 写 JSON；退出 **1** 表示完成清点但准入被阻塞，退出 **2**
表示参数对应的输入根目录、清单等整体无效。argparse 的语法错误遵循其标准
stderr/退出 2 行为。当前保守实现不会产生准入成功退出码。
`structural_audit_complete` 即使为 true，也只表示已实现检查没有发现问题；
报告始终保留未验证项，绝不能替代 iOS 兼容资格。

## 清单格式与缺件语义

机器可读 schema 在 `research/userspace-inputs/manifest.schema.json`；
工具使用同等的内置检查，不需要安装 jsonschema。小型空示例未包含任何 Apple 二进制。
结构示例仅使用占位路径，不指定目标版本：

```json
{
  "schema_version": 1,
  "source": "填写用户实际提供文件的来源说明",
  "files": [
    {"path": "SystemVersion.plist", "target_path": "/System/Library/CoreServices/SystemVersion.plist", "kind": "system_version", "role": "system_version"},
    {"path": "dyld", "target_path": "/usr/lib/dyld", "kind": "macho", "role": "dyld"},
    {"path": "launchd", "target_path": "/sbin/launchd", "kind": "macho", "role": "launchd"},
    {"path": "cache", "target_path": "/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64", "kind": "cache", "role": "cache"},
    {"path": "cache.01", "target_path": "/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64.01", "kind": "cache", "role": "cache_subcache"}
  ]
}
```

补入实际 `ProductVersion`、`BuildVersion`，以及需要时每文件的 `sha256`、
`uuid`。SHA-256 和 UUID 比对仅相对用户清单，不认证清单本身。
单一 `uuid` 约束要求匹配文件某个 slice 的 UUID；FAT 每个 slice 的 UUID 另行列出。
`kind=data` 只清点哈希，不能冒充 dyld、launchd 等必需角色。
角色 `libsystem` 可表示独立原库，但缓存内库尚不解析，因此不认定依赖闭包成立。
清单中每项均要求实际存在；不存在则产生 `missing_file`。
各角色文件出现并不意味着它们是原版 Apple 文件，或具备正确的 MH_DYLINKER /
MH_EXECUTE 角色语义；这些仍在运行闭包未验证范围内。

普通目录模式只检查三个固定路径：
`System/Library/CoreServices/SystemVersion.plist`、`usr/lib/dyld`、`sbin/launchd`；
另检查 `usr/lib/libSystem.B.dylib` 是否存在，及单个固定缓存目录
`System/Library/Caches/com.apple.dyld` 下 `dyld_shared_cache_` 前缀文件。
**不递归、不搜索用户主目录、不自动寻找卷、恢复镜像或另一构建。**
其他布局、散放文件及明确的 subcache 列表应采用清单模式。
目录模式对缓存主文件/分卷不作未经格式验证的命名推断；采用清单可标记角色，
但标记也不能证明配对关系。

文件使用 `dir_fd`、`O_NOFOLLOW` 逐层打开，拒绝目录内软链接、FIFO、设备和
路径逃逸；明确提供的 root 自身先规范化。根内路径只允许普通文件。
清单不允许绝对源路径、空组件、`.`、`..`、反斜杠或重复路径/键。
上限：256 文件、清单/plist 2 MiB、每文件 8 GiB、合计 16 GiB、
签名 64 MiB；目录缓存候选枚举最多 1024 项。超过上限显式拒绝。
大文件只读 mmap，SHA-256 分块计算；输入文件需在审计期间保持静止，
前后大小/mtime/ctime 检查不能替代外部不可变快照。

## 已核实的解析范围

| 格式 | 当前实现 |
| --- | --- |
| Mach-O 64 | 头、原始 CPU/subtype/能力位、UUID、段文件范围、LC_BUILD_VERSION、动态链接器、各类 dylib 路径、RPATH、签名位置；小端普通 arm64 ALL/V8 才属于当前架构候选 |
| FAT32 / FAT64 | 两种字节序的封装边界、对齐、互不重叠、重复架构和内外头一致性；逐 slice 报告；内部要求 64 位 Mach-O |
| CodeDirectory | 0x20001–0x20600 的已知版本字段；SHA-1、SHA-256、截断 SHA-256、SHA-384 的普通连续代码槽；codeLimit、页 shift、完整覆盖和逐块哈希 |
| 签名容器 | 主/备用 CodeDirectory、SuperBlob 索引/重复/重叠边界、XML/DER entitlement 是否存在、CMS 是否存在 |
| 4K / 16K 签名 | 分别报告代码完整性；16K 即使哈希正确仍阻塞当前默认 4K 路径；页 shift 0 的连续哈希可清点但同样不准入 |
| dyld cache | **仅 XNU 镜像结构的前 104 字节**：magic/架构、每文件 UUID、旧映射/镜像计数、签名位置；签名走同一代码槽检查；所有 cache 均明确 `cache_layout_unsupported` |
| 版本/依赖 | plist 与明确版本比对；直接依赖路径在清单中是否出现；缺失路径和 @rpath 等未解析路径分别列明 |

未完整解析的 load command 列入 `unparsed_load_commands`。
所有 slice 都清点；额外不支持架构也产生阻塞，未实现挑选一个 slice 后忽略其余的策略。
完整代码槽哈希只证明被检查字节与 CodeDirectory 一致，不等于 Apple 签名认证。
特殊槽、外部资源、entitlement 内容和 CMS 信任未验证；特殊槽存在时会产生
`special_slot_validation`，包括实际自有文件的该情况。

## 显式拒绝或未验证

拒绝 32 位/非 Mach-O/嵌套 FAT、arm64e 或未知 subtype/能力位、加密可执行映像、
未知 CodeDirectory 版本/算法/页大小、scatter、pre-encryption、linkage 签名、
chained fixups 格式及损坏/截断/覆盖不完整输入。Big-endian 64 位头可以清点，
但不属于本机普通 arm64 准入。

缓存现代 header、image index、subcache 表的不同 entry 布局、主从 UUID 配对、
symbols 身份、映射/slide/auth 指针一概未实现。每个明确提供的缓存/分卷可独立
清点哈希和前缀 UUID；清单指定但缺失的分卷有缺件记录；
**不能根据当前解析结果断言没有遗漏其他分卷**。
`cache_companions_unverified` / `subcache_discovery_verified=false` 是硬阻塞，
不从未经核对的 offset 或文件名猜出配对关系。没有尝试支持所有 dyld 版本。

仅有文件级 UUID/哈希、版本 plist 或自有样本通过，不证明它们属于同一 Apple
发行构建。缓存中的库、符号链接/runpath 解析、递归库闭包、ISA 可达性、AMFI/
Image4 与信任链、4K 缺页/映射、dyld/launchd 运行及实机能力均未验证。
没有实际目标输入时，ProductVersion/BuildVersion 保持 null，不选“推荐兼容版本”。

## 固定格式依据

已读取 `ci/sources.json`：XNU 固定提交
`d8b80295118ef25ac3a784134bcf95cd8e88109f` / `xnu-10063.141.1`。
以下为实际核对的本地补丁后快照，路径前缀
`/private/tmp/chiron-console-source/`；这些文件哈希固定本次所读定义，
不额外宣称该目录已经独立认证为上游 Git 对象：

| 文件 | SHA-256 |
| --- | --- |
| `EXTERNAL_HEADERS/mach-o/loader.h` | `8780a1e622375833b563f488bc820422c2fb5b8727bb8df8ceaa73ab6f0f88e3` |
| `osfmk/mach/machine.h` | `d8001e350d106083f9571ebd1d30ccbae006487f76ea5bd29cef59bb2ecbf7f8` |
| `osfmk/kern/cs_blobs.h` | `7cd21aa6574bd8b934488da8391eb188599873568e486c4a0583c0cd7e0750e0` |
| `osfmk/kern/debug.h`，`struct _dyld_cache_header` | `024c763b6905a3803fd7e2fc1b74156402bd062167518db3a4da43491dcc41e0` |

FAT64 另核对本机
`/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/usr/include/mach-o/fat.h`，
SHA-256 `19925cdb5fed9b5b7981b05420856f65b99abb54bca34ea95e0e581da8cc580f`。
缓存前缀采用具名顺序 `16s,4I,7Q,16s`，字段来自上述真实结构，
未采用任意猜测的现代缓存偏移。固定 dyld 完整头联网核对未取得内容，
因此未实现它的分卷布局。

现有 `ci/build_ram_selftest.py` 的 `load_commands` / `signature_info` 绑定普通
静态 ARM64、单主 SHA-256/4K 签名，不适合作为 FAT/动态文件通用解析器直接调用。
本工具沿用其边界检查与逐代码槽验证方式，在自身范围内实现解析，不修改共享文件。
测试对现有自有 Mach-O 只读调用其可独立复用的 `signature_info` 交叉核对 CDHash/
codeLimit/页大小，不运行 builder、不编译、不执行目标。

## 验证与真实文件阻塞

测试包含自造且不可执行的 Mach-O/FAT、版本化 CodeDirectory、短尾页、
篡改内容、坏索引/偏移/计数、16K 与 4K 区分、缓存前缀拒绝、缺失分卷、
版本冲突、路径逃逸、软链接/FIFO 和无输入 JSON。
如已有 `dist/source-trace-39770335838/qemu-logs/user-sigbus-capture/launchd.macho`，
仅将其作为**自有解析器交叉验证样本**；缺少时该项跳过，其他测试不依赖 dist。
测试不会将自有 `arm64-apple-ios17.0` 文件认证为 iOS 17 系统。

真实文件阻塞仍是：尚未提供具体版本/构建的原版 dyld、launchd、运行库或完整缓存
及其来源与身份资料。即便后续提供，缓存/分卷等上述不支持项仍会停止准入，
需主 agent 决定下一阶段适配。未修改共享 builder、CI、config 或其他 agent 文件；
由主 agent 将此 CLI 与测试接入统一验收。
