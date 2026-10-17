"""One-off: build the knowledge graph for documents that were indexed before the ingestion function app started building it.

New documents get their graph edges when they are ingested (local_function_app/function_app.py). Documents indexed earlier have chunks in the
`documents` collection but no edges, so graph retrieval finds nothing for them.
    python -m backend.utils.graph_backfill                       # lists what would be built and the model calls it needs, changes nothing
    python -m backend.utils.graph_backfill --apply               # builds the edges for every document that has none
    python -m backend.utils.graph_backfill --apply --only "file.pdf"   # just one file (by its name), even if it already has edges

It reads each file's chunks back from the database (so no PDF is re-read), sends them to the same small model, in the same blocks, the function app uses,
and replaces that file's edges. Safe to re-run: files that already have edges are skipped unless named with --only. The model calls are real spend
(a few cents for a whole knowledge base), which is why it defaults to a dry run.
"""
import sys
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

from backend.utils.db_utils import get_db  # noqa: E402  (after load_dotenv so the connection string is set)
from local_function_app import function_app as fa  # noqa: E402


def files_in_the_database(db):
    """{(affiliate, source): [chunk text in page order]} for every indexed document."""
    grouped = defaultdict(list)
    for chunk in db["documents"].find({}, {"affiliate": 1, "source": 1, "page": 1, "text": 1}):
        affiliate, source = chunk.get("affiliate"), chunk.get("source")
        if affiliate and source and chunk.get("text"):
            grouped[(affiliate, source)].append((chunk.get("page") or 0, chunk["text"]))
    return {key: [text for _, text in sorted(items, key=lambda item: item[0])] for key, items in grouped.items()}


def main(argv) -> int:
    # File names can hold characters a Windows console cannot print (an emoji in a title); show a ? instead of stopping.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    apply = "--apply" in argv
    only = argv[argv.index("--only") + 1] if "--only" in argv and argv.index("--only") + 1 < len(argv) else None
    db = get_db()
    if db is None:
        print("No database is configured, so there is nothing to build.")
        return 1
    have_edges = {(e["_id"]["affiliate"], e["_id"]["source"]) for e in db[fa.GRAPH_EDGES_COLLECTION].aggregate(
        [{"$group": {"_id": {"affiliate": "$affiliate", "source": "$source"}}}])}
    files = files_in_the_database(db)
    todo = {key: texts for key, texts in files.items() if (key[1] == only if only else key not in have_edges)}
    calls = 0
    for (affiliate, source), texts in sorted(todo.items()):
        batches = min(len(fa._batch_texts(texts, fa.GRAPH_BATCH_CHARS)), fa.GRAPH_MAX_BATCHES)
        calls += batches
        print(f"{'Building' if apply else 'Would build'} {affiliate} / {source}: {len(texts)} chunks, {batches} model calls")
        if apply:
            stored = fa.index_graph_for_source(db, affiliate, source, texts)
            print(f"    {stored} relationships stored")
    print(f"\n{len(todo)} of {len(files)} documents need a graph; {calls} model calls in all. Already built: {len(files) - len(todo) if not only else 'n/a'}.")
    if not apply and todo:
        print("Nothing was written. Re-run with --apply to build them.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
