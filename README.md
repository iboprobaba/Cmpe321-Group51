# CMPE321 Project 3 — Modular DBMS Engine

A modular database management system engine composed of four layered Python modules, built for CMPE 321 Introduction to Database Systems, Spring 2026.

---

## Project Structure

```
archive.py                    Entry point: wires all modules, runs input file
config.json                   Configuration file
workload_generator.py         Workload generation script for experiments
record.txt                    Commands used for experiment reproduction
ai_usage.md                   AI tool usage disclosure
README.md
report.pdf                    Design decisions and analysis
individual_contribution.pdf   Per-student contribution report

result_types/
  __init__.py                 PageResult, WriteResult, BufferResult, RecordResult, QueryResult

disk_space_manager/
  __init__.py                 Exports DiskSpaceManager
  dsm.py                      Raw page I/O, seek-based access, IO counter

buffer_manager/
  __init__.py                 Exports BufferManager
  bm.py                       In-memory page cache, LRU/MRU eviction

file_index_manager/
  __init__.py                 Exports FileIndexManager
  catalog.py                  System catalog (data/catalog.json)
  page_format.py              Slotted page encode/decode
  heap_scan.py                Sequential scan index strategy
  hash_index.py               Static hash index (64 buckets)
  bplus_tree.py               B+ tree index (order=50, leaf capacity=100)
  fim.py                      Dispatcher: create_type, create_record, delete, search, range

query_processor/
  __init__.py                 Exports QueryProcessor
  parser.py                   Parses input lines into Command dataclass
  qp.py                       output.txt, log.csv, stats_output.txt handling
```

---

## How to Run

```bash
python archive.py config.json input.txt
```

Output files are written next to `archive.py`:
- `output.txt` — query results and explain output
- `stats_output.txt` — statistics snapshots (overwritten on each `stats` command)
- `log.csv` — persistent append-only operation log

**Clean run** (reset all output files):
```bash
rm -f output.txt log.csv stats_output.txt
python archive.py config.json input.txt
```

---

## Configuration

Edit `config.json` to change engine behavior:

```json
{
  "page_size": 4096,
  "max_records_per_page": 10,
  "buffer_pool_size": 16,
  "replacement_policy": "LRU",
  "index_strategy": "bplus_tree"
}
```

| Parameter | Values | Description |
|---|---|---|
| `page_size` | `4096` | Bytes per page |
| `max_records_per_page` | up to `10` | Slots per page |
| `buffer_pool_size` | `4`, `8`, `16`, `32`, `64` | Page frames in buffer pool |
| `replacement_policy` | `LRU`, `MRU` | Page eviction policy |
| `index_strategy` | `heap_scan`, `hash_index`, `bplus_tree` | Record lookup strategy |

---

## Supported Operations

### Data Definition

```
create type <type-name> <num-fields> <primary-key-order> <field1-name> <field1-type> ...
```

- Field types: `int` (4 bytes) or `str` (32 bytes, null-padded)
- Primary key order is 1-indexed

### Data Manipulation

```
create record <type> <v1> <v2> ...
delete record <type> <pk-value>
search record <type> <pk-value>
range_search <type> <field> <low> <high>
```

- `search` and `delete` use the primary key
- `range_search` works on any integer field
- If `hash_index` is active, `range_search` falls back to heap scan

### System Commands

```
explain <any DML command>
stats
stats reset
```

- `explain` prints the query plan, result, and actual I/O stats to `output.txt`
- `stats` writes a full statistics snapshot to `stats_output.txt`
- `stats reset` resets all layer counters to zero

---

## Output Format

### output.txt

Search/range results are written one record per line (space-separated field values).

Explain output format:
```
--- PLAN ---
Query: search record house Harkonnen
Strategy: bplus_tree
Estimated I/O: 3
--- RESULT ---
Harkonnen GiediPrime Baron 12000 3000 200
--- STATS ---
Actual I/O: 2 reads, 0 writes
Buffer Hits: 1
Buffer Misses: 1
Pages Scanned: 2
```

### stats_output.txt

```
=== STATISTICS ===
Disk I/O: 45 reads, 12 writes
Buffer Pool: 128 requests, 91 hits, 37 misses (71.1% hit rate)
Evictions: 29 (14 dirty writebacks)
Index: bplus_tree, 23 nodes visited
Records: 82 scanned, 15 returned
```

### log.csv

Append-only log of every operation:
```
<unix-timestamp>,<operation-string>,<success|failure>
```

---

## Failure Conditions

The system logs `failure` in `log.csv` and does not crash on:
- Creating a type that already exists
- Creating a record with a duplicate primary key
- Deleting or searching a non-existing record or type
- Range search on a non-integer field

---

## Workload Generator

```bash
python workload_generator.py --mode MODE --records N --queries Q > workload.txt
python archive.py config.json workload.txt
```

| Mode | Description |
|---|---|
| `sequential` | Insert N records, then Q full-table scans |
| `random` | Insert N records, then Q random equality searches |
| `range` | Insert N records, then Q random range queries on an int field |
| `mixed` | Insert N records, then Q mixed ops (search / insert / delete) |

---

## Internal Design Choices

| Decision | Choice |
|---|---|
| `int` field width | 4 bytes, signed (`struct '>i'`) |
| `str` field width | 32 bytes, null-padded UTF-8 |
| Page header | 16 bytes (page_num:4, record_count:4, slot_bitmap:2, reserved:6) |
| Max slots per page | 10 (spec cap) |
| System catalog | `data/catalog.json` (not paged — metadata only) |
| B+ tree root | Always at page 0; root split copies old root to a new page |
| Hash index | 64 buckets at pages 0–63; overflow pages chained via `next_page` |

---

## Technical Constraints

- Python standard library only — no third-party packages
- All type names, field names, and string values are alphanumeric (a–z, A–Z, 0–9)
- All data is persistent across runs; re-invoking with the same `data/` directory recovers state
