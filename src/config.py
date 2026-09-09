from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class PerfettoConfig(BaseModel):

    config_file: str = Field(
        default="./perfetto_config.txtpb",
        description="Perfetto config file"
    )

    record_script: str = Field(
        default="./record_android_trace",
        description="Perfetto record script path"
    )


# ========== Game Specific Configurations ===========

class SgameConfig(BaseModel):
    
    package_name: str = "com.tencent.tmgp.sgame"
    package_name_short: str = "cent.tmgp.sgame"

# =========== Config root ==============

class Config(BaseSettings):

    output_dir: str = Field(
        default="gs/test",
        description="Output directory for traces and metadata"
    )

    run_duration: int = Field(
        default=600,
        description="Single run valid duration"
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

    model_config = SettingsConfigDict(
        toml_file="config.toml",
        extra="ignore"
    )


_config: Config | None = None
def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config

