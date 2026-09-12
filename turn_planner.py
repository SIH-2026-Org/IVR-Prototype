from config import IVR_TARGET_MAX_CALLBACKS
from question_bank import Question, choose_missing_field, question_for_field

BATCH_PROMPTS = {
    ("income_annual", "project_cost"): {
        "en": "Please tell me your annual family income and the total cost of your project, naming each amount.",
        "hi": "कृपया अपनी सालाना पारिवारिक आय और परियोजना की कुल लागत, दोनों का नाम लेकर बताइए।",
    },
    ("activity", "project_cost"): {
        "en": "What business do you want support for, and what is the approximate total project cost?",
        "hi": "आप किस व्यवसाय के लिए सहायता चाहते हैं और परियोजना की अनुमानित कुल लागत कितनी है?",
    },
}


def batch_question(fields, language):
    return Question(fields[0], BATCH_PROMPTS[tuple(fields)].get(language, BATCH_PROMPTS[tuple(fields)]["en"]))


def plan_turn(missing_fields, profile, callbacks_used, language):
    missing = {field for field in missing_fields if profile.get(field) is None}
    remaining = IVR_TARGET_MAX_CALLBACKS - callbacks_used
    if len(missing) >= max(1, remaining - 1):
        for pair in BATCH_PROMPTS:
            if set(pair) <= missing:
                return list(pair), batch_question(pair, language)
    field = choose_missing_field(missing_fields, profile)
    return ([field], question_for_field(field, language)) if field else ([], None)
