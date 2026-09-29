"""Standard-library-only packaging and seed-only installation of domain knowledge.

Private documents belong in an explicitly requested local installer, never in
source assets or the public installer. Only the latest document is transferred.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SEED_RELATIVE_PATH = Path("data/install-seeds/domain_knowledge.json")


def validate_seed(document: dict) -> dict:
    if not isinstance(document, dict):
        raise ValueError("Domain knowledge must be a document object")
    result = {}
    for key, maximum in (("title", 160), ("body", 60000), ("editing_guidelines", 12000)):
        value = document.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"Invalid domain knowledge field: {key}")
        result[key] = value.strip()
    return result


def export_document(db_path: Path) -> dict:
    # mode=ro also prevents an incorrect source path from creating an empty DB.
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT document FROM revisions ORDER BY version DESC LIMIT 1").fetchone()
    if row is None:
        raise ValueError("No saved domain knowledge to include")
    return validate_seed(json.loads(row[0]))


def seed_database(db_path: Path, document: dict) -> bool:
    document = validate_seed(document)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=15)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS revisions (version INTEGER PRIMARY KEY, document TEXT NOT NULL)")
        if db.execute("SELECT 1 FROM revisions LIMIT 1").fetchone():
            return False
        revision = {**document, "version": 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": "installer"}
        db.execute("INSERT INTO revisions(version, document) VALUES(?, ?)",
                   (1, json.dumps(revision, ensure_ascii=False)))
    return True


def install_seed(app_root: Path, document: dict, data_root: Path) -> bool:
    document = validate_seed(document)
    seed_path = app_root / SEED_RELATIVE_PATH
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    # Preserve the installation's initial seed, just as we preserve its live edits.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=seed_path.parent,
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2)
        # Atomic create-if-absent; another installer never sees partial JSON.
        try:
            os.link(temporary, seed_path)
        except FileExistsError:
            document = validate_seed(json.loads(seed_path.read_text(encoding="utf-8")))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return seed_database(data_root / "knowledge" / "domain_knowledge.sqlite3", document)
