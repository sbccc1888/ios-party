# CHRAMFS v1 独立多文件只读 RAM 根卷实验

主任务追加记录：首轮云运行 `39780009249` 的真实 XNU 编译发现挂载结构体
16408 字节超过 16 KiB typed allocation 上限。节点指针表已改为独立分配，
挂载结构与节点表各有静态大小守卫；宿主分配适配器新增同等限制和第三处分配失败覆盖，
原 12 项测试复测通过。真实编译及挂载运行资格仍需后续云端结果。

日期：2026-10-22。任务 A 仅新增 `tools/chiron_ramfs.py`、
`ci/apply_chiron_ramfs.py`、`package/ChironRamfs/`、`tests/test_ramfs.py`、
本文；不修改 init/selftest、既有 N4KROOT builder/校验器、主 CI 入口、
configs、README、RUNTIME_STATUS。没有内核全量构建、云任务、设备操作、
依赖安装、IPSW 下载、提交或推送。共享工作区中的其他改动保留。

## 交付阶段与集成边界

已实现宿主镜像生成/解析、实际供 overlay 使用的公共 C 解析器、
固定补丁后 mockfs 的独立 mount/lookup/getattr/readdir/blockmap 分支。
仅显式应用 overlay 后、根设备 magic 精确为 `CHRAMFS\0` 时启用。
普通单文件和 `N4KROOT\0` v1 仍进入旧分支；没有重写其 ABI、
文件布局或默认构建配置。

本轮验证分为：

| 层级 | 本轮结果与证据边界 |
| --- | --- |
| Host | Python builder/decoder 与生产 C parser 的独立验证；C 不由 Python 结果代替 |
| Host overlay | 编译实际变换后的 mockfs 三个 `.c`，连接声明清楚的宿主 XNU API 适配层；执行真实 overlay 的挂载分流、目录/vnode 查找、getattr、readdir、read/blockmap 调用及清理逻辑；ASan/UBSan 检查 |
| ARM64 parser compile | `arm64-apple-darwin -ffreestanding` 编译公共解析器；不等于整个 mockfs 已通过真实 XNU 头文件编译 |
| XNU archive compile | 由主 agent 的独立可选 archive builder 另行资格验证，本任务不提供成功声明 |
| QEMU / hardware | 未运行；真实 mount、UBC、VM pagein、文件 mmap/执行、设备授权/生命周期均待运行资格 |

宿主适配层的 `cluster_read` 用 fixture 字节测试精确长度和真实 blockmap
调用契约，不包含 XNU VM/cache 实现。`cluster_pagein` 明确返回 `EIO`，
测试只断言 overlay 传入精确 EOF 并向上传播错误，**不把它写成页入成功**。
锁、vnode_create/vflush、dirent 的宿主替身也不能证明 XNU ABI 或并发正确。

## 格式与限制

格式所有整数为 little-endian。新 magic 与旧 `N4KROOT` 完全不同。
采用版本字段拒绝未知版本；没有自动降级新格式。

头部固定 64 字节，Python struct 为 `<8s4I3Q16s`：

| 偏移 | 字段 |
| --- | --- |
| 0 | `magic[8] = CHRAMFS\0` |
| 8 | `uint32 version = 1` |
| 12 | `uint32 header_bytes = 64` |
| 16 | `uint32 record_bytes = 128` |
| 20 | `uint32 node_count`，包含唯一根节点 |
| 24 | `uint64 image_bytes`，必须等于设备/输入精确长度 |
| 32 | `uint64 table_offset = 64` |
| 40 | `uint64 data_offset = align4096(64 + count*128)` |
| 48 | 16 字节保留，全零 |

每个节点固定 128 字节，Python struct 为 `<8I2Q80s`：

| 偏移 | 字段 |
| --- | --- |
| 0 | `uint32 parent_index`，根为 0；其余必须小于自身索引 |
| 4 | `uint32 kind`：1=目录，2=普通文件 |
| 8 / 12 / 16 | `uint32 mode / uid / gid` |
| 20 | `uint32 reserved = 0` |
| 24 | `uint32 name_bytes` |
| 28 | `uint32 reserved = 0` |
| 32 / 40 | `uint64 backing_offset / exact_file_bytes`；目录均为 0 |
| 48 | `name[80]`，有效名称后的字节必须全零 |

根记录索引 0，类型目录，名称长度 0、父索引 0。其余记录只保存单个名称，
父目录必须已出现且为目录；因此无需递归即可拒绝环、自指、缺父目录、
文件充当父目录。同一父目录不允许同名；不同目录下同名为独立文件。
Python builder 按组件元组排序，C parser 接受所有符合父先于子的规范布局，
不要求兄弟排序。文件数据按节点表的文件出现顺序紧密排列，起点 4 KiB 对齐，
末页填零；空文件允许零长度且不占数据页。所有 extent、padding 和总长必须精确，
不接受重叠、间隙、额外尾页或截断。

当前 v1 实现的显式接受界限：

- 整卷最多 32 MiB，长度为 4 KiB 整数倍；元数据也计入总长。
- 最多 128 节点，含根；最多 16 层，根深度 0。
- 绝对路径最多 1023 字节，每个名称最多 79 字节。
- 名称采用可打印 ASCII，允许空格；拒绝 `/`、`\`、NUL、控制字符、
  非 ASCII、空组件、`.` 和 `..`。镜像内不允许路径遍历。
- `mode` 仅支持 `0000..0777` 权限位；文件类型由 kind 表达，拒绝 setid、
  sticky、类型位等未支持位。uid/gid 为无符号 32 位，显式拒绝负数和溢出。
- 没有符号链接、硬链接、压缩、xattr、可写节点、APFS 或 Apple 卷认证。
- 文件内容无内置加密哈希；`inspect` 提供每文件和镜像 SHA-256 供集成绑定。
  mount 验证结构和零填充，不提供内容真实性/签名认证。

32 MiB 是本版本 parser/builder 的实验接受上限，不是 64 位 extent 字段的
编码极限，也不是当前手机传输能力。提高它需要独立版本/边界审查和传输验证。

## 传输容量必须单独核验

现有 N4KROOT builder/kernel 接受上限为 32 MiB；CHRAMFS 实验也选择
32 MiB 卷上限。两者都不能推导出现有 boot image 能传入同样大的根卷。

主 agent 本轮核对实机通过镜像给出的实际 FAT 数据是：

- 物理传输前 24 MiB，逻辑 FAT 卷 48 MiB，cluster=512 字节。
- kernel 长 21,424,328 字节，分配范围 `[792576,22217216)`。
- 旧 root 长 110,592 字节，范围 `[22280704,22391296)`。
- 前 24 MiB 剩余空闲数据空间仅 2,774,016 字节。

这些是主 agent 提供的已核验输入，本任务未重解析该成品。新增/替换 root
的可用空间还依赖 FAT 分配策略、是否回收旧 root，以及其余载荷；不能简单
把 2,774,016 字节当作任意打包方式的根卷上限。仅保证本实验的小 fixture
很小，不宣称能传输任意 Apple dyld 共享缓存或 subcache。没有调整
zero-tail 地址、容量、DarwinBoot owned window 或手机载荷布局。

## 宿主 API 与示例

模块：`tools/chiron_ramfs.py`，仅 Python 标准库。

```python
from chiron_ramfs import Entry, build, parse, read_file

image = build([
    Entry("/", "dir", mode=0o755),
    Entry("/dev", "dir", mode=0o755),
    Entry("/sbin", "dir", mode=0o755),
    Entry("/sbin/launchd", "file", launchd_bytes, mode=0o555),
    Entry("/probe.bin", "file", child_bytes, mode=0o444),
    Entry("/usr", "dir", mode=0o755),
    Entry("/usr/lib", "dir", mode=0o755),
    Entry("/usr/lib/dyld", "file", dyld_bytes, mode=0o555),
])
entries = parse(image)
assert read_file(image, "/usr/lib/dyld") == dyld_bytes
```

此例变量由调用方提供；没有在本任务获取或执行 Apple 文件。
`Entry(path,kind,data=b"",mode=0o555,uid=0,gid=0)` 要求显式根和所有父目录，
不自动补目录、不将不同路径映射到同一槽。`parse` 返回记录列表，
含 path/parent/kind/mode/uid/gid/offset/size/depth；错误抛 `ValueError`。

已附带纯文本 fixture，**不是可执行 init 或内核启动样本**：

```sh
python3 -B tools/chiron_ramfs.py build \
  --manifest package/ChironRamfs/fixtures/manifest.json \
  --output /private/tmp/chramfs-v1-fixture.img
python3 -B tools/chiron_ramfs.py inspect /private/tmp/chramfs-v1-fixture.img
```

manifest schema=1，显式 entries；每项使用 path/type/mode/uid/gid，
普通文件额外 `source`，相对 manifest 所在目录解析。mode 可为八进制字符串
或整数。源输入必须是有界普通文件，拒绝设备、FIFO 和最终 symlink；
不隐式递归打包宿主目录。

## Overlay API、固定身份与基线保留

参考源是 `/private/tmp/chiron-console-source/bsd/miscfs/mockfs/`，
含固定 UniXNU 上游补丁后的两槽实现。上游参考目录为
`/private/tmp/ios-party-research/unixnu`，只读检查了其 mockfs 补丁、
package 与根卷交接契约。这些临时目录没有 Git 元数据；只以实际源码哈希
绑定输入，不将路径存在当作整个上游树来源证明。

应用器固定六个原文件 SHA-256，四个核心值与原
`ci/build_ram_selftest.py.MOCKFS_HASHES` 完全相同，另外固定 `mockfs.h`、
`mockfs_vnops.h`。源码有任何变动或 overlay 已存在，预检即失败。
全部输入、生成结果和目标冲突均预检后才写入；不自动重放/覆盖应用。

```python
from pathlib import Path
from ci.apply_chiron_ramfs import apply, verify

report = apply(Path("/path/to/isolated/patched-xnu"), enable_experimental=True)
report = verify(Path("/path/to/isolated/patched-xnu"))
```

`apply(source, *, enable_experimental=False)` 默认拒绝；`verify(source)` 只读。
均返回含 `schema_version=1`、format/version、baseline_sha256、
overlay_sha256、payload_sha256、experimental、资格标志的字典。
CLI 等价于：

```sh
python3 -B ci/apply_chiron_ramfs.py \
  --source /path/to/isolated/patched-xnu --enable-experimental \
  --report /private/tmp/chramfs-overlay.json
python3 -B ci/apply_chiron_ramfs.py \
  --source /path/to/isolated/patched-xnu --verify
```

只能对独立实验树应用。没有环境变量暗中启用，也没有接入正常内核或自检。
主 agent 若用显式 env/配置触发，须在其独立入口选择上述调用。

身份校验不从可编辑的报告读取“正确哈希”：生成后的六个文件必须匹配
应用器中固定的 `OVERLAY` 哈希表；三个新增文件
`chiron_ramfs.h`、`ramfs_mount.inc`、`ramfs_vnops.inc` 必须逐字节匹配
本任务 package 的当前内容。报告包含它们的 SHA-256，最终构建仍需绑定
项目提交/源码包哈希。恶意同时修改应用器和 package 不在该身份检查的
独立信任边界内；不能将自报哈希称为安全签名。

原 `verify_mockfs` 不修改、不跳过，overlay 树按预期会被它拒绝。
在独立 archive 路线，复制 mockfs 后应用 overlay，使用优先 overlay/bsd
include 路径，单独编译三个 `.c` 并核对 `verify`；保留正常 kernel/root
资格不变。主 agent 的 archive builder 不属于本任务写入范围。

## 内核分支的行为

- mount 先读取 magic；新格式走 C `cr_validate`，读取有界设备页并拒绝
  I/O 错误、非零 residual、非法块大小及超设备范围。仅支持 512/4096 等能
  整除 4096 的设备块大小，不启用旧 32 位 mi_base 内存快捷路径。
- 新节点由受限数组独立拥有，真实父指针关联；不会使用旧三子节点槽。
  仅根标记为 root，其他目录为独立 `MOCKFS_RAM_DIR`。卸载先 vflush，
  再逆序清理所有节点与元数据；不是递归调用不受限的磁盘树。
- lookup 对指定父目录做精确名称比较，不写入 namei 名称内存；正常 VFS
  `.`/`..` 返回对应节点，镜像记录不接受这两个名称。缺项 ENOENT，
  普通文件被当目录 ENOTDIR，末组件写操作 EROFS。未知路径不再别名到两个槽。
- getattr 返回权限、uid/gid、独立 inode、parent id、目录链接数、精确 EOF
  和页对齐分配长度；保留调用者 active 属性掩码。
- readdir 输出 `.`/`..` 和真实子项，支持普通字节游标分页，拒绝负数、
  游标落在记录内部及未支持的 extended/seekoff 模式。
- 现有 read/UBC vnode filesize/pagein 的 exact size 来源为不同 fsnode；
  新 blockmap 使用公共 `cr_map`，将各自文件偏移映射到独立 backing extent。
  负数、溢出、未对齐、空文件/下一整页 EOF、跨越分配末页的请求返回 EINVAL，
  不进入旧 blockmap 的越界 panic。页内 EOF 的已分配零尾仍有明确 backing。
- mount 标记只读；既有 open(FWRITE) 拒绝和默认写操作拒绝保留。
  不支持创建、改名、写文件或建立可写 `/var`。

此为源码实现说明。真实 VFS/VM 的权限调用、块请求形态、并发 vnode 生命周期、
大目录分页、devfs 在真实 `/dev` 挂载等仍需 archive 和运行阶段确认。

## 测试与后续验收

本地完整命令：

```sh
python3 -B -m unittest discover -s tests -p test_ramfs.py -v
```

本轮实际结果：**12 tests，OK，无 skip**；包含本地固定 mockfs 快照的
overlay 测试，最后一轮耗时 2.800 秒。基线追加回归：

```sh
python3 -B -m unittest discover -s tests -p test_ram_selftest.py -k ContainerTests -v
```

结果 **3 tests，OK**。没有执行该文件的 ARM64 SVC 模拟或整个内核构建。
上面的文本 fixture CLI build/inspect 均实际执行通过，产物为
`/private/tmp/chramfs-v1-fixture.img`，12,288 字节、8 节点，
SHA-256 为
`130efd9a6b5fa79509255e1fbe04f2f9fd3553aa3e77ba6157e6cc6e65aa6f47`。
该镜像只有文本文件，不能作为 initrd 启动用户态。

测试覆盖 Python 生成/解析、CLI、元数据、独立同名文件、空文件、79 字节名称、
128 节点和恰好 32 MiB 镜像；拒绝路径遍历、缺父目录、重名、截断、非法保留位、
非零填充、版本/计数/offset/size 溢出。公共 C parser 实际读同一畸形语料，
外加 1200 次确定性 metadata 变异，与 Python decoder 差分比较。
实际 overlay C 文件在 ASan/UBSan 下检查独立 vnode、原 N4KROOT/单文件分支、
多页文件读/EOF、块范围拒绝、目录分页、分配失败、设备错误/短读及卸载回收。
宿主编译仅为原 lookup 的既有未使用 ctx 变量关闭
`-Wunused-but-set-variable`，其余使用 `-Wall -Wextra -Werror`。

测试在没有固定本地 mockfs 快照时会显式跳过 overlay 源码测试，并显示原因；
不得把该 skip 记录为 overlay 已通过。可用 `CHIRON_RAMFS_PINNED_SOURCE`
指定另一份哈希一致的补丁后 XNU 快照。

剩余条件按顺序单独验收：

1. 主 agent 独立 archive 在真实已编译 XNU 树头文件/编译参数下通过；
   不与正常内核链接、替换或覆盖。
2. 经显式实验配置构建新内核，保留原实机通过镜像；为 root、内核、
   overlay 身份、载荷传输范围建立同一份精确产物清单。
3. QEMU 真正 mount 新格式，逐路径核对内容 SHA-256、mode/uid/gid、
   inode 和长度；挂载缺件/畸形 root 应明确失败。
4. 真正文件 read/pread、EOF、文件 mmap/缺页、末页零尾、保护、
   多文件交错读以及子进程执行/回收分别通过。宿主适配器不可替代。
5. 用户操作手机启动同一实验成品并单独记录硬件结果。

Apple dyld/库能否运行还依赖目标文件、架构、签名、安全接口、共享缓存 slide
及传输空间；本实验交付不表示这些闭包已经完成。
