"""Downloads and verifies the WASI CPython interpreter used by backend/services/python_sandbox.py.

Run once after cloning, or any time python-3.12.0.wasm is missing/corrupted:

    python backend/sandbox/fetch_sandbox.py

The binary itself is gitignored (26MB, third-party, downloadable) — this script and the
committed .sha256sum file are what make it reproducible instead of just trusting whatever
happens to be on disk.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

RELEASE_TAG = "python/3.12.0+20231211-040d5a6"
FILENAME = "python-3.12.0.wasm"
URL = (
    "https://github.com/vmware-labs/webassembly-language-runtimes/releases/download/"
    "python%2F3.12.0%2B20231211-040d5a6/python-3.12.0.wasm"
)

SANDBOX_DIR = Path(__file__).resolve().parent
DEST_PATH = SANDBOX_DIR / FILENAME
CHECKSUM_PATH = SANDBOX_DIR / f"{FILENAME}.sha256sum"


def expected_sha256() -> str:
    return CHECKSUM_PATH.read_text().split()[0].strip()


def actual_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    expected = expected_sha256()

    if DEST_PATH.exists() and actual_sha256(DEST_PATH) == expected:
        print(f"{FILENAME} already present and verified.")
        return 0

    print(f"Downloading {FILENAME} from {RELEASE_TAG} ...")
    urllib.request.urlretrieve(URL, DEST_PATH)

    actual = actual_sha256(DEST_PATH)
    if actual != expected:
        DEST_PATH.unlink(missing_ok=True)
        print(f"Checksum mismatch: expected {expected}, got {actual}. Deleted the download.", file=sys.stderr)
        return 1

    print(f"Downloaded and verified {FILENAME} ({DEST_PATH.stat().st_size:,} bytes).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
