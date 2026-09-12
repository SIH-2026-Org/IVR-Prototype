from fastapi import FastAPI, Request
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather

import config
from audio_pipeline import AudioPipelineError, recording_transcriber
from bhashini_provider import BhashiniError
from conversation_manager import TurnDecision, conversation_manager
from question_bank import initial_question, recording_prompt
from session_store import session_store
from speech_provider import read_gather_transcript

BASE_URL = config.PUBLIC_BASE_URL

app = FastAPI(title="Saarthi-Setu IVR")


def _count_opening_callback(form):
    call_sid = str(form.get("CallSid", "")).strip()
    if call_sid:
        session = session_store.get_or_create(call_sid, "en", "en-IN")
        session.callbacks_used += 1
        session_store.save(session)

def twiml_response(response):
    xml = str(response)

    print("\n========== TWIML RESPONSE ==========")
    print(xml)
    print("====================================\n")

    return Response(
        content=xml,
        media_type="application/xml"
    )

def _twilio_language(language: str) -> str:
    return "hi-IN" if language == "hi" else "en-IN"


def _twilio_voice(language: str) -> str:
    return "Google.hi-IN-Standard-E" if language == "hi" else "Google.en-IN-Standard-E"


def _append_question(response: VoiceResponse, decision: TurnDecision, language: str) -> None:
    action = f"{config.PUBLIC_BASE_URL}/ivr/need?lang={language}"
    use_bhashini_recording = (
        decision.input_type == "speech"
        and config.SPEECH_PROVIDER == "bhashini"
        and config.BHASHINI_ENABLED
    )
    if use_bhashini_recording:
        response.say(
            recording_prompt(decision.prompt, language),
            voice=_twilio_voice(language),
            language=_twilio_language(language),
        )
        response.record(
            action=f"{config.PUBLIC_BASE_URL}/ivr/recording-answer?lang={language}",
            method="POST",
            timeout=config.TWILIO_RECORD_SILENCE_TIMEOUT,
            max_length=config.TWILIO_RECORD_MAX_SECONDS,
            finish_on_key=config.TWILIO_RECORD_FINISH_KEY,
            play_beep=False,
            trim="trim-silence",
        )
        return
    if decision.input_type in {"speech", "twilio_speech"}:
        gather = Gather(
            input="speech",
            action=action,
            method="POST",
            language=_twilio_language(language),
            speech_timeout="auto",
            timeout=8,
            action_on_empty_result=True,
        )
    elif decision.input_type == "numeric_dtmf":
        gather = Gather(
            input="dtmf",
            action=action,
            method="POST",
            finish_on_key="#",
            timeout=8,
            action_on_empty_result=True,
        )
    else:
        gather = Gather(
            input="dtmf",
            action=action,
            method="POST",
            num_digits=decision.num_digits or 1,
            timeout=8,
            action_on_empty_result=True,
        )
    gather.say(
        decision.prompt,
        voice=_twilio_voice(language),
        language=_twilio_language(language),
    )
    response.append(gather)


def _render_decision(decision: TurnDecision, language: str):
    response = VoiceResponse()
    if decision.action in {"ask", "confirm"}:
        _append_question(response, decision, language)
    else:
        response.say(
            decision.prompt,
            voice=_twilio_voice(language),
            language=_twilio_language(language),
        )
        response.hangup()
    return twiml_response(response)


async def _process_user_turn(
    call_sid: str,
    language: str,
    *,
    speech: str = "",
    digits: str = "",
    confidence=None,
    forced_field: str | None = None,
    speech_provider: str = "twilio",
    stt_ms: int | None = None,
):
    language_code = _twilio_language(language)

    if not call_sid:
        response = VoiceResponse()
        response.say("Sorry, I could not identify this call. Please try again.")
        response.hangup()
        return twiml_response(response)

    session = session_store.get_or_create(call_sid, language, language_code)
    if forced_field:
        session.current_field = forced_field
        session.current_input = "speech"
        session.location_speech_fallback = forced_field == "state"
        session_store.save(session)

    print("\n========== IVR TURN ==========")
    print("CALL SID:", call_sid)
    print("TURN:", session.turn_count + 1)
    print("FIELD BEING ANSWERED:", session.current_field)
    print("RAW SPEECH:", speech)
    print("DTMF:", digits)
    print("CONFIDENCE:", confidence)
    print("==============================\n")

    try:
        decision = await conversation_manager.handle_turn(
            call_sid,
            language,
            language_code,
            speech=speech,
            digits=digits,
            confidence=confidence,
            speech_provider=speech_provider,
            stt_ms=stt_ms,
        )
    except Exception as error:
        print("DRE ERROR:", repr(error))
        session_store.delete(call_sid)
        response = VoiceResponse()
        response.say(
            "The scheme matching service is temporarily unavailable. Please try again shortly.",
            voice=_twilio_voice(language),
            language=language_code,
        )
        response.hangup()
        return twiml_response(response)

    return _render_decision(decision, language)


async def _handle_ivr_turn(request: Request, language: str, forced_field: str | None = None):
    form = await request.form()
    transcript = read_gather_transcript(form)
    return await _process_user_turn(
        str(form.get("CallSid", "")).strip(),
        language,
        speech=transcript.text,
        digits=str(form.get("Digits", "")).strip(),
        confidence=transcript.confidence,
        forced_field=forced_field,
    )


def _log_bhashini_turn(call_sid: str, field: str | None, language: str, processed) -> None:
    print("\n" + "=" * 60)
    print("BHASHINI SPEECH TURN")
    print("=" * 60)
    print("CALL SID:", call_sid)
    print("FIELD:", field)
    print("LANGUAGE:", language)
    print("RECORDING DURATION:", processed.recording_duration or "not supplied")
    print("SOURCE FORMAT:", processed.source_format)
    print("CONVERTED FORMAT:", processed.converted_format)
    print("ASR PROVIDER: BHASHINI")
    print("ASR STATUS: SUCCESS")
    print("ASR LATENCY:", processed.asr.latency_ms, "ms")
    print("TRANSCRIPT:")
    print(processed.asr.transcript)
    print("=" * 60 + "\n")


@app.post("/ivr/recording-answer")
async def recording_answer(request: Request):
    form = await request.form()
    language = request.query_params.get("lang", "en")
    call_sid = str(form.get("CallSid", "")).strip()
    recording_url = str(form.get("RecordingUrl", "")).strip()
    session = session_store.get(call_sid) if call_sid else None

    if not call_sid:
        return await _process_user_turn("", language)

    try:
        if not recording_url:
            raise AudioPipelineError("missing_recording_url")
        processed = await recording_transcriber.transcribe(
            recording_url,
            language,
            form.get("RecordingDuration"),
        )
        _log_bhashini_turn(call_sid, session.current_field if session else None, language, processed)
        return await _process_user_turn(
            call_sid,
            language,
            speech=processed.asr.transcript,
            speech_provider=processed.asr.provider,
            stt_ms=processed.asr.latency_ms,
        )
    except Exception as error:
        reason = str(error) if isinstance(error, (AudioPipelineError, BhashiniError)) else error.__class__.__name__
        print(f"BHASHINI FALLBACK: {reason}")
        try:
            decision = await conversation_manager.handle_turn(
                call_sid,
                language,
                _twilio_language(language),
                speech="",
            )
        except Exception:
            session_store.delete(call_sid)
            response = VoiceResponse()
            response.say(
                "The speech service is temporarily unavailable. Please try again shortly.",
                voice=_twilio_voice(language),
                language=_twilio_language(language),
            )
            response.hangup()
            return twiml_response(response)
        if decision.action in {"ask", "confirm"} and decision.input_type == "speech":
            decision.input_type = "twilio_speech"
            prefix = (
                "क्षमा कीजिए, आवाज़ समझने में समस्या हुई। कृपया एक बार फिर बोलिए। "
                if language == "hi"
                else "Sorry, I had trouble understanding that recording. Please say it once more. "
            )
            decision.prompt = prefix + decision.prompt
        return _render_decision(decision, language)


@app.get("/")
def home():
    return {
        "service": "Saarthi-Setu IVR",
        "status": "running"
    }


@app.post("/ivr")
async def ivr(request: Request):
    _count_opening_callback(await request.form())
    response = VoiceResponse()

    gather = Gather(
        input="dtmf",
        num_digits=1,
        action=f"{BASE_URL}/ivr/language",
        method="POST",
        timeout=7
    )

    gather.say(
        "Welcome to Saarthi Setu.",
        voice="Google.en-IN-Standard-E",
        language="en-IN"
    )

    gather.say(
        "हिंदी के लिए एक दबाएँ।",
        voice="Google.hi-IN-Standard-E",
        language="hi-IN"
    )

    gather.say(
        "For English, press 2.",
        voice="Google.en-IN-Standard-E",
        language="en-IN"
    )

    response.append(gather)

    response.say(
        "We did not receive your selection. Please try again.",
        voice="Google.en-IN-Standard-E",
        language="en-IN"
    )

    response.redirect(f"{BASE_URL}/ivr", method="POST")

    return twiml_response(response)


@app.post("/ivr/language")
async def language(request: Request):
    form = await request.form()
    _count_opening_callback(form)
    digit = form.get("Digits")

    response = VoiceResponse()

    if digit == "1":

        gather = Gather(
            input="dtmf",
            num_digits=1,
            action=f"{BASE_URL}/ivr/menu?lang=hi",
            method="POST",
            timeout=7
        )

        gather.say(
            "सारथी सेतु में आपका स्वागत है। "
            "सरकारी योजना खोजने के लिए एक दबाएँ। "
            "जरूरी दस्तावेज जानने के लिए दो दबाएँ। "
            "निकटतम अधिकृत सहायता केंद्र की जानकारी के लिए तीन दबाएँ।",
            voice="Google.hi-IN-Standard-E",
            language="hi-IN"
        )

        response.append(gather)

    elif digit == "2":

        gather = Gather(
            input="dtmf",
            num_digits=1,
            action=f"{BASE_URL}/ivr/menu?lang=en",
            method="POST",
            timeout=7
        )

        gather.say(
            "Welcome to Saarthi Setu. "
            "Press 1 to find a suitable government scheme. "
            "Press 2 for document guidance. "
            "Press 3 to find an authorized assistance partner.",
            voice="Google.en-IN-Standard-E",
            language="en-IN"
        )

        response.append(gather)

    else:
        response.say("Invalid selection. Please try again.")
        response.redirect(f"{BASE_URL}/ivr", method="POST")

    return twiml_response(response)


@app.post("/ivr/menu")
async def menu(request: Request, lang: str = "en"):
    form = await request.form()
    _count_opening_callback(form)
    digit = form.get("Digits")
    call_sid = str(form.get("CallSid", "")).strip()

    response = VoiceResponse()

    if digit == "1":
        if not call_sid:
            response.say("Sorry, I could not identify this call. Please try again.")
            response.hangup()
        else:
            language_code = _twilio_language(lang)
            conversation_manager.start_session(call_sid, lang, language_code)
            question = initial_question(lang)
            _append_question(
                response,
                TurnDecision(
                    action="ask",
                    prompt=question.prompt,
                    field=None,
                    input_type=question.input_type,
                ),
                lang,
            )

    elif digit == "2":

        if lang == "hi":
            response.say(
                "दस्तावेज मार्गदर्शन सुविधा जल्द जोड़ी जा रही है।",
                voice="Google.hi-IN-Standard-E",
                language="hi-IN"
            )
        else:
            response.say(
                "Document guidance will be connected in the next prototype stage.",
                voice="Google.en-IN-Standard-E",
                language="en-IN"
            )

        response.hangup()

    elif digit == "3":

        if lang == "hi":
            response.say(
                "अधिकृत सहायता केंद्र खोजने की सुविधा "
                "अगले चरण में जोड़ी जाएगी।",
                voice="Google.hi-IN-Standard-E",
                language="hi-IN"
            )
        else:
            response.say(
                "Authorized partner routing will be connected "
                "in the next prototype stage.",
                voice="Google.en-IN-Standard-E",
                language="en-IN"
            )

        response.hangup()

    else:
        response.say("Invalid option.")
        response.hangup()

    return twiml_response(response)


@app.post("/ivr/need")
async def ivr_need(request: Request):
    lang = request.query_params.get("lang", "en")
    return await _handle_ivr_turn(request, lang)


@app.post("/ivr/income")
async def capture_income(request: Request, lang: str = "en"):
    return await _handle_ivr_turn(request, lang, forced_field="income_annual")


@app.post("/ivr/location")
async def capture_location(request: Request, lang: str = "en"):
    return await _handle_ivr_turn(request, lang, forced_field="state")
