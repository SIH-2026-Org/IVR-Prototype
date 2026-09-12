import asyncio
from dre_client import match_text


async def main():
    result = await match_text(
        text=(
            "I am a 28 year old woman from SC category living in Rajasthan. "
            "I want to start a new dairy business. "
            "My annual family income is one lakh twenty thousand rupees. "
            "The total project cost is two lakh rupees and "
            "I need a loan of one lakh twenty thousand rupees."
        ),
        language="en-IN",
        session_id="python_ivr_test_001"
    )

    print("SUCCESS:", result.get("success"))
    print("STATUS:", result.get("status"))
    print("MISSING:", result.get("missing_fields"))
    print("PROFILE:", result.get("profile_summary"))
    print("SUMMARY:", result.get("summary_text"))


if __name__ == "__main__":
    asyncio.run(main())
