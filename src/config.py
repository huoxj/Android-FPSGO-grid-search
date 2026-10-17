from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings, SettingsConfigDict,
    TomlConfigSettingsSource
)


class PerfettoConfig(BaseModel):

    config_dir: str = Field(
        default="./perfetto_configs",
        description="Perfetto config file dir"
    )

# ========== Game Specific Configurations ===========

class SgameConfig(BaseModel):
    
    package_name: str = "com.tencent.tmgp.sgame"
    package_name_short: str = "cent.tmgp.sgame"

    activity_name: str = "com.tencent.tmgp.sgame.SGameActivity"

    resource_dir: str = "./resources/sgame"

    # Replay picker card grid (landscape frame, 2720x1224), measured
    # on-device: card0 = first card center, 6 per row, 2 visible rows
    # (repeat <= 12 without scrolling)
    replay_grid: dict = Field(
        default_factory=lambda: {
            "card0": [560, 487], "dx": 346, "dy": 500, "cols": 6,
        },
    )

# =========== Power measurement ==============

class PowerConfig(BaseModel):
    # Software charge-cut via /proc/tc_charger_cmd/en_power_path (X6873).
    # Required for meaningful batt.power_mw; runs fail loudly on devices
    # without the node - disable there.
    enable: bool = Field(
        default=True,
        description="Cut charging during runs for battery-side power measurement"
    )
    # Warm-up burn before the ready wait: cold devices take hours to cool
    # INTO the hot band; a controlled burn takes tens of minutes.
    warmup: bool = Field(
        default=True,
        description="Burn CPU load before each run so the ready gate passes quickly"
    )
    # Each run starts from a full battery: charge with the screen off until
    # the gauge reaches this, KEEP charging through warmup and the ready
    # wait, and cut only right before the run itself.
    charge_target: int = Field(
        default=100, ge=1, le=100,
        description="Battery capacity (%%) to wait for before each run"
    )
    charge_wait_max: int = Field(
        default=2700,
        description="Max seconds to wait for the charge target"
    )
    # Warm-up = play replay card 0 for this long (game = CPU+GPU+screen
    # heater; its equilibrium sits ~1-2K above the ready thresholds, while
    # a throttled CPU-only burn equilibrates BELOW them - measured).
    warmup_secs: int = Field(
        default=600, ge=0,
        description="Seconds of replay playback for warm-up (0 = skip)"
    )

# =========== Config root ==============

class Config(BaseSettings):

    output_dir: str = Field(
        default="gs/test",
        description="Output directory for traces and metadata"
    )

    grid: dict[str, list] = Field(
        default_factory=dict,
        description="FPSGO param axes for grid search. Key 'a,b' = joint axis"
    )

    # Per-run replay card index. 'group' = one replay per repeat group
    # (group g plays card g: each combo is validated across scenarios
    # instead of repeating one replay). 'fixed' always uses replay_index.
    # Card indices 0..11 (2 rows x 6 visible without scrolling).
    replay_index_mode: str = Field(
        default="group",
        description="'group' (per repeat group) or 'fixed'"
    )
    replay_index: int = Field(
        default=0,
        description="Explicit replay card index when replay_index_mode='fixed'"
    )

    run_duration: int = Field(
        default=600,
        description="Single run valid duration"
    )

    repeats: int = Field(
        default=1, ge=1,
        description="Runs per grid combo"
    )

    shuffle_seed: int | None = Field(
        default=None,
        description="Seed for run-order shuffle; None = random"
    )

    run_max_attempts: int = Field(
        default=8,
        description="Max attempts for a single run"
    )

    backoff_initial_secs: int = Field(
        default=15,
        description="Initial backoff duration on single run"
    )

    early_stop_dur: int = Field(
        default=60,
        description="Duration for early stopping check"
    )

    early_stop_check_interval: int = Field(
        default=5,
        description="Interval for early stopping check"
    )

    early_stop_fps_threshold: int = Field(
        default=114,
        description="FPS threshold for early stopping"
    )

    # Per-zone ready temperature thresholds (milli-°C), simulating a
    # continuous-run hot state. zone0=soc_max, zone44=tsx_ntc, zone53=battery
    ready_temp_soc_max: int = Field(
        default=60000,
        description="Ready threshold for thermal zone 0 (soc_max)"
    )
    ready_temp_tsx: int = Field(
        default=48000,
        description="Ready threshold for thermal zone 44 (tsx_ntc)"
    )
    ready_temp_battery: int = Field(
        default=42000,
        description="Ready threshold for thermal zone 53 (battery)"
    )

    perfetto: PerfettoConfig = PerfettoConfig()

    power: PowerConfig = PowerConfig()

    # Game specific conf
    sgame: SgameConfig = SgameConfig()

    # Pydantic
    model_config = SettingsConfigDict(
        toml_file="config.toml",
        extra="ignore"
    )
    
    @classmethod
    def settings_customise_sources(
        cls, settings_cls,
        init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (TomlConfigSettingsSource(settings_cls), init_settings,
                env_settings, dotenv_settings, file_secret_settings)

_config: Config | None = None
def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config

