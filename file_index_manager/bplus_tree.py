"""
B+ Tree index on primary key (int keys only for range; str keys hashed for equality).
Stored in data/<type>_btree.db.

Node page layout (HEADER = 16 bytes):
  Byte  0:     is_leaf         (uint8,  0=internal, 1=leaf)
  Bytes 1-4:   num_keys        (uint32)
  Bytes 5-8:   parent_page     (uint32, 0 = root or unknown)
  Bytes 9-12:  next_leaf       (uint32, leaf only; 0 = none)
  Bytes 13-15: reserved

Internal node (is_leaf=0):
  Layout after header: [ptr4, key4, ptr4, key4, ..., key4, ptr4]
  ptr = page_num (uint32), key = signed int32
  max_keys = floor((page_size - 16 - 4) / 8) — last ptr needs 4 bytes
  With 4096 bytes: (4096 - 16 - 4) / 8 = 509 keys — we cap at ORDER=50

Leaf node (is_leaf=1):
  Entries after header: (key:4, data_page:4, slot:2) * leaf_capacity
  leaf_capacity = floor((page_size - 16) / 10) — capped at LEAF_CAP=100

Page 0 = root (initially a leaf, page 0 of the btree file).
A separate metadata page is not used; root is always page 0.
When root splits, we allocate two child pages and overwrite page 0 as the new internal root.
"""
import struct

NODE_HEADER = 16
INTERNAL_ENTRY = 8   # key(4) + ptr(4)
LEAF_ENTRY = 10      # key(4) + data_page(4) + slot(2)
INTERNAL_PTR = 4

ORDER = 50        # max keys in an internal node
LEAF_CAP = 100    # max entries in a leaf node


def _pk_to_key(pk_value, pk_type: str) -> int:
    if pk_type == "int":
        return int(pk_value)
    s = str(pk_value)
    h = sum(ord(c) * (31 ** i) for i, c in enumerate(s)) & 0x7FFFFFFF
    return h


class BPlusTree:
    def __init__(self, config: dict, buffer):
        self.page_size = config.get("page_size", 4096)
        self.buffer = buffer

    def _file_id(self, type_name: str) -> str:
        return type_name + "_btree"

    def _read_page(self, file_id: str, page_num: int) -> bytearray:
        buf_res = self.buffer.get_page(file_id, page_num)
        return bytearray(buf_res.page.data)

    def _unpin(self, file_id: str, page_num: int):
        self.buffer.unpin_page(file_id, page_num)

    def _write_page(self, file_id: str, page_num: int, data: bytearray):
        self.buffer.write_page(file_id, page_num, bytes(data))

    def _alloc_page(self, file_id: str) -> int:
        return self.buffer.disk.allocate_page(file_id)

    # ------------------------------------------------------------------ node encoding

    def _new_leaf(self, entries=None, next_leaf=0, parent=0) -> bytearray:
        data = bytearray(self.page_size)
        entries = entries or []
        data[0] = 1  # is_leaf
        struct.pack_into(">III", data, 1, len(entries), parent, next_leaf)
        offset = NODE_HEADER
        for (key, dp, sl) in entries:
            struct.pack_into(">iIH", data, offset, key, dp, sl)
            offset += LEAF_ENTRY
        return data

    def _new_internal(self, keys=None, ptrs=None, parent=0) -> bytearray:
        data = bytearray(self.page_size)
        keys = keys or []
        ptrs = ptrs or []
        data[0] = 0  # is_leaf=False
        struct.pack_into(">III", data, 1, len(keys), parent, 0)
        offset = NODE_HEADER
        # layout: ptr[0] key[0] ptr[1] key[1] ... key[n-1] ptr[n]
        for i, ptr in enumerate(ptrs):
            struct.pack_into(">I", data, offset, ptr)
            offset += 4
            if i < len(keys):
                struct.pack_into(">i", data, offset, keys[i])
                offset += 4
        return data

    def _read_leaf(self, data: bytearray):
        """Returns (num_keys, parent, next_leaf, entries)."""
        num_keys, parent, next_leaf = struct.unpack(">III", data[1:13])
        entries = []
        offset = NODE_HEADER
        for _ in range(num_keys):
            key, dp, sl = struct.unpack(">iIH", data[offset:offset + LEAF_ENTRY])
            entries.append((key, dp, sl))
            offset += LEAF_ENTRY
        return num_keys, parent, next_leaf, entries

    def _read_internal(self, data: bytearray):
        """Returns (num_keys, parent, ptrs, keys)."""
        num_keys, parent, _ = struct.unpack(">III", data[1:13])
        ptrs = []
        keys = []
        offset = NODE_HEADER
        for i in range(num_keys + 1):
            ptr = struct.unpack(">I", data[offset:offset + 4])[0]
            ptrs.append(ptr)
            offset += 4
            if i < num_keys:
                key = struct.unpack(">i", data[offset:offset + 4])[0]
                keys.append(key)
                offset += 4
        return num_keys, parent, ptrs, keys

    def _write_leaf(self, data: bytearray, parent: int, next_leaf: int, entries: list):
        data[0] = 1
        struct.pack_into(">III", data, 1, len(entries), parent, next_leaf)
        # Zero out entry area
        data[NODE_HEADER:NODE_HEADER + LEAF_CAP * LEAF_ENTRY] = b"\x00" * (LEAF_CAP * LEAF_ENTRY)
        offset = NODE_HEADER
        for (key, dp, sl) in entries:
            struct.pack_into(">iIH", data, offset, key, dp, sl)
            offset += LEAF_ENTRY

    def _write_internal(self, data: bytearray, parent: int, ptrs: list, keys: list):
        data[0] = 0
        struct.pack_into(">III", data, 1, len(keys), parent, 0)
        data[NODE_HEADER:] = b"\x00" * (self.page_size - NODE_HEADER)
        offset = NODE_HEADER
        for i, ptr in enumerate(ptrs):
            struct.pack_into(">I", data, offset, ptr)
            offset += 4
            if i < len(keys):
                struct.pack_into(">i", data, offset, keys[i])
                offset += 4

    # ------------------------------------------------------------------ initialize

    def initialize(self, type_name: str):
        """Create the btree file with an empty root leaf at page 0."""
        file_id = self._file_id(type_name)
        self.buffer.disk.create_file(file_id)
        page_num = self._alloc_page(file_id)  # page 0
        assert page_num == 0
        root = self._new_leaf()
        self._write_page(file_id, 0, root)

    # ------------------------------------------------------------------ find leaf

    def _find_leaf(self, file_id: str, key: int):
        """Traverse from root (page 0) to the leaf that should contain key.
        Returns (leaf_page_num, nodes_visited)."""
        page_num = 0
        nodes_visited = 0
        while True:
            data = self._read_page(file_id, page_num)
            nodes_visited += 1
            is_leaf = data[0]
            if is_leaf:
                self._unpin(file_id, page_num)
                return page_num, nodes_visited
            _, _, ptrs, keys = self._read_internal(data)
            child_idx = len(keys)
            for i, k in enumerate(keys):
                if key < k:
                    child_idx = i
                    break
            next_page = ptrs[child_idx]
            self._unpin(file_id, page_num)
            page_num = next_page

    # ------------------------------------------------------------------ insert

    def insert(self, type_name: str, pk_value, pk_type: str, data_page: int, slot_num: int):
        """Insert (key, data_page, slot_num) into the B+ tree. Returns nodes_visited."""
        file_id = self._file_id(type_name)
        key = _pk_to_key(pk_value, pk_type)
        leaf_pnum, nodes_visited = self._find_leaf(file_id, key)
        nodes_visited += self._leaf_insert(file_id, leaf_pnum, key, data_page, slot_num)
        return nodes_visited

    def _leaf_insert(self, file_id, leaf_pnum, key, data_page, slot_num):
        data = self._read_page(file_id, leaf_pnum)
        _, parent, next_leaf, entries = self._read_leaf(data)

        entry = (key, data_page, slot_num)
        pos = len(entries)
        for i, (k, _, _) in enumerate(entries):
            if key <= k:
                pos = i
                break
        entries.insert(pos, entry)

        if len(entries) <= LEAF_CAP:
            self._write_leaf(data, parent, next_leaf, entries)
            self._write_page(file_id, leaf_pnum, data)
            self._unpin(file_id, leaf_pnum)
            return 0

        mid = len(entries) // 2
        left_entries = entries[:mid]
        right_entries = entries[mid:]
        push_up_key = right_entries[0][0]

        right_pnum = self._alloc_page(file_id)
        right_data = bytearray(self.page_size)
        self._write_leaf(right_data, parent, next_leaf, right_entries)
        self._write_page(file_id, right_pnum, right_data)
        self._unpin(file_id, right_pnum)

        self._write_leaf(data, parent, right_pnum, left_entries)
        self._write_page(file_id, leaf_pnum, data)
        self._unpin(file_id, leaf_pnum)

        return 1 + self._internal_insert(file_id, parent, push_up_key, leaf_pnum, right_pnum, leaf_pnum == 0)

    def _internal_insert(self, file_id, parent_pnum, push_key, left_pnum, right_pnum, from_root=False):
        """Insert push_key into internal node at parent_pnum. Handles root split."""
        nodes_visited = 0
        if parent_pnum == 0 and from_root:
            new_root_data = bytearray(self.page_size)
            old_root_data = self._read_page(file_id, left_pnum)
            new_left_pnum = self._alloc_page(file_id)
            new_left_data = bytearray(self.page_size)
            new_left_data[:] = old_root_data
            self._write_page(file_id, new_left_pnum, new_left_data)
            self._unpin(file_id, left_pnum)
            self._unpin(file_id, new_left_pnum)

            right_data = self._read_page(file_id, right_pnum)
            struct.pack_into(">I", right_data, 5, 0)
            self._write_page(file_id, right_pnum, right_data)
            self._unpin(file_id, right_pnum)

            self._write_internal(new_root_data, 0, [new_left_pnum, right_pnum], [push_key])
            self._write_page(file_id, 0, new_root_data)
            self._unpin(file_id, 0)
            nodes_visited += 1
            return nodes_visited

        data = self._read_page(file_id, parent_pnum)
        nodes_visited += 1
        _, gparent, ptrs, keys = self._read_internal(data)

        pos = len(keys)
        for i, k in enumerate(keys):
            if push_key < k:
                pos = i
                break
        keys.insert(pos, push_key)
        ptrs.insert(pos + 1, right_pnum)

        if len(keys) <= ORDER:
            self._write_internal(data, gparent, ptrs, keys)
            self._write_page(file_id, parent_pnum, data)
            self._unpin(file_id, parent_pnum)
            return nodes_visited

        mid = len(keys) // 2
        up_key = keys[mid]
        left_keys = keys[:mid]
        right_keys = keys[mid + 1:]
        left_ptrs = ptrs[:mid + 1]
        right_ptrs = ptrs[mid + 1:]

        right_pnum2 = self._alloc_page(file_id)
        right_data = bytearray(self.page_size)
        self._write_internal(right_data, gparent, right_ptrs, right_keys)
        self._write_page(file_id, right_pnum2, right_data)
        self._unpin(file_id, right_pnum2)

        self._write_internal(data, gparent, left_ptrs, left_keys)
        self._write_page(file_id, parent_pnum, data)
        self._unpin(file_id, parent_pnum)

        nodes_visited += self._internal_insert(file_id, gparent, up_key,
                                                parent_pnum, right_pnum2,
                                                parent_pnum == 0)
        return nodes_visited

    # ------------------------------------------------------------------ search

    def search(self, type_name: str, pk_value, pk_type: str):
        """Returns (data_page, slot_num, nodes_visited) or (None, None, nodes_visited)."""
        file_id = self._file_id(type_name)
        key = _pk_to_key(pk_value, pk_type)
        leaf_pnum, nodes_visited = self._find_leaf(file_id, key)
        data = self._read_page(file_id, leaf_pnum)
        _, _, _, entries = self._read_leaf(data)
        self._unpin(file_id, leaf_pnum)
        for (k, dp, sl) in entries:
            if k == key:
                return dp, sl, nodes_visited
        return None, None, nodes_visited

    # ------------------------------------------------------------------ range search

    def range_search(self, type_name: str, low_pk, high_pk, pk_type: str = "int"):
        """
        Returns (list_of_(data_page, slot_num), nodes_visited).
        Traverses leaves from the leaf containing low_pk to the leaf past high_pk.
        """
        file_id = self._file_id(type_name)
        low_key = _pk_to_key(low_pk, pk_type)
        high_key = _pk_to_key(high_pk, pk_type)
        leaf_pnum, nodes_visited = self._find_leaf(file_id, low_key)

        results = []
        page_num = leaf_pnum
        while True:
            data = self._read_page(file_id, page_num)
            nodes_visited += 1
            _, _, next_leaf, entries = self._read_leaf(data)
            done = False
            for (k, dp, sl) in entries:
                if k > high_key:
                    done = True
                    break
                if k >= low_key:
                    results.append((dp, sl))
            self._unpin(file_id, page_num)
            if done:
                break
            if next_leaf == 0:
                break
            page_num = next_leaf

        return results, nodes_visited

    # ------------------------------------------------------------------ delete

    def delete(self, type_name: str, pk_value, pk_type: str):
        """Remove key from leaf. No rebalancing (lazy deletion). Returns nodes_visited."""
        file_id = self._file_id(type_name)
        key = _pk_to_key(pk_value, pk_type)
        leaf_pnum, nodes_visited = self._find_leaf(file_id, key)
        data = self._read_page(file_id, leaf_pnum)
        nodes_visited += 1
        _, parent, next_leaf, entries = self._read_leaf(data)
        new_entries = [(k, dp, sl) for (k, dp, sl) in entries if k != key]
        if len(new_entries) != len(entries):
            self._write_leaf(data, parent, next_leaf, new_entries)
            self._write_page(file_id, leaf_pnum, data)
        self._unpin(file_id, leaf_pnum)
        return nodes_visited
