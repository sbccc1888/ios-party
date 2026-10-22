# DarwinBoot 交接阶段诊断

日期：2026-10-21。零尾部传输镜像已实机进入 UniXNU/DarwinBoot。用户随后确认屏幕存在顶部绿色横条。结合固定加载器源码，支持 `ExitBootServices` 成功返回并进入 `DarwinEnter`；仍不能证明 XNU 第一条指令、初始化或 PID 1 已运行。

## 绿色条的边界

原 `Entry.S` 在进入 `DarwinEnter` 后立即画绿色条，然后才执行：

1. 按 owned window 遍历缓存行，执行 `DC CIVAC`；照片中的窗口为 1 GiB。
2. 执行指令缓存维护及屏障。
3. 清除 SCTLR_EL1 的 M/C/I 位，维护 TLB。
4. `BR x1` 分支到 XNU 入口。

原版后续不再修改绿条，因此“原版只有绿色”不能定位为缓存操作已经挂死；它也可能已经执行了后续操作但没有新的屏幕输出。`CONFIG_XNU_BOOT_PROGRESS=n` 与这个加载器自身标记无关。

## 分阶段标记

`scripts/chiron-handoff-stages.S` 保留原来的缓存范围算法、缓存/MMU/TLB 操作及启动参数，增加三条色带：

| 自上而下 | 已到达的阶段 |
| --- | --- |
| 绿 | `ExitBootServices` 后，`DarwinEnter` 开始 |
| 蓝 | owned window 缓存清理、指令缓存维护及屏障完成 |
| 红 | MMU/缓存关闭和 TLB 维护完成，接下来分支到 XNU |

每条占实际 framebuffer 的 1/16；当前 1080×2160 / row 4320 模式下各约 135 行。判读必须针对这个新镜像，不能反过来用新规则解释原版只有一条绿条的画面。

即使三条全部出现，也只证明交接代码到达分支前，不能直接宣布 XNU 已运行。颜色后来被其他代码覆盖时，应结合视频/后续日志继续判断。

## 产物内修改

`tools/build_chiron_handoff_stages.py` 只接受已工作的零尾部镜像：

```text
14c51d4478e7e5e7437f5d444f919b3ec23a337d10d6259dd185e08c9d28aaa9
```

检查并解开实际 FD 内的 LZMA FV 后，确认 DarwinBoot PE 与原独立成品一致：

```text
a74961df659b638b0ea458e44314ab2ca84b27074c29c56ccbdb9c433fb20edb
```

仅替换代码节最后的 `DarwinEnter` 函数，从 148 字节扩展为 212 字节，使用原始统一 `0xCC` 链接填充区；其入口 RVA 保持 `0x4cbc`。增加的代码不改变 PE 文件长度、raw section 尺寸或下个 section 地址，只更新 `.text` VirtualSize。对非固定版本、已占用或非统一填充、有安全目录或非零 PE 校验字段的输入拒绝打补丁。

内部 FV 的 83 个文件中，其他 82 个文件保持原字节，DarwinBoot 的 FFS/FV 校验重新验证。重新压缩时保留原 LZMA 五字节属性（含 16 MiB 字典），并写入 EDK2 所需的已知解压长度；独立解码结果与修改后的完整内部 FV 一致。

零尾部入口前缀、DTB、24 MiB RAM 传输前缀全部保持原字节，XNU/init 文件未修改。因此继续沿用已经实机到达 DarwinBoot 的完整逻辑卷，不为本次测试修改内核。

## 验证

- 23 项回归检查通过：PE 布局与统一填充、未知/签名布局拒绝、FFS 数据/头校验、嵌套 FV 往返与非目标文件保持、LZMA 已知长度与属性等。
- 对实际补丁 PE 中的交接指令运行 4 项 Unicorn 场景：对齐/非对齐窗口、无 framebuffer、零 framebuffer 大小。
- 缓存行轨迹与原算法匹配；stage 顺序、色带值、x0..x3、SP/LR 均符合预期，最终分支至 `0x403f9488`。
- 模拟仅为 identity-mapped RAM，测试窗口缩小；没有执行真实 1 GiB 窗口清理，没有模拟手机 UEFI 页表和实际缓存。
- 模拟初始 M=0，只检查 C/I 控制位转换及原操作编码/顺序，不作为实机 MMU 转换成功的证明。
- 模拟在 XNU 目标地址停止，没有执行 XNU。

报告与模拟预览：

```text
dist/ram-boot-handoff-stages/handoff-report.json
dist/ram-boot-handoff-stages/validation/emulation-report.json
dist/ram-boot-handoff-stages/validation/stripe-preview.png
```

## 实机命令

输出 `dist/ram-boot-handoff-stages/chiron-ram-init-handoff-stages.img`，26,693,632 字节：

```text
17d0c5b0e09daa37916f9c20129dcd89cd558aca0e3ed6257c54fc914214f6a5
```

```sh
fastboot boot /Users/mac/Documents/ChatGPT/ios_party/dist/ram-boot-handoff-stages/chiron-ram-init-handoff-stages.img 2>&1 | tee /tmp/chiron-handoff-stages.log
```

本次 FV 重新压缩后 boot kernel_size 比原零尾部镜像缩小，ramdisk 文件偏移减少一页；没有声称原始容器偏移不变。传输前缀及入口地址检查保持不变，后者不匹配时仍由零尾部入口拒绝继续。该分阶段镜像尚待实机反馈。
