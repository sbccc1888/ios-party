# Chiron 4 KiB 装载边界检查

日期：2026-10-22。原子任务 agent 中途被平台风险检查中止；本记录由主任务
审查其已写出的文件、执行测试并补齐集成后完成。

## 工具与证据范围

`tools/chiron_page_qualification.py` 先核对固定 XNU/UniXNU 快照的内容哈希，
再提取实际 `cs_validate_codedirectory`、`cs_native4k_prepare`、
`cs_validate_hash`、共享缓存 sanity check 和 pager 拷贝循环。
宿主替身提供文件读取、内存分配、只读保护和 CommonCrypto 摘要接口；
这些替身不证明 XNU 的锁、vnode 生命周期、页表或实际 Apple 信任链。

```sh
python3 tools/chiron_page_qualification.py --json --output out/page-qualification.json
python3 -m unittest discover -s tests -p test_page_qualification.py -v
```

本地 10 项测试通过，完整提取运行共通过 203 个宿主场景，
本轮原始报告为 `out/page-qualification-20261022.json`。
完整提取检查只在具备对应固定快照和 macOS
CommonCrypto 时执行；其他 CI 环境明确跳过这些宿主执行项，源码缺失/变化
拒绝检查仍运行。独立工具遇到缺少或变化的源码始终非零退出，不产生通过报告。
ASan/UBSan 覆盖实际提取函数的宿主执行，模拟签名块和文件是自有测试数据。

## 结果

当前配置未显式开启 `XNU_NATIVE_CS_HASH_CACHE`，固定上游默认值为 0。
关闭时实际 CodeDirectory 验证代码拒绝 16 KiB 签名页；开启分支的宿主检查覆盖
完整原块验证、四个子页摘要、尾块、布局与算术边界及资源清理。
这不等于本项目已经启用桥接，也不证明任何目标 iOS 文件通过。

在固定源码的 pager 提取循环中，VM 页面为 4096 字节而 slide 单元为
16384 字节时，单次拷贝跨出逻辑页 12288 字节；相邻四个 4 KiB fault
也会重用 slide index 0。宿主检查使用较大缓冲记录这类逻辑跨度，
不在真实设备制造越界。完整缓存格式和这些路径的可达性仍需目标文件核验。

`package/ChironPageQualification/slide_guard.h` 提供保守的候选跨度检查：
要求 VM 页和 slide 页相同，检查范围、对齐及索引上界。
它未接入内核，不是 16 KiB slide 的 4 KiB 兼容实现。
本轮没有修改签名认证策略、内核缓存映射或任何设备状态。

## 尚缺条件

需要具体目标 dyld/cache 文件、其签名粒度和 slide 格式，才能决定是否符合
当前路径，或是否需要另行实现并验证兼容 pager。
本报告不声明目标缓存兼容、动态用户态运行通过或实机通过。
