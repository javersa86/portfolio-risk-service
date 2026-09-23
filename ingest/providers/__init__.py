from ingest.providers.base import PriceProvider
from ingest.providers.fixture import FixtureProvider
from ingest.providers.tiingo import TiingoProvider


def get_provider(name: str, api_key: str = "") -> PriceProvider:
    """Map the PRICE_PROVIDER setting to a provider instance."""
    if name == "fixture":
        return FixtureProvider()
    if name == "tiingo":
        return TiingoProvider(api_key)
    raise ValueError(f"Unknown price provider: {name!r}")
