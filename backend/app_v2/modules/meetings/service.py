from __future__ import annotations

import copy

from app_v2.modules.meetings.repository import MeetingRepository
from app_v2.shared.result import fail, ok


class MeetingService:
    def __init__(self, repo: MeetingRepository):
        self.repo = repo

    def create_meeting(self, meeting: dict):
        if not (meeting.get("title") or "").strip():
            return fail("title required")
        saved = self.repo.create_meeting(meeting)
        return ok({"meeting": saved})

    def update_meeting(self, meeting_id: str, patch: dict):
        items = self.repo.list_meetings()
        meeting = next((m for m in items if m.get("id") == meeting_id), None)
        if not meeting:
            return fail("meeting not found")
        next_meeting = {**meeting, **patch}
        saved = self.repo.replace_meeting(meeting_id, next_meeting)
        if not saved:
            return fail("meeting not found")
        return ok({"meeting": saved})

    def delete_meeting(self, meeting_id: str):
        deleted = self.repo.delete_meeting(meeting_id)
        if not deleted:
            return fail("meeting not found")
        return ok({"meeting": deleted})

    def add_session(self, meeting_id: str, session: dict, meeting_updated_at: str):
        items = self.repo.list_meetings()
        meeting = next((m for m in items if m.get("id") == meeting_id), None)
        if not meeting:
            return fail("meeting not found")
        next_meeting = copy.deepcopy(meeting)
        next_meeting.setdefault("sessions", []).append(session)
        next_meeting["updated_at"] = meeting_updated_at
        saved = self.repo.replace_meeting(meeting_id, next_meeting)
        if not saved:
            return fail("meeting not found")
        return ok({"meeting": saved, "session": session})

    def update_session(self, meeting_id: str, session_id: str, patch: dict, meeting_updated_at: str):
        items = self.repo.list_meetings()
        meeting = next((m for m in items if m.get("id") == meeting_id), None)
        if not meeting:
            return fail("meeting not found")
        next_meeting = copy.deepcopy(meeting)
        sessions = next_meeting.get("sessions") or []
        for idx, session in enumerate(sessions):
            if session.get("id") != session_id:
                continue
            next_session = {**session, **patch}
            sessions[idx] = next_session
            next_meeting["sessions"] = sessions
            next_meeting["updated_at"] = meeting_updated_at
            saved = self.repo.replace_meeting(meeting_id, next_meeting)
            if not saved:
                return fail("meeting not found")
            return ok({"meeting": saved, "session": next_session})
        return fail("session not found")

    def delete_session(self, meeting_id: str, session_id: str, meeting_updated_at: str):
        items = self.repo.list_meetings()
        meeting = next((m for m in items if m.get("id") == meeting_id), None)
        if not meeting:
            return fail("meeting not found")
        next_meeting = copy.deepcopy(meeting)
        sessions = next_meeting.get("sessions") or []
        next_sessions = [s for s in sessions if s.get("id") != session_id]
        if len(next_sessions) == len(sessions):
            return fail("session not found")
        next_meeting["sessions"] = next_sessions
        next_meeting["updated_at"] = meeting_updated_at
        saved = self.repo.replace_meeting(meeting_id, next_meeting)
        if not saved:
            return fail("meeting not found")
        return ok({"meeting": saved})

    def add_agenda(self, meeting_id: str, session_id: str, agenda: dict, meeting_updated_at: str):
        items = self.repo.list_meetings()
        meeting = next((m for m in items if m.get("id") == meeting_id), None)
        if not meeting:
            return fail("meeting not found")
        next_meeting = copy.deepcopy(meeting)
        for sidx, session in enumerate(next_meeting.get("sessions") or []):
            if session.get("id") != session_id:
                continue
            next_session = copy.deepcopy(session)
            next_session.setdefault("agendas", []).append(agenda)
            next_session["updated_at"] = meeting_updated_at
            next_meeting["sessions"][sidx] = next_session
            next_meeting["updated_at"] = meeting_updated_at
            saved = self.repo.replace_meeting(meeting_id, next_meeting)
            if not saved:
                return fail("meeting not found")
            return ok({"meeting": saved, "session": next_session, "agenda": agenda})
        return fail("session not found")

    def update_agenda(self, meeting_id: str, session_id: str, agenda_id: str, patch: dict, meeting_updated_at: str):
        items = self.repo.list_meetings()
        meeting = next((m for m in items if m.get("id") == meeting_id), None)
        if not meeting:
            return fail("meeting not found")
        next_meeting = copy.deepcopy(meeting)
        for sidx, session in enumerate(next_meeting.get("sessions") or []):
            if session.get("id") != session_id:
                continue
            agendas = session.get("agendas") or []
            for aidx, agenda in enumerate(agendas):
                if agenda.get("id") != agenda_id:
                    continue
                next_session = copy.deepcopy(session)
                next_agenda = {**agenda, **patch}
                next_session["agendas"][aidx] = next_agenda
                next_session["updated_at"] = meeting_updated_at
                next_meeting["sessions"][sidx] = next_session
                next_meeting["updated_at"] = meeting_updated_at
                saved = self.repo.replace_meeting(meeting_id, next_meeting)
                if not saved:
                    return fail("meeting not found")
                return ok({"meeting": saved, "session": next_session, "agenda": next_agenda})
            return fail("agenda not found")
        return fail("session not found")

    def delete_agenda(self, meeting_id: str, session_id: str, agenda_id: str, meeting_updated_at: str):
        items = self.repo.list_meetings()
        meeting = next((m for m in items if m.get("id") == meeting_id), None)
        if not meeting:
            return fail("meeting not found")
        next_meeting = copy.deepcopy(meeting)
        for sidx, session in enumerate(next_meeting.get("sessions") or []):
            if session.get("id") != session_id:
                continue
            agendas = session.get("agendas") or []
            next_agendas = [a for a in agendas if a.get("id") != agenda_id]
            if len(next_agendas) == len(agendas):
                return fail("agenda not found")
            next_session = copy.deepcopy(session)
            next_session["agendas"] = next_agendas
            next_session["updated_at"] = meeting_updated_at
            next_meeting["sessions"][sidx] = next_session
            next_meeting["updated_at"] = meeting_updated_at
            saved = self.repo.replace_meeting(meeting_id, next_meeting)
            if not saved:
                return fail("meeting not found")
            return ok({"meeting": saved, "session": next_session})
        return fail("session not found")
