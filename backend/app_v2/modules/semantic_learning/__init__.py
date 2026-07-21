"""Semantic learning — extract new vocabulary from meetings/informs/tracker/logs.

Wave 1 / Track C: extractor + proposer + inbox modules only. Router hooks
arrive in Wave 3 (`backend/routers/{meetings,informs,tracker}.py` and the
startup batch job over `flowi_activity.jsonl`).

The lifecycle is:

    extract_*  → candidate term strings
    classify_proposal → mapping / new_canonical / conflict / reject
    enqueue_proposal  → admin queue (data/flow-data/semantic/proposals/*.json)
    update_proposal_status → approve/reject decision

When an admin approves a proposal, a separate (Wave 3) layer applies it to
`semantic_lexicon` via `upsert_alias_group`.
"""
from .extractor import (
    extract_from_activity_log,
    extract_from_inform,
    extract_from_meeting,
    extract_from_tracker_comment,
    extract_terms,
)
from .hooks import (
    submit_activity_log_batch,
    submit_inform,
    submit_meeting,
    submit_tracker_comment,
)
from .inbox import (
    enqueue_proposal,
    list_proposals,
    update_proposal_status,
)
from .proposer import classify_proposal

__all__ = [
    "classify_proposal",
    "enqueue_proposal",
    "extract_from_activity_log",
    "extract_from_inform",
    "extract_from_meeting",
    "extract_from_tracker_comment",
    "extract_terms",
    "list_proposals",
    "submit_activity_log_batch",
    "submit_inform",
    "submit_meeting",
    "submit_tracker_comment",
    "update_proposal_status",
]
