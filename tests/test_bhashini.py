import asyncio
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import wave

import config
from audio_pipeline import (
    AudioPipelineError,
    FfmpegAudioConverter,
    RecordingTranscriber,
    TwilioRecordingDownloader,
    detect_audio_format,
)
from bhashini_provider import ASRResult, BhashiniASRProvider, BhashiniError, validate_pcm_wav


def wav_bytes(rate=16000, channels=1, width=2, frames=320):
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(rate)
        stream.writeframes(b"\x00" * frames * channels * width)
    return output.getvalue()


class FakeResponse:
    def __init__(self, *, status=200, content=b"audio", data=None):
        self.status_code = status
        self.content = content
        self._data = data

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            request = httpx.Request("GET", "https://api.twilio.com/recording.wav")
            raise httpx.HTTPStatusError("request failed", request=request, response=httpx.Response(self.status_code, request=request))

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class BhashiniProviderTests(unittest.IsolatedAsyncioTestCase):
    async def _transcribe(self, language, transcript):
        response = FakeResponse(data={"pipelineResponse": [{"output": [{"source": transcript}]}]})
        client = FakeClient([response])
        with (
            patch.object(config, "BHASHINI_ENABLED", True),
            patch.object(config, "BHASHINI_UDYAT_KEY", "udyat-secret"),
            patch.object(config, "BHASHINI_INFERENCE_KEY", "inference-secret"),
            patch.object(config, "BHASHINI_ASR_SERVICE_ID", "public-service-id"),
        ):
            result = await BhashiniASRProvider(client).transcribe_audio(wav_bytes(), language)
        return result, client.calls[0]

    async def test_hindi_asr_uses_proven_request_shape(self):
        result, (_, request) = await self._transcribe("hi", "मैं डेयरी का काम शुरू करना चाहता हूँ")
        task = request["json"]["pipelineTasks"][0]
        self.assertEqual(task["taskType"], "asr")
        self.assertEqual(task["config"]["language"]["sourceLanguage"], "hi")
        self.assertEqual(task["config"]["audioFormat"], "wav")
        self.assertEqual(task["config"]["samplingRate"], 16000)
        self.assertEqual(result.provider, "bhashini")
        self.assertIn("डेयरी", result.transcript)

    async def test_english_asr_uses_en_language(self):
        result, (_, request) = await self._transcribe("en-IN", "I want to start a dairy business")
        self.assertEqual(request["json"]["pipelineTasks"][0]["config"]["language"]["sourceLanguage"], "en")
        self.assertEqual(result.language, "en")

    async def test_empty_or_malformed_response_is_rejected(self):
        client = FakeClient([FakeResponse(data={"pipelineResponse": [{"output": [{"source": ""}]}]})])
        with (
            patch.object(config, "BHASHINI_ENABLED", True),
            patch.object(config, "BHASHINI_UDYAT_KEY", "set"),
            patch.object(config, "BHASHINI_INFERENCE_KEY", "set"),
            patch.object(config, "BHASHINI_ASR_SERVICE_ID", "set"),
        ):
            with self.assertRaisesRegex(BhashiniError, "empty_transcript"):
                await BhashiniASRProvider(client).transcribe_audio(wav_bytes(), "hi")

    async def test_timeout_is_sanitized(self):
        client = AsyncMock()
        import httpx
        client.post.side_effect = httpx.ReadTimeout("timeout")
        with (
            patch.object(config, "BHASHINI_ENABLED", True),
            patch.object(config, "BHASHINI_UDYAT_KEY", "set"),
            patch.object(config, "BHASHINI_INFERENCE_KEY", "set"),
            patch.object(config, "BHASHINI_ASR_SERVICE_ID", "set"),
        ):
            with self.assertRaisesRegex(BhashiniError, "inference_request_failed"):
                await BhashiniASRProvider(client).transcribe_audio(wav_bytes(), "hi")

    async def test_debuggable_result_never_contains_auth_secrets(self):
        result, _ = await self._transcribe("hi", "नमस्ते")
        rendered = repr(result)
        self.assertNotIn("udyat-secret", rendered)
        self.assertNotIn("inference-secret", rendered)


class AudioPipelineTests(unittest.IsolatedAsyncioTestCase):
    def test_source_audio_format_detection(self):
        self.assertEqual(detect_audio_format(wav_bytes()), "wav")
        self.assertEqual(detect_audio_format(b"ID3payload"), "mp3")
        self.assertEqual(detect_audio_format(b"unrecognized"), "unknown")

    async def test_twilio_recording_download_uses_basic_auth_and_wav_url(self):
        client = FakeClient([FakeResponse(content=b"recording")])
        downloader = TwilioRecordingDownloader(client=client)
        with (
            patch.object(config, "TWILIO_ACCOUNT_SID", "AC-test"),
            patch.object(config, "TWILIO_AUTH_TOKEN", "auth-secret"),
        ):
            content = await downloader.download("https://api.twilio.com/2010/Recordings/RE123")
        self.assertEqual(content, b"recording")
        self.assertTrue(client.calls[0][0].endswith("RE123.wav"))
        self.assertEqual(client.calls[0][1]["auth"], ("AC-test", "auth-secret"))

    async def test_download_retries_briefly_when_recording_is_not_ready(self):
        client = FakeClient([FakeResponse(status=404), FakeResponse(content=b"ready")])
        sleeps = []
        downloader = TwilioRecordingDownloader(client=client, sleep=lambda seconds: asyncio.sleep(0, result=sleeps.append(seconds)))
        self.assertEqual(await downloader.download("https://api.twilio.com/recording"), b"ready")
        self.assertEqual(len(client.calls), 2)

    async def test_download_rejects_non_twilio_url_and_empty_content(self):
        with self.assertRaisesRegex(AudioPipelineError, "invalid_recording_url"):
            await TwilioRecordingDownloader(client=FakeClient([])).download("https://example.com/audio")
        with self.assertRaisesRegex(AudioPipelineError, "empty_recording"):
            await TwilioRecordingDownloader(client=FakeClient([FakeResponse(content=b"")])).download("https://api.twilio.com/audio")

    def test_real_ffmpeg_conversion_produces_pcm16_mono_16000_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            destination = Path(directory) / "converted.wav"
            source.write_bytes(wav_bytes(rate=8000, channels=2, width=1))
            FfmpegAudioConverter().convert(source, destination)
            validate_pcm_wav(destination.read_bytes())

    def test_ffmpeg_failure_is_reported(self):
        class Result:
            returncode = 1
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AudioPipelineError, "ffmpeg_conversion_failed"):
                FfmpegAudioConverter(runner=lambda *args, **kwargs: Result()).convert(
                    Path(directory) / "source", Path(directory) / "output.wav"
                )

    async def test_temporary_audio_files_are_removed_on_success(self):
        observed_paths = []

        class Downloader:
            async def download(self, _url): return b"source"
        class Converter:
            def convert(self, source, destination):
                observed_paths.extend([source, destination])
                destination.write_bytes(wav_bytes())
        class Provider:
            async def transcribe_audio(self, _audio, language):
                return ASRResult("bhashini", "transcript", language, 1)

        result = await RecordingTranscriber(Downloader(), Converter(), Provider()).transcribe(
            "https://api.twilio.com/audio", "hi", "4"
        )
        self.assertEqual(result.asr.transcript, "transcript")
        self.assertTrue(all(not path.exists() for path in observed_paths))

    async def test_temporary_audio_files_are_removed_on_provider_failure(self):
        observed_paths = []
        class Downloader:
            async def download(self, _url): return b"source"
        class Converter:
            def convert(self, source, destination):
                observed_paths.extend([source, destination]); destination.write_bytes(wav_bytes())
        class Provider:
            async def transcribe_audio(self, _audio, _language): raise BhashiniError("failed")
        with self.assertRaises(BhashiniError):
            await RecordingTranscriber(Downloader(), Converter(), Provider()).transcribe("https://api.twilio.com/audio", "hi")
        self.assertTrue(all(not path.exists() for path in observed_paths))
