"""Secret-Scan: im Projekt duerfen keine echten Schluessel stehen."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = [r"sk-ant-[A-Za-z0-9_-]{20,}", r"\bsk_[a-f0-9]{30,}", r"pat-[a-z0-9]{2,4}-[a-f0-9-]{20,}",
            r"GOCSPX-[A-Za-z0-9_-]{10,}", r"wrkspc_[A-Za-z0-9]{12,}", r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"]


def test_keine_schluessel_im_code():
    hits = []
    for f in ROOT.rglob("*"):
        if f.is_file() and f.suffix in {".py", ".md", ".html", ".txt", ".json"} and "vendor" not in f.parts \
                and "backups" not in f.parts and f.name != "test_secrets.py":
            text = f.read_text(encoding="utf-8", errors="ignore")
            hits += [f"{f.relative_to(ROOT)}: {p}" for p in PATTERNS if re.search(p, text)]
    assert not hits, hits
