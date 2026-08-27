"""Typed settings with a hard interlock against live trading."""

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_paper_trade: bool = True
    google_api_key: str = ""
    drawdownguard_env: Literal["dev", "judging"] = "dev"

    @field_validator("alpaca_paper_trade")
    @classmethod
    def refuse_live_trading(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError(
                "ALPACA_PAPER_TRADE must be true. This project never trades "
                "real money; refusing to start."
            )
        return value

    @property
    def alpaca_base_url(self) -> str:
        return "https://paper-api.alpaca.markets"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
