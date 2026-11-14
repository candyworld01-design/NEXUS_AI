import asyncio
import random
from collections import defaultdict


class MusicQueue:
    """One independent queue per Telegram chat."""

    def __init__(self):
        self._queues = defaultdict(list)
        self._lock = asyncio.Lock()

    async def add(self, chat_id, song_info, limit=30):
        async with self._lock:
            queue = self._queues[chat_id]
            if len(queue) >= limit:
                raise ValueError(f"Queue is full (maximum {limit} songs).")
            queue.append(song_info)
            return len(queue)

    async def get_next(self, chat_id):
        async with self._lock:
            queue = self._queues[chat_id]
            return queue.pop(0) if queue else None

    async def snapshot(self, chat_id):
        async with self._lock:
            return list(self._queues[chat_id])

    async def clear(self, chat_id):
        async with self._lock:
            old = list(self._queues[chat_id])
            self._queues[chat_id].clear()
            return old

    async def remove(self, chat_id, index):
        async with self._lock:
            queue = self._queues[chat_id]
            if index < 1 or index > len(queue):
                return None
            return queue.pop(index - 1)

    async def shuffle(self, chat_id):
        async with self._lock:
            random.shuffle(self._queues[chat_id])
            return len(self._queues[chat_id])

    async def size(self, chat_id):
        async with self._lock:
            return len(self._queues[chat_id])

    async def is_empty(self, chat_id):
        return await self.size(chat_id) == 0


queue = MusicQueue()
