# GUI 设计

目标：给不想改 TOML、不想盯终端的人一个窗口。三步走完「配置 → 搜索 → 看结果」。
CLI 保持原样，GUI 只是它外面的一层。

## 技术选择

Flet（Flutter + Python）。理由：Windows 打包一条命令、控件是原生渲染而不是浏览器套壳、
中文字体和图表（`flet-charts`）都在本地，不依赖 CDN。代价是 Flutter 运行时比较大（~40MB）。

## 结构

```
src/gui_main.py     入口（桌面窗口；GUI_WEB=1 时开浏览器版）
src/gui/app.py      三个页面的 Flet 界面 + 1s 轮询
src/gui_backend.py  配置读写、子进程、状态/日志读取、结果加载
src/status.py       CLI 侧写进度（GUI_STATUS=1 时才启用）
```

数据只有两个方向：

1. GUI 改 `config.toml`，然后以子进程启动 `python src/main.py`（打包后是同目录的
   `fpsgo-optim.exe`），环境变量 `GUI_STATUS=1`。
2. CLI 每变一次状态，原子替换 `output_dir/.gui/status.json`；GUI 每秒读它，并 tail
   `output_dir/grid_search.log`。搜索结果页直接调 `src/webui/server.py` 的
   `load_runs` / `load_pareto`，指标定义和 CLI 完全一致（带 `<trace>.metrics.json` 缓存）。

没有共享内存、没有 socket、没有改 `main.py` 的主循环。CLI 单独跑、GUI 崩掉、进程
被杀，三者互不影响。状态文件放在 `.gui/` 子目录，是因为 `CheckpointMgr` 会扫描
`output_dir` 顶层的 `*.json` 当 run 元数据，放顶层会被当成一条 run。

## status.json 契约

| 字段 | 含义 |
| --- | --- |
| `state` | `searching` / `finished` / `stopped` |
| `total` `done` `skipped` `current` | 总进度（按参数组合 × 重复次数） |
| `stage` `stage_started_at` `stages[]` | 当前 run 的阶段轴：charge / warmup / ready / cut / run |
| `battery` `current_ma` `temps` `targets` | 充电、热机、冷却面板；只在非录制阶段采样，避免打扰测量 |
| `params` `group` `replay` | 当前 run 在跑什么 |
| `run_duration` | 录制时长，用于算录制进度 |

写入由 `StatusWriter` 在一个采样线程里完成（5s 一次，仅 charge/warmup/ready）。
进程异常退出时 `atexit` 把 `state` 标成 `stopped`。

## 交互决定

- **顶部步骤轴**：上一步/下一步按钮 + 地铁线路图。完成过的步骤打勾，但直接跳到第 3 步时
  中间步骤不会假装完成。
- **配置页**：所有可编辑项走表单，非默认值右侧出现重置按钮；`grid` 用「一行一条轴」表达，
  同一行多个参数就是联动轴（值写成 `2800000/2500000`）。「预览参数组合」弹出全部组合。
- **续跑**：进入页面时看 `output_dir` 里的 run 元数据数，少于应有次数就提示「继续上次搜索」；
  「换个目录重新开始」会带时间戳另起目录，不删旧数据。
- **搜索页**：总进度条按 跳过 / 已完成 / 正在跑 / 还没跑 分段；ETA 用已完成 run 的平均耗时；
  当前 run 有独立的阶段轴（含每步开始时间和耗时）；日志原样 tail。
- **结果页**：只保留三张推荐卡 + 包络线图 + 已选点详情 + 可折叠的明细表。
  不展示 trace 文件名、录制时间这些对决策没用的信息。y 轴按「越小越靠上」翻转。
  `jank_s/jank/jank_l` 在 `report.py` 里是**帧数**（gap > 12/16.67/125ms 的帧），不是每秒次数，
  界面统一标「帧」。

## 打包

```bash
flet build windows          # 需要 Flutter SDK
```

产物 `fpsgo-optim-gui.exe` 与 `fpsgo-optim.exe`、`config.toml` 放同一目录：
GUI 会优先启动同目录的 `fpsgo-optim.exe`，找不到才退回当前解释器。

未验证项：Windows 打包没有在本机跑过（Linux 上无法产出 Windows 包）；
真机流程的 UI 表现是用手工构造的 `status.json` 验证的，第一次真机跑需要盯一眼阶段轴。
