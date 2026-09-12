from dataclasses import dataclass, field
import time
from typing import Callable

from config import SESSION_TTL_SECONDS


@dataclass
class IVRSession:
    call_sid: str
    language: str
    language_code: str
    profile: dict = field(default_factory=dict)
    current_field: str | None = None
    current_input: str = "speech"
    turn_count: int = 0
    callbacks_used: int = 0
    current_fields: list[str] = field(default_factory=list)
    confirmed_fields: list[str] = field(default_factory=list)
    retry_count: int = 0
    last_missing_fields: list[str] = field(default_factory=list)
    conversation: list[dict] = field(default_factory=list)
    pending_confirmation: dict | None = None
    location_speech_fallback: bool = False
    pincode: str | None = None
    updated_at: float = field(default_factory=time.time)


class InMemorySessionStore:
    def __init__(
        self,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ):
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._sessions: dict[str, IVRSession] = {}

    def cleanup_expired(self) -> int:
        now = self.clock()
        expired = [
            call_sid
            for call_sid, session in self._sessions.items()
            if now - session.updated_at >= self.ttl_seconds
        ]
        for call_sid in expired:
            del self._sessions[call_sid]
        return len(expired)

    def get(self, call_sid: str) -> IVRSession | None:
        self.cleanup_expired()
        session = self._sessions.get(call_sid)
        if session:
            session.updated_at = self.clock()
        return session

    def get_or_create(self, call_sid: str, language: str, language_code: str) -> IVRSession:
        session = self.get(call_sid)
        if session is None:
            session = IVRSession(
                call_sid=call_sid,
                language=language,
                language_code=language_code,
                updated_at=self.clock(),
            )
            self._sessions[call_sid] = session
        return session

    def save(self, session: IVRSession) -> None:
        session.updated_at = self.clock()
        self._sessions[session.call_sid] = session

    def delete(self, call_sid: str) -> None:
        self._sessions.pop(call_sid, None)

    def __len__(self) -> int:
        self.cleanup_expired()
        return len(self._sessions)


session_store = InMemorySessionStore()
