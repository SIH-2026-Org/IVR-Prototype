"""Speech provider contracts and the legacy Twilio Gather transcript adapter."""
from dataclasses import dataclass
from typing import Protocol
from config import SPEECH_PROVIDER


class AudioSpeechProvider(Protocol):
    async def transcribe_audio(self, audio: bytes, language_hint: str): ...


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: str | None
    provider: str = "twilio"
    stt_ms: float | None = None


def read_gather_transcript(form) -> Transcript:
    if SPEECH_PROVIDER != "twilio":
        print("SPEECH FALLBACK: using Twilio Gather STT for this retry.")
    return Transcript(str(form.get("SpeechResult", "")).strip(), form.get("Confidence"))
