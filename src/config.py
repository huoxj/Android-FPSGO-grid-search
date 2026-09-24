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

    run_duration: int = Field(
        default=600,
        description="Single run valid duration"
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

    ready_temp_threshold: int = Field(
        default=35000,
        description="Temperature threshold for device readiness"
    )

    perfetto: PerfettoConfig = PerfettoConfig()
    
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

