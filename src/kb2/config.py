from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://surv:surv@localhost:5432/procurement_surv"

    seed_dir: Path = Path(__file__).resolve().parents[2] / "data" / "seed"

    api_host: str = "0.0.0.0"
    api_port: int = 8003

    model_config = {
        "env_file": [
            Path(__file__).resolve().parents[2] / ".env",
            ".env",
        ],
        "env_prefix": "KB2_",
        "extra": "ignore",
    }


settings = Settings()
