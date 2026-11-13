"""Which files of a project may be sent to SAAPP so Sonic can read them: the SAME rules the browser's folder upload applies (local/src/localWorkspace.ts) and the server enforces again whatever a sender says
(backend/services/local_workspace.py). There are three copies of these lists (browser, server, here); a test (backend/tests/test_runner_files_rules.py) pins them equal, so a rule cannot be added to one and
forgotten in another. Secrets are never sent: this is the first filter, and the server's refusal is the second.
"""
from typing import FrozenSet

MAX_FILE_BYTES = 500_000
MAX_DOCX_BYTES = 3_000_000          # a Word or Excel file is sent whole so the server can make a text outline of it (and keep only that)
MAX_TOTAL_BYTES = 60_000_000
MAX_FILES = 8000
BATCH_MAX_BYTES = 2_500_000
BATCH_MAX_FILES = 300

SKIP_DIRS: FrozenSet[str] = frozenset({
    "node_modules", ".git", "dist", "build", "__pycache__", ".venv", "venv", ".next", "coverage", "chroma_db", "index-db",
    ".claude",
})
TEXT_EXTENSIONS: FrozenSet[str] = frozenset({
    "py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "json", "md", "mdx", "css", "scss", "html", "htm",
    "toml", "yaml", "yml", "txt", "sh", "ps1", "bat", "sql", "ini", "cfg", "conf", "xml", "graphql",
    "csv", "tsv", "log", "ics", "tex", "rst", "org", "srt", "vtt",
})
TEXT_FILENAMES: FrozenSet[str] = frozenset({"dockerfile", "makefile", "procfile", "license", "readme", ".gitignore", ".gitattributes", ".dockerignore", ".editorconfig"})
SKIPPED_FILENAMES: FrozenSet[str] = frozenset({"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"})
SECRET_FILENAMES: FrozenSet[str] = frozenset({
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc", "credentials.json",
    "secrets.json", "secrets.yaml", "secrets.yml", "secrets.toml",
})
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".env")
ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template")
OFFICE_SUFFIXES = (".docx", ".xlsx", ".xlsm")


def is_secret_name(lower_name: str) -> bool:
    if lower_name.endswith(ENV_TEMPLATE_SUFFIXES):
        return False
    return lower_name == ".env" or lower_name.startswith(".env.") or lower_name in SECRET_FILENAMES or lower_name.endswith(SECRET_SUFFIXES)


def is_syncable_file_name(name: str) -> bool:
    """Whether a file is worth sending: source or text by extension or well-known name, not a lockfile or minified bundle, and never anything that looks like a secret."""
    lower = name.lower()
    if is_secret_name(lower) or lower in SKIPPED_FILENAMES or lower.endswith(".min.js") or lower.endswith(".map"):
        return False
    if lower.startswith("~$"):
        return False                                    # Word's lock file for a document that is open
    if lower.endswith(OFFICE_SUFFIXES):
        return True
    if lower.endswith(ENV_TEMPLATE_SUFFIXES):
        return True
    if lower in TEXT_FILENAMES:
        return True
    dot = lower.rfind(".")
    return dot > 0 and lower[dot + 1:] in TEXT_EXTENSIONS


def is_office_name(name: str) -> bool:
    return name.lower().endswith(OFFICE_SUFFIXES)


def size_limit(name: str) -> int:
    return MAX_DOCX_BYTES if is_office_name(name) else MAX_FILE_BYTES
