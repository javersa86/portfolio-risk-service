# Loads configuration from environment variables or the .env file,
# so no connection strings or keys are hard-coded anywhere in the code.
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    App configuration. Each field maps to an env var of the same name
    (case-insensitive): database_url <- DATABASE_URL, and so on.
    """

    # Read from .env if present; ignore env vars this class doesn't define
    # (like POSTGRES_USER, which only Docker uses).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Defaults are used only if the env var is missing.
    database_url: str = "postgresql+psycopg://risk:risk@localhost:5432/risk"
    price_provider: str = "fixture"
    price_provider_api_key: str = ""
    tickers: str = "SPY,QQQ,AAPL,MSFT,JPM,GS,TLT,GLD"

    @property
    def ticker_list(self) -> list[str]:
        """TICKERS as a clean list: 'spy, qqq' -> ['SPY', 'QQQ']."""
        return [t.strip().upper() for t in self.tickers.split(",") if t.strip()]


# One shared instance; everything else imports this.
settings = Settings()
