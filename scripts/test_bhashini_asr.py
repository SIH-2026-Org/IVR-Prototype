#!/usr/bin/env python3
import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from bhashini_provider import BhashiniASRProvider, BhashiniError


async def run(path: Path, language: str) -> int:
    required = {
        "BHASHINI_UDYAT_KEY": config.BHASHINI_UDYAT_KEY,
        "BHASHINI_INFERENCE_KEY": config.BHASHINI_INFERENCE_KEY,
        "BHASHINI_ASR_SERVICE_ID": (
            getattr(config, f"BHASHINI_ASR_SERVICE_ID_{language.upper()}", "")
            or config.BHASHINI_ASR_SERVICE_ID
        ),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        print("BHASHINI API AUTH: NOT TESTED")
        print("ASR: NOT TESTED")
        print("MISSING:", ", ".join(missing))
        return 2
    if not path.is_file():
        print("BHASHINI API AUTH: NOT TESTED")
        print("ASR: FAIL")
        print("ERROR: audio file not found")
        return 2
    try:
        result = await BhashiniASRProvider().transcribe_audio(path.read_bytes(), language)
    except BhashiniError as error:
        print("BHASHINI API AUTH: FAIL")
        print("ASR: FAIL")
        print("ERROR:", str(error))
        return 1
    print("BHASHINI API AUTH: PASS")
    print("ASR: PASS")
    print("TRANSCRIPT:", result.transcript)
    print("LATENCY:", result.latency_ms, "ms")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Bhashini ASR on a PCM16 mono 16 kHz WAV file.")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--language", choices=("hi", "en"), default="hi")
    args = parser.parse_args()
    return asyncio.run(run(args.audio, args.language))


if __name__ == "__main__":
    raise SystemExit(main())
