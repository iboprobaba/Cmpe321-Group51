from dataclasses import dataclass, field
from typing import Optional, Any, List, Tuple


@dataclass
class PageResult:
    data: bytes
    file_id: str
    page_num: int
    io_performed: bool
    status: str = "success"


@dataclass
class WriteResult:
    success: bool
    file_id: str
    page_num: int
    old_data: bytes
    new_data: bytes
    status: str = "success"


@dataclass
class BufferResult:
    page: PageResult
    cache_hit: bool
    evicted_page_id: Optional[Tuple[str, int]]
    dirty_writeback: bool


@dataclass
class RecordResult:
    records: List[dict]
    pages_accessed: int
    index_nodes_visited: int
    status: str  # "success" | "failure"
    message: str = ""


@dataclass
class QueryResult:
    output_lines: List[str]
    status: str
    operation: str
    io_reads: int = 0
    io_writes: int = 0
    buffer_hits: int = 0
    buffer_misses: int = 0
    pages_scanned: int = 0
    index_nodes_visited: int = 0
