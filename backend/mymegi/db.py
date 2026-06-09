from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from mymegi.config import Settings


class Database:
    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None

    async def connect(self, settings: Settings) -> None:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=5)

    async def disconnect(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        if self.pool is None:
            raise RuntimeError("Database pool is not initialized")
        async with self.pool.acquire() as connection:
            yield connection


database = Database()

