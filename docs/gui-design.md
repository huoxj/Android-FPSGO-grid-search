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
| `run_aligned_at` | 游戏 timeline 走到 0s 的时刻；录制计时从这里起算，之前显示「等待对齐中」 |
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
  录制计时跟对局 0s 对齐（`run_aligned_at`），对齐前只显示「正在进入游戏对局等待对齐中」和
  不确定进度条；对齐后显示「对局 mm:ss」与「录制 mm:ss / run_dur」，录制从 0s 后 30s 起算；
  当前 run 有独立的阶段轴（含每步开始时间和耗时）；日志原样 tail。
- **结果页**：只保留三张推荐卡 + 包络线图 + 已选点详情 + 可折叠的明细表。
  不展示 trace 文件名、录制时间这些对决策没用的信息。y 轴按「越小越靠上」翻转。
  `jank_s/jank/jank_l` 在 `report.py` 里是**帧数**（gap > 12/16.67/125ms 的帧），不是每秒次数，
  界面统一标「帧」。

## 错误处理

原则：任何失败都要在界面上说清楚，不能只剩一行日志。

- **启动前**：`adb devices` 查一次，没有已授权设备就不启动子进程，直接弹窗说原因。
- **运行中**：状态文件里的阶段不是录制、且 3 分钟没更新，同时日志里有
  `no devices` / `device offline` / `unauthorized`，判定为卡在重试，弹窗提示（10 分钟最多一次）。
- **进程退出**：非 0 退出码且状态不是 `finished`，读取 `grid_search.log` 与
  `.gui/stdout.log` 的末尾，优先取最后一段 `Traceback`，按关键字归类（设备、权限、配置、缺文件）。
- **界面自身异常**：`ticker` 和结果分析线程里的异常同样走弹窗，附完整 traceback。

弹窗固定三个出口：停止 / 重新编辑搜索配置 / 重跑当前对局（重跑会重新过一遍前置检查）。

## 停止与后台窗口

- **停止 = 请后端自己收尾**。GUI 写 `output_dir/.gui/stop`，CLI 里由 `status.py` 的看门狗线程
  （只在 `GUI_STATUS=1` 时存在）调用 `main.py` 注册的 `_teardown`：熄屏 + 恢复充电，然后退出。
  GUI 最多等 120 秒，等不到才 `terminate()` 硬杀。直接 terminate 只会让手机亮着、充电通路断着，
  和 Ctrl+C 的行为不一样。
- **不留黑窗**。Windows 上每次 spawn 控制台程序都会闪一下，而采样阶段几秒一次 adb。
  `utils/adb.py` 定义 `NO_WINDOW = subprocess.CREATE_NO_WINDOW`，所有 `adb` 调用（含
  `sgame.py` 里直接起的 logcat）和 GUI 起子进程都带上它。
  分析阶段 perfetto 库自己 spawn `tp_shell_bin.exe`，拿不到参数，所以 `gui_backend`
  在 Windows 上把 `subprocess.Popen` 换成自动带 `CREATE_NO_WINDOW` 的子类（只影响 GUI 进程）。

## 字体

界面字体是提交进仓库的子集（`src/gui/assets/fonts/cjk3.otf`），因为 Windows 构建机不一定有
Noto CJK。改了界面文案后要重新生成：

```bash
uv run python scripts/gen_gui_font.py
```

脚本会扫描 `src/gui/app.py`、`gui_backend.py`、`status.py` 里的所有非 ASCII 字符。换文件名是
为了绕开缓存，换完同步 `page.fonts`。漏字的症状是某些汉字直接消失（不是显示成方块）。

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
