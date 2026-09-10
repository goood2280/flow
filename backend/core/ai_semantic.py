"""Read-only hash-pinned references from operator-owned DB/AI."""
import hashlib
import json
import re
from pathlib import PurePosixPath
from core.paths import PATHS


def _load():
    root = PATHS.db_root / "AI"
    missing = {"status": "missing", "version": "", "document_count": 0, "error": ""}
    if not (root / "manifest.json").exists():
        return missing, []
    try:
        def read(relative, limit):
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or chr(92) in relative or ":" in relative:
                raise ValueError()
            target = root
            if root.is_symlink():
                raise ValueError()
            for part in path.parts:
                target = target / part
                if target.is_symlink():
                    raise ValueError()
            target.resolve().relative_to(root.resolve())
            with target.open("rb") as stream:
                content = stream.read(limit + 1)
            if len(content) > limit:
                raise ValueError()
            return content
        manifest = json.loads(read("manifest.json", 65536))
        if set(manifest) != {"schema_version", "release"} or manifest["schema_version"] not in (1, 2):
            raise ValueError()
        release = manifest["release"]
        if set(release) != {"version", "approved", "documents"} or release["approved"] is not True:
            raise ValueError()
        version = release["version"]
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version):
            raise ValueError()
        docs = release["documents"]
        if not isinstance(docs, list) or not 1 <= len(docs) <= 32:
            raise ValueError()
        loaded, seen, total = [], set(), 0
        for doc in docs:
            if set(doc) != {"path", "sha256"}:
                raise ValueError()
            path = doc["path"]
            if not isinstance(path, str) or not path.startswith(f"releases/{version}/") or not path.endswith(".md" if manifest["schema_version"] == 1 else ".json") or path in seen:
                raise ValueError()
            content = read(path, 131072)
            total += len(content)
            if total > 524288 or hashlib.sha256(content).hexdigest() != doc["sha256"]:
                raise ValueError()
            seen.add(path)
            if manifest["schema_version"] == 1:
                loaded.append({"path": path, "text": content.decode("utf-8")})
            else:
                records = json.loads(content)
                if not isinstance(records, list) or len(records) > 1000:
                    raise ValueError()
                for record in records:
                    _validate_record(record)
                    if any(d.get("record", {}).get("id") == record["id"] for d in loaded):
                        raise ValueError()
                    loaded.append({"path": path, "record": record, "text": json.dumps(record, ensure_ascii=False)})
        if manifest["schema_version"] == 2:
            product_ids = {d["record"]["id"] for d in loaded if d["record"]["kind"] == "product"}
            for doc in loaded:
                record = doc["record"]
                if record["kind"] == "measurement_binding" and record["product_id"] not in product_ids:
                    raise ValueError()
        return {"status": "ready", "version": version, "document_count": len(docs), "error": ""}, loaded
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {**missing, "status": "invalid", "error": "manifest 형식·승인·파일 경로·SHA256을 확인하세요."}, []


def snapshot():
    return _load()[0]


def prompt_context(query):
    state, docs = _load()
    terms = set(re.findall(r"[\w가-힣]{2,}", query.lower()))
    product_ids = set()
    # Exact aliases expand the search vocabulary; facts remain product-scoped.
    for doc in docs:
        record = doc.get("record", {})
        if record.get("kind") == "product" and any(_mentioned(alias, query) for alias in record.get("aliases", []) + [record.get("canonical_name", "")]):
            product_ids.add(record["id"])
            terms.update([record["id"].lower(), record["canonical_name"].lower()])
    if product_ids:
        docs = [doc for doc in docs if doc.get("record", {}).get("kind") != "measurement_binding"
                or doc["record"]["product_id"] in product_ids]
    docs.sort(key=lambda d: sum(term in d["text"].lower() for term in terms), reverse=True)
    excerpts, remaining = [], 12000
    for doc in docs[:8]:
        # Structured facts must never be cut in the middle of JSON.
        if doc.get("record") and len(doc["text"]) > remaining:
            continue
        text = doc["text"] if doc.get("record") else doc["text"][:min(4000, remaining)]
        excerpts.append({"path": doc["path"], "text": text})
        remaining -= len(text)
        if remaining <= 0:
            break
    return {**state, "reference_only": True, "documents": excerpts}



def _mentioned(alias, query):
    return bool(alias and re.search(r"(?<![A-Za-z0-9_])" + re.escape(alias) + r"(?![A-Za-z0-9_])", query, re.I))


def _validate_record(record):
    if not isinstance(record, dict) or record.get("kind") not in {"product", "measurement_binding", "term", "rule"}:
        raise ValueError()
    for key in ("id", "created_at", "updated_at"):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError()
    from datetime import datetime
    for key in ("created_at", "updated_at"):
        if datetime.fromisoformat(record[key].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError()
    if record.get("status") != "active" or not isinstance(record.get("source_ids"), list) or not record["source_ids"]:
        raise ValueError()
    if not all(isinstance(x, str) and x for x in record["source_ids"]):
        raise ValueError()
    if len(json.dumps(record, ensure_ascii=False)) > 4000:
        raise ValueError()
    if record["kind"] == "product":
        if not isinstance(record.get("canonical_name"), str) or not record["canonical_name"]:
            raise ValueError()
        if not isinstance(record.get("aliases"), list) or not all(isinstance(x, str) and x for x in record["aliases"]):
            raise ValueError()
    if record["kind"] == "measurement_binding":
        for key in ("product_id", "concept", "step_id", "item"):
            if not isinstance(record.get(key), str) or not record[key]:
                raise ValueError()


def product_alias_candidates(query, products):
    state, docs = _load()
    if state["status"] != "ready":
        return []
    by_name = {str(p).casefold(): p for p in products}
    found = set()
    for doc in docs:
        record = doc.get("record", {})
        if record.get("kind") == "product" and any(_mentioned(alias, query) for alias in record["aliases"]):
            canonical = by_name.get(record["canonical_name"].casefold())
            if canonical:
                found.add(canonical)
    return sorted(found)
