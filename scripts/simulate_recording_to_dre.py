#!/usr/bin/env python3
"""Exercise actual temp-file/ffmpeg/DRE code with a mocked Bhashini transcript."""
import asyncio
from io import BytesIO
from pathlib import Path
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audio_pipeline import FfmpegAudioConverter, RecordingTranscriber
from bhashini_provider import ASRResult
from dre_client import match_text


TRANSCRIPT = (
    "मैं राजस्थान से हूं और डेयरी का काम शुरू करना चाहता हूँ "
    "मेरी सालाना पारिवारिक आय एक लाख बीस हज़ार रुपये है"
)


def source_audio():
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(1)
        stream.setframerate(8000)
        stream.writeframes(b"\x80" * 16000)
    return output.getvalue()


class LocalRecording:
    async def download(self, _url): return source_audio()


class MockedSuccessfulBhashini:
    async def transcribe_audio(self, _audio, language):
        return ASRResult("bhashini", TRANSCRIPT, language, 1)


async def main():
    pipeline = RecordingTranscriber(LocalRecording(), FfmpegAudioConverter(), MockedSuccessfulBhashini())
    processed = await pipeline.transcribe("https://api.twilio.com/local-test", "hi", "2")
    result = await match_text(processed.asr.transcript, "hi-IN", "LOCAL_RECORDING_DRE")
    print("BHASHINI API AUTH: MOCKED")
    print("AUDIO CONVERSION: PASS")
    print("TRANSCRIPT:", processed.asr.transcript)
    print("DRE STATUS:", result.get("status"))
    print("NORMALIZED PROFILE:", result.get("normalized_profile"))
    print("MISSING FIELDS:", result.get("missing_fields"))


if __name__ == "__main__":
    asyncio.run(main())
