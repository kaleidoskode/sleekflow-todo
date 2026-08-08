"""Batch status changes and deletes, isolated per item.

The point of a batch here is that one refusal does not sink the rest: telling
someone "12 completed, 3 are still blocked" is useful, and rolling all 15 back
because of 3 is not. That is a deliberate reading of "bulk operations" —
all-or-nothing was the alternative, and it is the wrong shape for a board where
being blocked is a normal, expected state rather than an error.

Isolation is structural rather than argued: each item runs in its **own
session**, so its transaction commits or rolls back alone. Sharing one session
would mean a single failed statement poisons the transaction for every item
after it, and the existing services commit internally — so a savepoint dance
would have to reach into them. A session per item keeps the guarantee obvious
and leaves those services untouched.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import DomainError
from app.domain.enums import Status
from app.schemas.bulk import BulkItem, BulkItemResult, BulkResult
from app.services.status_service import StatusService
from app.services.todo_service import TodoService


class BulkService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], actor_id: UUID | None
    ) -> None:
        self.session_factory = session_factory
        self.actor_id = actor_id

    async def change_status(self, items: list[BulkItem], target: Status) -> BulkResult:
        async def run(item: BulkItem) -> int:
            async with self.session_factory() as session:
                updated, _ = await StatusService(session, self.actor_id).change_status(
                    item.id, item.version, target
                )
                return updated.version

        return await self._apply(items, run)

    async def delete(self, items: list[BulkItem]) -> BulkResult:
        async def run(item: BulkItem) -> int:
            async with self.session_factory() as session:
                deleted = await TodoService(session, self.actor_id).delete(item.id, item.version)
                return deleted.version

        return await self._apply(items, run)

    @staticmethod
    async def _apply(
        items: list[BulkItem], run: Callable[[BulkItem], Awaitable[int]]
    ) -> BulkResult:
        results: list[BulkItemResult] = []

        # Sequential on purpose. Each item holds a pooled connection for its
        # transaction, so fanning 200 out with gather() would ask for 200
        # connections at once and deadlock against a pool of five. Bounded
        # concurrency would be the optimisation if this ever got hot; the
        # measured cost of the serial version is in docs/performance.md.
        for item in items:
            try:
                version = await run(item)
            except DomainError as exc:
                # Every refusal this API can express is a DomainError, and each
                # carries the code and sentence the single-item endpoint would
                # have returned — so a caller handles one shape, not two.
                results.append(
                    BulkItemResult(id=item.id, ok=False, code=exc.code, detail=exc.detail)
                )
            else:
                results.append(BulkItemResult(id=item.id, ok=True, version=version))

        succeeded = sum(1 for r in results if r.ok)
        return BulkResult(
            succeeded=succeeded, failed=len(results) - succeeded, results=results
        )
