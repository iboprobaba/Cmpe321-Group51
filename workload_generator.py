#!/usr/bin/env python3
"""
Workload generator for CMPE321 Project 3 DBMS.

Usage:
    python3 workload_generator.py --mode MODE --records N --queries Q

Modes:
    sequential  Insert N records, then Q full-table scans (range over entire key space)
    random      Insert N records, then Q random equality searches
    range       Insert N records, then Q random range queries on an int field
    mixed       Insert N records, then Q mixed ops (search / insert / delete)

Output is printed to stdout (pipe to a file for use as input.txt).
"""
import argparse
import random
import string


TYPE_NAME = "worker"
FIELDS = "id int name str age int salary int dept str score int"
# fields: id(pk), name, age, salary, dept, score
NUM_FIELDS = 6
PK_ORDER = 1  # id is primary key

DEPTS = ["Engineering", "Marketing", "Finance", "HR", "Sales", "Legal"]
NAMES = ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Hank",
         "Iris", "Jack", "Kate", "Leo", "Mona", "Ned", "Olive", "Paul"]


def random_name():
    return random.choice(NAMES) + str(random.randint(1, 999))


def random_dept():
    return random.choice(DEPTS)


def make_record(rec_id: int) -> str:
    name = random_name()
    age = random.randint(22, 65)
    salary = random.randint(30000, 200000)
    dept = random_dept()
    score = random.randint(1, 100)
    return f"create record {TYPE_NAME} {rec_id} {name} {age} {salary} {dept} {score}"


def header() -> str:
    return f"create type {TYPE_NAME} {NUM_FIELDS} {PK_ORDER} {FIELDS}"


def generate(mode: str, num_records: int, num_queries: int):
    lines = [header()]
    ids = list(range(1, num_records + 1))
    random.shuffle(ids)

    for rec_id in ids:
        lines.append(make_record(rec_id))

    inserted_ids = list(ids)

    if mode == "sequential":
        for _ in range(num_queries):
            lines.append(f"range_search {TYPE_NAME} salary 1 999999999")

    elif mode == "random":
        for _ in range(num_queries):
            pk = random.choice(inserted_ids) if inserted_ids else 1
            lines.append(f"search record {TYPE_NAME} {pk}")

    elif mode == "range":
        for _ in range(num_queries):
            lo = random.randint(1, 100000)
            hi = lo + random.randint(10000, 50000)
            lines.append(f"range_search {TYPE_NAME} salary {lo} {hi}")

    elif mode == "mixed":
        next_id = num_records + 1
        for _ in range(num_queries):
            op = random.choice(["search", "search", "insert", "delete"])
            if op == "search" and inserted_ids:
                pk = random.choice(inserted_ids)
                lines.append(f"search record {TYPE_NAME} {pk}")
            elif op == "insert":
                lines.append(make_record(next_id))
                inserted_ids.append(next_id)
                next_id += 1
            elif op == "delete" and inserted_ids:
                pk = random.choice(inserted_ids)
                inserted_ids.remove(pk)
                lines.append(f"delete record {TYPE_NAME} {pk}")
            else:
                if inserted_ids:
                    pk = random.choice(inserted_ids)
                    lines.append(f"search record {TYPE_NAME} {pk}")

    for line in lines:
        print(line)


def main():
    parser = argparse.ArgumentParser(description="DBMS workload generator")
    parser.add_argument("--mode", required=True,
                        choices=["sequential", "random", "range", "mixed"])
    parser.add_argument("--records", type=int, default=100)
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    generate(args.mode, args.records, args.queries)


if __name__ == "__main__":
    main()
