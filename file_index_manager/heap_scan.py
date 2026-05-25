from result_types import RecordResult
from .page_format import (decode_page, record_byte_size, max_slots, HEADER_SIZE,
                           write_slot_to_page, clear_slot_in_page, encode_record,
                           find_free_slot, encode_page, MAX_SLOTS_HARD_CAP, set_slot)
import struct


class HeapScan:
    def __init__(self, config: dict, buffer):
        self.page_size = config.get("page_size", 4096)
        self.max_rec = config.get("max_records_per_page", 10)
        self.buffer = buffer

    # ------------------------------------------------------------------ helpers

    def _page_count(self, file_id: str) -> int:
        return self.buffer.disk.get_page_count(file_id)

    def _read_page(self, file_id: str, page_num: int) -> bytearray:
        buf_res = self.buffer.get_page(file_id, page_num)
        return bytearray(buf_res.page.data)

    def _unpin(self, file_id: str, page_num: int):
        self.buffer.unpin_page(file_id, page_num)

    def _write_page(self, file_id: str, page_num: int, data: bytearray):
        self.buffer.write_page(file_id, page_num, bytes(data))

    def _n_slots(self, fields: list) -> int:
        rec_size = record_byte_size(fields)
        return max_slots(self.page_size, rec_size, self.max_rec)

    # ------------------------------------------------------------------ public API

    def insert(self, file_id: str, fields: list, values: dict) -> tuple:
        """Insert record; return (page_num, slot_num) or raise on failure."""
        rec_size = record_byte_size(fields)
        n_slots = max_slots(self.page_size, rec_size, self.max_rec)
        pages_accessed = 0

        page_count = self._page_count(file_id)
        for pnum in range(page_count):
            page_data = self._read_page(file_id, pnum)
            pages_accessed += 1
            _, _, bitmap, _ = decode_page(bytes(page_data), fields, self.page_size, self.max_rec)
            slot = find_free_slot(bitmap, n_slots)
            if slot != -1:
                rec_bytes = encode_record(values, fields)
                write_slot_to_page(page_data, slot, rec_bytes, fields, self.page_size, self.max_rec)
                self._write_page(file_id, pnum, page_data)
                self._unpin(file_id, pnum)
                return pnum, slot, pages_accessed
            self._unpin(file_id, pnum)

        # No free slot found — allocate new page
        new_pnum = self.buffer.disk.allocate_page(file_id)
        page_data = bytearray(self._read_page(file_id, new_pnum))
        struct.pack_into(">I", page_data, 0, new_pnum)
        rec_bytes = encode_record(values, fields)
        write_slot_to_page(page_data, 0, rec_bytes, fields, self.page_size, self.max_rec)
        self._write_page(file_id, new_pnum, page_data)
        self._unpin(file_id, new_pnum)
        return new_pnum, 0, pages_accessed + 1

    def delete(self, file_id: str, fields: list, pk_field: dict, pk_value) -> RecordResult:
        page_count = self._page_count(file_id)
        pages_accessed = 0
        pk_name = pk_field["name"]
        pk_type = pk_field["type"]
        cmp_val = int(pk_value) if pk_type == "int" else str(pk_value)

        for pnum in range(page_count):
            page_data = self._read_page(file_id, pnum)
            pages_accessed += 1
            _, _, bitmap, slots = decode_page(bytes(page_data), fields, self.page_size, self.max_rec)
            found = None
            for slot_idx, rec in slots.items():
                rec_pk = int(rec[pk_name]) if pk_type == "int" else str(rec[pk_name])
                if rec_pk == cmp_val:
                    clear_slot_in_page(page_data, slot_idx, fields, self.page_size, self.max_rec)
                    self._write_page(file_id, pnum, page_data)
                    found = rec
                    break
            self._unpin(file_id, pnum)
            if found is not None:
                return RecordResult(records=[found], pages_accessed=pages_accessed,
                                    index_nodes_visited=0, status="success")

        return RecordResult(records=[], pages_accessed=pages_accessed,
                            index_nodes_visited=0, status="failure",
                            message=f"Record with pk={pk_value} not found")

    def search(self, file_id: str, fields: list, pk_field: dict, pk_value) -> RecordResult:
        page_count = self._page_count(file_id)
        pages_accessed = 0
        pk_name = pk_field["name"]
        pk_type = pk_field["type"]
        cmp_val = int(pk_value) if pk_type == "int" else str(pk_value)

        for pnum in range(page_count):
            page_data = self._read_page(file_id, pnum)
            pages_accessed += 1
            _, _, _, slots = decode_page(bytes(page_data), fields, self.page_size, self.max_rec)
            found = None
            for rec in slots.values():
                rec_pk = int(rec[pk_name]) if pk_type == "int" else str(rec[pk_name])
                if rec_pk == cmp_val:
                    found = rec
                    break
            self._unpin(file_id, pnum)
            if found is not None:
                return RecordResult(records=[found], pages_accessed=pages_accessed,
                                    index_nodes_visited=0, status="success")

        return RecordResult(records=[], pages_accessed=pages_accessed,
                            index_nodes_visited=0, status="failure",
                            message=f"Record with pk={pk_value} not found")

    def range_search(self, file_id: str, fields: list, target_field: dict, low, high) -> RecordResult:
        page_count = self._page_count(file_id)
        pages_accessed = 0
        fname = target_field["name"]
        results = []

        for pnum in range(page_count):
            page_data = self._read_page(file_id, pnum)
            pages_accessed += 1
            _, _, _, slots = decode_page(bytes(page_data), fields, self.page_size, self.max_rec)
            for rec in slots.values():
                val = int(rec[fname])
                if int(low) <= val <= int(high):
                    results.append(rec)
            self._unpin(file_id, pnum)

        status = "success" if results else "failure"
        return RecordResult(records=results, pages_accessed=pages_accessed,
                            index_nodes_visited=0, status=status)

    def full_scan(self, file_id: str, fields: list) -> RecordResult:
        page_count = self._page_count(file_id)
        pages_accessed = 0
        results = []

        for pnum in range(page_count):
            page_data = self._read_page(file_id, pnum)
            pages_accessed += 1
            _, _, _, slots = decode_page(bytes(page_data), fields, self.page_size, self.max_rec)
            results.extend(slots.values())
            self._unpin(file_id, pnum)

        return RecordResult(records=results, pages_accessed=pages_accessed,
                            index_nodes_visited=0, status="success")
