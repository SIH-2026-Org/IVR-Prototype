import base64
from dataclasses import dataclass
import time
import wave
from io import BytesIO

import httpx

import config


LANGUAGE_CONFIG = {
    "en": {"source_language": "en", "service_id_setting": "BHASHINI_ASR_SERVICE_ID_EN"},
    "en-IN": {"source_language": "en", "service_id_setting": "BHASHINI_ASR_SERVICE_ID_EN"},
    "hi": {"source_language": "hi", "service_id_setting": "BHASHINI_ASR_SERVICE_ID_HI"},
    "hi-IN": {"source_language": "hi", "service_id_setting": "BHASHINI_ASR_SERVICE_ID_HI"},
}


class BhashiniError(RuntimeError):
    pass


@dataclass(frozen=True)
class ASRResult:
    provider: str
    transcript: str
    language: str
    latency_ms: int


def validate_pcm_wav(audio: bytes) -> None:
    try:
        with wave.open(BytesIO(audio), "rb") as stream:
            valid = (
                stream.getnchannels() == 1
                and stream.getframerate() == 16000
                and stream.getsampwidth() == 2
                and stream.getcomptype() == "NONE"
                and stream.getnframes() > 0
            )
    except (EOFError, wave.Error) as error:
        raise BhashiniError("invalid_wav") from error
    if not valid:
        raise BhashiniError("wav_must_be_pcm16_mono_16000")


class BhashiniASRProvider:
    def __init__(self, client=None):
        self.client = client

    async def transcribe_audio(self, audio: bytes, language_hint: str) -> ASRResult:
        if not config.BHASHINI_ENABLED:
            raise BhashiniError("provider_disabled")
        language = LANGUAGE_CONFIG.get(language_hint)
        if not language:
            raise BhashiniError("unsupported_language")
        service_id = getattr(config, language["service_id_setting"], "") or config.BHASHINI_ASR_SERVICE_ID
        if not config.BHASHINI_UDYAT_KEY or not config.BHASHINI_INFERENCE_KEY or not service_id:
            raise BhashiniError("provider_not_configured")
        validate_pcm_wav(audio)

        payload = {
            "pipelineTasks": [{
                "taskType": "asr",
                "config": {
                    "language": {"sourceLanguage": language["source_language"]},
                    "serviceId": service_id,
                    "audioFormat": "wav",
                    "samplingRate": 16000,
                },
            }],
            "inputData": {"audio": [{"audioContent": base64.b64encode(audio).decode("ascii")}]},
        }
        headers = {
            config.BHASHINI_UDYAT_KEY_NAME: config.BHASHINI_UDYAT_KEY,
            config.BHASHINI_INFERENCE_KEY_NAME: config.BHASHINI_INFERENCE_KEY,
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        try:
            if self.client is not None:
                response = await self.client.post(config.BHASHINI_INFERENCE_URL, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=config.BHASHINI_REQUEST_TIMEOUT_SECONDS) as client:
                    response = await client.post(config.BHASHINI_INFERENCE_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise BhashiniError("inference_request_failed") from error

        try:
            transcript = data["pipelineResponse"][0]["output"][0]["source"].strip()
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise BhashiniError("malformed_inference_response") from error
        if not transcript:
            raise BhashiniError("empty_transcript")
        return ASRResult(
            provider="bhashini",
            transcript=transcript,
            language=language["source_language"],
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
