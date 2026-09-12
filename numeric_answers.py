from __future__ import annotations

import re


NUMERIC_FIELDS = {
    "age",
    "income_annual",
    "project_cost",
    "loan_required",
    "existing_loans",
    "business_age_years",
    "employees_count",
}

NUMBER_VALUES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5,
    "panch": 5, "chhe": 6, "che": 6, "saat": 7, "aath": 8, "nau": 9,
    "das": 10, "bees": 20, "tees": 30, "chaalis": 40, "chalis": 40,
    "pachaas": 50, "pachas": 50, "saath": 60, "sattar": 70, "assi": 80,
    "nabbe": 90, "dedh": 1.5, "dhai": 2.5,
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5,
    "पाँच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    "बीस": 20, "तीस": 30, "चालीस": 40, "पचास": 50, "साठ": 60,
    "सत्तर": 70, "अस्सी": 80, "नब्बे": 90, "डेढ़": 1.5, "ढाई": 2.5,
}
HUNDREDS = {"hundred", "sau", "सौ"}
SCALES = {
    "thousand": 1_000, "hazaar": 1_000, "hazar": 1_000,
    "हजार": 1_000, "हज़ार": 1_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
    "लाख": 100_000,
    "crore": 10_000_000, "crores": 10_000_000, "करोड़": 10_000_000,
}
FILLERS = {"and", "aur", "और"}


def _valid_for_field(field: str, value: float) -> bool:
    if value < 0 or value > 9_007_199_254_740_991:
        return False
    if field in {"age", "business_age_years", "employees_count"} and not float(value).is_integer():
        return False
    if field in {"age", "business_age_years"} and value > 120:
        return False
    return True


def _parse_tokens(tokens: list[str]) -> float | None:
    total = 0.0
    current = 0.0
    saw_number = False
    prior_scale = float("inf")

    for token in tokens:
        if token in FILLERS:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            current += float(token)
            saw_number = True
        elif token in NUMBER_VALUES:
            current += NUMBER_VALUES[token]
            saw_number = True
        elif token in HUNDREDS:
            current = (current or 1) * 100
            saw_number = True
        elif token in SCALES:
            scale = SCALES[token]
            if scale >= prior_scale:
                return None
            total += (current or 1) * scale
            current = 0
            prior_scale = scale
            saw_number = True

    return total + current if saw_number else None


def parse_numeric_answer(field: str, text: str) -> int | float | None:
    """Return one unambiguous field-aware number, otherwise None."""
    if field not in NUMERIC_FIELDS or not isinstance(text, str) or not text.strip():
        return None

    if re.search(r"(?:^|\s)-(?:\s*)\d|\b(?:minus|negative)\b|माइनस|ऋणात्मक", text, re.I):
        return None
    normalized = re.sub(r"(?<=\d),(?=\d)", "", text.lower().replace("-", " "))
    digit_tokens = re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", normalized)
    if len(digit_tokens) > 1:
        return None
    if re.search(r"\b(?:or)\b|या", normalized, re.I):
        return None

    # Whitespace tokenization preserves Devanagari combining marks, which Python's
    # conventional ``\w`` character class otherwise splits apart.
    words = re.findall(r"\d+(?:\.\d+)?|[^\s,.;:!?।₹]+", normalized, re.UNICODE)
    number_tokens = [
        token for token in words
        if re.fullmatch(r"\d+(?:\.\d+)?", token)
        or token in NUMBER_VALUES or token in HUNDREDS or token in SCALES or token in FILLERS
    ]
    # A conjunction is meaningful only inside an otherwise numeric expression.
    number_tokens = [token for token in number_tokens if token not in FILLERS or len(number_tokens) > 1]
    value = _parse_tokens(number_tokens)
    if value is None or not _valid_for_field(field, value):
        return None
    return int(value) if float(value).is_integer() else value


def parse_numeric_dtmf(field: str, digits: str) -> int | None:
    if field not in NUMERIC_FIELDS:
        return None
    value_text = str(digits or "").strip()
    if value_text.endswith("#"):
        value_text = value_text[:-1]
    if not value_text or not value_text.isascii() or not value_text.isdigit():
        return None
    value = int(value_text)
    return value if _valid_for_field(field, value) else None
