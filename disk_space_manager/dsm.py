import os
import sys
import struct
from result_types import PageResult, WriteResult


class DiskSpaceManager:
    def __init__(self, config: dict):
        self.page_size = config.get("page_size", 4096)
        self.data_dir = config.get("base_dir", os.path.dirname(os.path.abspath(sys.argv[0])))
        self._reads = 0
        self._writes = 0

    # ------------------------------------------------------------------ helpers

    def _path(self, file_id: str) -> str:
        return os.path.join(self.data_dir, file_id + ".db")

    def log_write(self, file_id: str, page_num: int, data: bytes):
        pass  # stub — called on every write

    # ------------------------------------------------------------------ public API

    def create_file(self, file_id: str):
        path = self._path(file_id)
        if not os.path.exists(path):
            open(path, "wb").close()

    def file_exists(self, file_id: str) -> bool:
        return os.path.exists(self._path(file_id))

    def get_page_count(self, file_id: str) -> int:
        path = self._path(file_id)
        if not os.path.exists(path):
            return 0
        size = os.path.getsize(path)
        return size // self.page_size

    def read_page(self, file_id: str, page_num: int) -> PageResult:
        path = self._path(file_id)
        with open(path, "rb") as f:
            f.seek(page_num * self.page_size)
            data = f.read(self.page_size)
        if len(data) < self.page_size:
            data = data + b"\x00" * (self.page_size - len(data))
        self._reads += 1
        return PageResult(data=data, file_id=file_id, page_num=page_num, io_performed=True)

    def write_page(self, file_id: str, page_num: int, data: bytes) -> WriteResult:
        assert len(data) == self.page_size, f"Page data must be exactly {self.page_size} bytes"
        path = self._path(file_id)
        old_data = b"\x00" * self.page_size
        if os.path.exists(path):
            size = os.path.getsize(path)
            if page_num * self.page_size < size:
                with open(path, "rb") as f:
                    f.seek(page_num * self.page_size)
                    old_data = f.read(self.page_size)
        with open(path, "r+b" if os.path.exists(path) else "wb") as f:
            f.seek(page_num * self.page_size)
            f.write(data)
        self.log_write(file_id, page_num, data)
        self._writes += 1
        return WriteResult(success=True, file_id=file_id, page_num=page_num,
                           old_data=old_data, new_data=data)

    def allocate_page(self, file_id: str) -> int:
        new_page_num = self.get_page_count(file_id)
        blank = b"\x00" * self.page_size
        path = self._path(file_id)
        with open(path, "ab") as f:
            f.write(blank)
        self._writes += 1
        self.log_write(file_id, new_page_num, blank)
        return new_page_num

    def get_stats(self) -> dict:
        return {"reads": self._reads, "writes": self._writes}

    def reset_stats(self):
        self._reads = 0
        self._writes = 0
