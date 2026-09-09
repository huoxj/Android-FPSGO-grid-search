import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo

from utils.adb import adb
from utils.device_utils import get_tgid

logger = logging.getLogger(__name__)

_FBT_ATTR_PATH = "/sys/kernel/fpsgo/fbt/fbt_attr_by_pid"
_RENDER_INFO_PARAMS_PATH = "/sys/kernel/fpsgo/common/render_info_params"


def fbt_field(*, dump_name: str | None = None, **kw: Any) -> Any:
    """per-pid fbt attr 参数。

    dump_name: render_info_params dump 里的键；缺省 = 字段名。
    （写入 fbt_attr_by_pid 的 cmd 始终用字段名。）

    返回 Any：让静态检查把默认值当字段默认处理（等同 pydantic 原生
    ``Field`` 在 dataclass_transform 下的豁免），避免
    ``Type "FieldInfo" is not assignable to "int"`` 误报。
    """
    return Field(
        **kw,
        json_schema_extra={"fpsgo_io": {"kind": "fbt", "dump_name": dump_name}},
    )


def sysfs_field(node: str, **kw: Any) -> Any:
    """模块/内核 sysfs 参数：``echo {val} > {node}``。"""
    return Field(
        **kw,
        json_schema_extra={"fpsgo_io": {"kind": "sysfs", "node": node}},
    )


class FpsgoParams(BaseModel):
    # 未知键当场报错，防 typo
    model_config = ConfigDict(extra="forbid")

    # ── per-pid fbt 通道 ──
    rescue_enable: int = fbt_field(
        default=1, ge=0, le=1,
        description="Rescue 开关 (0=off, 1=on)",
    )
    blc_boost: int = fbt_field(
        default=100, ge=0,
        description="BLC boost 值 (百分比或偏移量)",
    )
    qr_t2wnt_x: int = fbt_field(
        default=0,
        description="QR t2wnt X 偏移",
    )
    qr_t2wnt_y_p: int = fbt_field(
        default=0,
        description="QR t2wnt Y 正偏移",
    )
    qr_t2wnt_y_n: int = fbt_field(
        default=0,
        description="QR t2wnt Y 负偏移",
    )
    rescue_second_group: int = fbt_field(
        default=1, ge=0,
        description="Rescue second group 设置",
    )
    filter_frame_kmin: int = fbt_field(
        default=3, ge=0, dump_name="ff_k_min",
        description="K-min 滤波窗口大小 (帧数)",
    )
    gcc_enable: int = fbt_field(
        default=1, ge=0, le=2,
        description="GCC 使能 (0=off, 1=on, 2=aggressive)",
    )
    gcc_fps_margin: int = fbt_field(
        default=0,
        description="GCC FPS margin",
    )
    gcc_down_step: int = fbt_field(
        default=10, ge=1,
        description="GCC down step 步长",
    )
    expected_fps_margin: int = fbt_field(
        default=-1,
        description="预期 FPS margin (-1=auto)",
    )
    rescue_second_time: int = fbt_field(
        default=1, ge=1,
        description="Rescue second time 窗口 (秒)",
    )

    # ── sysfs 通道 ──
    rescue_enhance_f: int = sysfs_field(
        "/sys/module/mtk_fpsgo/parameters/rescue_enhance_f",
        default=25, ge=0,
        description="Rescue enhance 因子",
    )
    limit_rfreq: int = sysfs_field(
        "/sys/kernel/fpsgo/fbt/limit_rfreq",
        default=0, ge=0,
        description="大核回救天花板 (kHz)",
    )
    limit_rfreq_m: int = sysfs_field(
        "/sys/kernel/fpsgo/fbt/limit_rfreq_m",
        default=0, ge=0,
        description="中核回救天花板 (kHz)",
    )
    limit_cfreq: int = sysfs_field(
        "/sys/kernel/fpsgo/fbt/limit_cfreq",
        default=0, ge=0,
        description="大核地板 (kHz)",
    )
    limit_cfreq_m: int = sysfs_field(
        "/sys/kernel/fpsgo/fbt/limit_cfreq_m",
        default=0, ge=0,
        description="中核地板 (kHz)",
    )

    @classmethod
    def default(cls) -> FpsgoParams:
        return cls()

    @classmethod
    def read(cls, package_name: str) -> FpsgoParams:
        """从设备读回全量参数，构造 FpsgoParams。"""
        # 1. Get tgid
        tgid = get_tgid(package_name)

        vals: dict[str, int] = {}

        # 2. sysfs 通道：逐节点 cat
        failures: list[str] = []
        for name, fi in _SYSFS_FIELDS:
            node = _io(fi)["node"]
            raw = adb(f"cat {node}")
            try:
                vals[name] = int(raw)
            except (ValueError, TypeError):
                failures.append(f"{node} (raw={raw!r})")
        if failures:
            raise RuntimeError(
                "Failed to read global FPSGO params: " + "; ".join(failures)
            )

        # 3. fbt 通道：render_info_params 单次 dump
        wanted = [(name, _fbt_dump_candidates(name, fi)) for name, fi in _FBT_FIELDS]
        fbt_read = _read_fbt_params(tgid, wanted)
        if fbt_read is None:
            raise RuntimeError(
                f"Failed to read fbt params for tgid={tgid} "
                f"from {_RENDER_INFO_PARAMS_PATH}"
            )
        missing = [name for name, _ in wanted if name not in fbt_read]
        if missing:
            raise RuntimeError(
                f"fbt params missing fields for tgid={tgid}: {missing}"
            )
        vals.update(fbt_read)

        return cls(**vals)

    def apply(self, package_name: str) -> None:
        """全量写参数：fbt 通道 per-pid，sysfs 通道逐节点。"""
        # 1. Get tgid
        tgid = get_tgid(package_name)

        d = self.model_dump()

        # 2. per-pid fbt 通道（cmd = 字段名）
        for name, _ in _FBT_FIELDS:
            adb(f"echo '{name} s {tgid} {d[name]}' > {_FBT_ATTR_PATH}")

        # 3. sysfs 通道
        for name, fi in _SYSFS_FIELDS:
            node = _io(fi)["node"]
            adb(f"echo {d[name]} > {node}")


def _io(fi: FieldInfo) -> dict[str, Any]:
    """取字段的 fpsgo_io 元数据。"""
    extra = fi.json_schema_extra
    meta = extra.get("fpsgo_io") if isinstance(extra, dict) else None
    if not isinstance(meta, dict):
        raise RuntimeError(
            f"fpsgo field '{_field_name_of(fi)}' lacks fpsgo_io metadata: "
            "declare with fbt_field()/sysfs_field()"
        )
    return meta


def _field_name_of(fi: FieldInfo) -> str:
    for name, other in FpsgoParams.model_fields.items():
        if other is fi:
            return name
    return "?"


def _fbt_dump_candidates(name: str, fi: FieldInfo) -> list[str]:
    """read 时在 dump header 里依次尝试的键：字段名优先，其次 dump_name。"""
    dump_name = _io(fi).get("dump_name")
    candidates = [name]
    if dump_name and dump_name != name:
        candidates.append(dump_name)
    return candidates


def _read_fbt_params(
    tgid: str, wanted: list[tuple[str, list[str]]]
) -> dict[str, int] | None:
    """Read per-pid fbt params from render_info_params.

    解析失败（节点不可读 / tgid 块不存在）返回 None → 调用方报错。
    """
    raw = adb(f"cat {_RENDER_INFO_PARAMS_PATH}")
    if not raw:
        logger.warning(f"Cannot read {_RENDER_INFO_PARAMS_PATH}")
        return None
    return _parse_render_info_params(raw, tgid, wanted)


def _parse_render_info_params(
    dump: str, target_tgid: str, wanted: list[tuple[str, list[str]]]
) -> dict[str, int] | None:
    """Parse render_info_params dump, extract fbt params for the target tgid.

    参数名与数值的相对行/列由 dump 头部自动对齐（不写死）：
      1. 头部参数名（第一个 'NEW PID:' 块之前）→ 位置表 name → (row, col)
      2. 定位 'NEW PID: <pid>, <name>, <tgid>' 块（按第三字段 tgid 匹配）
      3. 块后固定行数的数值行，每行用正则提取整数（值之间可能混用
         空格/逗号，如 '1,   -1    0    0,    0,'）
      4. wanted 中每个 (model_field, 候选键) 按候选键依次在位置表查找。

    Returns {model_field: int, ...} 或 None。
    """
    lines = dump.splitlines()

    # ── 1. 定位 meta 行 + 数据块，解析头部参数名 ──
    # 布局：第 0 行 'NEW PID: PID, NAME, TGID'（meta，也是 NEW PID 开头！）
    #       之后若干行参数名
    #       之后每块 'NEW PID: <pid>, <name>, <tgid>' + 固定行数数值行
    pid_idx = [
        i for i, line in enumerate(lines) if line.startswith("NEW PID:")
    ]
    if len(pid_idx) < 2:
        logger.debug(
            "render_info_params dump: "
            "need meta + >=1 block, got %d NEW PID lines",
            len(pid_idx),
        )
        return None

    # 头部参数名：meta 行（pid_idx[0]）之后、第一个数据块（pid_idx[1]）之前
    header: list[list[str]] = []
    for line in lines[pid_idx[0] + 1 : pid_idx[1]]:
        names = [t.strip() for t in line.split(",") if t.strip()]
        if names:
            header.append(names)
    if not header:
        logger.debug("render_info_params dump: empty header")
        return None

    # 位置表：dump 参数名 → (相对行, 列)
    pos: dict[str, tuple[int, int]] = {}
    for r, names in enumerate(header):
        for c, name in enumerate(names):
            pos[name] = (r, c)

    # ── 2. 定位目标 tgid 的块（NEW PID 行的第三字段是 tgid） ──
    block_start = None
    for i in pid_idx[1:]:  # 跳过 meta 行，从第一个数据块开始
        m = re.match(r"NEW PID:\s*(\d+),\s*([^,]+),\s*(\d+)", lines[i])
        if m and m.group(3) == target_tgid:
            block_start = i
            break
    if block_start is None:
        logger.debug(
            "tgid=%s not found in render_info_params dump", target_tgid
        )
        return None

    # ── 3. 按行提取数值 ──
    data: list[list[int]] = []
    for r in range(len(header)):
        idx = block_start + 1 + r
        line = lines[idx] if idx < len(lines) else ""
        vals = [int(x) for x in re.findall(r"-?\d+", line)]
        if len(vals) != len(header[r]):
            logger.warning(
                "render_info_params row %d: expected %d values, got %d",
                r, len(header[r]), len(vals),
            )
            continue  # 该行不取，字段缺失由调用方报错
        data.append(vals)

    # ── 4. 按 wanted 字段取位置 → 值 ──
    result: dict[str, int] = {}
    for field, candidates in wanted:
        dump_name = next((c for c in candidates if c in pos), None)
        if dump_name is None:
            logger.debug(
                "fbt field %s not in render_info_params header", field
            )
            continue
        r, c = pos[dump_name]
        if r < len(data) and c < len(data[r]):
            result[field] = data[r][c]
    return result if result else None


def _partition_fields() -> tuple[
    list[tuple[str, FieldInfo]], list[tuple[str, FieldInfo]]
]:
    """按字段的 fpsgo_io 元数据分流（自动跟随字段声明，零手工名单）。"""
    fbt: list[tuple[str, FieldInfo]] = []
    sysfs: list[tuple[str, FieldInfo]] = []
    for name, fi in FpsgoParams.model_fields.items():
        meta = _io(fi)
        kind = meta.get("kind")
        if kind == "fbt":
            fbt.append((name, fi))
        elif kind == "sysfs":
            sysfs.append((name, fi))
        else:
            raise RuntimeError(f"fpsgo field '{name}': unknown io kind {kind!r}")
    return fbt, sysfs


_FBT_FIELDS, _SYSFS_FIELDS = _partition_fields()

