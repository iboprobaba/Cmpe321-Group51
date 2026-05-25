"""
Slotted page format (unpacked, fixed-length records).

Page layout:
  Bytes 0-3:  page_num       (uint32, big-endian)
  Bytes 4-7:  record_count   (uint32)
  Bytes 8-9:  slot_bitmap    (uint16, bit i=1 means slot i is occupied)
  Bytes 10-15: reserved (zeros)
  Bytes 16+:  slots, each record_size bytes

Field encoding:
  int  -> 4 bytes, signed big-endian (struct '>i')
  str  -> 32 bytes, UTF-8, null-padded

Max slots per page is capped at min(max_records_per_page, floor((page_size-16)/record_size)).
"""
import struct

INT_SIZE = 4
STR_SIZE = 32
HEADER_SIZE = 16
MAX_SLOTS_HARD_CAP = 10  # from spec


def field_byte_size(ftype: str) -> int:
    return INT_SIZE if ftype == "int" else STR_SIZE


def record_byte_size(fields: list) -> int:
    return sum(field_byte_size(f["type"]) for f in fields)


def max_slots(page_size: int, rec_size: int, max_records_per_page: int) -> int:
    capacity = (page_size - HEADER_SIZE) // rec_size
    return min(capacity, max_records_per_page, MAX_SLOTS_HARD_CAP)


def encode_record(values: dict, fields: list) -> bytes:
    buf = b""
    for f in fields:
        val = values.get(f["name"])
        if f["type"] == "int":
            buf += struct.pack(">i", int(val))
        else:
            s = str(val).encode("utf-8")[:STR_SIZE]
            buf += s + b"\x00" * (STR_SIZE - len(s))
    return buf


def decode_record(data: bytes, fields: list) -> dict:
    offset = 0
    rec = {}
    for f in fields:
        if f["type"] == "int":
            rec[f["name"]] = struct.unpack(">i", data[offset:offset + INT_SIZE])[0]
            offset += INT_SIZE
        else:
            raw = data[offset:offset + STR_SIZE]
            rec[f["name"]] = raw.rstrip(b"\x00").decode("utf-8")
            offset += STR_SIZE
    return rec


def encode_page(page_num: int, slots: dict, fields: list, page_size: int) -> bytes:
    """
    slots: {slot_index: record_dict} — only occupied slots
    Returns exactly page_size bytes.
    """
    rec_size = record_byte_size(fields)
    n_slots = max_slots(page_size, rec_size, MAX_SLOTS_HARD_CAP)

    bitmap = 0
    record_count = 0
    slot_data = [b"\x00" * rec_size] * n_slots

    for idx, rec in slots.items():
        if 0 <= idx < n_slots:
            bitmap |= (1 << idx)
            slot_data[idx] = encode_record(rec, fields)
            record_count += 1

    header = struct.pack(">IIHHH", page_num, record_count, bitmap, 0, 0)
    # ">IIHHH" = uint32 + uint32 + uint16 + uint16 + uint16 = 4+4+2+2+2 = 14 bytes — pad to 16
    header += b"\x00" * (HEADER_SIZE - len(header))

    body = b"".join(slot_data)
    page = header + body
    # Pad to page_size
    if len(page) < page_size:
        page += b"\x00" * (page_size - len(page))
    return page[:page_size]


def decode_page(data: bytes, fields: list, page_size: int, max_records_per_page: int):
    """
    Returns (page_num, record_count, bitmap, slots_dict)
    slots_dict: {slot_index: record_dict} for occupied slots only.
    """
    rec_size = record_byte_size(fields)
    n_slots = max_slots(page_size, rec_size, max_records_per_page)

    page_num, record_count, bitmap = struct.unpack(">IIH", data[0:10])

    slots = {}
    for i in range(n_slots):
        if bitmap & (1 << i):
            start = HEADER_SIZE + i * rec_size
            end = start + rec_size
            slots[i] = decode_record(data[start:end], fields)

    return page_num, record_count, bitmap, slots


def find_free_slot(bitmap: int, n_slots: int) -> int:
    """Return index of first free slot, or -1 if full."""
    for i in range(n_slots):
        if not (bitmap & (1 << i)):
            return i
    return -1


def set_slot(bitmap: int, slot_idx: int) -> int:
    return bitmap | (1 << slot_idx)


def clear_slot(bitmap: int, slot_idx: int) -> int:
    return bitmap & ~(1 << slot_idx)


def write_slot_to_page(page_data: bytearray, slot_idx: int, record_bytes: bytes,
                       fields: list, page_size: int, max_records_per_page: int):
    rec_size = record_byte_size(fields)
    n_slots = max_slots(page_size, rec_size, max_records_per_page)

    # Update bitmap and record_count in header
    page_num, record_count, bitmap = struct.unpack(">IIH", page_data[0:10])
    if not (bitmap & (1 << slot_idx)):
        bitmap = set_slot(bitmap, slot_idx)
        record_count += 1
    # Write updated header fields
    struct.pack_into(">IIH", page_data, 0, page_num, record_count, bitmap)

    # Write record bytes into slot
    start = HEADER_SIZE + slot_idx * rec_size
    page_data[start:start + rec_size] = record_bytes


def clear_slot_in_page(page_data: bytearray, slot_idx: int, fields: list,
                       page_size: int, max_records_per_page: int):
    rec_size = record_byte_size(fields)

    page_num, record_count, bitmap = struct.unpack(">IIH", page_data[0:10])
    if bitmap & (1 << slot_idx):
        bitmap = clear_slot(bitmap, slot_idx)
        record_count -= 1
        struct.pack_into(">IIH", page_data, 0, page_num, record_count, bitmap)
        # Zero out the slot
        start = HEADER_SIZE + slot_idx * rec_size
        page_data[start:start + rec_size] = b"\x00" * rec_size
