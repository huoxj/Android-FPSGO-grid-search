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

`scripts/build.ps1` 只产出一个 `fpsgo-optim-gui.exe`（`flet pack`，底层是 PyInstaller）。
CLI 通过同一个 exe 的 `--cli` 模式内嵌，不再单独打 `fpsgo-optim.exe` / `fpsgo-webui.exe`。
不需要 Flutter SDK / VS：`flet pack` 用的是预编译的 Flutter 客户端，首次构建下载约 46MB
（缓存在 `~/.flet/client`），运行时从 exe 里解包，不联网。

```powershell
uv run --group gui python scripts\pack_gui.py src\gui_main.py -y -n fpsgo-optim-gui --distpath dist -- `
  --paths src `
  --hidden-import webui.server `
  --collect-data perfetto `
  --collect-data flet_web `
  --add-data "src\gui\assets;assets"
```

- `scripts/pack_gui.py` 是 `flet pack` 的薄包装：flet-cli 1.1.2 在 Windows 上用
  `open(exe, "ab", encoding="utf-8")` 恢复 exe overlay，Python 3 直接 ValueError，
  所以先在站点包里把这一行改掉（幂等）再调 CLI。上游升级后如果这行没了，脚本会报错提醒删掉。
- `--hidden-import webui.server`：结果页是函数内延迟导入，PyInstaller 静态分析看不到，
  漏了打包能成功但点结果页会 ModuleNotFoundError。
- 字体：frozen 时把 `FLET_ASSETS_DIR` 指到 `_MEIPASS/assets`，否则中文是方块。
- `vendor/perfetto/tp_shell_bin.exe`、`perfetto_configs/`、`resources/`、`config.toml`
  仍是 exe 旁边的外部文件：`report.py:52` 和 `config.py` 里是相对 CWD 的路径。

未验证项：Windows 上没跑过（Linux 上验证过同一套 `flet pack` 能出包、能从 exe 里解出客户端）；
真机流程的 UI 表现是用手工构造的 `status.json` 验证的。
