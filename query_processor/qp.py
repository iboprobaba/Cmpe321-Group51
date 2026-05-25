import time
import os
import sys
from .parser import parse, Command
from result_types import QueryResult


class QueryProcessor:
    def __init__(self, config: dict, file_idx, buffer, disk):
        self.config = config
        self.fim = file_idx
        self.buffer = buffer
        self.disk = disk
        base_dir = config.get("base_dir", os.path.dirname(os.path.abspath(sys.argv[0])))
        self._output_path = os.path.join(base_dir, "output.txt")
        self._log_path = os.path.join(base_dir, "log.csv")
        self._stats_path = os.path.join(base_dir, "stats_output.txt")
        # Clear output.txt at start of each run
        open(self._output_path, "w").close()

    # ------------------------------------------------------------------ I/O helpers

    def _write_output(self, text: str):
        with open(self._output_path, "a") as f:
            f.write(text + "\n")

    def _log(self, command_str: str, status: str):
        with open(self._log_path, "a") as f:
            f.write(f"{int(time.time())},{command_str},{status}\n")

    # ------------------------------------------------------------------ record formatting

    def _format_record(self, rec: dict, fields: list) -> str:
        parts = []
        for f in fields:
            val = rec.get(f["name"], "")
            parts.append(str(val))
        return " ".join(parts)

    def _get_fields(self, type_name: str):
        t = self.fim.catalog.get_type(type_name)
        return t["fields"] if t else []

    # ------------------------------------------------------------------ command execution

    def process(self, line: str):
        cmd = parse(line)
        if cmd is None or cmd.op == "error":
            msg = cmd.args.get("msg", "Parse error") if cmd else "Empty line"
            self._log(line, "failure")
            return

        if cmd.op == "explain":
            self._execute_explain(cmd)
        elif cmd.op == "stats":
            self._execute_stats()
        elif cmd.op == "stats_reset":
            self._execute_stats_reset(line)
        else:
            result = self._dispatch(cmd)
            status = result.status if result else "failure"
            self._log(line, status)
            if cmd.op == "search_record" and result and result.status == "success":
                fields = self._get_fields(cmd.args["type_name"])
                for rec in result.records:
                    self._write_output(self._format_record(rec, fields))
            elif cmd.op == "range_search" and result and result.status == "success":
                fields = self._get_fields(cmd.args["type_name"])
                for rec in result.records:
                    self._write_output(self._format_record(rec, fields))

    def _dispatch(self, cmd: Command):
        op = cmd.op
        a = cmd.args

        if op == "create_type":
            return self.fim.create_type(a["name"], a["num_fields"], a["pk_order"], a["fields"])
        elif op == "create_record":
            return self.fim.create_record(a["type_name"], a["values"])
        elif op == "delete_record":
            return self.fim.delete_record(a["type_name"], a["pk_value"])
        elif op == "search_record":
            return self.fim.search_record(a["type_name"], a["pk_value"])
        elif op == "range_search":
            return self.fim.range_search(a["type_name"], a["field_name"], a["low"], a["high"])
        return None

    # ------------------------------------------------------------------ explain

    def _execute_explain(self, cmd: Command):
        inner = cmd.args["inner"]
        strategy = self.config.get("index_strategy", "heap_scan")

        # Estimate I/O
        est_io = self._estimate_io(inner, strategy)

        # Write plan
        self._write_output("--- PLAN ---")
        self._write_output(f"Query: {inner.raw}")
        self._write_output(f"Strategy: {strategy}")
        self._write_output(f"Estimated I/O: {est_io}")

        # Snapshot stats before
        disk_before = self.disk.get_stats()
        buf_before = self.buffer.get_stats()

        # Execute
        result = self._dispatch(inner)

        # Snapshot stats after
        disk_after = self.disk.get_stats()
        buf_after = self.buffer.get_stats()

        # Write result
        self._write_output("--- RESULT ---")
        if result and result.status == "success" and result.records:
            fields = self._get_fields(inner.args.get("type_name", ""))
            for rec in result.records:
                self._write_output(self._format_record(rec, fields))
        # (nothing if no records or failure)

        actual_reads = disk_after["reads"] - disk_before["reads"]
        actual_writes = disk_after["writes"] - disk_before["writes"]
        actual_hits = buf_after["hits"] - buf_before["hits"]
        actual_misses = buf_after["misses"] - buf_before["misses"]
        pages_scanned = result.pages_accessed if result else 0

        self._write_output("--- STATS ---")
        self._write_output(f"Actual I/O: {actual_reads} reads, {actual_writes} writes")
        self._write_output(f"Buffer Hits: {actual_hits}")
        self._write_output(f"Buffer Misses: {actual_misses}")
        self._write_output(f"Pages Scanned: {pages_scanned}")

        status = result.status if result else "failure"
        self._log(cmd.raw, status)

    def _estimate_io(self, cmd: Command, strategy: str) -> int:
        op = cmd.op
        if op == "search_record":
            if strategy == "heap_scan":
                return 5
            elif strategy == "hash_index":
                return 2
            elif strategy == "bplus_tree":
                return 3
        elif op == "range_search":
            return 10
        elif op == "create_record":
            return 2
        elif op == "delete_record":
            return 3
        return 1

    # ------------------------------------------------------------------ stats

    def _execute_stats(self):
        disk_stats = self.disk.get_stats()
        buf_stats = self.buffer.get_stats()
        fim_stats = self.fim.get_stats()

        total_req = buf_stats["requests"]
        hits = buf_stats["hits"]
        misses = buf_stats["misses"]
        hit_rate = (hits / total_req * 100) if total_req > 0 else 0.0
        evictions = buf_stats["evictions"]
        dirty_wb = buf_stats["dirty_writebacks"]
        strategy = fim_stats["index_strategy"]
        nodes = fim_stats["nodes_visited"]
        scanned = fim_stats["records_scanned"]
        returned = fim_stats["records_returned"]

        lines = [
            "=== STATISTICS ===",
            f"Disk I/O: {disk_stats['reads']} reads, {disk_stats['writes']} writes",
            f"Buffer Pool: {total_req} requests, {hits} hits, {misses} misses ({hit_rate:.1f}% hit rate)",
            f"Evictions: {evictions} ({dirty_wb} dirty writebacks)",
            f"Index: {strategy}, {nodes} nodes visited",
            f"Records: {scanned} scanned, {returned} returned",
        ]

        with open(self._stats_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        self._log("stats", "success")

    def _execute_stats_reset(self, raw_line: str):
        self.disk.reset_stats()
        self.buffer.reset_stats()
        self.fim.reset_stats()
        self._log(raw_line, "success")
