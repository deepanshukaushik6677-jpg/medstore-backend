from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings

# Two things Neon specifically needs that a plain local Postgres doesn't:
#
# 1. asyncpg doesn't understand libpq-style query params (sslmode,
#    channel_binding) that Neon's connection string includes by default —
#    it raises on them. They're stripped here and SSL is passed via
#    connect_args instead.
# 2. Neon's free tier suspends the database after 5 minutes idle.
#    pool_recycle keeps connections from going stale past that window, and
#    pool_pre_ping catches anything that slips through with a cheap check
#    before using it. Neither of these matters for local Postgres, but
#    they're harmless there too.
_raw_url = make_url(settings.database_url)
if _raw_url.drivername == "postgresql":
    _raw_url = _raw_url.set(drivername="postgresql+asyncpg")
_is_neon = "neon.tech" in (_raw_url.host or "")
_engine_url = _raw_url.difference_update_query(["sslmode", "channel_binding"])

engine = create_async_engine(
    _engine_url,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=280,  # just under Neon's 5-minute scale-to-zero window
    connect_args={"ssl": "require"} if _is_neon else {},
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session