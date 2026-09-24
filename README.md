# FPSGO Param Automated Optimization Framework

自动化 FPSGO 参数搜索调优框架。在 MTK Android 设备上自动运行游戏做逐参数组合的实机调参，产出 Perfetto trace 文件及全面地 FPS/卡顿指标报告，支持断点续跑、剪枝早停。

## 工作流程

根据配置的搜索参数空间，遍历每个参数组合执行一次完整 run：

1. 等待设备就绪（如电量、温度到达阈值），保证每次 run 时设备的起始状态接近
2. 初始化设备：亮屏、清理 logcat 等
3. 进入具体游戏流程。对于具体游戏，可能包括重入游戏对局、开启 Perfetto 录制、模拟触摸进行自动化游戏
4. 早停校验，即当前配置在早期帧数明显低于一般情况（可选）
5. 游戏流程结束，停止 Perfetto、关闭游戏、熄屏、保存 trace 与录制元数据
6. 若单 run 失败（如遇到游戏强制护眼休息、预期外的广告弹窗），通过指数退避重试此 run

## 系统要求

- Python >= 3.12
- 已连接并授权 adb 设备；`adb devices` 能显示设备已经连接
- 目标设备的 `adb shell` 需要 root 执行权限
- 报告分析需要主机平台的 Perfetto `trace_processor` 二进制

## 运行

复制 `config.example.toml` 为 `config.toml` 并修改配置

## 配置

使用 TOML 进行配置。默认如下：

```toml
output_dir = "gs/test"         # trace 与元数据输出目录

run_duration = 600             # 单次有效录制时长（秒）
run_max_attempts = 8           # 单点最大重试次数
backoff_initial_secs = 15      # 重试初始退避（秒），之后指数翻倍
early_stop_dur = 60            # 提前停止检测窗口（秒）
early_stop_check_interval = 5  # FPS 采样间隔（秒）
early_stop_fps_threshold = 114 # 低于此平均 FPS 则提前结束/早停
ready_temp_threshold = 35000   # 设备就绪温度阈值（毫摄氏度）

[grid]
# 网格轴定义，见下节

[perfetto]
config_dir = "./perfetto_configs"   # .txtpb 配置目录（sgame.txtpb 等）
record_script = "./record_android_trace"

[sgame]
package_name = "com.tencent.tmgp.sgame"
package_name_short = "cent.tmgp.sgame"   # fpsgo_status 行匹配串
resource_dir = "./resources/sgame"
```

### grid 语法

Grid 网格轴配置用于灵活指定参数搜索组合。每个 toml 键是一条轴，最终搜索的是“轴内同步，轴间组合”的结果。

比如要搜索 a, b, c 三个参数的如下组合：

```
a = 1, b = 1, c = 10
a = 1, b = 1, c = 100
a = 2, b = 2, c = 10
a = 2, b = 2, c = 100
```

也就是，a 和 b 在 `1, 2` 内联合变动；c 在 `10, 100` 内变动；二者进行全组合（笛卡尔积）。对应的 grid 写法为：

```toml
[grid]
"a,b" = [1, 2]
c = [10, 100]
```

进一步地，也支持 a 和 b 联合变动，但各用不同的值：

```toml
[grid]
"a,b" = [[1, 100], [2, 200]]
```

这样会产出如下组合：

```
a = 1, b = 100
a = 2, b = 200
```

也支持指定单值，比如默认 a 参数为 2，但搜索中想让 a 为 200：

```toml
[grid]
a = [200]
```

总结如下：
| 形式        | 示例                                                         | 含义                                     |
| ----------- | ------------------------------------------------------------ | ---------------------------------------- |
| 单键        | `blc_boost = [100]`                                          | 单参数单值（也可多值）                   |
| 联合键·同值 | `"qr_t2wnt_y_p,qr_t2wnt_y_n" = [0, 30]`                      | 逗号分隔两个键，每项同时赋给两键（广播） |
| 联合键·异值 | `"limit_rfreq,limit_rfreq_m" = [[2700000, 2600000], [2800000, 2660000]]` | 每项是长度等于键数的元组，逐键取值       |

## 支持游戏

在调优具体的游戏之前，请务必查看具体**游戏详细文档中的注意事项**等内容。

### 王者荣耀

王者荣耀国服(`com.tencent.tmgp.sgame`)。具体信息参考[王者荣耀](./docs/sgame.md)

### 和平精英

待兼容。此游戏无回放系统，故需要复杂的自动化操作流程来保证每一局负载可基本重现

### 原神

待兼容。情况同和平精英。

## 报告

每次跑完所有 grids 之后，框架会自动对结果进行分析。具体来说，会使用 perfetto `trace_processor` 从 perfetto trace 中提取与计算指标，输出两份：stdout 的 Top-N 表 + `output_dir/report_<时间戳>.txt`（全量 runs 表、按参数组合聚合的 combos 表、各轴边际分析）。

指标口径（均来自 Perfetto `actual_frame_timeline_slice`）：

| 指标      | 含义                                                         |
| --------- | ------------------------------------------------------------ |
| `fps_avg` | 每秒帧数 FPS（按 1s 分桶计算，丢弃首尾不完整桶后取平均）     |
| `fps_std` | FPS 标准差                                                   |
| `jank`    | actual timeline slices 间的 gap 值，在 target_fps 为 120 Hz 情况下，用 > 12ms、> 16.67ms、> 125ms 分为三种严重程度的 jank |
| `j‰`      | `jank / n_frames * 1000`，跨 run 可比（run 时长不一致时用）  |
| `dur_s`   | frame timeline 跨度                                          |

## 注意事项

- 全自动操作真机：会强停/重启游戏、自动进回放、改 sysfs、长时间占屏，跑批期间设备不可他用

- 游戏 UI 自动化依赖 `resources/<game>/` 下的模板图与固定点击坐标，换机型/分辨率需重新截图