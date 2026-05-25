"""
Static hash index on primary key.
Stored in data/<type>_hash.db.

Bucket page format:
  Bytes 0-3:  bucket_id    (uint32)
  Bytes 4-7:  entry_count  (uint32)
  Bytes 8-11: next_page    (uint32, 0 = no overflow)
  Entries:    (key_bytes:4, data_page_num:4, slot_num:2) * max_entries_per_page

key_bytes: 4-byte signed int for int PKs; hash-derived 4-byte signed int for str PKs.
"""
import struct
from result_types import RecordResult
from .page_format import decode_page, record_byte_size, max_slots

NUM_BUCKETS = 64
BUCKET_HEADER_SIZE = 12
ENTRY_SIZE = 10  # 4 (key) + 4 (page_num) + 2 (slot_num)


def _pk_to_key(pk_value, pk_type: str) -> int:
    if pk_type == "int":
        return int(pk_value)
    s = str(pk_value)
    h = sum(ord(c) * (31 ** i) for i, c in enumerate(s)) & 0x7FFFFFFF
    return h


def _bucket_for(pk_value, pk_type: str) -> int:
    return _pk_to_key(pk_value, pk_type) % NUM_BUCKETS


class HashIndex:
    def __init__(self, config: dict, buffer):
        self.page_size = config.get("page_size", 4096)
        self.buffer = buffer
        self._max_entries = (self.page_size - BUCKET_HEADER_SIZE) // ENTRY_SIZE

    def _file_id(self, type_name: str) -> str:
        return type_name + "_hash"

    def _read_page(self, file_id: str, page_num: int) -> bytearray:
        buf_res = self.buffer.get_page(file_id, page_num)
        return bytearray(buf_res.page.data)

    def _unpin(self, file_id: str, page_num: int):
        self.buffer.unpin_page(file_id, page_num)

    def _write_page(self, file_id: str, page_num: int, data: bytearray):
        self.buffer.write_page(file_id, page_num, bytes(data))

    def _decode_bucket_page(self, data: bytearray):
        bucket_id, entry_count, next_page = struct.unpack(">III", data[0:12])
        entries = []
        offset = BUCKET_HEADER_SIZE
        for _ in range(entry_count):
            key, pnum, slot = struct.unpack(">iIH", data[offset:offset + ENTRY_SIZE])
            entries.append((key, pnum, slot))
            offset += ENTRY_SIZE
        return bucket_id, entry_count, next_page, entries

    def _encode_bucket_page(self, data: bytearray, bucket_id: int, entries: list, next_page: int):
        struct.pack_into(">III", data, 0, bucket_id, len(entries), next_page)
        offset = BUCKET_HEADER_SIZE
        for (key, pnum, slot) in entries:
            struct.pack_into(">iIH", data, offset, key, pnum, slot)
            offset += ENTRY_SIZE

    def initialize(self, type_name: str):
        """Create and initialize NUM_BUCKETS pages for a new hash index."""
        file_id = self._file_id(type_name)
        self.buffer.disk.create_file(file_id)
        for b in range(NUM_BUCKETS):
            page_num = self.buffer.disk.allocate_page(file_id)
            page_data = bytearray(self.page_size)
            struct.pack_into(">III", page_data, 0, b, 0, 0)
            self._write_page(file_id, page_num, page_data)

    def insert(self, type_name: str, pk_value, pk_type: str, data_page: int, slot_num: int):
        file_id = self._file_id(type_name)
        bucket = _bucket_for(pk_value, pk_type)
        key = _pk_to_key(pk_value, pk_type)
        nodes_visited = 0

        page_num = bucket
        while True:
            page_data = self._read_page(file_id, page_num)
            nodes_visited += 1
            bid, entry_count, next_page, entries = self._decode_bucket_page(page_data)

            if entry_count < self._max_entries:
                entries.append((key, data_page, slot_num))
                self._encode_bucket_page(page_data, bid, entries, next_page)
                self._write_page(file_id, page_num, page_data)
                self._unpin(file_id, page_num)
                return nodes_visited

            if next_page == 0:
                new_pnum = self.buffer.disk.allocate_page(file_id)
                new_page = bytearray(self.page_size)
                new_entries = [(key, data_page, slot_num)]
                self._encode_bucket_page(new_page, bucket, new_entries, 0)
                self._write_page(file_id, new_pnum, new_page)
                self._unpin(file_id, new_pnum)
                struct.pack_into(">I", page_data, 8, new_pnum)
                self._write_page(file_id, page_num, page_data)
                self._unpin(file_id, page_num)
                return nodes_visited + 1

            self._unpin(file_id, page_num)
            page_num = next_page

    def lookup(self, type_name: str, pk_value, pk_type: str):
        """Return (data_page_num, slot_num, nodes_visited) or (None, None, nodes_visited)."""
        file_id = self._file_id(type_name)
        bucket = _bucket_for(pk_value, pk_type)
        key = _pk_to_key(pk_value, pk_type)
        nodes_visited = 0

        page_num = bucket
        while True:
            page_data = self._read_page(file_id, page_num)
            nodes_visited += 1
            _, _, next_page, entries = self._decode_bucket_page(page_data)
            found = next(((pnum, slot) for (k, pnum, slot) in entries if k == key), None)
            self._unpin(file_id, page_num)
            if found is not None:
                return found[0], found[1], nodes_visited
            if next_page == 0:
                break
            page_num = next_page

        return None, None, nodes_visited

    def delete(self, type_name: str, pk_value, pk_type: str):
        file_id = self._file_id(type_name)
        bucket = _bucket_for(pk_value, pk_type)
        key = _pk_to_key(pk_value, pk_type)
        nodes_visited = 0

        page_num = bucket
        while True:
            page_data = self._read_page(file_id, page_num)
            nodes_visited += 1
            bid, _, next_page, entries = self._decode_bucket_page(page_data)
            new_entries = [e for e in entries if e[0] != key]
            if len(new_entries) != len(entries):
                self._encode_bucket_page(page_data, bid, new_entries, next_page)
                self._write_page(file_id, page_num, page_data)
                self._unpin(file_id, page_num)
                return nodes_visited
            self._unpin(file_id, page_num)
            if next_page == 0:
                break
            page_num = next_page

        return nodes_visited
