from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    field: str | None
    prompt: str
    input_type: str = "speech"
    num_digits: int | None = None


FIELD_PRIORITY = [
    "activity",
    "age",
    "state",
    "social_category",
    "gender",
    "existing_business",
    "income_annual",
    "project_cost",
    "loan_required",
    "area_type",
]

INITIAL_PROMPTS = {
    "en": (
        "Please tell me what business you want support for and any details you know, "
        "such as your age, location, income, project cost, and loan requirement."
    ),
    "hi": (
        "कृपया बताइए कि आप किस व्यवसाय के लिए सहायता चाहते हैं और जो जानकारी आपको पता है, "
        "जैसे आयु, स्थान, आय, परियोजना लागत और ऋण की राशि।"
    ),
}

SPEECH_PROMPTS = {
    "activity": {
        "en": "What business or activity do you want financial support for?",
        "hi": "आप किस व्यवसाय या काम के लिए आर्थिक सहायता चाहते हैं?",
    },
    "project_cost": {
        "en": "What is the approximate total cost of your project?",
        "hi": "आपकी परियोजना की अनुमानित कुल लागत कितनी है?",
    },
    "income_annual": {
        "en": "What is your annual family income?",
        "hi": "आपके परिवार की वार्षिक आय कितनी है?",
    },
    "age": {
        "en": "What is your age?",
        "hi": "आपकी आयु कितनी है?",
    },
    "social_category": {
        "en": "Please tell me your social category, for example SC, ST, OBC, EWS, minority, or general.",
        "hi": "कृपया अपनी सामाजिक श्रेणी बताइए, जैसे एस सी, एस टी, ओ बी सी, ई डब्ल्यू एस, अल्पसंख्यक या सामान्य।",
    },
    "state": {
        "en": "Please tell me which state you live in.",
        "hi": "कृपया बताइए कि आप किस राज्य में रहते हैं।",
    },
    "loan_required": {
        "en": "Approximately how much loan or financial assistance do you need?",
        "hi": "आपको लगभग कितने ऋण या आर्थिक सहायता की आवश्यकता है?",
    },
}

DTMF_PROMPTS = {
    "social_category": {
        "en": "For your social category, press 1 for S C, 2 for S T, 3 for O B C, 4 for E W S, 5 for general, or 6 for minority.",
        "hi": "सामाजिक वर्ग के लिए एस सी हेतु 1, एस टी हेतु 2, ओ बी सी हेतु 3, ई डब्ल्यू एस हेतु 4, सामान्य हेतु 5, या अल्पसंख्यक हेतु 6 दबाएँ।",
    },
    "gender": {
        "en": "Please tell me your gender. Press 1 for female, 2 for male, or 3 for other.",
        "hi": "कृपया अपना लिंग बताइए। महिला के लिए 1, पुरुष के लिए 2, या अन्य के लिए 3 दबाएँ।",
    },
    "existing_business": {
        "en": "Press 1 if you already run this business, or press 2 if this is a new business.",
        "hi": "यदि आप यह व्यवसाय पहले से चलाते हैं तो 1 दबाएँ, या नया व्यवसाय है तो 2 दबाएँ।",
    },
    "area_type": {
        "en": "Press 1 if you live in a rural area, or press 2 for an urban area.",
        "hi": "यदि आप ग्रामीण क्षेत्र में रहते हैं तो 1 दबाएँ, या शहरी क्षेत्र के लिए 2 दबाएँ।",
    },
}


def initial_question(language: str, retry: bool = False) -> Question:
    prompt = INITIAL_PROMPTS.get(language, INITIAL_PROMPTS["en"])
    if retry:
        prefix = "क्षमा कीजिए, मुझे सुनाई नहीं दिया। " if language == "hi" else "Sorry, I did not catch that. "
        prompt = prefix + prompt
    return Question(field=None, prompt=prompt)


def question_for_field(
    field: str,
    language: str,
    *,
    retry: bool = False,
    state_speech_fallback: bool = False,
) -> Question | None:
    selected_language = language if language in {"en", "hi"} else "en"
    if field == "state" and not state_speech_fallback:
        prompt = (
            "कृपया अपना छह अंकों का पिन कोड दर्ज करें।"
            if selected_language == "hi"
            else "Please enter your six digit PIN code."
        )
        question = Question(field=field, prompt=prompt, input_type="pincode", num_digits=6)
    elif field in DTMF_PROMPTS:
        question = Question(
            field=field,
            prompt=DTMF_PROMPTS[field][selected_language],
            input_type="dtmf",
            num_digits=1,
        )
    elif field in SPEECH_PROMPTS:
        question = Question(field=field, prompt=SPEECH_PROMPTS[field][selected_language])
    else:
        return None

    if retry:
        prefix = "क्षमा कीजिए। कृपया फिर से बताइए। " if selected_language == "hi" else "Sorry, please try again. "
        question = Question(
            field=question.field,
            prompt=prefix + question.prompt,
            input_type=question.input_type,
            num_digits=question.num_digits,
        )
    return question


def choose_missing_field(missing_fields: list[str], profile: dict) -> str | None:
    requested = set(missing_fields)
    for field in FIELD_PRIORITY:
        if field in requested and profile.get(field) is None:
            return field
    return None


INTENT_PROMPTS = {
    "QUESTION": {"en": "I can collect your information for scheme matching now. Documents depend on the scheme selected. ",
                 "hi": "अभी मैं योजना मिलान के लिए आपकी जानकारी ले सकता हूँ। दस्तावेज चुनी गई योजना पर निर्भर हैं। "},
    "HELP": {"en": "Tell me what you know. You can correct an earlier answer. ",
             "hi": "जो जानकारी आपको मालूम है बताइए। आप पिछला उत्तर सुधार सकते हैं। "},
    "REPEAT_REQUEST": {"en": "I will repeat the question. ", "hi": "मैं सवाल दोहराता हूँ। "},
}

BUDGET_EXHAUSTED = {
    "en": "We still need more information to make a reliable match, but this call has reached its question limit. Please try again with the remaining information ready.",
    "hi": "सही योजना मिलान के लिए अभी और जानकारी चाहिए, लेकिन इस कॉल की प्रश्न सीमा पूरी हो गई है। कृपया बाकी जानकारी लेकर फिर कॉल करें।",
}

RECORDING_INSTRUCTION = {
    "en": " When you are finished, you may press the hash key.",
    "hi": " जवाब पूरा होने पर आप हैश कुंजी दबा सकते हैं।",
}


def recording_prompt(prompt: str, language: str) -> str:
    return prompt + RECORDING_INSTRUCTION.get(language, RECORDING_INSTRUCTION["en"])


def numeric_dtmf_question(field: str, language: str, retry: bool = False) -> Question:
    selected_language = language if language in {"en", "hi"} else "en"
    if selected_language == "hi":
        prompt = "कृपया राशि रुपये में कीपैड से दर्ज करें और फिर हैश कुंजी दबाएँ।"
        if field == "age":
            prompt = "कृपया अपनी आयु कीपैड से दर्ज करें और फिर हैश कुंजी दबाएँ।"
        if retry:
            prompt = "यह संख्या मान्य नहीं थी। " + prompt
    else:
        prompt = "Please enter the amount in rupees using your keypad, then press the hash key."
        if field == "age":
            prompt = "Please enter your age using your keypad, then press the hash key."
        if retry:
            prompt = "That number was not valid. " + prompt
    return Question(field=field, prompt=prompt, input_type="numeric_dtmf")
