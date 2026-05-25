import json
import os


class Catalog:
    """
    Stores type metadata in data/catalog.json.
    Schema: { type_name: { num_fields, pk_order (1-indexed), fields: [{name, type}] } }
    """

    def __init__(self, data_dir: str):
        self._path = os.path.join(data_dir, "catalog.json")
        self._types: dict = {}
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            with open(self._path, "r") as f:
                self._types = json.load(f)

    def _save(self):
        with open(self._path, "w") as f:
            json.dump(self._types, f, indent=2)

    def type_exists(self, name: str) -> bool:
        return name in self._types

    def create_type(self, name: str, num_fields: int, pk_order: int, fields: list):
        self._types[name] = {
            "num_fields": num_fields,
            "pk_order": pk_order,       # 1-indexed
            "fields": fields,            # [{"name": x, "type": "int"|"str"}]
        }
        self._save()

    def get_type(self, name: str) -> dict:
        return self._types.get(name)

    def get_pk_field(self, name: str) -> dict:
        t = self._types[name]
        return t["fields"][t["pk_order"] - 1]

    def all_types(self) -> list:
        return list(self._types.keys())
