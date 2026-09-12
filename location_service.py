import re

import httpx

from config import PINCODE_API_URL


class PincodeResolver:
    def __init__(self, provider_url: str = PINCODE_API_URL, timeout: float = 5.0):
        self.provider_url = provider_url
        self.timeout = timeout

    async def resolve(self, pincode: str) -> dict | None:
        if not re.fullmatch(r"\d{6}", pincode or ""):
            return None
        if not self.provider_url:
            return None

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.provider_url.format(pincode=pincode))
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, KeyError):
            return None

        try:
            post_offices = payload[0].get("PostOffice") or []
            first = post_offices[0]
            state = first.get("State")
            district = first.get("District")
        except (IndexError, AttributeError, TypeError):
            return None

        if not state:
            return None
        return {"pincode": pincode, "state": state, "district": district}


location_resolver = PincodeResolver()
