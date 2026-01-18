from __future__ import annotations

from dataclasses import dataclass
from typing import List

from redbot.core import commands


@dataclass(frozen=True)
class FairQueueItem:
    requester_id: int
    label: str


class FairQueue:
    """A fair queue that distributes items across requesters."""

    def __init__(self) -> None:
        self._items: List[FairQueueItem] = []

    def add(self, item: FairQueueItem) -> int:
        """Insert an item so each requester stays fairly distributed.

        The new item is inserted after the last occurrence of the same requester,
        but before the next repeated requester appears in the cycle.
        """
        last_index = -1
        for index in range(len(self._items) - 1, -1, -1):
            if self._items[index].requester_id == item.requester_id:
                last_index = index
                break
        insert_at = last_index + 1
        seen_requesters = set()
        for index in range(insert_at, len(self._items)):
            requester_id = self._items[index].requester_id
            if requester_id in seen_requesters:
                break
            seen_requesters.add(requester_id)
            insert_at = index + 1
        self._items.insert(insert_at, item)
        return insert_at

    def list(self) -> List[FairQueueItem]:
        return list(self._items)

    def clear(self) -> None:
        self._items.clear()


class FairQueueCog(commands.Cog):
    """Queue items fairly across users."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._queue = FairQueue()

    @commands.group(name="fairqueue", invoke_without_command=True)
    async def fairqueue_group(self, ctx: commands.Context) -> None:
        """Show the current fair queue."""
        await ctx.invoke(self.fairqueue_list)

    @fairqueue_group.command(name="add")
    async def fairqueue_add(self, ctx: commands.Context, *, label: str) -> None:
        """Add an item to the fair queue."""
        item = FairQueueItem(requester_id=ctx.author.id, label=label)
        position = self._queue.add(item)
        await ctx.send(
            f"Queued '{label}' for {ctx.author.display_name} at position {position + 1}."
        )

    @fairqueue_group.command(name="list")
    async def fairqueue_list(self, ctx: commands.Context) -> None:
        """List items in the fair queue."""
        items = self._queue.list()
        if not items:
            await ctx.send("The fair queue is empty.")
            return
        lines = []
        for index, item in enumerate(items, start=1):
            lines.append(f"{index}. <@{item.requester_id}> — {item.label}")
        await ctx.send("\n".join(lines))

    @fairqueue_group.command(name="clear")
    @commands.mod_or_permissions(manage_guild=True)
    async def fairqueue_clear(self, ctx: commands.Context) -> None:
        """Clear the fair queue."""
        self._queue.clear()
        await ctx.send("The fair queue has been cleared.")
