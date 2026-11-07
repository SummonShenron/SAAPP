"""A small in-memory stand-in for the database with just the operations the knowledge base uses (backend/utils/knowledge_base.py): equality on dotted paths and $in, $set and $inc (with upsert),
insert_one (assigns an _id like Mongo), delete_many, count_documents, and an aggregate() that returns the rows a test supplies and records the pipeline it was given."""
import copy
from types import SimpleNamespace


def _get(doc, path):
    for part in path.split("."):
        if not isinstance(doc, dict) or part not in doc:
            return None
        doc = doc[part]
    return doc


def _set(doc, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        doc = doc.setdefault(part, {})
    doc[parts[-1]] = value


def _match(doc, query):
    for key, cond in (query or {}).items():
        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            value = _get(doc, key)
            for op, arg in cond.items():
                if op == "$in" and value not in arg:
                    return False
        elif _get(doc, key) != cond:
            return False
    return True


class Collection:
    def __init__(self):
        self.docs = []
        self.rows = []              # what aggregate() returns
        self.pipelines = []
        self.projections = []

    def find(self, query=None, projection=None):
        self.projections.append(projection)
        out = [copy.deepcopy(d) for d in self.docs if _match(d, query)]
        if isinstance(projection, dict) and projection and all(v == 0 for v in projection.values()):
            for d in out:
                for field in projection:
                    d.pop(field, None)
        return out

    def find_one(self, query=None, projection=None):
        found = self.find(query, projection)
        return found[0] if found else None

    def count_documents(self, query):
        return len([d for d in self.docs if _match(d, query)])

    def insert_one(self, doc):
        assert not any(d.get("_id") == doc.get("_id") and "_id" in doc for d in self.docs), "duplicate _id"
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", f"auto-{len(self.docs)}-{id(doc)}")          # Mongo assigns one when the document has none
        self.docs.append(stored)

    def update_one(self, query, update, upsert=False):
        target = next((d for d in self.docs if _match(d, query)), None)
        if target is None:
            if not upsert:
                return SimpleNamespace(modified_count=0)
            target = {k: v for k, v in query.items() if not isinstance(v, dict)}
            self.docs.append(target)
        for key, value in update.get("$set", {}).items():
            _set(target, key, value)
        for key, value in update.get("$inc", {}).items():
            _set(target, key, (_get(target, key) or 0) + value)
        return SimpleNamespace(modified_count=1)

    def delete_many(self, query):
        self.docs = [d for d in self.docs if not _match(d, query)]

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return [copy.deepcopy(r) for r in self.rows]


class Db(dict):
    def __getitem__(self, name):
        return self.setdefault(name, Collection())
