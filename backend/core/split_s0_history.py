"""Observed SOP revisions; naive source timestamps use the site's KST clock."""
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return (dt.replace(tzinfo=KST) if dt.tzinfo is None else dt).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def observe(history, rows, observed_at):
    """Append changed steps, including removals, from a complete source snapshot."""
    if timestamp(observed_at) is None:
        return
    normalized = {str(step).casefold(): row for step, row in rows.items()}
    for step in set(history) | set(normalized):
        row = normalized.get(step) or {}
        ppid = str(row.get("ppid") or "").strip()
        events = history.setdefault(step, [])
        if events and events[-1]["ppid"] == ppid:
            continue
        if not events and not ppid:
            continue
        events.append({"ppid": ppid, "step_id": str(row.get("step_id") or step),
                       "effective_at": observed_at})


def resolve(events, as_of):
    """Latest observation at the requested time; prehistory uses first receipt.

    A removal is returned explicitly with an empty PPID. It must not resurrect
    an older SOP. Invalid/missing time is unknown, never silently 'today'.
    """
    at = timestamp(as_of)
    if at is None or not events:
        return None
    valid = sorted((e for e in events if timestamp(e.get("effective_at")) is not None),
                   key=lambda e: timestamp(e["effective_at"]))
    if not valid:
        return None
    index = bisect_right([timestamp(e["effective_at"]) for e in valid], at) - 1
    return {**valid[max(0, index)], "basis": "first_received" if index < 0 else "as_of",
            "as_of": str(as_of)}
