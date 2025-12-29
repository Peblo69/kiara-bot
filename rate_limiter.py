import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Any, Optional
import time


@dataclass(order=True)
class QueueItem:
    priority: int
    timestamp: float = field(compare=False)
    user_id: int = field(compare=False)
    callback: Callable = field(compare=False)
    future: asyncio.Future = field(compare=False)


class RateLimitedQueue:
    """
    Rate-limited queue for Vertex AI requests

    Ensures we don't exceed the RPM limit and handles backoff
    """

    def __init__(self, requests_per_minute: int = 10):
        self.rpm = requests_per_minute
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self.request_times: deque = deque(maxlen=requests_per_minute)
        self.is_running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def start(self):
        """Start the queue worker"""
        if not self.is_running:
            self.is_running = True
            self._worker_task = asyncio.create_task(self._worker())
            print(f"Queue worker started (limit: {self.rpm} RPM)")

    async def stop(self):
        """Stop the worker gracefully"""
        self.is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def add_request(
        self,
        user_id: int,
        callback: Callable,
        priority: int = 0
    ) -> asyncio.Future:
        """
        Add a request to the queue

        Args:
            user_id: Discord user ID
            callback: Async function to execute
            priority: Lower = higher priority

        Returns:
            Future that resolves with the result
        """
        future = asyncio.get_event_loop().create_future()
        item = QueueItem(
            priority=priority,
            timestamp=time.time(),
            user_id=user_id,
            callback=callback,
            future=future
        )
        await self.queue.put(item)
        return future

    async def _can_make_request(self) -> bool:
        """Check if we have capacity for another request"""
        now = time.time()
        # Remove entries older than 60 seconds
        while self.request_times and now - self.request_times[0] > 60:
            self.request_times.popleft()
        return len(self.request_times) < self.rpm

    async def _wait_for_slot(self):
        """Wait until a rate limit slot is available"""
        while not await self._can_make_request():
            if self.request_times:
                oldest = self.request_times[0]
                wait_time = 60 - (time.time() - oldest) + 0.1
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
            else:
                await asyncio.sleep(0.1)

    async def _worker(self):
        """Main worker loop processing the queue"""
        while self.is_running:
            try:
                # Get next item with timeout
                try:
                    item: QueueItem = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                # Wait for rate limit slot
                await self._wait_for_slot()

                # Execute the request
                try:
                    async with self._lock:
                        self.request_times.append(time.time())

                    result = await item.callback()
                    item.future.set_result(result)

                except Exception as e:
                    item.future.set_exception(e)

                finally:
                    self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Queue worker error: {e}")
                await asyncio.sleep(1)

    @property
    def queue_size(self) -> int:
        """Current number of pending requests"""
        return self.queue.qsize()

    @property
    def requests_in_window(self) -> int:
        """Requests made in the last minute"""
        now = time.time()
        return sum(1 for t in self.request_times if now - t <= 60)
