import os

from dotenv import load_dotenv

load_dotenv()


def _bool_env(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "http://localhost:8000",
).rstrip("/")
DRE_URL = os.getenv("DRE_URL", "http://localhost:3000/api/v1/match")
PINCODE_API_URL = os.getenv(
    "PINCODE_API_URL",
    "https://api.postalpincode.in/pincode/{pincode}",
)
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "1200"))
MAX_CONVERSATION_TURNS = int(os.getenv("MAX_CONVERSATION_TURNS", "15"))
MAX_FIELD_RETRIES = int(os.getenv("MAX_FIELD_RETRIES", "2"))
LOW_STT_CONFIDENCE = float(os.getenv("LOW_STT_CONFIDENCE", "0.35"))
IVR_TARGET_MAX_CALLBACKS = int(os.getenv("IVR_TARGET_MAX_CALLBACKS", "9"))
SPEECH_PROVIDER = os.getenv("SPEECH_PROVIDER", "twilio")
IVR_DEBUG = _bool_env("IVR_DEBUG")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
BHASHINI_ENABLED = _bool_env("BHASHINI_ENABLED")
BHASHINI_UDYAT_KEY = os.getenv("BHASHINI_UDYAT_KEY", "")
BHASHINI_UDYAT_KEY_NAME = os.getenv("BHASHINI_UDYAT_KEY_NAME", "userID")
BHASHINI_INFERENCE_KEY = os.getenv("BHASHINI_INFERENCE_KEY", "")
BHASHINI_INFERENCE_KEY_NAME = os.getenv("BHASHINI_INFERENCE_KEY_NAME", "Authorization")
BHASHINI_INFERENCE_URL = os.getenv(
    "BHASHINI_INFERENCE_URL",
    "https://dhruva-api.bhashini.gov.in/services/inference/pipeline",
)
BHASHINI_ASR_SERVICE_ID = os.getenv("BHASHINI_ASR_SERVICE_ID", "")
BHASHINI_ASR_SERVICE_ID_HI = os.getenv("BHASHINI_ASR_SERVICE_ID_HI", "")
BHASHINI_ASR_SERVICE_ID_EN = os.getenv("BHASHINI_ASR_SERVICE_ID_EN", "")
BHASHINI_REQUEST_TIMEOUT_SECONDS = float(os.getenv("BHASHINI_REQUEST_TIMEOUT_SECONDS", "30"))
TWILIO_RECORD_MAX_SECONDS = int(os.getenv("TWILIO_RECORD_MAX_SECONDS", "15"))
TWILIO_RECORD_SILENCE_TIMEOUT = int(os.getenv("TWILIO_RECORD_SILENCE_TIMEOUT", "3"))
TWILIO_RECORD_FINISH_KEY = os.getenv("TWILIO_RECORD_FINISH_KEY", "#")
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
