import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app as ivr_app
from audio_pipeline import AudioPipelineError, ProcessedRecording
from bhashini_provider import ASRResult
from conversation_manager import TurnDecision


class IVRRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(ivr_app.app)

    def test_opening_routes_count_three_callbacks(self):
        ivr_app.session_store.delete('COUNT')
        self.client.post('/ivr', data={'CallSid': 'COUNT'})
        self.client.post('/ivr/language', data={'CallSid': 'COUNT', 'Digits': '2'})
        self.client.post('/ivr/menu?lang=en', data={'CallSid': 'COUNT', 'Digits': '1'})
        self.assertEqual(ivr_app.session_store.get('COUNT').callbacks_used, 3)

    def test_open_question_uses_record_with_one_direct_action_callback(self):
        with patch.object(ivr_app.config, "SPEECH_PROVIDER", "bhashini"), patch.object(ivr_app.config, "BHASHINI_ENABLED", True):
            response = self.client.post(
                "/ivr/menu?lang=en",
                data={"Digits": "1", "CallSid": "CAMENU"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn('<Record', response.text)
        self.assertIn('/ivr/recording-answer?lang=en', response.text)
        self.assertIn('finishOnKey="#"', response.text)
        self.assertIn('maxLength="15"', response.text)
        self.assertNotIn('<Redirect', response.text)

    def test_social_category_question_remains_dtmf(self):
        response = ivr_app._render_decision(
            TurnDecision("ask", "category", field="social_category", input_type="dtmf", num_digits=1), "en"
        )
        text = response.body.decode()
        self.assertIn('input="dtmf"', text)
        self.assertNotIn('<Record', text)

    def test_numeric_dtmf_uses_variable_digits_and_hash_finish_key(self):
        response = ivr_app._render_decision(
            TurnDecision("ask", "Enter amount", field="income_annual", input_type="numeric_dtmf"), "en"
        )
        text = response.body.decode()
        self.assertIn('input="dtmf"', text)
        self.assertIn('finishOnKey="#"', text)
        self.assertNotIn('numDigits=', text)
        self.assertNotIn('<Record', text)

    def test_recording_callback_sends_bhashini_transcript_to_existing_turn_handler(self):
        processed = ProcessedRecording(ASRResult("bhashini", "मैं डेयरी शुरू करना चाहता हूँ", "hi", 42), "4", "twilio-recording")
        decision = TurnDecision("ask", "next", field="income_annual", input_type="speech")
        ivr_app.session_store.get_or_create("CAREC", "hi", "hi-IN")
        with (
            patch.object(ivr_app.recording_transcriber, "transcribe", AsyncMock(return_value=processed)) as transcriber,
            patch.object(ivr_app.conversation_manager, "handle_turn", AsyncMock(return_value=decision)) as handler,
            patch.object(ivr_app.config, "SPEECH_PROVIDER", "bhashini"),
            patch.object(ivr_app.config, "BHASHINI_ENABLED", True),
        ):
            response = self.client.post("/ivr/recording-answer?lang=hi", data={
                "CallSid": "CAREC", "RecordingUrl": "https://api.twilio.com/recording", "RecordingDuration": "4"
            })
        self.assertEqual(handler.await_args.kwargs["speech"], "मैं डेयरी शुरू करना चाहता हूँ")
        self.assertEqual(transcriber.await_args.args[0], "https://api.twilio.com/recording")
        self.assertIn("<Record", response.text)
        self.assertIsNotNone(ivr_app.session_store.get("CAREC"))

    def test_bhashini_debug_block_contains_no_credentials_or_recording_url(self):
        processed = ProcessedRecording(ASRResult("bhashini", "डेयरी", "hi", 42), "4", "wav")
        output = StringIO()
        with redirect_stdout(output):
            ivr_app._log_bhashini_turn("CADEBUG", "activity", "hi", processed)
        rendered = output.getvalue()
        self.assertIn("ASR STATUS: SUCCESS", rendered)
        self.assertIn("डेयरी", rendered)
        self.assertNotIn("RecordingUrl", rendered)
        self.assertNotIn("Authorization", rendered)

    def test_bhashini_failure_reasks_once_with_legacy_twilio_stt(self):
        decision = TurnDecision("ask", "What business?", field="activity", input_type="speech")
        ivr_app.session_store.get_or_create("CAFALLBACK", "en", "en-IN")
        with (
            patch.object(ivr_app.recording_transcriber, "transcribe", AsyncMock(side_effect=AudioPipelineError("download_failed"))),
            patch.object(ivr_app.conversation_manager, "handle_turn", AsyncMock(return_value=decision)),
        ):
            response = self.client.post("/ivr/recording-answer?lang=en", data={
                "CallSid": "CAFALLBACK", "RecordingUrl": "https://api.twilio.com/recording"
            })
        self.assertIn('input="speech"', response.text)
        self.assertNotIn("<Record", response.text)
        self.assertIn("trouble understanding", response.text)

    def test_missing_recording_url_uses_same_safe_fallback(self):
        decision = TurnDecision("ask", "What business?", field="activity", input_type="speech")
        with patch.object(ivr_app.conversation_manager, "handle_turn", AsyncMock(return_value=decision)):
            response = self.client.post("/ivr/recording-answer?lang=en", data={"CallSid": "CANOURl"})
        self.assertIn('input="speech"', response.text)

    def test_legacy_speechresult_route_still_reuses_turn_handler(self):
        decision = TurnDecision("complete", "Actual DRE result")
        with patch.object(ivr_app.conversation_manager, "handle_turn", AsyncMock(return_value=decision)) as handler:
            response = self.client.post("/ivr/need?lang=en", data={"CallSid": "CALEGACY", "SpeechResult": "dairy"})
        self.assertEqual(handler.await_args.kwargs["speech"], "dairy")
        self.assertIn("Actual DRE result", response.text)

    def test_needs_more_info_returns_gather_without_hangup(self):
        decision = TurnDecision(
            "ask",
            "What is your annual family income?",
            field="income_annual",
            input_type="speech",
        )
        with (
            patch.object(ivr_app.conversation_manager, "handle_turn", AsyncMock(return_value=decision)),
            patch.object(ivr_app.config, "SPEECH_PROVIDER", "bhashini"),
            patch.object(ivr_app.config, "BHASHINI_ENABLED", True),
        ):
            response = self.client.post(
                "/ivr/need?lang=en",
                data={"SpeechResult": "dairy", "CallSid": "CAPARTIAL"},
            )
        self.assertIn("<Record", response.text)
        self.assertNotIn("<Hangup", response.text)
        self.assertIn("annual family income", response.text)

    def test_dre_failure_speaks_friendly_error_and_terminates(self):
        with patch.object(
            ivr_app.conversation_manager,
            "handle_turn",
            AsyncMock(side_effect=TimeoutError("DRE timed out")),
        ):
            response = self.client.post(
                "/ivr/need?lang=en",
                data={"SpeechResult": "dairy", "CallSid": "CAERROR"},
            )
        self.assertIn("temporarily unavailable", response.text)
        self.assertIn("<Hangup", response.text)

    def test_missing_call_sid_terminates_without_creating_shared_session(self):
        response = self.client.post("/ivr/need?lang=en", data={"SpeechResult": "dairy"})
        self.assertIn("could not identify this call", response.text)
        self.assertIn("<Hangup", response.text)
