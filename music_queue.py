import asyncio

class MusicQueue:
    def __init__(self):
        self._queue = []
        self._lock = asyncio.Lock()

    async def add(self, song_info: tuple):
        async with self._lock:
            self._queue.append(song_info)

    async def get_next(self):
        async with self._lock:
            if self._queue:
                return self._queue.pop(0)
            return None

    async def clear(self):
        async with self._lock:
            self._queue.clear()

    async def is_empty(self):
        async with self._lock:
            return len(self._queue) == 0

    async def get_size(self):
        async with self._lock:
            return len(self._queue)

queue = MusicQueue()
