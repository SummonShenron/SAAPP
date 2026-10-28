"""A small in-memory stand-in for the database, with just the operations the runner's job queue uses (backend/utils/runner_jobs.py): equality and a few comparison filters, $set and $unset, upsert,
and an atomic find-one-and-update with a sort. Shared by the queue's tests and the end-to-end test."""
import copy
from typing import Any, Dict, List, Optional


def _matches(doc: Dict[str, Any], flt: Optional[Dict[str, Any]]) -> bool:
    for key, want in (flt or {}).items():
        have = doc.get(key)
        if isinstance(want, dict) and any(str(k).startswith("$") for k in want):
            for op, arg in want.items():
                if op == "$in" and have not in arg:
                    return False
                if op == "$gt" and not (have is not None and have > arg):
                    return False
                if op == "$gte" and not (have is not None and have >= arg):
                    return False
                if op == "$lt" and not (have is not None and have < arg):
                    return False
                if op == "$lte" and not (have is not None and have <= arg):
                    return False
                if op == "$ne" and have == arg:
                    return False
        elif have != want:
            return False
    return True


def _apply(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
    for key, value in (update.get("$set") or {}).items():
        doc[key] = copy.deepcopy(value)
    for key in (update.get("$unset") or {}):
        doc.pop(key, None)


class FakeCollection:
    def __init__(self) -> None:
        self.docs: Dict[Any, Dict[str, Any]] = {}

    def insert_one(self, doc: Dict[str, Any]) -> None:
        doc = copy.deepcopy(doc)
        self.docs[doc["_id"]] = doc

    def find(self, flt: Optional[Dict[str, Any]] = None, projection: Any = None) -> List[Dict[str, Any]]:
        return [copy.deepcopy(d) for d in self.docs.values() if _matches(d, flt)]

    def find_one(self, flt: Optional[Dict[str, Any]] = None, projection: Any = None) -> Optional[Dict[str, Any]]:
        found = self.find(flt)
        return found[0] if found else None

    def count_documents(self, flt: Optional[Dict[str, Any]] = None) -> int:
        return len(self.find(flt))

    def _first(self, flt: Dict[str, Any], sort: Any) -> Optional[Dict[str, Any]]:
        live = [d for d in self.docs.values() if _matches(d, flt)]
        for field, direction in reversed(sort or []):
            live.sort(key=lambda d: (d.get(field) is None, d.get(field)), reverse=direction < 0)
        return live[0] if live else None

    def update_one(self, flt: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> Any:
        doc = self._first(flt, None)
        if doc is None and upsert:
            doc = {"_id": flt["_id"]} if "_id" in flt else {}
            self.docs[doc["_id"]] = doc
        if doc is None:
            return type("R", (), {"matched_count": 0, "modified_count": 0})()
        _apply(doc, update)
        return type("R", (), {"matched_count": 1, "modified_count": 1})()

    def find_one_and_update(self, flt: Dict[str, Any], update: Dict[str, Any], sort: Any = None, return_document: Any = False) -> Optional[Dict[str, Any]]:
        doc = self._first(flt, sort)
        if doc is None:
            return None
        before = copy.deepcopy(doc)
        _apply(doc, update)
        return copy.deepcopy(doc) if return_document else before

    def delete_many(self, flt: Optional[Dict[str, Any]] = None) -> None:
        for key in [k for k, d in self.docs.items() if _matches(d, flt)]:
            del self.docs[key]

    def create_index(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeDB:
    def __init__(self) -> None:
        self.collections: Dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())
