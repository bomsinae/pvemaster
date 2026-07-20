from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import database_is_ready


@dataclass(frozen=True)
class Readiness:
    database: bool
    redis: bool

    @property
    def ready(self) -> bool:
        return self.database and self.redis


async def redis_is_ready(client: Redis) -> bool:
    try:
        return bool(await client.ping())
    except Exception:
        return False


async def check_readiness(engine: AsyncEngine, redis: Redis) -> Readiness:
    return Readiness(
        database=await database_is_ready(engine),
        redis=await redis_is_ready(redis),
    )
