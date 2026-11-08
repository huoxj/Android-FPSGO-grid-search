"""grid-search GUI（Flet）——真实接入，不是原型。

- 配置页读写仓库根目录 config.toml
- 搜索页启动 `python src/main.py` 子进程（打包后是 fpsgo-optim.exe），
  轮询 output_dir/gui_status.json 与 grid_search.log
- 结果页复用 src/webui/server.py 的指标计算（perfetto + 磁盘缓存）

运行：
    GUI_WEB=1 uv run python src/gui/app.py    # 浏览器
    uv run python src/gui_main.py             # 桌面窗口
"""

import asyncio
import itertools
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

import flet as ft
import flet_charts as fc

# 以脚本方式跑时（python src/gui/app.py），src 不在 sys.path 上
_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import gui_backend as backend  # noqa: E402
from models.fpsgo_params import FpsgoParams

# ---------------------------------------------------------------- 视觉常量
W = 1120
BG = "#F4F5F7"
CARD = "#FFFFFF"
INK = "#111827"
MUTED = "#374151"
FAINT = "#575F6B"
LINE = "#E5E7EB"
BLUE = "#2563EB"
BLUE_BG = "#EFF6FF"
GREEN = "#16A34A"
GREEN_BG = "#ECFDF3"
AMBER = "#D97706"
AMBER_BG = "#FFF7ED"
RED = "#DC2626"
TRACK = "#EDEFF3"
MONO = {"font_family": "Consolas", "font_family_fallback": ["CJK", "Courier New", "monospace"]}
TF = dict(color=INK, border_color="#B6BEC9", focused_border_color=BLUE,
          border_radius=8, content_padding=ft.Padding(10, 8, 10, 8))
DD = dict(color=INK, border_color="#B6BEC9", focused_border_color=BLUE, bgcolor=CARD)

STEP_NAMES = ["编辑搜索配置", "搜索", "结果分析"]

# 参数清单直接从 pydantic 模型来，避免 GUI 里再抄一份
PARAMS = [(name, int(f.default) if isinstance(f.default, int) else 0,
           f.description or "")
          for name, f in FpsgoParams.model_fields.items()]
P = {p[0]: p for p in PARAMS}

METRICS = {
    "fps": dict(label="平均帧率", unit="fps", lower=False, fmt="{:.1f}"),
    "power": dict(label="平均功耗", unit="W", lower=True, fmt="{:.2f}"),
    "jank": dict(label="掉帧数", unit="帧", lower=True, fmt="{:.0f}"),
    "bjank": dict(label="严重掉帧", unit="帧", lower=True, fmt="{:.0f}"),
    "std": dict(label="帧率波动", unit="fps", lower=True, fmt="{:.1f}"),
}

STAGE_ORDER = {"charge": 0, "warmup": 1, "ready": 2, "cut": 3, "run": 3,
               "teardown": 3}
STAGE_NAMES = ["充电", "热机", "冷却", "正式搜索"]
STAGE_ICONS = [ft.Icons.BOLT, ft.Icons.LOCAL_FIRE_DEPARTMENT,
               ft.Icons.AC_UNIT, ft.Icons.FIBER_MANUAL_RECORD]


def t(s, size=14, color=INK, weight=None, **kw):
    kw.setdefault("font_family", "CJK")
    return ft.Text(s, size=size, color=color, weight=weight, **kw)


def card(*controls, pad=20, gap=12, **kw):
    return ft.Container(
        content=ft.Column(list(controls), spacing=gap),
        bgcolor=CARD, border=ft.Border.all(1, LINE), border_radius=14, padding=pad, **kw)


def section(title, tip=None, extra=None):
    row = [t(title, size=16, weight=ft.FontWeight.W_600)]
    if tip:
        row.append(ft.Icon(ft.Icons.HELP_OUTLINE, size=15, color=FAINT, tooltip=tip))
    row.append(ft.Container(expand=True))
    if extra:
        row.append(extra)
    return ft.Row(row, spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)


def pill(text, fg=MUTED, bg=TRACK):
    return ft.Container(
        content=t(text, size=11, color=fg, weight=ft.FontWeight.W_500),
        bgcolor=bg, border_radius=20, padding=ft.Padding(8, 3, 8, 3))


def dot(color, size=8):
    return ft.Container(width=size, height=size, border_radius=size, bgcolor=color)


def field_col(label, control, tip=None):
    head = [t(label, size=12, color=MUTED)]
    if tip:
        head.append(ft.Icon(ft.Icons.HELP_OUTLINE, size=13, color=FAINT, tooltip=tip))
    return ft.Column([ft.Row(head, spacing=4), control], spacing=6)


def as_int(v, default):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def parse_dt(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except (TypeError, ValueError):
        return None


def fmt_dur(secs):
    secs = max(int(secs), 0)
    if secs < 3600:
        return f"{secs // 60} 分 {secs % 60} 秒"
    return f"{secs // 3600} 小时 {secs % 3600 // 60} 分"


def param_short(params: dict, limit=2) -> str:
    return " · ".join(f"{k} {v}" for k, v in list(params.items())[:limit])


ERROR_RULES = [
    (("no devices", "device not found", "device offline"),
     "手机没有连上",
     "USB 调试没开、数据线松了，或者 adb 服务没起来。先在命令行跑 adb devices 确认能看到设备。"),
    (("unauthorized",),
     "手机没允许这台电脑调试",
     "解锁手机，在弹窗里点「允许 USB 调试」后重试。"),
    (("PermissionError", "Access is denied"),
     "文件被占用或没有权限",
     "输出目录不可写，或者 trace 文件正被别的程序打开。"),
    (("ConfigError", "ValidationError", "validation error"),
     "配置有问题",
     "某个参数超出允许范围，或 config.toml 格式被改坏了。回配置页检查。"),
    (("FileNotFoundError", "No such file"),
     "缺少文件",
     "回放目录、perfetto 配置或 tp_shell_bin.exe 可能不在程序旁边。"),
]


def classify_error(text: str) -> tuple[str, str]:
    low = (text or "").lower()
    for keys, title, cause in ERROR_RULES:
        if any(k.lower() in low for k in keys):
            return title, cause
    return "搜索程序异常退出", "程序内部出错，具体原因看下面的原始报错。"


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        page.title = "FPSGO 参数搜索"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.fonts = {"CJK": "fonts/cjk3.otf"}
        page.theme = ft.Theme(font_family="CJK")
        page.bgcolor = BG
        page.padding = 0
        page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        page.window.width = 1180
        page.window.height = 920

        try:
            self.cfg = backend.load_config()
        except Exception as e:
            page.add(ft.Container(
                content=t(f"读取 config.toml 失败：{e}\n\n把 GUI 和 config.toml 放在同一个目录再启动。",
                          size=14, color=RED), padding=40))
            return
        # 调试/截图用：GUI_STEP=2/3 直接进对应步骤（不改变正常流程）
        self.step = int(os.environ.get("GUI_STEP", "1"))
        self.proc = None
        self.status = None
        self.log_lines = []
        self.log_offset = 0
        self.start_error = None
        self.results = None
        self.results_error = None
        self.loading_results = False
        self.selected = None
        self.visited = {self.step}
        self.group = "all"
        self.xk, self.yk = "fps", "power"
        self._log_drawn = 0
        self._error_shown = False
        self._user_stopped = False
        self._stopped_local = False
        self._replay_ack = False
        self._stopping = False
        self._last_err_dialog = datetime.min
        self._adb_tick = 99

        self.build()
        if self.step >= 2:
            self.build_search()
        if self.step >= 3:
            self.build_results()
        self.body.content = (self.config_view if self.step == 1
                             else self.search_view if self.step == 2 else self.result_view)
        page.add(self.shell)
        self.update_adb()
        if self.step >= 3:
            self.ensure_results()
        self.render()
        page.update()
        page.run_task(self.ticker)

    # ------------------------------------------------------------ 骨架
    def build(self):
        self.body = ft.Container(expand=True)
        self.stepper = self.build_stepper()
        self.adb_dot = dot(FAINT)
        self.adb_text = t("检查中…", size=12, color=MUTED)
        self.adb_pill = ft.Container(
            content=ft.Row([self.adb_dot, self.adb_text], spacing=6),
            bgcolor=TRACK, border_radius=20, padding=ft.Padding(10, 5, 12, 5))
        head = ft.Row([
            ft.Column([
                t("FPSGO 参数搜索", size=22, weight=ft.FontWeight.BOLD),
                t("在手机上自动试参数，找出更流畅、更省电的配置", size=12, color=MUTED),
            ], spacing=2),
            ft.Container(expand=True),
            self.adb_pill,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER)

        self.build_config()
        self.body.content = self.config_view
        shell = ft.Column([head, self.stepper, self.body], spacing=14, expand=True)
        self.shell = ft.Container(content=shell, width=W, expand=True,
                                  padding=ft.Padding(0, 18, 0, 10))

    def build_stepper(self):
        self.nodes, self.conns = [], []
        parts = []
        for i, name in enumerate(STEP_NAMES):
            circle = ft.Container(width=36, height=36, border_radius=18,
                                  alignment=ft.Alignment.CENTER,
                                  border=ft.Border.all(2, LINE), bgcolor=CARD)
            circle.content = t(str(i + 1), size=15, weight=ft.FontWeight.BOLD, color=FAINT)
            nm = t(name, size=14, color=MUTED)
            sub = t("", size=12, color=MUTED)
            parts.append(ft.Row([circle, ft.Column([nm, sub], spacing=1, tight=True)],
                                spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))
            self.nodes.append(dict(circle=circle, name=nm, sub=sub, idx=i))
            if i < 2:
                line = ft.Container(height=2, bgcolor=LINE, margin=ft.Margin(10, 0, 10, 0))
                parts.append(ft.Container(content=line, expand=True))
                self.conns.append(line)

        self.prev_btn = ft.OutlinedButton(
            content=t("上一步", size=13), icon=ft.Icons.ARROW_BACK, height=40,
            on_click=self.on_prev, style=ft.ButtonStyle(padding=ft.Padding(14, 0, 16, 0)))
        self.btn_start = ft.FilledButton(
            content=t("开始搜索", size=13, color=CARD), icon=ft.Icons.PLAY_ARROW,
            height=40, bgcolor=BLUE, on_click=self.on_next,
            style=ft.ButtonStyle(padding=ft.Padding(16, 0, 14, 0)))
        self.btn_new = ft.FilledButton(
            content=t("开始新的搜索", size=13, color=CARD), icon=ft.Icons.PLAY_ARROW,
            height=40, bgcolor=BLUE, on_click=self.on_next,
            style=ft.ButtonStyle(padding=ft.Padding(16, 0, 14, 0)))
        self.btn_back_running = ft.OutlinedButton(
            content=t("回到进行中搜索", size=13, color=BLUE), icon=ft.Icons.UNDO,
            height=40, on_click=lambda e: self.goto(2),
            style=ft.ButtonStyle(padding=ft.Padding(12, 0, 14, 0),
                                 side=ft.BorderSide(1, BLUE)))
        self.btn_view = ft.FilledButton(
            content=t("查看结果", size=13, color=CARD), icon=ft.Icons.INSIGHTS,
            height=40, bgcolor=BLUE, on_click=self.on_next,
            style=ft.ButtonStyle(padding=ft.Padding(14, 0, 12, 0)))
        self.btn_last = ft.FilledButton(content=t("已是最后一步", size=13, color=FAINT),
                                        height=40, disabled=True, bgcolor="#E8EAEE")
        self.next_area = ft.Row([self.btn_start], spacing=8)
        self.step_row = ft.Row([self.prev_btn, ft.Container(width=6),
                                ft.Row(parts, spacing=0, expand=True),
                                ft.Container(width=6), self.next_area],
                               spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        return ft.Container(content=self.step_row, bgcolor=CARD, border_radius=14,
                            border=ft.Border.all(1, LINE), padding=ft.Padding(16, 10, 16, 10))

    # ------------------------------------------------------------ 配置页
    def build_config(self):
        cfg = self.cfg
        self.out_tf = ft.TextField(value=cfg.get("output_dir", "gs/test"), expand=True,
                                   dense=True, text_size=13, **TF,
                                   hint_style=ft.TextStyle(color=FAINT, size=12,
                                                           font_family="CJK"))
        self.grid_col = ft.Column(spacing=10)
        for row in backend.grid_rows(cfg):
            self.grid_col.controls.append(self.axis_row(row["keys"], row["vals"]))

        self.banner = ft.Container(visible=False)
        self.banner_icon = ft.Icon(ft.Icons.WARNING_AMBER, size=20, color=AMBER)
        self.banner_title = t("", size=13, weight=ft.FontWeight.W_600)
        self.banner_text = t("", size=12)
        self.btn_resume = ft.FilledButton(content=t("继续上次搜索", size=12, color=CARD),
                                          bgcolor="#B45309", on_click=self.on_resume)
        self.btn_fresh = ft.TextButton(content=t("换个目录重新开始", size=12, color=MUTED),
                                       on_click=self.on_fresh_dir)
        self.btn_stop_here = ft.OutlinedButton(
            content=t("停止当前搜索", size=12, color=RED), icon=ft.Icons.STOP_CIRCLE,
            height=34, style=ft.ButtonStyle(padding=ft.Padding(8, 0, 10, 0)),
            on_click=self.stop_search)
        self.btn_go_running = ft.TextButton(content=t("回到进行中搜索", size=12, color=BLUE),
                                            on_click=lambda e: self.goto(2))
        self.banner_actions = ft.Row([], spacing=8)
        self.banner.content = ft.Row([
            self.banner_icon,
            ft.Column([self.banner_title, self.banner_text], spacing=2),
            ft.Container(expand=True),
            self.banner_actions,
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.banner.border_radius = 12
        self.banner.padding = ft.Padding(14, 10, 12, 10)

        power = cfg.get("power", {})
        self.dur_col, self.dur_tf = self.num_field(
            "每次录制多长", max(cfg.get("run_duration", 600) // 60, 1), 10, "分钟",
            tip="一局游戏的有效录制时长，不含进游戏和加载（写回 run_duration，单位秒）")
        self.rep_col, self.rep_tf = self.num_field(
            "每个组合跑几次", cfg.get("repeats", 3), 3, "次",
            tip="同一组参数跑几遍。默认每遍换一局回放，用不同对局验证稳定性")
        self.charge_col, self.charge_tf = self.num_field(
            "充电到", power.get("charge_target", 100), 100, "%",
            tip="每次运行前先充到这么满，保证起点一致")
        self.warm_sw = ft.Switch(value=bool(power.get("warmup", True)),
                                 active_color=BLUE, on_change=self.on_form_change)
        self.temps = {}
        self.temps_cols = {}
        for key, label, field, default in (
                ("soc", "SOC 最高温度", "ready_temp_soc_max", 60),
                ("tsx", "机身温度", "ready_temp_tsx", 48),
                ("batt", "电池温度", "ready_temp_battery", 42)):
            col, tf = self.num_field(label, int(cfg.get(field, default * 1000)) // 1000,
                                     default, "℃", width=120)
            self.temps[key] = tf
            self.temps_cols[key] = col

        self.total_text = t("", size=13, color=INK)
        self.grid_pill = pill("", BLUE, BLUE_BG)

        self.adv_fields = {}
        self.adv = ft.ExpansionTile(
            title=t("高级设置（一般不用改）", size=13, color=MUTED),
            controls_padding=ft.Padding(0, 8, 0, 4),
            controls=[self.adv_grid()])

        self.config_view = ft.Column([
            self.banner,
            t("先确认要试哪些参数。其余的保持默认即可，开始搜索时会写回 config.toml。",
              size=13, color=MUTED),
            card(section("结果保存到", "每次运行的记录、日志和 trace 都放这里。换目录等于换一次搜索"),
                 ft.Row([self.out_tf,
                         ft.TextButton(content=t("选择文件夹", size=12, color=MUTED),
                                       on_click=self.pick_dir)], spacing=8),
                 ft.Row([t("这个目录里有记录时，重新进来会提示你继续上次没跑完的搜索。",
                           size=12, color=MUTED)])),
            card(section("配置搜索参数",
                         "每行一条轴。程序把各行的取值做全组合，逐个跑",
                         extra=self.grid_pill),
                 ft.Row([t("一行一个参数：值之间用逗号。例如 ", size=12, color=MUTED),
                         t("100, 150", size=12, color=INK, **MONO), t("（跑 2 次）",
                                                                    size=12, color=MUTED)],
                        wrap=True, spacing=4),
                 ft.Row([t("一行多个参数一起变：每组里用 / 连接，组之间用逗号。例如 ",
                           size=12, color=MUTED),
                         t("2800000/2500000, 2800000/2660000", size=12, color=INK, **MONO),
                         t("（两个参数各自取这两组值）", size=12, color=MUTED)],
                        wrap=True, spacing=4),
                 ft.Row([t("各行之间是全组合：上面两行放一起就是 2 × 2 = 4 个组合。",
                           size=12, color=MUTED)], wrap=True),
                 self.grid_col,
                 ft.Row([ft.TextButton(content=t("＋ 添加参数", size=13, color=BLUE),
                                       on_click=self.on_add_axis),
                         ft.TextButton(content=t("＋ 添加联动参数", size=13, color=BLUE),
                                       on_click=self.on_add_joint),
                         ft.Container(expand=True),
                         ft.OutlinedButton(
                             content=t("预览要搜索的参数组合", size=13),
                             icon=ft.Icons.VISIBILITY_OUTLINED,
                             on_click=self.show_preview, height=38,
                             style=ft.ButtonStyle(side=ft.BorderSide(1, BLUE), color=BLUE)),
                         self.total_text], spacing=14,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER)),
            card(section("跑多久 · 跑几次"),
                 ft.Row([self.dur_col, self.rep_col], spacing=24)),
            card(section("设备准备"),
                 ft.Row([self.charge_col, ft.Column([
                     ft.Row([t("跑前热机", size=12, color=MUTED),
                             ft.Icon(ft.Icons.HELP_OUTLINE, size=13, color=FAINT,
                                     tooltip="先把手机烧热到接近长时间游戏的状态，再等它降到目标温度。"
                                             "这样每次运行的起点一致")], spacing=4),
                     self.warm_sw], spacing=6)], spacing=28),
                 ft.Column([
                     ft.Row([t("就绪温度", size=12, color=MUTED),
                             ft.Icon(ft.Icons.HELP_OUTLINE, size=13, color=FAINT,
                                     tooltip="温度降到这三个值以下才开始跑。这里填的是王者荣耀的推荐值")],
                            spacing=4),
                     ft.Row([self.temps_cols[k] for k in ("soc", "tsx", "batt")],
                            spacing=18),
                 ], spacing=6)),
            self.adv,
            ft.Container(height=40),
        ], spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
        self.refresh_config()

    def num_field(self, label, value, default, suffix, width=110, tip=None):
        tf = ft.TextField(value=str(value), width=width, dense=True, text_size=14,
                          text_align=ft.TextAlign.RIGHT, **TF,
                          keyboard_type=ft.KeyboardType.NUMBER,
                          hint_style=ft.TextStyle(color=FAINT, size=12, font_family="CJK"),
                          suffix=ft.Text(suffix, size=12, color=MUTED, font_family="CJK"))
        reset = ft.IconButton(icon=ft.Icons.REFRESH, icon_size=17, tooltip="恢复默认值",
                              visible=str(value) != str(default),
                              on_click=lambda e: self.reset_field(tf, reset, default))
        tf.on_change = lambda e: self.field_changed(tf, reset, default)
        return field_col(label, ft.Row([tf, reset], spacing=2), tip), tf

    def reset_field(self, tf, btn, default):
        tf.value = str(default)
        btn.visible = False
        tf.update()
        btn.update()
        self.refresh_config()

    def field_changed(self, tf, btn, default):
        btn.visible = tf.value.strip() != str(default)
        btn.update()
        self.refresh_config()

    def on_form_change(self, e=None):
        self.refresh_config()

    def adv_grid(self):
        cfg = self.cfg
        spec = [
            ("attempts", "最大重试次数", cfg.get("run_max_attempts", 8), None, 90),
            ("backoff", "重试初始间隔", cfg.get("backoff_initial_secs", 15), "秒", 90),
            ("early_dur", "早停检测窗口", cfg.get("early_stop_dur", 60), "秒", 90),
            ("early_fps", "早停帧率阈值", cfg.get("early_stop_fps_threshold", 0),
             "0 = 不早停", 90),
            ("profile", "Perfetto 配置", cfg.get("perfetto", {}).get("profile", "sgame"),
             None, 110),
            ("package", "游戏包名",
             cfg.get("sgame", {}).get("package_name", "com.tencent.tmgp.sgame"), None, 240),
            ("port", "结果网页端口", cfg.get("webui_port", 8787), None, 90),
            ("log_level", "日志级别", cfg.get("log_level", "INFO"), None, 90),
        ]
        fields = []
        for key, label, val, unit, width in spec:
            tf = ft.TextField(value=str(val), width=width, dense=True, text_size=12, **TF,
                              hint_style=ft.TextStyle(color=FAINT, size=12, font_family="CJK"),
                              suffix=ft.Text(unit, size=11, color=MUTED, font_family="CJK")
                              if unit else None)
            self.adv_fields[key] = tf
            fields.append(field_col(label, tf))
        out = [ft.Row(fields[i:i + 2], spacing=24, wrap=True)
               for i in range(0, len(fields), 2)]
        return ft.Column(out, spacing=10)

    # ---- 参数行
    def param_dd(self, value):
        return ft.Dropdown(value=value, width=210, dense=True, text_size=13, **DD,
                           options=[ft.DropdownOption(key=k, text=k) for k, *_ in PARAMS],
                           menu_height=320, enable_search=True,
                           hint_style=ft.TextStyle(color=FAINT, size=12, font_family="CJK"),
                           tooltip=f"{P[value][2]}" if value in P else None,
                           on_select=self.on_form_change)

    def used_params(self):
        out = set()
        for r in self.grid_col.controls:
            out |= {dd.value for dd in r.data["dds"]}
        return out

    def axis_row(self, keys, vals):
        dds = [self.param_dd(k) for k in keys]
        text = ", ".join("/".join(str(v) for v in pair) for pair in vals)
        tf = ft.TextField(value=text, expand=True, dense=True, text_size=13, **TF,
                          hint_text=("每组用 / 连接，组间用逗号。例如 2800000/2500000, "
                                     "2800000/2660000" if len(dds) > 1
                                     else "值之间用逗号。例如 100, 150"),
                          hint_style=ft.TextStyle(color=FAINT, size=12, font_family="CJK"),
                          on_change=self.on_form_change)
        reset = ft.IconButton(icon=ft.Icons.REFRESH, icon_size=17, tooltip="恢复默认值")
        delete = ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_size=17, tooltip="删除这一行")
        param_row = ft.Row(spacing=6, wrap=True,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER)
        value_row = ft.Row([tf, reset, delete], spacing=4,
                           vertical_alignment=ft.CrossAxisAlignment.CENTER)
        row = ft.Column([param_row, value_row], spacing=6)
        row.data = dict(dds=dds, tf=tf)

        def rebuild():
            bits = []
            for i, dd in enumerate(row.data["dds"]):
                if i:
                    bits.append(t("/", size=13, color=MUTED))
                bits.append(dd)
            bits += [
                ft.TextButton(content=t("＋ 参数", size=12, color=BLUE),
                              tooltip="这一行再加一个参数，让它和这一行其它参数一起变",
                              on_click=lambda e: add_param()),
                ft.TextButton(content=t("－ 参数", size=12, color=MUTED),
                              tooltip="去掉这一行最后一个参数",
                              visible=len(row.data["dds"]) > 1,
                              on_click=lambda e: drop_param()),
            ]
            param_row.controls = bits
            try:
                param_row.update()
            except Exception:
                pass

        def add_param():
            free = next((p[0] for p in PARAMS if p[0] not in self.used_params()), None)
            if not free:
                return
            old_n = len(row.data["dds"])
            row.data["dds"].append(self.param_dd(free))
            if old_n == 1:
                ints = self.parse(self.grid_col.controls[
                    self.grid_col.controls.index(row)].data["tf"].value) or []
                row.data["tf"].value = ", ".join(f"{v}/{P[free][1]}" for v in ints)
            else:
                pairs = self.parse_pairs(row.data["tf"].value, old_n)
                if pairs:
                    row.data["tf"].value = ", ".join(
                        "/".join([*(str(v) for v in pair), str(P[free][1])]) for pair in pairs)
            rebuild()
            self.refresh_config()

        def drop_param():
            if len(row.data["dds"]) <= 1:
                return
            row.data["dds"].pop()
            n = len(row.data["dds"])
            pairs = self.parse_pairs(row.data["tf"].value, n + 1)
            if pairs:
                row.data["tf"].value = (", ".join(str(p[0]) for p in pairs) if n == 1
                                        else ", ".join("/".join(str(v) for v in p[:n])
                                                        for p in pairs))
            rebuild()
            self.refresh_config()

        delete.on_click = lambda e: self.on_del_axis(row)
        reset.on_click = lambda e: self.reset_axis(row)
        rebuild()
        return row

    def reset_axis(self, row):
        dds = row.data["dds"]
        if len(dds) == 1:
            row.data["tf"].value = str(P[dds[0].value][1])
        else:
            row.data["tf"].value = "/".join(str(P[dd.value][1]) for dd in dds)
        row.data["tf"].update()
        self.refresh_config()

    def on_del_axis(self, row):
        if len(self.grid_col.controls) <= 1:
            return
        self.grid_col.controls.remove(row)
        self.grid_col.update()
        self.refresh_config()

    def on_add_axis(self, e=None):
        free = next((p[0] for p in PARAMS if p[0] not in self.used_params()), None)
        if not free:
            return
        self.grid_col.controls.append(self.axis_row([free], [(P[free][1],)]))
        self.grid_col.update()
        self.refresh_config()

    def on_add_joint(self, e=None):
        used = self.used_params()
        pair = next((p for p in (("limit_cfreq", "limit_cfreq_m"),
                                 ("limit_rfreq", "limit_rfreq_m"))
                     if p[0] not in used and p[1] not in used), None)
        if pair is None:
            free = [p[0] for p in PARAMS if p[0] not in used]
            if len(free) < 2:
                return
            pair = (free[0], free[1])
        self.grid_col.controls.append(
            self.axis_row(list(pair), [(P[pair[0]][1], P[pair[1]][1])]))
        self.grid_col.update()
        self.refresh_config()

    @staticmethod
    def parse(text):
        parts = [p.strip() for p in (text or "").replace("，", ",").split(",") if p.strip()]
        try:
            return [int(p) for p in parts] or []
        except ValueError:
            return []

    @staticmethod
    def parse_pairs(text, n):
        out = []
        for part in (text or "").replace("，", ",").split(","):
            part = part.strip()
            if not part:
                continue
            bits = [b.strip() for b in part.replace("：", "/").replace(":", "/").split("/")]
            if len(bits) != n:
                return None
            try:
                out.append(tuple(int(b) for b in bits))
            except ValueError:
                return None
        return out or None

    def grid_rows_now(self):
        """控件 -> backend 的行模型；格式不对返回 None。"""
        rows = []
        for r in self.grid_col.controls:
            keys = [dd.value for dd in r.data["dds"]]
            if len(set(keys)) != len(keys):
                return None
            if len(keys) == 1:
                ints = self.parse(r.data["tf"].value)
                vals = [(v,) for v in ints]
            else:
                vals = self.parse_pairs(r.data["tf"].value, len(keys))
            if not vals:
                return None
            rows.append({"keys": keys, "vals": vals})
        return rows

    def total_runs(self):
        rows = self.grid_rows_now()
        if not rows:
            return 0
        return backend.total_from_rows(rows, as_int(self.rep_tf.value, 3))

    def refresh_config(self):
        rows = self.grid_rows_now()
        n = backend.total_from_rows(rows, 1) if rows else 0
        rep = max(as_int(self.rep_tf.value, 3), 1)
        total = n * rep
        self.grid_pill.content.value = f"{n} 个组合"
        self.total_text.value = f"共 {n} 个组合 × {rep} 次 = {total} 次运行"
        info = backend.resume_info(
            backend.resolve_output_dir({"output_dir": self.out_tf.value}), total)
        self.update_banner(info)
        label = "继续搜索" if (info["done"] or self._stopped_local) else "开始搜索"
        if self.btn_start.content.value != label:
            self.btn_start.content = t(label, size=13, color=CARD)

    def update_banner(self, info):
        """配置页顶部：正在跑 / 已停止 / 目录里有旧记录，三种状态。"""
        st = self.status or {}
        if self.running():
            self.banner.visible = True
            self.banner.bgcolor = BLUE_BG
            self.banner.border = ft.Border.all(1, "#BFDBFE")
            self.banner_icon.name, self.banner_icon.color = ft.Icons.PLAY_CIRCLE_FILL, BLUE
            self.banner_title.value = "搜索正在运行"
            self.banner_title.color = "#1E3A8A"
            self.banner_text.value = (f"第 {st.get('current')}/{st.get('total')} 次。"
                                      "想改配置就先停止，改完点「继续搜索」，跑过的会自动跳过。")
            self.banner_text.color = "#1E3A8A"
            self.banner_actions.controls = [self.btn_go_running, self.btn_stop_here]
            return
        if self._stopped_local and info["done"]:
            self.banner.visible = True
            self.banner.bgcolor = "#F3F4F6"
            self.banner.border = ft.Border.all(1, LINE)
            self.banner_icon.name = ft.Icons.PAUSE_CIRCLE_OUTLINE
            self.banner_icon.color = MUTED
            self.banner_title.value = "搜索已停止"
            self.banner_title.color = INK
            self.banner_text.value = (f"这个目录已经跑了 {info['done']} 次。"
                                      "改完配置点「继续搜索」，跑过的会自动跳过。")
            self.banner_text.color = MUTED
            self.banner_actions.controls = []
            return
        if info["incomplete"]:
            self.banner.visible = True
            self.banner.bgcolor = AMBER_BG
            self.banner.border = ft.Border.all(1, "#FCD34D")
            self.banner_icon.name, self.banner_icon.color = ft.Icons.WARNING_AMBER, AMBER
            self.banner_title.value = "这个目录里已有跑过的记录"
            self.banner_title.color = "#92400E"
            self.banner_text.value = (f"已有 {info['done']}/{info['total']} 次运行。"
                                      "继续会跳过已经跑过的组合。")
            self.banner_text.color = "#92400E"
            self.banner_actions.controls = [self.btn_resume, self.btn_fresh]
            return
        self.banner.visible = False

    # ---- 交互
    def pick_dir(self, e):
        async def go():
            picker = ft.FilePicker()
            self.page.services.append(picker)
            self.page.update()
            try:
                path = await picker.get_directory_path(dialog_title="选择结果保存目录")
            except Exception as ex:
                self.show_error("打不开文件夹选择框",
                                "当前平台不支持系统文件对话框，直接在输入框里填路径即可。",
                                f"{type(ex).__name__}: {ex}")
                return
            if path:
                self.out_tf.value = path
                self.out_tf.update()
                self.refresh_config()
        self.page.run_task(go)

    def on_resume(self, e):
        self.start_search()

    def on_fresh_dir(self, e):
        stamp = datetime.now().strftime("%m%d_%H%M")
        self.out_tf.value = f"{self.out_tf.value.rstrip('/')}-{stamp}"
        self.out_tf.update()
        self.refresh_config()
        if self.running():
            self.stop_process(then=self.start_search)
        else:
            self.start_search()

    def show_preview(self, e):
        rows = self.grid_rows_now()
        if not rows:
            self.page.show_dialog(ft.AlertDialog(
                bgcolor=CARD,
                title=t("取值格式不对", size=17, color=INK),
                content=t("单参数填 100, 150；联动参数每组写成 2800000/2500000。", size=13, color=INK),
                actions=[ft.TextButton(content=t("知道了", size=13),
                                       on_click=lambda e: self.page.pop_dialog())]))
            return
        keys = [k for r in rows for k in r["keys"]]
        combos = []
        for pick in itertools.product(*[r["vals"] for r in rows]):
            params = {}
            for r, vs in zip(rows, pick):
                params.update(dict(zip(r["keys"], vs)))
            combos.append(params)
        head = ft.Row([t("组合", size=12, color=MUTED, width=60)] +
                      [t(k, size=12, color=MUTED, width=140) for k in keys])
        body = [head] + [
            ft.Row([t(f"#{i + 1}", size=12, color=MUTED, width=60)] +
                   [t(str(c.get(k, "")), size=12, width=140) for k in keys])
            for i, c in enumerate(combos[:40])]
        for row in body:
            row.scroll = ft.ScrollMode.AUTO
        self.page.show_dialog(ft.AlertDialog(
            bgcolor=CARD,
            title=t("将要搜索的参数组合", size=17, weight=ft.FontWeight.W_600, color=INK),
            content=ft.Container(width=680,
                                 height=min(420, 110 + 26 * min(len(body), 12)),
                                 content=ft.Column([
                                     t(f"共 {len(combos)} 个组合 × {self.rep_tf.value} 次 = "
                                       f"{len(combos) * int(self.rep_tf.value or 3)} 次运行",
                                       size=13, color=INK),
                                     ft.Column(body, spacing=6, expand=True,
                                               scroll=ft.ScrollMode.AUTO),
                                 ], spacing=12, expand=True)),
            actions=[ft.FilledButton(content=t("知道了", size=13, color=CARD), bgcolor=BLUE,
                                     on_click=lambda e: self.page.pop_dialog())]))

    def save_form(self) -> bool:
        rows = self.grid_rows_now()
        if not rows:
            self.start_error = "参数取值格式不对，先改一下再开始。"
            return False
        try:
            backend.save_config(
                output_dir=self.out_tf.value.strip(),
                run_duration=int(self.dur_tf.value) * 60,
                repeats=int(self.rep_tf.value),
                charge_target=int(self.charge_tf.value),
                warmup=bool(self.warm_sw.value),
                temps=(int(self.temps["soc"].value), int(self.temps["tsx"].value),
                       int(self.temps["batt"].value)),
                attempts=int(self.adv_fields["attempts"].value),
                backoff=int(self.adv_fields["backoff"].value),
                early_dur=int(self.adv_fields["early_dur"].value),
                early_fps=int(self.adv_fields["early_fps"].value),
                profile=self.adv_fields["profile"].value,
                package=self.adv_fields["package"].value,
                port=int(self.adv_fields["port"].value),
                log_level=self.adv_fields["log_level"].value,
                rows=rows)
            return True
        except Exception as e:
            self.start_error = f"写 config.toml 失败：{e}"
            return False

    # ------------------------------------------------------------ 搜索页
    def build_search(self):
        self.s_head = t("", size=20, weight=ft.FontWeight.BOLD)
        self.s_stats = t("", size=12, color=MUTED)
        self.s_eta = t("", size=12, color=MUTED)
        self.bar = ft.Row(spacing=0)
        self.legend = ft.Row(spacing=18)
        self.run_title = t("", size=15, weight=ft.FontWeight.W_600)
        self.run_chip = pill("", MUTED, TRACK)
        self.run_params = t("", size=11, color=MUTED, width=1020, max_lines=2, **MONO)
        self.stage_row, self.stage_nodes = self.build_stages()
        self.status_box = ft.Container()
        self.log_list = ft.ListView(expand=True, spacing=1, auto_scroll=True)
        self.log_box = ft.Container(content=self.log_list, height=450,
                                    bgcolor="#0F172A", border_radius=10,
                                    padding=ft.Padding(12, 8, 12, 8))
        self.search_error = t("", size=12, color=RED)
        self.btn_stop = ft.OutlinedButton(
            content=t("停止搜索", size=12, color=RED), icon=ft.Icons.STOP_CIRCLE,
            height=34, style=ft.ButtonStyle(padding=ft.Padding(8, 0, 10, 0)),
            on_click=self.stop_search)

        self.search_view = ft.Column([
            ft.Row([ft.Column([self.s_head, self.s_stats], spacing=2),
                    ft.Container(expand=True), self.s_eta,
                    ft.OutlinedButton(content=t("重启搜索", size=12, color=MUTED),
                                      icon=ft.Icons.RESTART_ALT, height=34,
                                      on_click=self.ask_restart),
                    self.btn_stop],
                   spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self.search_error,
            card(section("总进度", "跳过 = 之前已经跑过（续跑时自动跳过），或重试多次仍失败放弃的那一次"),
                 ft.Container(content=self.bar, height=16, expand=True, border_radius=8,
                              clip_behavior=ft.ClipBehavior.ANTI_ALIAS, bgcolor="#E1E4E9"),
                 self.legend),
            card(ft.Row([self.run_title, self.run_chip], spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER),
                 self.run_params,
                 self.stage_row, ft.Divider(height=1, color=LINE), self.status_box),
            card(section("程序日志", extra=pill("实时", GREEN, GREEN_BG)), self.log_box),
        ], spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)

    def build_stages(self):
        nodes, parts, conns = [], [], []
        for i, (name, icon) in enumerate(zip(STAGE_NAMES, STAGE_ICONS)):
            circle = ft.Container(width=30, height=30, border_radius=15,
                                  alignment=ft.Alignment.CENTER,
                                  border=ft.Border.all(2, LINE))
            ic = ft.Icon(icon, size=16, color=FAINT)
            circle.content = ic
            nm = t(name, size=12, color=FAINT)
            tm = t("", size=10, color=FAINT)
            parts.append(ft.Column([circle, nm, tm], spacing=3,
                                   alignment=ft.MainAxisAlignment.CENTER,
                                   horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                   width=140))
            nodes.append(dict(circle=circle, icon=ic, name=nm, time=tm))
            if i < 3:
                line = ft.Container(height=2, bgcolor=LINE, margin=ft.Margin(0, 0, 0, 26))
                parts.append(ft.Container(content=line, expand=True))
                conns.append(line)
        self.stage_conns = conns
        return ft.Row(parts, spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER), nodes

    def legend_item(self, color, label):
        return ft.Row([dot(color, 7), t(label, size=11, color=MUTED)], spacing=5)

    # ------------------------------------------------------------ 结果页
    def build_results(self):
        self.rec_row = ft.Row(spacing=12)
        self.chart_holder = ft.Container()
        self.detail = ft.Container(width=240)
        self.table_holder = ft.Container()
        self.results_head = t("", size=12, color=MUTED)
        self.results_empty = ft.Container(visible=False)
        self.loading_card = card(ft.Row([
            ft.ProgressRing(width=18, height=18, stroke_width=2, color=BLUE),
            t("正在分析 trace，第一次会比较慢（每个 trace 只算一次，之后走缓存）", size=12,
              color=MUTED)], spacing=10))
        self.group_seg = ft.SegmentedButton(
            segments=[ft.Segment(value="all", label=t("全部", size=12))],
            selected=["all"], show_selected_icon=False, on_change=self.on_group)
        self.xdd = ft.Dropdown(value="平均帧率", width=142, dense=True, text_size=12, **DD,
                               options=[ft.DropdownOption(key=m["label"], text=m["label"])
                                        for m in METRICS.values()],
                               on_select=lambda e: self.set_metric("x", e.control.value))
        self.ydd = ft.Dropdown(value="平均功耗", width=142, dense=True, text_size=12, **DD,
                               options=[ft.DropdownOption(key=m["label"], text=m["label"])
                                        for m in METRICS.values()],
                               on_select=lambda e: self.set_metric("y", e.control.value))
        self.result_view = ft.Column([
            ft.Row([ft.Column([
                t("结果分析", size=20, weight=ft.FontWeight.BOLD), self.results_head,
            ], spacing=2), ft.Container(expand=True), self.group_seg],
                vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self.results_empty,
            self.rec_row,
            card(section("包络线", "橙线上的配置，没有一个能在帧率和功耗上同时比它更好。左上角最好"),
                 ft.Row([field_col("横轴", self.xdd), field_col("竖轴", self.ydd),
                         ft.Container(expand=True),
                         ft.Row([dot("#CBD2DA", 7), t("全部运行", size=11, color=MUTED),
                                 dot("#F59E0B", 7), t("包络线", size=11, color=MUTED),
                                 dot(BLUE, 7), t("当前选择", size=11, color=MUTED)],
                                spacing=6)]),
                 ft.Row([self.chart_holder, self.detail], spacing=16,
                        vertical_alignment=ft.CrossAxisAlignment.START)),
            ft.ExpansionTile(title=t("全部运行记录", size=13, color=MUTED),
                             controls_padding=ft.Padding(0, 8, 0, 4),
                             controls=[self.table_holder]),
            ft.Container(height=4),
        ], spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)

    # ------------------------------------------------------------ 导航
    def on_prev(self, e):
        if self.step > 1:
            self.goto(self.step - 1)

    def on_next(self, e):
        if self.step == 1:
            self.ask_start()
        elif self.step == 2:
            self.goto(3)

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ask_start(self):
        if self.running():
            self.page.show_dialog(ft.AlertDialog(
                bgcolor=CARD,
                title=t("现在有一次搜索正在运行", size=17, weight=ft.FontWeight.W_600, color=INK),
                content=t("开始新的搜索会先停掉它。已经跑完的结果会保留，不会丢。",
                          size=13, color=INK),
                actions=[ft.TextButton(content=t("取消", size=13),
                                       on_click=lambda e: self.page.pop_dialog()),
                         ft.FilledButton(content=t("停止并重新开始", size=13, color=CARD),
                                         bgcolor=RED, on_click=self._stop_and_start)]))
        else:
            self.start_search()

    def _stop_and_start(self, e=None):
        self.page.pop_dialog()
        self.stop_process(then=self.start_search)

    def start_search(self):
        self.start_error = None
        ok, why = backend.adb_devices()
        if not ok:
            self.show_error(
                f"手机没连上（{why}）",
                "USB 调试没开、数据线松了，或者 adb 服务没起来。"
                "先在命令行跑 adb devices，能列出设备再回来重试。")
            return
        if not self.save_form():
            self.show_error("配置没有保存成功", "按提示改好后再点开始搜索。",
                            self.start_error or "")
            return
        info = backend.resume_info(backend.resolve_output_dir({"output_dir": self.out_tf.value}),
                                   self.total_runs())
        if info["done"] == 0 and not self._replay_ack:
            self.ask_replays()
            return
        self._start_now()

    def ask_replays(self):
        rep = max(as_int(self.rep_tf.value, 3), 1)
        mins = as_int(self.dur_tf.value, 10) + 5
        self.page.show_dialog(ft.AlertDialog(
            bgcolor=CARD, modal=True,
            title=t("开始前确认回放", size=17, weight=ft.FontWeight.W_600, color=INK),
            content=ft.Container(width=560, height=140, content=ft.Column([
                t(f"请确保「我的回放」下至少有 {rep} 个回放，且每局时长不低于 {mins} 分钟。",
                  size=14, color=INK),
                t("每个组合要跑不同对局；回放不够或太短，跑到后面对局会出错。",
                  size=12, color=MUTED)], spacing=10)),
            actions=[ft.TextButton(content=t("取消", size=13),
                                   on_click=lambda e: self.page.pop_dialog()),
                     ft.FilledButton(content=t("已准备好，开始搜索", size=13, color=CARD),
                                     bgcolor=BLUE, on_click=self._ack_replays)]))

    def _ack_replays(self, e=None):
        self.page.pop_dialog()
        self._replay_ack = True
        self._start_now()

    def _start_now(self):
        self.cfg = backend.load_config()
        out = self.cfg["output_dir"]
        self.results = None
        self.selected = None
        self.status = None
        self.log_lines = []
        self.log_offset = 0
        self._log_drawn = 0
        self._error_shown = False
        self._user_stopped = False
        self._stopped_local = False
        try:
            self.proc = backend.start_search(out)
        except Exception:
            self.show_error("启动搜索进程失败",
                            "exe 可能被安全软件拦住了，或输出目录不可写。",
                            traceback.format_exc())
            return
        self.goto(2)

    def _mark_stopped(self):
        self._user_stopped = True
        self._stopped_local = True
        st = self.status or {}
        st["state"] = "stopped"
        st["stopped_at"] = datetime.now().isoformat(timespec="seconds")
        st["error"] = "已手动停止"
        self.status = st

    def stop_process(self, then=None):
        """请后端自己收尾（熄屏、恢复充电）再退出，等不到才硬杀。"""
        self.page.run_task(self._stop_flow, then)

    async def _stop_flow(self, then=None):
        self._user_stopped = True      # 正常停止，不要让 check_process 弹错误
        self._stopping = True
        proc = self.proc
        if proc is not None and proc.poll() is None:
            backend.request_stop(backend.resolve_output_dir(self.cfg))
            for _ in range(120):
                if proc.poll() is not None:
                    break
                await asyncio.sleep(1)
            else:
                backend.stop_search(proc)
        self.proc = None
        self._stopping = False
        self._mark_stopped()
        self.render()
        self.page.update()
        if then is not None:
            then()

    def stop_search(self, e=None):
        self.stop_process()

    # ---- 错误
    def check_stuck(self):
        """CLI 遇到设备掉线会自己重试十几分钟，别让用户干等。"""
        if self.proc is None or self._error_shown:
            return
        st = self.status or {}
        if st.get("stage") == "run" or st.get("state") != "searching":
            return                      # 录制阶段本来就长时间不更新状态
        upd = parse_dt(st.get("updated_at"))
        if not upd or (datetime.now() - upd).total_seconds() < 180:
            return
        if (datetime.now() - self._last_err_dialog).total_seconds() < 600:
            return
        raw = self.error_text()
        if not any(k in raw.lower() for k in
                   ("no devices", "device offline", "unauthorized")):
            return
        self._last_err_dialog = datetime.now()
        self.show_error("手机连接断了，程序在重试",
                        "插好数据线、确认手机解锁并允许 USB 调试后，点「重跑当前对局」。"
                        "不想等就点「停止」。", raw)

    def error_text(self) -> str:
        out = backend.resolve_output_dir(self.cfg)
        chunks = []
        for name in ("grid_search.log", ".gui/stdout.log"):
            try:
                with open(Path(out) / name, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    f.seek(max(size - 8000, 0))
                    chunks.append(f.read().decode("utf-8", "replace"))
            except OSError:
                continue
        text = "\n".join(chunks)
        i = text.rfind("Traceback (most recent call last)")
        if i >= 0:
            return text[i:].strip()
        lines = [l for l in text.splitlines() if " E " in l or "ERROR" in l]
        return "\n".join(lines[-10:]) or text[-1200:].strip()

    def show_error(self, title, cause, raw=""):
        self._error_shown = True
        parts = [t("发生了什么", size=11, color=FAINT), t(title, size=14, color=INK),
                 t("可能的原因", size=11, color=FAINT), t(cause, size=13, color=MUTED)]
        if raw:
            parts += [t("原始报错", size=11, color=FAINT),
                      ft.Container(height=200, bgcolor="#0F172A", border_radius=8,
                                   padding=10,
                                   content=ft.Column(
                                       [t(raw[-4000:], size=11, color="#E5E7EB",
                                          selectable=True, **MONO)],
                                       scroll=ft.ScrollMode.AUTO, expand=True))]
        self.page.show_dialog(ft.AlertDialog(
            bgcolor=CARD, modal=True,
            title=t("出错了", size=18, weight=ft.FontWeight.BOLD, color=INK),
            content=ft.Container(width=760, height=430 if raw else 180,
                                 content=ft.Column(parts, spacing=8)),
            actions=[ft.TextButton(content=t("停止", size=13, color=MUTED),
                                   on_click=self._err_stop),
                     ft.OutlinedButton(content=t("重新编辑搜索配置", size=13, color=BLUE),
                                       on_click=self._err_edit),
                     ft.FilledButton(content=t("重跑当前对局", size=13, color=CARD),
                                     bgcolor=BLUE, on_click=self._err_retry)]))

    def _err_stop(self, e=None):
        self.page.pop_dialog()
        self.stop_process()

    def _err_edit(self, e=None):
        self.page.pop_dialog()
        self._error_shown = False
        self.goto(1)

    def _err_retry(self, e=None):
        self.page.pop_dialog()
        self._error_shown = False
        self.stop_process(then=self.start_search)

    def check_process(self):
        self.check_stuck()
        if self.proc is None:
            return
        rc = self.proc.poll()
        if rc is None:
            return
        self.proc = None
        if self._user_stopped:
            return
        st = self.status or {}
        if rc == 0 and st.get("state") == "finished":
            return
        raw = self.error_text() or f"进程退出码 {rc}"
        title, cause = classify_error(raw + f"\n(exit {rc})")
        self.show_error(title, cause, raw)

    def ask_restart(self, e=None):
        if not self.running():
            self.start_search()
            return
        self.page.show_dialog(ft.AlertDialog(
            bgcolor=CARD,
            title=t("重新启动搜索？", size=17, weight=ft.FontWeight.W_600, color=INK),
            content=t("会先停掉当前进程，再用同一份配置重新开始。已经跑完的 run 会在断点里被跳过。",
                      size=13, color=INK),
            actions=[ft.TextButton(content=t("取消", size=13),
                                   on_click=lambda e: self.page.pop_dialog()),
                     ft.FilledButton(content=t("重新启动", size=13, color=CARD), bgcolor=BLUE,
                                     on_click=self._restart)]))

    def _restart(self, e=None):
        self.page.pop_dialog()
        self.stop_process(then=self.start_search)

    def goto(self, step):
        self.step = step
        self.visited.add(step)
        if step == 2 and not hasattr(self, "search_view"):
            self.build_search()
        if step == 3 and not hasattr(self, "result_view"):
            self.build_results()
        self.body.content = (self.config_view if step == 1
                             else self.search_view if step == 2 else self.result_view)
        if step == 3:
            self.ensure_results()
        self.render()
        self.page.update()

    # ------------------------------------------------------------ 轮询
    async def ticker(self):
        while True:
            await asyncio.sleep(1)
            try:
                if self.step == 2:
                    # 状态文件可能来自上一个进程（比如 GUI 重启过），所以不依赖 running()
                    self.poll_status()
                    self.poll_log()
                self.check_process()
                if self.step == 1:
                    self._adb_tick += 1
                    if self._adb_tick >= 5:
                        self._adb_tick = 0
                        self.update_adb()
                self.render()
                self.page.update()
            except Exception:
                if not self._error_shown:
                    self.show_error("界面刷新出错",
                                    "程序内部错误，可能是状态文件或日志读到一半。",
                                    traceback.format_exc())
                continue

    def update_adb(self):
        ok, text = backend.adb_devices()
        self.adb_dot.bgcolor = GREEN if ok else RED
        self.adb_text.value = text
        self.adb_text.color = "#166534" if ok else RED
        self.adb_pill.bgcolor = GREEN_BG if ok else "#FEF2F2"

    def poll_status(self):
        if self._stopped_local:
            return
        out = backend.resolve_output_dir(self.cfg)
        st = backend.read_status(out)
        if st:
            if st.get("state") == "searching" and self.proc is None:
                upd = parse_dt(st.get("updated_at"))
                if upd and (datetime.now() - upd).total_seconds() > 90:
                    st["state"] = "stopped"
                    st["stopped_at"] = st.get("updated_at")
            self.status = st
        if self.proc is not None and self.proc.poll() is not None and self.status is None:
            self.status = {"state": "stopped",
                           "error": f"进程已退出（exit {self.proc.returncode}）"}

    def poll_log(self):
        out = backend.resolve_output_dir(self.cfg)
        lines, self.log_offset = backend.read_log(out, self.log_offset)
        if not lines:
            return
        for line in lines:
            color = CARD
            if " W " in line:
                color = AMBER
            elif " E " in line:
                color = RED
            self.log_lines.append(ft.Text(line, size=11, color=color, selectable=True,
                                          **MONO))
        if len(self.log_lines) > 400:
            self.log_lines = self.log_lines[-400:]

    # ------------------------------------------------------------ 渲染
    def render(self):
        for i, n in enumerate(self.nodes):
            done = (i + 1) in self.visited and i < self.step - 1
            cur = i == self.step - 1
            if done:
                n["circle"].bgcolor, n["circle"].border = GREEN, ft.Border.all(2, GREEN)
                n["circle"].content = ft.Icon(ft.Icons.CHECK, size=18, color=CARD)
                n["name"].color, n["name"].weight = INK, ft.FontWeight.W_500
            elif cur:
                n["circle"].bgcolor, n["circle"].border = BLUE, ft.Border.all(3, "#BFDBFE")
                n["circle"].content = t(str(i + 1), size=15, color=CARD,
                                        weight=ft.FontWeight.BOLD)
                n["name"].color, n["name"].weight = BLUE, ft.FontWeight.W_600
            else:
                n["circle"].bgcolor, n["circle"].border = CARD, ft.Border.all(2, LINE)
                n["circle"].content = t(str(i + 1), size=15, color=FAINT)
                n["name"].color, n["name"].weight = MUTED, None
        for i, c in enumerate(self.conns):
            c.bgcolor = GREEN if i < self.step - 1 else LINE

        st = self.status or {}
        subs = [f"{self.total_runs()} 次运行",
                (f"正在第 {st.get('current')}/{st.get('total')} 次" if self.running()
                 else f"已完成 {st.get('done', 0)} 次" if st.get("done") else "还没开始"),
                ""
                ]
        res_n = len(self.results[2]) if self.results else 0
        subs[2] = f"包络线 {res_n} 个" if self.step == 3 else (
            f"可以先看 {st.get('done')} 次" if st.get("done") else "还没有结果")
        for i, node in enumerate(self.nodes):
            node["sub"].value = subs[i]

        self.prev_btn.disabled = self.step == 1
        if self.step == 1:
            self.next_area.controls = ([self.btn_back_running, self.btn_new]
                                       if self.running() else [self.btn_start])
            self.refresh_config()
        elif self.step == 2:
            self.btn_view.content = t("查看已跑结果" if self.running() else "查看结果",
                                      size=13, color=CARD)
            self.next_area.controls = [self.btn_view]
            self.render_search()
        else:
            self.next_area.controls = [self.btn_last]

    def render_search(self):
        st = self.status or {}
        total = max(st.get("total", 0), 0)
        done = st.get("done", 0)
        skipped = st.get("skipped", 0)
        current = st.get("current", 0)
        state = st.get("state")
        # 一个 run 结束后 stage 变成 teardown/None 到下一次 run_start 之间，
        # 没有人真的在跑，别把它算成「正在跑」，否则没跑数会少 1
        busy = st.get("stage") not in (None, "teardown")
        active_now = 1 if (current and state == "searching" and busy) else 0
        left = max(total - done - skipped - active_now, 0)
        self.btn_stop.disabled = not self.running()
        if state == "finished":
            self.s_head.value = f"搜索已完成，共 {total} 次"
        elif state == "stopped":
            self.s_head.value = "搜索已停止"
        elif busy and current:
            self.s_head.value = f"正在第 {current} 次，共 {total} 次"
        elif done:
            self.s_head.value = f"已完成 {done} 次，共 {total} 次"
        else:
            self.s_head.value = "等待搜索进程..."
        self.s_stats.value = f"跳过 {skipped} · 已完成 {done} · 还没跑 {left}" if total else ""
        if self._stopping:
            self.s_head.value = "正在停止…"
            self.s_stats.value = "等程序熄屏、恢复充电后退出"
        self.search_error.value = (self.start_error or st.get("error") or "")

        started = parse_dt(st.get("started_at"))
        if state in ("finished", "stopped"):
            self.s_eta.value = ""
        elif done and started:
            avg = (datetime.now() - started).total_seconds() / 60 / done
            eta = datetime.now().timestamp() + (avg * (left + 1) * 60)
            eta_dt = datetime.fromtimestamp(eta)
            need = int(avg * (left + 1))
            self.s_eta.value = (f"已完成 {done} 次，平均 {avg:.1f} 分钟/次 · "
                                f"预计 {eta_dt:%H:%M} 完成（还需 {need // 60} 小时 {need % 60} 分）")
        else:
            self.s_eta.value = "第一次还没跑完，暂时算不出总耗时" if total else ""

        segs = [("#B7BEC9", skipped), (GREEN, done), (BLUE, active_now),
                ("#E1E4E9", left)]
        self.bar.controls = [ft.Container(expand=c, height=16, bgcolor=col)
                             for col, c in segs if c > 0]
        self.legend.controls = [self.legend_item("#B7BEC9", f"跳过 {skipped}"),
                                self.legend_item(GREEN, f"已完成 {done}"),
                                self.legend_item(BLUE, f"正在跑 {active_now}"),
                                self.legend_item("#E1E4E9", f"还没跑 {left}")]

        params = st.get("params") or {}
        if busy and current:
            self.run_title.value = f"第 {current} 次运行"
        elif current:
            self.run_title.value = f"第 {current} 次运行已结束"
        else:
            self.run_title.value = "当前没有运行中的 run"
        self.run_chip.content.value = (f"第 {(st.get('replay') or 0) + 1} 轮"
                                       if st.get("replay") is not None else "")
        self.run_params.value = "  ".join(f"{k}={v}" for k, v in params.items())

        stages = st.get("stages") or []
        stage_name = st.get("stage")
        active = STAGE_ORDER.get(stage_name, -1)
        for i, node in enumerate(self.stage_nodes):
            if i < active:
                node["circle"].bgcolor, node["circle"].border = GREEN, ft.Border.all(2, GREEN)
                node["icon"].color, node["icon"].size = CARD, 14
                node["name"].color = INK
                rec = next((s for s in stages if STAGE_ORDER.get(s["name"]) == i), None)
                beg, end = parse_dt(rec.get("started_at") if rec else None), \
                    parse_dt(rec.get("ended_at") if rec else None)
                if beg and end:
                    node["time"].value = f"{beg:%H:%M} · {fmt_dur((end - beg).total_seconds())}"
                else:
                    node["time"].value = ""
            elif i == active:
                node["circle"].bgcolor, node["circle"].border = BLUE, ft.Border.all(3, "#BFDBFE")
                node["icon"].color, node["icon"].size = CARD, 16
                node["name"].color = BLUE
                node["time"].value = ("收尾中" if stage_name == "teardown"
                                      else "进行中" if state == "searching"
                                      else "已停止")
            else:
                node["circle"].bgcolor, node["circle"].border = CARD, ft.Border.all(2, LINE)
                node["icon"].color = FAINT
                node["name"].color = FAINT
                node["time"].value = ""

        for i, line in enumerate(self.stage_conns):
            line.bgcolor = GREEN if i < active else LINE

        self.status_box.content = self.stage_panel(st, active)
        if self._log_drawn != len(self.log_lines):
            self.log_list.controls = self.log_lines
            self._log_drawn = len(self.log_lines)

    def stage_panel(self, st, active):
        if active < 0:
            return ft.Column([t("搜索进程还没有进入任何阶段。日志在下面实时更新。",
                                size=12, color=MUTED)], spacing=8)

        stage_name = st.get("stage")
        started = parse_dt(st.get("stage_started_at"))
        ref = datetime.now()
        if st.get("state") != "searching":
            ref = parse_dt(st.get("stopped_at") or st.get("updated_at")) or ref
        elapsed = (ref - started).total_seconds() if started else 0
        targets = st.get("targets") or {}
        temps = st.get("temps") or {}
        stopped = st.get("state") != "searching"

        if stage_name == "charge":
            pct = st.get("battery")
            return ft.Column([
                ft.Row([t(f"{pct if pct is not None else '--'}%", size=34,
                          weight=ft.FontWeight.BOLD),
                        t("目标 100%", size=12, color=MUTED),
                        ft.Container(expand=True),
                        t(f"充电电流 {st.get('current_ma') or '--'} mA", size=12, color=MUTED),
                        t("已用 " + fmt_dur(elapsed), size=12, color=MUTED)],
                       spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.ProgressBar(value=(pct or 0) / 100, height=10, color=GREEN,
                               bgcolor=TRACK, border_radius=5),
                t("充电阶段被中断。" if stopped else
                  "屏幕已关闭充电中。充满后自动进入下一步。", size=12, color=MUTED),
            ], spacing=10)

        if stage_name in ("warmup", "ready", "cut"):
            hot = stage_name == "warmup"
            rows = []
            for zone, name in (("zone0", "SOC"), ("zone44", "机身"), ("zone53", "电池")):
                cur = (temps.get(zone) or 0) / 1000
                tgt = (targets.get(zone) or 0) / 1000
                ok = cur > tgt if hot else (cur < tgt + 0.5 and cur > 0)
                rows.append(ft.Row([
                    t(name, size=12, color=MUTED, width=40),
                    t(f"{cur:.1f}℃", size=16, weight=ft.FontWeight.W_600, width=80),
                    t(f"目标 {'高于' if hot else '低于'} {tgt:.1f}℃", size=11, color=MUTED),
                    ft.Container(expand=True),
                    t("已达成" if ok else f"还差 {abs(cur - tgt):.1f}℃", size=11,
                      color=GREEN if ok else AMBER)], spacing=10))
            hint = ({"warmup": "回放以 4x 播放把手机烧热；温度到目标以上后进入冷却。",
                     "ready": "游戏已退出，等温度降到目标区间就自动开始。",
                     "cut": "温度已达标，正在断开充电并校验电池电流。"}[stage_name]
                    if not stopped else "这一步被中断了。")
            return ft.Column([
                ft.Row([t({"warmup": "正在热机", "ready": "正在冷却",
                           "cut": "正在断电测量"}[stage_name], size=13, color=INK),
                        ft.Container(expand=True),
                        t("已用 " + fmt_dur(elapsed), size=12, color=MUTED)]),
                *rows, t(hint, size=12, color=MUTED)], spacing=8)

        if stage_name == "teardown":
            return ft.Column([t("这次运行结束了，正在收尾：熄屏、恢复充电。",
                                size=12, color=MUTED)], spacing=8)

        # run
        dur = st.get("run_duration") or 600
        running = st.get("state") == "searching"
        aligned = parse_dt(st.get("run_aligned_at"))
        if aligned is None:
            return ft.Column([
                ft.Row([t("正在进入游戏对局等待对齐中" if running else "已停止", size=13),
                        ft.Container(expand=True),
                        ft.ProgressRing(width=16, height=16, stroke_width=2, color=BLUE,
                                        visible=running)]),
                ft.ProgressBar(value=None if running else 0, height=10, color=BLUE,
                               bgcolor=TRACK, border_radius=5),
                t("进游戏、进回放，等对局开始的 0 秒信号。对齐之前不计时。",
                  size=12, color=MUTED)], spacing=10)

        game = max((ref - aligned).total_seconds(), 0)
        rec = int(max(game - 30, 0))       # 0s→30s 是下发参数、等战斗开始，不算录制
        secs = int(min(rec, dur))
        return ft.Column([
            ft.Row([t("正在录制" if running else "已停止", size=13),
                    ft.Container(expand=True),
                    t(f"对局 {int(game) // 60}:{int(game) % 60:02d}", size=13, color=MUTED),
                    t(f"录制 {secs // 60}:{secs % 60:02d} / {dur // 60}:00", size=18,
                      weight=ft.FontWeight.W_600)], spacing=10),
            ft.ProgressBar(value=min(secs / max(dur, 1), 1), height=10, color=BLUE,
                           bgcolor=TRACK, border_radius=5),
            t("这次录制被中断，不会计入结果。" if stopped else
              "已切断充电（电池单独供电），回放对局正在自动操作。录满设定时长后停止并保存；"
              "统计从对局第 30 秒开始，前面加载和参数下发不计入。", size=12, color=MUTED),
        ], spacing=10)

    # ------------------------------------------------------------ 结果加载
    def ensure_results(self):
        if self.results is not None or self.loading_results:
            return
        self.loading_results = True
        self.results_error = None
        self.rec_row.controls = [self.loading_card]
        self.page.run_task(self._load_results)

    async def _load_results(self):
        out = backend.resolve_output_dir(self.cfg)
        try:
            runs, combos, front = await asyncio.to_thread(backend.load_results, out)
            self.results = (runs, combos, front)
            self.build_result_widgets()
        except Exception as e:
            self.results_error = str(e)
        finally:
            self.loading_results = False
            self.render()
            self.page.update()

    def build_result_widgets(self):
        runs, combos, front = self.results
        self.results_head.value = (f"{len(runs)} 次运行，{len(combos)} 个配置。"
                                   if runs else "这个目录里还没有可分析的 run。")
        self.results_empty.visible = not runs
        self.results_empty.content = card(
            ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=18, color=MUTED),
                    t("跑完至少一次运行后，这里会出现包络线和推荐配置。", size=13, color=MUTED)],
                   spacing=8))
        reps = max(int(self.rep_tf.value or 3), 1)
        self.group_seg.segments = (
            [ft.Segment(value="all", label=t("全部", size=12))] +
            [ft.Segment(value=str(g), label=t(f"第 {g} 轮", size=12))
             for g in range(1, reps + 1)])
        self.refresh_results()

    # ---- 结果数据
    @staticmethod
    def run_metric(run, key):
        if key == "power":
            mw = run.get("power_avg_mw")
            return (mw / 1000) if mw is not None else None
        return {"fps": run.get("fps_avg"), "jank": run.get("jank_s"),
                "bjank": run.get("jank_l"), "std": run.get("fps_std")}[key]

    @staticmethod
    def combo_metric(c, key):
        if key == "power":
            mw = c.get("power_avg_mw")
            return (mw / 1000) if mw is not None else None
        return {"fps": c.get("fps_avg"), "jank": c.get("jank_s"),
                "bjank": c.get("jank_l"), "std": c.get("fps_std")}[key]

    def runs_view(self):
        if not self.results:
            return []
        runs = self.results[0]
        if self.group == "all":
            return runs
        g = int(self.group)
        return [r for r in runs if (r.get("replay_index") or 0) + 1 == g]

    def on_group(self, e):
        self.group = e.control.selected[0]
        self.refresh_results()
        self.page.update()

    def set_metric(self, axis, label):
        key = next(k for k, m in METRICS.items() if m["label"] == label)
        if axis == "x":
            self.xk = key
        else:
            self.yk = key
        self.refresh_results()
        self.page.update()

    def combo_key(self, params):
        return tuple(sorted(params.items()))

    def refresh_results(self):
        if not self.results:
            return
        runs = self.runs_view()
        combos = self.results[1]
        front = self.results[2]
        if self.selected is None and front:
            self.selected = self.combo_key(front[0]["params"])
        sel = next((c for c in combos if self.combo_key(c["params"]) == self.selected),
                   combos[0] if combos else None)
        self.rec_row.controls = [self.rec_card(c, i, c.get("on_front"))
                                 for i, c in enumerate(self.pick_cards()[:3])]
        self.chart_holder.content = self.build_chart(runs, front, sel)
        self.detail.content = self.build_detail(sel) if sel else ft.Container()
        self.table_holder.content = self.build_table(runs)

    def pick_cards(self):
        combos = self.results[1]
        front = self.results[2] or combos
        if not front:
            return []
        fps_max = max(front, key=lambda c: c.get("fps_avg") or 0)
        pw = [c for c in front if c.get("power_avg_mw") is not None]
        pw_min = min(pw, key=lambda c: c["power_avg_mw"]) if pw else front[0]
        mid = [c for c in front if c not in (fps_max, pw_min)]
        balanced = mid[0] if mid else fps_max
        out = [(balanced, "最均衡"), (fps_max, "帧率最高"), (pw_min, "最省电")]
        seen, picked = set(), []
        for c, role in out:
            k = self.combo_key(c["params"])
            if k in seen:
                continue
            seen.add(k)
            picked.append(dict(c, _role=role))
        return picked

    def rec_card(self, c, i, on_front):
        role = c.get("_role", "推荐")
        metrics = ft.Row([
            self.metric_block("帧率", f"{c.get('fps_avg') or 0:.1f}", "fps"),
            self.metric_block("功耗", f"{(c.get('power_avg_mw') or 0) / 1000:.2f}", "W"),
            self.metric_block("掉帧数", f"{c.get('jank_s') or 0:.0f}", "帧")], spacing=18)
        box = ft.Container(
            content=ft.Column([
                ft.Row([ft.Container(width=26, height=26, border_radius=13,
                                     bgcolor=BLUE if i == 0 else BLUE_BG,
                                     alignment=ft.Alignment.CENTER,
                                     content=t(str(i + 1), size=12,
                                               color=CARD if i == 0 else BLUE,
                                               weight=ft.FontWeight.BOLD)),
                        t(role, size=14, weight=ft.FontWeight.W_600),
                        ft.Container(expand=True),
                        pill("包络线", "#B45309", "#FEF3C7") if on_front else ft.Container()],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                t(param_short(c["params"]), size=11, color=MUTED, **MONO),
                metrics], spacing=8),
            bgcolor=CARD, border_radius=14, padding=16, expand=True,
            border=ft.Border.all(2, BLUE if self.combo_key(c["params"]) == self.selected
                                 else LINE),
            on_click=lambda e, k=self.combo_key(c["params"]): self.select(k))
        return box

    def metric_block(self, label, value, unit):
        return ft.Column([t(label, size=10, color=FAINT),
                          ft.Row([t(value, size=22, weight=ft.FontWeight.BOLD),
                                  t(unit, size=10, color=MUTED)], spacing=3,
                                 vertical_alignment=ft.CrossAxisAlignment.END)],
                         spacing=0)

    def select(self, key):
        self.selected = key
        self.refresh_results()
        self.page.update()

    def build_chart(self, runs, front, sel=None):
        xk, yk = self.xk, self.yk
        mx, my = METRICS[xk], METRICS[yk]
        pts = [(self.run_metric(r, xk), self.run_metric(r, yk), r) for r in runs]
        pts = [(x, y, r) for x, y, r in pts if x is not None and y is not None]
        if not pts:
            return ft.Container(content=t("没有可画的指标（缺功耗或帧率）", size=12, color=MUTED),
                                padding=20)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        px = (x1 - x0) * 0.15 or abs(x1) * 0.05 or 1
        py = (y1 - y0) * 0.2 or abs(y1) * 0.05 or 0.2
        x0, x1, y0, y1 = x0 - px, x1 + px, y0 - py, y1 + py
        neg = my["lower"]

        def fy(v):
            return -v if neg else v

        def ticks(lo, hi, n=4):
            return [lo + (hi - lo) * i / n for i in range(n + 1)]

        left = fc.ChartAxis(
            labels=[fc.ChartAxisLabel(value=fy(v),
                                      label=t(my["fmt"].format(v), size=10, color=MUTED))
                    for v in ticks(y0, y1)],
            title=t(f"{my['label']} ({my['unit']}" + ("，越小越靠上)" if my["lower"] else ")"),
                    size=11, color=MUTED))
        bottom = fc.ChartAxis(
            labels=[fc.ChartAxisLabel(value=v, label=t(mx["fmt"].format(v), size=10, color=MUTED))
                    for v in ticks(x0, x1, 5)],
            title=t(f"{mx['label']} ({mx['unit']})", size=11, color=MUTED))

        def meta(title, color, rec):
            return dict(title=title, color=color,
                        x=self.run_metric(rec, xk), y=fy(self.run_metric(rec, yk)),
                        fps=rec.get("fps_avg"), power=(rec.get("power_avg_mw") or 0) / 1000,
                        jank=rec.get("jank_s"), std=rec.get("fps_std"),
                        label=param_short(rec.get("params") or {}))

        meta_dots = [meta(f"一次运行 · 第 {(r.get('replay_index') or 0) + 1} 轮", "#CBD2DA", r)
                     for _, _, r in pts]
        front_pts = [(self.combo_metric(c, xk), self.combo_metric(c, yk), c) for c in front]
        front_pts = [(x, y, c) for x, y, c in front_pts if x is not None and y is not None]
        fpts = sorted(front_pts, key=lambda p: p[0])
        meta_env = [meta(f"包络线 · {c.get('n')} 次平均", "#F59E0B", c)
                    for _, _, c in fpts]

        dots = fc.LineChartData(
            stroke_width=0, color="#CBD2DA",
            point=fc.ChartCirclePoint(radius=4.5, color="#CBD2DA"),
            points=[fc.LineChartDataPoint(x, fy(y), show_tooltip=False) for x, y, _ in pts])
        env = fc.LineChartData(
            color="#F59E0B", stroke_width=2.5,
            point=fc.ChartCirclePoint(radius=6, color="#F59E0B", stroke_color=CARD,
                                      stroke_width=2),
            points=[fc.LineChartDataPoint(x, fy(y), show_tooltip=False)
                    for x, y, _ in fpts])
        series = [dots, env]
        metas = [meta_dots, meta_env]
        if sel is not None:
            sx, sy = self.combo_metric(sel, xk), self.combo_metric(sel, yk)
            if sx is not None and sy is not None:
                series.append(fc.LineChartData(
                    stroke_width=0,
                    point=fc.ChartCirclePoint(radius=7, color=BLUE, stroke_color=CARD,
                                              stroke_width=2),
                    points=[fc.LineChartDataPoint(sx, fy(sy), show_tooltip=False)]))
                metas.append([meta("当前选择", BLUE, sel)])
        self.chart_series = metas
        self.chart_range = (x0, x1, min(fy(y0), fy(y1)), max(fy(y0), fy(y1)))
        self._hover = None
        chart = fc.LineChart(
            data_series=series, width=720, height=380,
            min_x=x0, max_x=x1, min_y=self.chart_range[2], max_y=self.chart_range[3],
            left_axis=left, bottom_axis=bottom,
            horizontal_grid_lines=fc.ChartGridLines(interval=(y1 - y0) / 4,
                                                    color="#EEF0F3", width=1),
            vertical_grid_lines=fc.ChartGridLines(interval=(x1 - x0) / 5,
                                                  color="#F5F6F8", width=1),
            border=ft.Border.all(1, "#F0F1F4"), bgcolor=CARD,
            on_event=self.on_chart_hover)
        self.chart_tip = ft.Container(
            visible=False, width=215, bgcolor="#111827", border_radius=10,
            padding=ft.Padding(10, 8, 10, 8),
            shadow=ft.BoxShadow(blur_radius=14, color="#33000000", offset=ft.Offset(0, 4)))
        return ft.Stack([chart, self.chart_tip], width=720, height=380)

    def on_chart_hover(self, e):
        tip = getattr(self, "chart_tip", None)
        if tip is None:
            return
        if e.type == fc.ChartEventType.POINTER_EXIT or not e.spots:
            if tip.visible:
                tip.visible = False
                self.page.update()
            return
        spot = e.spots[0]
        try:
            m = self.chart_series[spot.bar_index][spot.spot_index]
        except (IndexError, TypeError):
            return
        if getattr(self, "_hover", None) == (spot.bar_index, spot.spot_index):
            return
        self._hover = (spot.bar_index, spot.spot_index)
        x0, x1, y0, y1 = self.chart_range
        px = 56 + (m["x"] - x0) / (x1 - x0) * (720 - 68)
        py = 12 + (y1 - m["y"]) / (y1 - y0) * (380 - 54)
        tw, th = 215, 100
        tip.left = min(max(px - tw / 2, 6), 720 - tw - 6)
        tip.top = py - th - 10 if py > th + 16 else py + 14
        tip.content = ft.Column([
            ft.Row([dot(m["color"], 7), t(m["title"], size=11, color="#E5E7EB")], spacing=6),
            t(f"{m['fps']:.1f} fps · {m['power']:.2f} W", size=15, color=CARD,
              weight=ft.FontWeight.W_600),
            t(f"掉帧 {m['jank']:.1f} 次/秒 · 波动 {m['std']:.1f}", size=11, color="#CBD5E1"),
            t(m["label"], size=11, color="#94A3B8")], spacing=3)
        tip.visible = True
        self.page.update()

    def build_detail(self, c):
        rows = []
        for key in ("fps", "power", "jank", "bjank", "std"):
            val = self.combo_metric(c, key)
            unit = METRICS[key]["unit"]
            rows.append(ft.Row([t(METRICS[key]["label"], size=12, color=MUTED),
                                ft.Container(expand=True),
                                t("—" if val is None else
                                  f"{METRICS[key]['fmt'].format(val)} {unit}".strip(),
                                  size=13, weight=ft.FontWeight.W_500)], spacing=6))
        params = [ft.Row([t(k, size=11, color=MUTED), t(str(v), size=11, **MONO)],
                         spacing=6, alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
                  for k, v in (c.get("params") or {}).items()]
        return ft.Column([
            ft.Row([t(param_short(c["params"], 2), size=13, weight=ft.FontWeight.W_600,
                      expand=True, max_lines=2),
                    pill("包络线", "#B45309", "#FEF3C7") if c.get("on_front")
                    else pill("未进包络线", MUTED, TRACK)], spacing=6),
            ft.Column(rows, spacing=7),
            ft.Divider(height=1, color=LINE),
            t("这次用的参数", size=11, color=FAINT),
            ft.Column(params, spacing=5),
            ft.Divider(height=1, color=LINE),
            t(f"共 {c.get('n')} 次运行的均值", size=11, color=MUTED)], spacing=10)

    def build_table(self, runs):
        rows = [ft.Row([t("轮次", size=11, color=FAINT, width=60),
                        t("参数", size=11, color=FAINT, width=420),
                        t("帧率", size=11, color=FAINT, width=80),
                        t("功耗", size=11, color=FAINT, width=80),
                        t("掉帧", size=11, color=FAINT, width=80)], spacing=0)]
        for r in sorted(runs, key=lambda x: x.get("time_iso") or ""):
            g = (r.get("replay_index") or 0) + 1
            pw = r.get("power_avg_mw")
            rows.append(ft.Row([
                t(f"第 {g} 轮", size=11, color=MUTED, width=60),
                t(param_short(r.get("params") or {}, 4), size=11, color=INK, width=420,
                  **MONO),
                t("—" if r.get("fps_avg") is None else f"{r['fps_avg']:.1f}",
                  size=11, width=80),
                t("—" if pw is None else f"{pw / 1000:.2f} W", size=11, width=80),
                t("—" if r.get("jank_s") is None else f"{r['jank_s']:.1f}",
                  size=11, width=80)], spacing=0))
        return ft.Column(rows, spacing=6)


HERE = Path(__file__).resolve().parent


def main(page: ft.Page):
    App(page)


def run():
    web = os.environ.get("GUI_WEB") == "1"
    if getattr(sys, "frozen", False):
        # onefile 把资源解压在 _MEIPASS；flet 优先读 FLET_ASSETS_DIR
        base = Path(getattr(sys, "_MEIPASS", HERE))
        os.environ.setdefault("FLET_ASSETS_DIR", str(base / "assets"))
    ft.run(main, view=ft.AppView.FLET_APP_WEB if web else ft.AppView.FLET_APP,
           port=int(os.environ.get("GUI_PORT", "8550")), no_cdn=True,
           assets_dir=str(HERE / "assets"))


if __name__ == "__main__":
    run()
