# 把 Mu-Silicium 固件封装成 chiron 认的 boot image

## 问题

Mu-Silicium 的 `build_uefi.py` 产出的 `Mu-chiron.img` 刷入时被拒绝：

```
fastboot: Booting FAILED (remote: 'BootImage is Incomplete')
```

## 原因

两个 boot image 的头部结构不同：

| 字段 | SMP8.img（能启动）| Mu-chiron.img（被拒）|
|---|---|---|
| `header_version` | 0 | **1** |
| `page_size` | **4096** | **2048** |
| `kernel_addr` | **0x8000** | **0x10008000** |
| `tags_addr` | **0x100** | **0x10000100** |
| `ramdisk_size` | 25165824 | **5** |

Mu-Silicium 用的是 `base = 0x10000000` 的**绝对地址**、`page_size = 2048`、`header_version = 1`；
而 chiron 的 bootloader 认的是本项目 `board.toml` 里记录的：

```toml
[bootimg.image]
header_version = 0
pagesize = 4096
base = 0
kernel_offset = 0x8000
ramdisk_offset = 0x1000000
tags_offset = 0x100
```

## 解法

固件载荷本身不用动——Mu-Silicium 的 FV 已经带了它自己的 boot shim：

```
Mu-chiron.img 的 kernel 段: gzip(3145904 字节) + DTB(425571 字节)
3145904 = boot shim 176 + FV 3145728
```

只需换容器。用 `ci/pack_firmware.py` 里同样的做法重新封装：

```python
decompressed = gzip.decompress(原 kernel 段前缀)     # 3145904 字节
kernel = gzip.compress(decompressed, mtime=0) + dtb
page = bytearray(4096)
page[:8] = b'ANDROID!'
struct.pack_into('<10I', page, 8,
                 len(kernel), 0x8000,      # kernel_addr 相对
                 0, 0x1000000,             # 无 ramdisk
                 0, 0,
                 0x100, 4096,              # tags_addr, page_size
                 0, version)
```

结果：

```
MuProbe-v0.img:  hv=0  page=4096  kernel_addr=0x8000  tags=0x100  ramdisk=0
```

刷入结果：**`Booting OKAY`**。

## 注意

`ci/pack_firmware.py` 不能直接用：它要求源固件是 0x600000 字节且带 ARM64 Image 头
（那是本项目自己的 `chiron-unixnu.fd` 格式），而 Mu-Silicium 的 FV 是 3145728 字节的裸 FV。
所以借的是它的**封装逻辑**，不是它的输入校验。
