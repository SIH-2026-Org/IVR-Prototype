from dataclasses import dataclass
import re
import time
from typing import Awaitable, Callable

from config import LOW_STT_CONFIDENCE, MAX_CONVERSATION_TURNS, MAX_FIELD_RETRIES, IVR_DEBUG, IVR_TARGET_MAX_CALLBACKS
from debug_output import format_final_match
from turn_planner import plan_turn, batch_question
from question_bank import INTENT_PROMPTS, BUDGET_EXHAUSTED, numeric_dtmf_question
from numeric_answers import NUMERIC_FIELDS, parse_numeric_answer, parse_numeric_dtmf
from dre_client import match_profile, match_text
from location_service import PincodeResolver, location_resolver
from question_bank import (
    Question,
    choose_missing_field,
    initial_question,
    question_for_field,
)
from session_store import IVRSession, InMemorySessionStore, session_store


IMPORTANT_NUMERIC_FIELDS = NUMERIC_FIELDS
DTMF_VALUES = {
    "social_category": {"1": "SC", "2": "ST", "3": "OBC", "4": "EWS", "5": "GEN", "6": "MINORITY"},
    "gender": {"1": "F", "2": "M", "3": "O"},
    "existing_business": {"1": True, "2": False},
    "area_type": {"1": "rural", "2": "urban"},
}


class DREProtocolError(RuntimeError):
    pass


@dataclass
class TurnDecision:
    action: str
    prompt: str
    field: str | None = None
    input_type: str | None = None
    num_digits: int | None = None
    result: dict | None = None


def contextualize_answer(field: str | None, speech: str) -> str:
    value = speech.strip()
    if re.search(r"\b(actually|income|cost|loan|from|want|what|why|repeat|help)\b|\?", value, re.I):
        return value
    templates = {
        "income_annual": "My annual family income is {value}.",
        "project_cost": "My project cost is {value}.",
        "loan_required": "I need a loan of {value}.",
        "age": "My age is {value} years.",
        "activity": "My business activity is {value}.",
        "social_category": "My social category is {value}.",
        "state": "I live in {value} state.",
    }
    template = templates.get(field)
    return template.format(value=value) if template else value


def normalize_dtmf(field: str, digit: str):
    return DTMF_VALUES.get(field, {}).get(digit)


def _is_conversation_control(text: str) -> bool:
    return bool(re.search(r"\b(?:repeat|help|what|why|how|dobara|madad)\b|दोबारा|दोहर|मदद|क्या|क्यों|कैसे|\?", text, re.I))


def _indian_number(value: int) -> str:
    digits = str(abs(int(value)))
    if len(digits) <= 3:
        formatted = digits
    else:
        tail = digits[-3:]
        head = digits[:-3]
        pairs = []
        while head:
            pairs.append(head[-2:])
            head = head[:-2]
        formatted = ",".join(reversed(pairs)) + "," + tail
    return ("minus " if value < 0 else "") + formatted


def confirmation_question(field: str, value, language: str) -> str:
    if field == "age":
        understood = f"{value} years"
    else:
        understood = f"{_indian_number(value)} rupees"
    if language == "hi":
        return f"मैंने आपकी जानकारी {understood} समझी है। सही है तो 1 दबाएँ, दोबारा बताने के लिए 2 दबाएँ।"
    labels = {
        "income_annual": "annual family income",
        "project_cost": "project cost",
        "loan_required": "loan requirement",
        "age": "age",
    }
    label = labels.get(field, field.replace("_", " "))
    return f"I understood your {label} as {understood}. Press 1 if this is correct, or 2 to say it again."


def sanitize_for_tts(text: str) -> str:
    cleaned = re.sub(r"[*#_`✓🎯❌⚠️•]", " ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def build_spoken_recommendation(result: dict) -> str:
    eligible = [
        item
        for item in result.get("eligible_schemes", [])
        if item.get("eligibility", {}).get("status") == "ELIGIBLE"
    ][:3]
    if not eligible:
        return "I could not find an eligible recommendation in the matching result."

    count_words = {1: "one", 2: "two", 3: "three"}
    parts = [f"I found {count_words[len(eligible)]} suitable scheme{'s' if len(eligible) != 1 else ''}."]
    labels = ["Your best match", "Your second match", "Your third match"]
    for index, item in enumerate(eligible):
        scheme = item.get("scheme", {})
        score = item.get("score", {}).get("total_score")
        name = sanitize_for_tts(scheme.get("name") or scheme.get("short_name") or "an eligible scheme")
        phrase = f"{labels[index]} is {name}"
        if isinstance(score, (int, float)):
            phrase += f", with a match score of {round(score)} out of 100"
        parts.append(phrase + ".")

    simulation = eligible[0].get("simulation") or {}
    emi = simulation.get("emi_monthly") if simulation.get("available") else None
    if isinstance(emi, (int, float)) and emi > 0:
        parts.append(f"The estimated monthly E M I is approximately {_indian_number(round(emi))} rupees.")
    return " ".join(parts)


def _validate_dre_result(result: dict) -> None:
    if not isinstance(result, dict):
        raise DREProtocolError("DRE response is not an object")
    if result.get("status") not in {"OK", "NEEDS_MORE_INFO", "NO_MATCH"}:
        raise DREProtocolError("DRE response has an invalid status")
    if not isinstance(result.get("normalized_profile"), dict):
        raise DREProtocolError("DRE response is missing normalized_profile")
    if not isinstance(result.get("missing_fields", []), list):
        raise DREProtocolError("DRE response has invalid missing_fields")


class ConversationManager:
    def __init__(
        self,
        store: InMemorySessionStore | None = None,
        text_matcher: Callable[..., Awaitable[dict]] | None = None,
        profile_matcher: Callable[..., Awaitable[dict]] | None = None,
        resolver: PincodeResolver | None = None,
    ):
        self.store = store if store is not None else session_store
        self.text_matcher = text_matcher or match_text
        self.profile_matcher = profile_matcher or match_profile
        self.resolver = resolver or location_resolver

    def start_session(self, call_sid: str, language: str, language_code: str) -> IVRSession:
        old_session = self.store.get(call_sid)
        callbacks = old_session.callbacks_used if old_session else 3
        self.store.delete(call_sid)
        session = self.store.get_or_create(call_sid, language, language_code)
        session.callbacks_used = callbacks
        return session

    async def handle_turn(
        self,
        call_sid: str,
        language: str,
        language_code: str,
        *,
        speech: str = "",
        digits: str = "",
        confidence: str | float | None = None,
        speech_provider: str = "twilio",
        stt_ms: int | None = None,
    ) -> TurnDecision:
        session = self.store.get_or_create(call_sid, language, language_code)
        started = time.perf_counter()
        session.turn_count += 1
        session.callbacks_used += 1
        if session.callbacks_used > IVR_TARGET_MAX_CALLBACKS:
            self.store.delete(call_sid)
            return TurnDecision("terminate", BUDGET_EXHAUSTED.get(language, BUDGET_EXHAUSTED['en']))
        if session.turn_count > MAX_CONVERSATION_TURNS:
            self.store.delete(call_sid)
            return TurnDecision("terminate", "This call has reached the maximum number of questions. Please try again later.")

        session.conversation.append({
            "turn": session.turn_count,
            "field": session.current_field,
            "raw_speech": speech.strip(),
            "digits": digits,
            "confidence": confidence,
        })

        if session.pending_confirmation:
            return await self._handle_confirmation(session, digits)

        if session.current_input == "pincode" and session.current_field == "state":
            return await self._handle_pincode(session, digits)

        if session.current_input == "dtmf" and session.current_field in DTMF_VALUES:
            return await self._handle_structured_dtmf(session, digits)

        if session.current_input == "numeric_dtmf" and session.current_field in NUMERIC_FIELDS:
            return await self._handle_numeric_dtmf(session, digits)

        answered_field = session.current_field
        requested = session.current_fields if answered_field in session.current_fields else ([answered_field] if answered_field else [])
        is_single_numeric = answered_field in NUMERIC_FIELDS and len(requested) <= 1

        if is_single_numeric and not _is_conversation_control(speech):
            value = parse_numeric_answer(answered_field, speech)
            if value is not None:
                return await self._handle_numeric_value(session, answered_field, value)
            return self._retry_numeric_with_dtmf(session)

        if not speech.strip() or self._is_low_confidence(confidence):
            return self._retry_current_question(session)

        contextualized = speech if len(requested) > 1 else contextualize_answer(answered_field, speech)
        previous_value = session.profile.get(answered_field) if answered_field else None
        result = await self.text_matcher(
            text=contextualized,
            language=session.language_code,
            session_id=session.call_sid,
            profile=session.profile,
            original_text=speech,
            current_fields=requested,
            confirmed_fields=session.confirmed_fields,
        )
        _validate_dre_result(result)
        session.profile = result["normalized_profile"]
        understanding = result.get("input_understanding", {})
        if IVR_DEBUG:
            print("INPUT UNDERSTANDING:", understanding)
            print("TURN LATENCY: STT:", f"{stt_ms} ms" if stt_ms is not None else "not supplied",
                  f"({speech_provider}); semantic:", understanding.get("semantic_ms"),
                  "ms; DRE:", result.get("duration_ms"), "ms; total:", round((time.perf_counter() - started) * 1000), "ms")

        intent = understanding.get("intent")
        if intent in INTENT_PROMPTS:
            decision = self._retry_current_question(session)
            decision.prompt = INTENT_PROMPTS[intent].get(language, INTENT_PROMPTS[intent]["en"]) + decision.prompt
            return decision

        if requested and all(session.profile.get(field) is None for field in requested) and result["status"] != "OK":
            return self._retry_current_question(session)

        if (
            answered_field in IMPORTANT_NUMERIC_FIELDS
            and session.profile.get(answered_field) is not None
            and session.profile.get(answered_field) != previous_value
            and (answered_field in understanding.get("uncertain_fields", [])
                 or (confidence not in (None, "") and self._needs_confirmation(confidence))
                 or bool(re.search(r"\bconfirm\b", speech, re.I)))
        ):
            if session.callbacks_used >= IVR_TARGET_MAX_CALLBACKS:
                self.store.delete(call_sid)
                return TurnDecision("terminate", BUDGET_EXHAUSTED.get(language, BUDGET_EXHAUSTED['en']))
            value = session.profile[answered_field]
            session.pending_confirmation = {"field": answered_field, "value": value}
            session.current_input = "confirmation"
            session.retry_count = 0
            self.store.save(session)
            return TurnDecision(
                "confirm",
                confirmation_question(answered_field, value, session.language),
                field=answered_field,
                input_type="dtmf",
                num_digits=1,
                result=result,
            )

        return self._advance(session, result)

    @staticmethod
    def _needs_confirmation(confidence):
        try:
            return float(confidence) < 0.65
        except (TypeError, ValueError):
            return False

    def _is_low_confidence(self, confidence: str | float | None) -> bool:
        if confidence in (None, ""):
            return False
        try:
            return float(confidence) < LOW_STT_CONFIDENCE
        except (TypeError, ValueError):
            return False

    def _retry_current_question(self, session: IVRSession) -> TurnDecision:
        if session.callbacks_used >= IVR_TARGET_MAX_CALLBACKS:
            self.store.delete(session.call_sid)
            return TurnDecision("terminate", BUDGET_EXHAUSTED.get(session.language, BUDGET_EXHAUSTED['en']))
        session.retry_count += 1
        if session.retry_count > MAX_FIELD_RETRIES:
            self.store.delete(session.call_sid)
            return TurnDecision(
                "terminate",
                "Sorry, I could not collect that information after several attempts. Please try again later.",
            )

        if len(session.current_fields) == 2:
            question = batch_question(session.current_fields, session.language)
        elif session.current_field:
            question = question_for_field(
                session.current_field,
                session.language,
                retry=True,
                state_speech_fallback=session.location_speech_fallback,
            )
        else:
            question = initial_question(session.language, retry=True)
        self.store.save(session)
        return self._question_decision(question)

    def _retry_numeric_with_dtmf(self, session: IVRSession, *, invalid_dtmf: bool = False) -> TurnDecision:
        if session.callbacks_used >= IVR_TARGET_MAX_CALLBACKS:
            self.store.delete(session.call_sid)
            return TurnDecision("terminate", BUDGET_EXHAUSTED.get(session.language, BUDGET_EXHAUSTED["en"]))
        session.retry_count += 1
        if session.retry_count > MAX_FIELD_RETRIES:
            self.store.delete(session.call_sid)
            return TurnDecision("terminate", "Sorry, I could not collect that number. Please try again later.")
        session.current_input = "numeric_dtmf"
        self.store.save(session)
        question = numeric_dtmf_question(session.current_field, session.language, retry=invalid_dtmf)
        if not invalid_dtmf:
            prefix = (
                "मैं राशि स्पष्ट रूप से नहीं समझ सका। "
                if session.language == "hi"
                else "I couldn't clearly understand the amount. "
            )
            question = Question(question.field, prefix + question.prompt, question.input_type, question.num_digits)
        return self._question_decision(question)

    async def _handle_numeric_value(self, session: IVRSession, field: str, value) -> TurnDecision:
        if IVR_DEBUG:
            print(f"NUMERIC FIELD PARSED: {field}={value}; semantic_ms: 0; Sarvam: skipped")
        session.profile[field] = value
        if field not in session.confirmed_fields:
            session.confirmed_fields.append(field)
        session.current_field = None
        session.current_fields = []
        session.current_input = "speech"
        session.retry_count = 0
        result = await self.profile_matcher(
            profile=session.profile,
            language=session.language_code,
            session_id=session.call_sid,
        )
        _validate_dre_result(result)
        session.profile = result["normalized_profile"]
        return self._advance(session, result)

    async def _handle_numeric_dtmf(self, session: IVRSession, digits: str) -> TurnDecision:
        value = parse_numeric_dtmf(session.current_field, digits)
        if value is None:
            return self._retry_numeric_with_dtmf(session, invalid_dtmf=True)
        return await self._handle_numeric_value(session, session.current_field, value)

    async def _handle_structured_dtmf(self, session: IVRSession, digits: str) -> TurnDecision:
        field = session.current_field
        value = normalize_dtmf(field, digits)
        if value is None:
            return self._retry_current_question(session)

        session.profile[field] = value
        if field not in session.confirmed_fields:
            session.confirmed_fields.append(field)
        session.current_field = None
        session.current_input = "speech"
        session.retry_count = 0
        result = await self.profile_matcher(
            profile=session.profile,
            language=session.language_code,
            session_id=session.call_sid,
        )
        _validate_dre_result(result)
        session.profile = result["normalized_profile"]
        return self._advance(session, result)

    async def _handle_pincode(self, session: IVRSession, digits: str) -> TurnDecision:
        location = await self.resolver.resolve(digits)
        if not location:
            if session.callbacks_used >= IVR_TARGET_MAX_CALLBACKS:
                return self._retry_current_question(session)
            session.location_speech_fallback = True
            session.current_input = "speech"
            session.retry_count = 0
            self.store.save(session)
            question = question_for_field("state", session.language, state_speech_fallback=True)
            prefix = (
                "मैं उस पिन कोड की पुष्टि नहीं कर सका। "
                if session.language == "hi"
                else "I could not verify that PIN code. "
            )
            return TurnDecision(
                "ask",
                prefix + question.prompt,
                field="state",
                input_type="speech",
                result=None,
            )

        session.pincode = location["pincode"]
        session.profile["state"] = location["state"]
        if location.get("district"):
            session.profile["district"] = location["district"]
        session.conversation[-1]["pincode_resolved"] = True
        session.current_field = None
        session.current_input = "speech"
        session.retry_count = 0
        result = await self.profile_matcher(
            profile=session.profile,
            language=session.language_code,
            session_id=session.call_sid,
        )
        _validate_dre_result(result)
        session.profile = result["normalized_profile"]
        return self._advance(session, result)

    async def _handle_confirmation(self, session: IVRSession, digits: str) -> TurnDecision:
        pending = session.pending_confirmation
        field = pending["field"]
        if digits == "1":
            if field not in session.confirmed_fields:
                session.confirmed_fields.append(field)
            session.pending_confirmation = None
            session.current_field = None
            session.current_input = "speech"
            session.retry_count = 0
            result = await self.profile_matcher(
                profile=session.profile,
                language=session.language_code,
                session_id=session.call_sid,
            )
            _validate_dre_result(result)
            session.profile = result["normalized_profile"]
            return self._advance(session, result)
        if digits == "2":
            session.profile[field] = None
            session.pending_confirmation = None
            session.current_field = field
            session.current_input = "speech"
            session.retry_count = 0
            self.store.save(session)
            if session.callbacks_used >= IVR_TARGET_MAX_CALLBACKS:
                return self._retry_current_question(session)
            return self._question_decision(question_for_field(field, session.language, retry=True))

        if session.callbacks_used >= IVR_TARGET_MAX_CALLBACKS:
            return self._retry_current_question(session)
        session.retry_count += 1
        if session.retry_count > MAX_FIELD_RETRIES:
            self.store.delete(session.call_sid)
            return TurnDecision("terminate", "Sorry, I could not confirm that answer. Please try again later.")
        self.store.save(session)
        return TurnDecision(
            "confirm",
            "Please press 1 to confirm, or 2 to say the answer again.",
            field=field,
            input_type="dtmf",
            num_digits=1,
        )

    def _advance(self, session: IVRSession, result: dict) -> TurnDecision:
        status = result["status"]
        missing_fields = result.get("missing_fields", [])
        session.last_missing_fields = missing_fields

        if status == "OK":
            if IVR_DEBUG:
                print(format_final_match(session.call_sid, result))
            self._log_dre(session, result, None)
            prompt = build_spoken_recommendation(result)
            self.store.delete(session.call_sid)
            return TurnDecision("complete", prompt, result=result)

        if status == "NO_MATCH":
            if IVR_DEBUG:
                print(format_final_match(session.call_sid, result))
            self._log_dre(session, result, None)
            self.store.delete(session.call_sid)
            return TurnDecision(
                "terminate",
                "I processed your information, but I could not find an eligible scheme at this time.",
                result=result,
            )

        if session.callbacks_used >= IVR_TARGET_MAX_CALLBACKS:
            self.store.delete(session.call_sid)
            return TurnDecision("terminate", BUDGET_EXHAUSTED.get(session.language, BUDGET_EXHAUSTED['en']), result=result)
        fields, question = plan_turn(missing_fields, session.profile, session.callbacks_used, session.language)
        next_field = fields[0] if fields else None
        if not next_field:
            self.store.delete(session.call_sid)
            return TurnDecision(
                "terminate",
                "I need additional information that cannot be collected in this call. Please contact an authorized assistance centre.",
                result=result,
            )

        session.current_field = next_field
        session.current_fields = fields
        session.location_speech_fallback = False if next_field == "state" else session.location_speech_fallback
        session.retry_count = 0
        session.current_input = question.input_type
        self.store.save(session)
        self._log_dre(session, result, next_field)
        return self._question_decision(question, result)

    @staticmethod
    def _question_decision(question: Question, result: dict | None = None) -> TurnDecision:
        return TurnDecision(
            "ask",
            question.prompt,
            field=question.field,
            input_type=question.input_type,
            num_digits=question.num_digits,
            result=result,
        )

    @staticmethod
    def _log_dre(session: IVRSession, result: dict, next_field: str | None) -> None:
        print("\n========== DRE ==========")
        print("STATUS:", result.get("status"))
        print("PROFILE:", result.get("normalized_profile"))
        print("MISSING:", result.get("missing_fields"))
        print("NEXT FIELD:", next_field)
        print("=========================\n")


conversation_manager = ConversationManager()
