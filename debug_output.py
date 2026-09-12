def eligible_items(result):
    return [item for item in result.get("eligible_schemes", [])
            if item.get("eligibility", {}).get("status") == "ELIGIBLE"][:3]


def format_final_match(call_sid, result):
    lines = ["=" * 60, "FINAL SCHEME MATCH", "=" * 60,
             f"CALL SID: {call_sid}", f"STATUS: {result.get('status')}", "NORMALIZED PROFILE:"]
    profile = result.get("normalized_profile", {})
    for key in ("age", "gender", "social_category", "state", "activity", "income_annual",
                "project_cost", "existing_business"):
        lines.append(f"{key}: {profile.get(key)}")
    eligible = eligible_items(result)
    lines.append(f"ELIGIBLE RANKED SCHEMES RETURNED: {len(eligible)}")
    for index, item in enumerate(eligible, 1):
        lines.extend([f"{index}. {item['scheme'].get('name', '')}", "   Eligibility: ELIGIBLE",
                      f"   Score: {item.get('score', {}).get('total_score')}"])
        simulation = item.get("simulation") or {}
        if simulation.get("available") and simulation.get("emi_monthly") is not None:
            lines.append(f"   Estimated EMI: {simulation['emi_monthly']}")
    lines.append("TOP RECOMMENDATION: " + (eligible[0]["scheme"].get("name", "") if eligible else "None"))
    lines.append("=" * 60)
    return "\n".join(lines)
