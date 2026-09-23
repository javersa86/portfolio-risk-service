from ingest.providers.base import PriceProvider
from ingest.providers.fixture import FixtureProvider


def get_provider(name: str) -> PriceProvider:
    """Map the PRICE_PROVIDER setting to a provider instance.
    Step 6 adds a branch here for the real API."""
    if name == "fixture":
        return FixtureProvider()
    raise ValueError(f"Unknown price provider: {name!r}")