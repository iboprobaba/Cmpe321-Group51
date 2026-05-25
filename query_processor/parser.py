from dataclasses import dataclass, field
from typing import Any


@dataclass
class Command:
    op: str       # create_type | create_record | delete_record | search_record |
                  # range_search | explain | stats | stats_reset
    args: dict = field(default_factory=dict)
    raw: str = ""


def parse(line: str) -> Command:
    line = line.strip()
    tokens = line.split()
    if not tokens:
        return None

    # stats reset
    if len(tokens) == 2 and tokens[0] == "stats" and tokens[1] == "reset":
        return Command(op="stats_reset", raw=line)

    # stats
    if tokens[0] == "stats":
        return Command(op="stats", raw=line)

    # explain <rest>
    if tokens[0] == "explain":
        inner_line = line[len("explain"):].strip()
        inner = parse(inner_line)
        if inner:
            inner.raw = inner_line
            return Command(op="explain", args={"inner": inner}, raw=line)
        return None

    # range_search <type> <field> <low> <high>
    if tokens[0] == "range_search":
        if len(tokens) < 5:
            return Command(op="error", args={"msg": "Invalid range_search syntax"}, raw=line)
        return Command(op="range_search", args={
            "type_name": tokens[1],
            "field_name": tokens[2],
            "low": tokens[3],
            "high": tokens[4],
        }, raw=line)

    # create type <name> <num_fields> <pk_order> <f1_name> <f1_type> ...
    if tokens[0] == "create" and len(tokens) > 1 and tokens[1] == "type":
        if len(tokens) < 6:
            return Command(op="error", args={"msg": "Invalid create type syntax"}, raw=line)
        name = tokens[2]
        num_fields = int(tokens[3])
        pk_order = int(tokens[4])
        field_tokens = tokens[5:]
        if len(field_tokens) < num_fields * 2:
            return Command(op="error", args={"msg": "Not enough field definitions"}, raw=line)
        fields = []
        for i in range(num_fields):
            fname = field_tokens[i * 2]
            ftype = field_tokens[i * 2 + 1]
            fields.append({"name": fname, "type": ftype})
        return Command(op="create_type", args={
            "name": name,
            "num_fields": num_fields,
            "pk_order": pk_order,
            "fields": fields,
        }, raw=line)

    # create record <type> <v1> <v2> ...
    if tokens[0] == "create" and len(tokens) > 1 and tokens[1] == "record":
        if len(tokens) < 4:
            return Command(op="error", args={"msg": "Invalid create record syntax"}, raw=line)
        type_name = tokens[2]
        values = tokens[3:]
        return Command(op="create_record", args={
            "type_name": type_name,
            "values": values,
        }, raw=line)

    # delete record <type> <pk_value>
    if tokens[0] == "delete" and len(tokens) > 1 and tokens[1] == "record":
        if len(tokens) < 4:
            return Command(op="error", args={"msg": "Invalid delete record syntax"}, raw=line)
        return Command(op="delete_record", args={
            "type_name": tokens[2],
            "pk_value": tokens[3],
        }, raw=line)

    # search record <type> <pk_value>
    if tokens[0] == "search" and len(tokens) > 1 and tokens[1] == "record":
        if len(tokens) < 4:
            return Command(op="error", args={"msg": "Invalid search record syntax"}, raw=line)
        return Command(op="search_record", args={
            "type_name": tokens[2],
            "pk_value": tokens[3],
        }, raw=line)

    return Command(op="error", args={"msg": f"Unknown command: {line}"}, raw=line)
