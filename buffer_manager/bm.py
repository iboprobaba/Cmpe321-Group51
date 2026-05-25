from collections import OrderedDict
from typing import Optional, Tuple
from result_types import PageResult, BufferResult


class _Frame:
    def __init__(self, file_id: str, page_num: int, data: bytes):
        self.file_id = file_id
        self.page_num = page_num
        self.data = bytearray(data)
        self.dirty = False
        self.pin_count = 0


class BufferManager:
    def __init__(self, config: dict, disk):
        self.capacity = config.get("buffer_pool_size", 16)
        self.policy = config.get("replacement_policy", "LRU").upper()
        self.disk = disk
        # OrderedDict: key=(file_id, page_num), value=_Frame
        # LRU: move_to_end(last=True) on access; evict first (least recent)
        # MRU: move_to_end(last=True) on access; evict last  (most recent)
        self._pool: OrderedDict = OrderedDict()
        self._requests = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._dirty_writebacks = 0

    # ------------------------------------------------------------------ internal

    def _evict(self) -> Tuple[Optional[Tuple[str, int]], bool]:
        candidates = reversed(list(self._pool.items())) if self.policy == "MRU" else iter(self._pool.items())
        for key, frame in candidates:
            if frame.pin_count == 0:
                del self._pool[key]
                dirty_wb = False
                if frame.dirty:
                    self.disk.write_page(frame.file_id, frame.page_num, bytes(frame.data))
                    dirty_wb = True
                    self._dirty_writebacks += 1
                self._evictions += 1
                return key, dirty_wb
        raise RuntimeError("Buffer pool full: all pages are pinned")

    # ------------------------------------------------------------------ public API

    def get_page(self, file_id: str, page_num: int) -> BufferResult:
        key = (file_id, page_num)
        self._requests += 1
        evicted_id = None
        dirty_wb = False

        if key in self._pool:
            self._hits += 1
            self._pool.move_to_end(key, last=True)
            frame = self._pool[key]
            frame.pin_count += 1
            page_res = PageResult(data=bytes(frame.data), file_id=file_id,
                                  page_num=page_num, io_performed=False)
            return BufferResult(page=page_res, cache_hit=True,
                                evicted_page_id=None, dirty_writeback=False)

        # miss — load from disk
        self._misses += 1
        if len(self._pool) >= self.capacity:
            evicted_id, dirty_wb = self._evict()

        page_res = self.disk.read_page(file_id, page_num)
        frame = _Frame(file_id, page_num, page_res.data)
        frame.pin_count = 1
        self._pool[key] = frame
        self._pool.move_to_end(key, last=True)

        buf_page = PageResult(data=bytes(frame.data), file_id=file_id,
                              page_num=page_num, io_performed=True)
        return BufferResult(page=buf_page, cache_hit=False,
                            evicted_page_id=evicted_id, dirty_writeback=dirty_wb)

    def unpin_page(self, file_id: str, page_num: int) -> None:
        key = (file_id, page_num)
        if key in self._pool:
            frame = self._pool[key]
            if frame.pin_count > 0:
                frame.pin_count -= 1

    def write_page(self, file_id: str, page_num: int, data: bytes) -> None:
        key = (file_id, page_num)
        if key in self._pool:
            self._pool[key].data = bytearray(data)
            self._pool[key].dirty = True
            self._pool.move_to_end(key, last=True)
        else:
            if len(self._pool) >= self.capacity:
                self._evict()
            frame = _Frame(file_id, page_num, data)
            frame.dirty = True
            self._pool[key] = frame
            self._pool.move_to_end(key, last=True)

    def mark_dirty(self, file_id: str, page_num: int):
        key = (file_id, page_num)
        if key in self._pool:
            self._pool[key].dirty = True

    def get_frame_data(self, file_id: str, page_num: int) -> Optional[bytearray]:
        key = (file_id, page_num)
        if key in self._pool:
            self._pool.move_to_end(key, last=True)
            return self._pool[key].data
        return None

    def flush_page(self, file_id: str, page_num: int):
        key = (file_id, page_num)
        if key in self._pool and self._pool[key].dirty:
            frame = self._pool[key]
            self.disk.write_page(frame.file_id, frame.page_num, bytes(frame.data))
            frame.dirty = False

    def flush(self):
        for key, frame in self._pool.items():
            if frame.dirty:
                self.disk.write_page(frame.file_id, frame.page_num, bytes(frame.data))
                frame.dirty = False

    def invalidate(self, file_id: str, page_num: int):
        key = (file_id, page_num)
        if key in self._pool:
            frame = self._pool.pop(key)
            if frame.dirty:
                self.disk.write_page(frame.file_id, frame.page_num, bytes(frame.data))

    def get_stats(self) -> dict:
        return {
            "requests": self._requests,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "dirty_writebacks": self._dirty_writebacks,
        }

    def reset_stats(self):
        self._requests = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._dirty_writebacks = 0
