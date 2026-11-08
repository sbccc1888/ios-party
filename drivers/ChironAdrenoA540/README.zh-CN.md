# ChironAdrenoA540

A540 (chiron GPU) 后端。目前只有第一层：GMU 固件的 legacy fw2 容器解析。

## 为什么不能复用 Adreno740 的解析器

`UniXNU/AdrenoFirmware` 的 `AdrenoFirmwarePlanGmu()` 期望的块格式是

```c
struct { u32 address; u32 length; u32 type; u32 value; }
```

第一个 word 是**块地址**。

而 A540 的 `a540_gpmu.fw2` 第一个 word 是**块的 dword 总数**：

```
+0x00  u32 block_dwords    整块 dword 数（含载荷），必须小于文件的 dword 数
+0x04  u32 block_id        2 = GPMU firmware
+0x08  u32 header_dwords   后面字段 dword 数，不含自身；偶数、非零、≤ 10
+0x0c  { u32 tag; u32 value; } × (header_dwords / 2)
...    payload，起点 dword (header_dwords + 3)，长度 (block_dwords - header_dwords - 2)
```

把 A540 固件喂给 `PlanGmu` 会读到 `address=0x00000b75 len=2`，然后
`address=0x00020000 len=589824` —— 文件只有 11736 字节，第二个块就越界，解析失败。

## 权威参考

MSM8998 内核：

| 位置 | 内容 |
|---|---|
| `drivers/gpu/msm/adreno_a5xx.c:683` | `_load_gpmu_firmware()` 主流程 |
| `drivers/gpu/msm/adreno_a5xx.c:1241` | `_read_fw2_block_header()` 头部校验 |
| `drivers/gpu/msm/adreno_a5xx.h:215-228` | `GPMU_*` / `HEADER_*` 常量 |
| `drivers/gpu/msm/adreno-gpulist.h` | A540 条目（gpmu_major=3 等）|

载荷偏移和长度用的是内核自己的算式，不是推断：

```c
cmds     = data + data[2] + 3;
cmd_size = data[0] - data[2] - 2;
```

## 实测数据（a540_gpmu.fw2，11736 字节 / 2934 dwords）

| 字段 | 值 |
|---|---|
| block_dwords | 2933 |
| block_id | 2 (GPMU firmware) |
| header_dwords | 8 |
| tag 1 (HEADER_MAJOR) | **3** |
| tag 2 (HEADER_MINOR) | **9** |
| tag 3 (HEADER_DATE) | 132123407 |
| tag 4 (HEADER_TIME) | 1316879 |
| payload 起点 | dword 11 |
| payload 长度 | 2923 dwords（11692 字节）|
| GPMU_INST_RAM_SIZE | 4095 dwords，2923 < 4095 ✓ |
| payload[0] | 0x00005ff4（加载长度）|
| payload[1] | 0x0000018d（第一条指令）|

`HEADER_MAJOR/MINOR` 是**字段标签**，不是版本号。真正的版本是紧随其后的 value。

内核 A540 条目要求 `gpmu_major=3, gpmu_minor=0`；固件给 3/9。
内核的检查是 `if ((major > header[i+1]) && header[i+1])`，即只拒绝更新的主版本，
所以 9 > 0 不是问题。

## 测试

```sh
python3 tests/test_chiron_adreno_fw2.py
```

测试用真实固件验证解析结果，再逐一验证 11 种畸形输入被拒绝：
block 尺寸越界、block id 未知、header 为零/奇数/超限/容不下、
文件过短、主版本过新，以及一个已知布局的合成镜像。

## 下一层

按 `research/CHIRON_GPU_A540_DELTA.zh-CN.md` 的顺序：

1. ~~legacy fw2 解析~~（本次完成）
2. GPUCC 时钟（`qcom,gpucc-8998-v2`，8 个时钟）
3. GMU 3.x 启动 + HFI
4. SMMU v2（`qcom,smmu-v2`，micro_mmu_control 0x6000）
5. PFP/PM4 加载 + CP 初始化
6. 命令提交与 fence
