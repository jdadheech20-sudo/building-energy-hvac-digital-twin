from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("CAMPUS_DATABASE_URL", "sqlite:///./campus_twin.db")
    data_dir: Path = Path(os.getenv("CAMPUS_DATA_DIR", "data/campus"))
    assistant_provider: str = os.getenv("CAMPUS_ASSISTANT_PROVIDER", "demo")
    aws_region: str | None = os.getenv("AWS_REGION")
    bedrock_model_id: str | None = os.getenv("BEDROCK_MODEL_ID")


settings = Settings()
