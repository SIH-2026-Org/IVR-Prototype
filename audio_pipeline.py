import asyncio
from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlparse, urlunparse

import httpx

import config
from bhashini_provider import ASRResult, BhashiniASRProvider


class AudioPipelineError(RuntimeError):
    pass


def detect_audio_format(audio: bytes) -> str:
    if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
        return "wav"
    if audio.startswith(b"ID3") or audio[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "mp3"
    if audio.startswith(b"OggS"):
        return "ogg"
    return "unknown"


class TwilioRecordingDownloader:
    def __init__(self, client=None, sleep=asyncio.sleep):
        self.client = client
        self.sleep = sleep

    @staticmethod
    def _media_url(recording_url: str) -> str:
        parsed = urlparse(recording_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (host == "twilio.com" or host.endswith(".twilio.com")):
            raise AudioPipelineError("invalid_recording_url")
        if parsed.path.lower().endswith((".wav", ".mp3")):
            return recording_url
        return urlunparse(parsed._replace(path=parsed.path + ".wav"))

    async def download(self, recording_url: str) -> bytes:
        url = self._media_url(recording_url)
        auth = None
        if config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN:
            auth = (config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=10.0)
        try:
            for attempt in range(3):
                try:
                    response = await client.get(url, auth=auth)
                    if response.status_code == 404 and attempt < 2:
                        await self.sleep(0.25 * (attempt + 1))
                        continue
                    response.raise_for_status()
                    if not response.content:
                        raise AudioPipelineError("empty_recording")
                    if len(response.content) > 10 * 1024 * 1024:
                        raise AudioPipelineError("recording_too_large")
                    return response.content
                except httpx.HTTPError as error:
                    if attempt == 2:
                        raise AudioPipelineError("recording_download_failed") from error
                    await self.sleep(0.25 * (attempt + 1))
        finally:
            if owns_client:
                await client.aclose()
        raise AudioPipelineError("recording_download_failed")


class FfmpegAudioConverter:
    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def convert(self, source: Path, destination: Path) -> None:
        command = [
            config.FFMPEG_BIN, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", str(destination),
        ]
        try:
            result = self.runner(command, capture_output=True, timeout=20, check=False)
        except (FileNotFoundError, subprocess.SubprocessError) as error:
            raise AudioPipelineError("ffmpeg_unavailable_or_failed") from error
        if result.returncode != 0 or not destination.exists() or destination.stat().st_size <= 44:
            raise AudioPipelineError("ffmpeg_conversion_failed")


@dataclass(frozen=True)
class ProcessedRecording:
    asr: ASRResult
    recording_duration: str | None
    source_format: str
    converted_format: str = "wav/pcm16/16000/mono"


class RecordingTranscriber:
    def __init__(self, downloader=None, converter=None, provider=None):
        self.downloader = downloader or TwilioRecordingDownloader()
        self.converter = converter or FfmpegAudioConverter()
        self.provider = provider or BhashiniASRProvider()

    async def transcribe(self, recording_url: str, language: str, recording_duration=None) -> ProcessedRecording:
        audio = await self.downloader.download(recording_url)
        with tempfile.TemporaryDirectory(prefix="saarthi_audio_") as directory:
            source = Path(directory) / "source_audio"
            converted = Path(directory) / "converted.wav"
            source.write_bytes(audio)
            self.converter.convert(source, converted)
            wav_audio = converted.read_bytes()
            asr = await self.provider.transcribe_audio(wav_audio, language)
            return ProcessedRecording(
                asr,
                str(recording_duration) if recording_duration else None,
                detect_audio_format(audio),
            )


recording_transcriber = RecordingTranscriber()
