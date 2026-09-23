from sqlalchemy import Engine, create_engine

from api.app.config import settings


def get_engine(url: str | None = None) -> Engine:
    """
    Create a SQLAlchemy engine (a managed pool of database connections).

    url: optional override. Tests will pass their own database URL here;
         everything else uses DATABASE_URL from settings.
    """
    # pool_pre_ping: test each connection before using it, so a dropped
    # connection gets replaced instead of raising an error mid-request.
    return create_engine(url or settings.database_url, pool_pre_ping=True)
