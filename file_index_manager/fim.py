import os
import sys
from result_types import RecordResult
from .catalog import Catalog
from .heap_scan import HeapScan
from .hash_index import HashIndex
from .bplus_tree import BPlusTree


class FileIndexManager:
    def __init__(self, config: dict, buffer):
        self.config = config
        self.buffer = buffer
        self.data_dir = config.get("base_dir", os.path.dirname(os.path.abspath(sys.argv[0])))
        self.index_strategy = config.get("index_strategy", "heap_scan")

        self.catalog = Catalog(self.data_dir)
        self.heap = HeapScan(config, buffer)
        self.hash_idx = HashIndex(config, buffer)
        self.btree = BPlusTree(config, buffer)

        self._nodes_visited = 0
        self._records_scanned = 0
        self._records_returned = 0

    # ------------------------------------------------------------------ helpers

    def _file_id(self, type_name: str) -> str:
        return type_name

    def _pk_field(self, type_name: str) -> dict:
        return self.catalog.get_pk_field(type_name)

    def _fields(self, type_name: str) -> list:
        return self.catalog.get_type(type_name)["fields"]

    def _pk_order(self, type_name: str) -> int:
        return self.catalog.get_type(type_name)["pk_order"]  # 1-indexed

    def _pk_value_from_values(self, type_name: str, values: list):
        pk_idx = self._pk_order(type_name) - 1
        return values[pk_idx]

    def _values_to_dict(self, type_name: str, values: list) -> dict:
        fields = self._fields(type_name)
        return {f["name"]: values[i] for i, f in enumerate(fields)}

    # ------------------------------------------------------------------ DDL

    def create_type(self, name: str, num_fields: int, pk_order: int, fields: list) -> RecordResult:
        if self.catalog.type_exists(name):
            return RecordResult(records=[], pages_accessed=0, index_nodes_visited=0,
                                status="failure", message=f"Type '{name}' already exists")

        self.catalog.create_type(name, num_fields, pk_order, fields)
        file_id = self._file_id(name)
        self.buffer.disk.create_file(file_id)

        if self.index_strategy == "hash_index":
            self.hash_idx.initialize(name)
        elif self.index_strategy == "bplus_tree":
            self.btree.initialize(name)

        return RecordResult(records=[], pages_accessed=0, index_nodes_visited=0, status="success")

    # ------------------------------------------------------------------ DML

    def create_record(self, type_name: str, values: list) -> RecordResult:
        if not self.catalog.type_exists(type_name):
            return RecordResult(records=[], pages_accessed=0, index_nodes_visited=0,
                                status="failure", message=f"Type '{type_name}' not found")

        fields = self._fields(type_name)
        pk_field = self._pk_field(type_name)
        pk_val = self._pk_value_from_values(type_name, values)

        # Duplicate PK check
        dup_check = self._search_by_pk(type_name, pk_val)
        if dup_check.status == "success" and dup_check.records:
            return RecordResult(records=[], pages_accessed=dup_check.pages_accessed,
                                index_nodes_visited=dup_check.index_nodes_visited,
                                status="failure",
                                message=f"Duplicate primary key: {pk_val}")

        values_dict = self._values_to_dict(type_name, values)
        pnum, slot, pages_acc = self.heap.insert(self._file_id(type_name), fields, values_dict)

        nodes_visited = 0
        if self.index_strategy == "hash_index":
            nodes_visited = self.hash_idx.insert(type_name, pk_val, pk_field["type"], pnum, slot)
        elif self.index_strategy == "bplus_tree":
            nodes_visited = self.btree.insert(type_name, pk_val, pk_field["type"], pnum, slot)

        self._nodes_visited += nodes_visited
        self._records_returned += 1

        return RecordResult(records=[values_dict],
                            pages_accessed=pages_acc + dup_check.pages_accessed,
                            index_nodes_visited=nodes_visited + dup_check.index_nodes_visited,
                            status="success")

    def delete_record(self, type_name: str, pk_value) -> RecordResult:
        if not self.catalog.type_exists(type_name):
            return RecordResult(records=[], pages_accessed=0, index_nodes_visited=0,
                                status="failure", message=f"Type '{type_name}' not found")

        pk_field = self._pk_field(type_name)
        fields = self._fields(type_name)
        nodes_visited = 0

        if self.index_strategy == "heap_scan":
            result = self.heap.delete(self._file_id(type_name), fields, pk_field, pk_value)
        elif self.index_strategy == "hash_index":
            dp, sl, nv = self.hash_idx.lookup(type_name, pk_value, pk_field["type"])
            nodes_visited += nv
            if dp is None:
                return RecordResult(records=[], pages_accessed=nv, index_nodes_visited=nv,
                                    status="failure", message=f"Record not found: {pk_value}")
            # Delete from data file
            result = self.heap.delete(self._file_id(type_name), fields, pk_field, pk_value)
            if result.status == "success":
                nodes_visited += self.hash_idx.delete(type_name, pk_value, pk_field["type"])
        elif self.index_strategy == "bplus_tree":
            dp, sl, nv = self.btree.search(type_name, pk_value, pk_field["type"])
            nodes_visited += nv
            if dp is None:
                return RecordResult(records=[], pages_accessed=nv, index_nodes_visited=nv,
                                    status="failure", message=f"Record not found: {pk_value}")
            result = self.heap.delete(self._file_id(type_name), fields, pk_field, pk_value)
            if result.status == "success":
                nodes_visited += self.btree.delete(type_name, pk_value, pk_field["type"])
        else:
            result = RecordResult(records=[], pages_accessed=0, index_nodes_visited=0,
                                  status="failure", message="Unknown index strategy")

        self._nodes_visited += nodes_visited
        result.index_nodes_visited += nodes_visited
        return result

    def search_record(self, type_name: str, pk_value) -> RecordResult:
        if not self.catalog.type_exists(type_name):
            return RecordResult(records=[], pages_accessed=0, index_nodes_visited=0,
                                status="failure", message=f"Type '{type_name}' not found")
        return self._search_by_pk(type_name, pk_value)

    def _search_by_pk(self, type_name: str, pk_value) -> RecordResult:
        pk_field = self._pk_field(type_name)
        fields = self._fields(type_name)
        nodes_visited = 0

        if self.index_strategy == "heap_scan":
            result = self.heap.search(self._file_id(type_name), fields, pk_field, pk_value)
        elif self.index_strategy == "hash_index":
            dp, sl, nv = self.hash_idx.lookup(type_name, pk_value, pk_field["type"])
            nodes_visited += nv
            if dp is None:
                result = RecordResult(records=[], pages_accessed=nv, index_nodes_visited=nv,
                                      status="failure", message=f"Record not found: {pk_value}")
            else:
                # Fetch record from data page
                buf_res = self.buffer.get_page(self._file_id(type_name), dp)
                from .page_format import decode_page
                _, _, _, slots = decode_page(buf_res.page.data, fields,
                                              self.config["page_size"],
                                              self.config["max_records_per_page"])
                rec = slots.get(sl)
                self.buffer.unpin_page(self._file_id(type_name), dp)
                if rec is None:
                    result = RecordResult(records=[], pages_accessed=nv + 1,
                                          index_nodes_visited=nv, status="failure",
                                          message=f"Record not found: {pk_value}")
                else:
                    result = RecordResult(records=[rec], pages_accessed=nv + 1,
                                          index_nodes_visited=nv, status="success")
        elif self.index_strategy == "bplus_tree":
            dp, sl, nv = self.btree.search(type_name, pk_value, pk_field["type"])
            nodes_visited += nv
            if dp is None:
                result = RecordResult(records=[], pages_accessed=nv, index_nodes_visited=nv,
                                      status="failure", message=f"Record not found: {pk_value}")
            else:
                buf_res = self.buffer.get_page(self._file_id(type_name), dp)
                from .page_format import decode_page
                _, _, _, slots = decode_page(buf_res.page.data, fields,
                                              self.config["page_size"],
                                              self.config["max_records_per_page"])
                rec = slots.get(sl)
                self.buffer.unpin_page(self._file_id(type_name), dp)
                if rec is None:
                    result = RecordResult(records=[], pages_accessed=nv + 1,
                                          index_nodes_visited=nv, status="failure",
                                          message=f"Record not found: {pk_value}")
                else:
                    result = RecordResult(records=[rec], pages_accessed=nv + 1,
                                          index_nodes_visited=nv, status="success")
        else:
            result = RecordResult(records=[], pages_accessed=0, index_nodes_visited=0,
                                  status="failure", message="Unknown index strategy")

        self._nodes_visited += result.index_nodes_visited
        self._records_scanned += result.pages_accessed
        if result.status == "success":
            self._records_returned += len(result.records)
        return result

    def range_search(self, type_name: str, field_name: str, low, high) -> RecordResult:
        if not self.catalog.type_exists(type_name):
            return RecordResult(records=[], pages_accessed=0, index_nodes_visited=0,
                                status="failure", message=f"Type '{type_name}' not found")

        fields = self._fields(type_name)
        target_field = next((f for f in fields if f["name"] == field_name), None)
        if target_field is None:
            return RecordResult(records=[], pages_accessed=0, index_nodes_visited=0,
                                status="failure", message=f"Field '{field_name}' not found")
        if target_field["type"] != "int":
            return RecordResult(records=[], pages_accessed=0, index_nodes_visited=0,
                                status="failure",
                                message=f"Range search only supported on int fields")

        pk_field = self._pk_field(type_name)
        is_pk_field = (target_field["name"] == pk_field["name"])

        nodes_visited = 0

        # B+ tree range search only works efficiently on the PK field
        if self.index_strategy == "bplus_tree" and is_pk_field:
            ptrs, nv = self.btree.range_search(type_name, low, high, pk_field["type"])
            nodes_visited = nv
            records = []
            pages_acc = nv
            for (dp, sl) in ptrs:
                buf_res = self.buffer.get_page(self._file_id(type_name), dp)
                from .page_format import decode_page
                _, _, _, slots = decode_page(buf_res.page.data, fields,
                                              self.config["page_size"],
                                              self.config["max_records_per_page"])
                pages_acc += 1
                rec = slots.get(sl)
                self.buffer.unpin_page(self._file_id(type_name), dp)
                if rec is not None:
                    val = int(rec[field_name])
                    if int(low) <= val <= int(high):
                        records.append(rec)
            status = "success" if records else "failure"
            result = RecordResult(records=records, pages_accessed=pages_acc,
                                  index_nodes_visited=nodes_visited, status=status)
        else:
            # heap_scan or hash_index (fallback) or non-PK field
            result = self.heap.range_search(self._file_id(type_name), fields, target_field, low, high)

        self._nodes_visited += result.index_nodes_visited
        self._records_scanned += result.pages_accessed
        if result.status == "success":
            self._records_returned += len(result.records)
        return result

    # ------------------------------------------------------------------ stats

    def get_stats(self) -> dict:
        return {
            "index_strategy": self.index_strategy,
            "nodes_visited": self._nodes_visited,
            "records_scanned": self._records_scanned,
            "records_returned": self._records_returned,
        }

    def reset_stats(self):
        self._nodes_visited = 0
        self._records_scanned = 0
        self._records_returned = 0
