import httpx

from config import DRE_URL


async def _match(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(DRE_URL, json=payload)
        response.raise_for_status()
        result = response.json()

    if not isinstance(result, dict):
        raise ValueError("DRE response must be a JSON object")
    return result


async def match_text(
    text: str,
    language: str = "en-IN",
    session_id: str = "ivr_test",
    profile: dict | None = None,
    top_n: int = 3,
    original_text: str | None = None,
    current_fields: list[str] | None = None,
    confirmed_fields: list[str] | None = None,
):
    payload = {
        "text": text,
        "channel": "ivr",
        "language_code": language,
        "session_id": session_id,
        "topN": top_n,
        "original_text": original_text if original_text is not None else text,
        "current_fields": current_fields or [],
        "confirmed_fields": confirmed_fields or [],
    }
    if profile:
        payload["profile"] = profile

    return await _match(payload)


async def match_profile(
    profile: dict,
    language: str = "en-IN",
    session_id: str = "ivr_test",
    top_n: int = 3,
):
    return await _match({
        "profile": profile,
        "channel": "ivr",
        "language_code": language,
        "session_id": session_id,
        "topN": top_n,
    })
