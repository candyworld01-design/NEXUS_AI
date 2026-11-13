
import asyncio


class MusicQueue:

    def __init__(self):
        self._queue = []
        self._lock = asyncio.Lock()

    async def add(self, song_info):
        async with self._lock:
            self._queue.append(song_info)

    async def get_next(self):
        async with self._lock:
            if self._queue:
                return self._queue.pop(0)

            return None

    async def clear(self):
        async with self._lock:
            old_queue = self._queue.copy()
            self._queue.clear()
            return old_queue

    async def get_size(self):
        async with self._lock:
            return len(self._queue)

    async def is_empty(self):
        async with self._lock:
            return len(self._queue) == 0


queue = MusicQueue() 
