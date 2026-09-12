import unittest
from unittest.mock import AsyncMock
from conversation_manager import ConversationManager, normalize_dtmf
from session_store import InMemorySessionStore
from turn_planner import plan_turn
from debug_output import format_final_match
from speech_provider import read_gather_transcript
from tests.test_conversation import result, eligible_scheme


class SemanticFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_social_category_dtmf_canonical_values(self):
        self.assertEqual([normalize_dtmf('social_category', str(i)) for i in range(1, 7)],
                         ['SC', 'ST', 'OBC', 'EWS', 'GEN', 'MINORITY'])

    async def test_category_one_callback_confirms_and_reruns(self):
        match = AsyncMock(return_value=result('NEEDS_MORE_INFO', {'social_category': 'SC'}, ['gender']))
        manager = ConversationManager(InMemorySessionStore(), profile_matcher=match)
        session = manager.start_session('CAT', 'en', 'en-IN')
        session.current_field = 'social_category'
        session.current_input = 'dtmf'
        decision = await manager.handle_turn('CAT', 'en', 'en-IN', digits='1')
        self.assertEqual(decision.field, 'gender')
        self.assertEqual(match.await_args.kwargs['profile']['social_category'], 'SC')
        self.assertIn('social_category', session.confirmed_fields)

    def test_tight_budget_batches_only_requested_unknown_fields(self):
        fields, question = plan_turn(['income_annual', 'project_cost'], {}, 7, 'en')
        self.assertEqual(fields, ['income_annual', 'project_cost'])
        self.assertIn('annual family income', question.prompt)
        fields, _ = plan_turn(['income_annual', 'project_cost'], {'income_annual': 120000}, 7, 'en')
        self.assertEqual(fields, ['project_cost'])

    async def test_batched_answer_preserves_raw_text_and_context_without_confirmation(self):
        match = AsyncMock(return_value=result('OK', {'income_annual': 120000, 'project_cost': 200000}, eligible=[eligible_scheme()]))
        manager = ConversationManager(InMemorySessionStore(), text_matcher=match)
        session = manager.start_session('BATCH', 'en', 'en-IN')
        session.current_fields = ['income_annual', 'project_cost']
        session.current_field = 'income_annual'
        text = 'Ghar ki kamai one lakh twenty thousand hai, project cost do lakh'
        decision = await manager.handle_turn('BATCH', 'en', 'en-IN', speech=text, confidence='0.95')
        self.assertEqual(decision.action, 'complete')
        self.assertEqual(match.await_args.kwargs['original_text'], text)
        self.assertEqual(match.await_args.kwargs['current_fields'], ['income_annual', 'project_cost'])

    async def test_repeat_reasks_current_question_without_losing_profile(self):
        response = result('NEEDS_MORE_INFO', {'activity': 'dairy'}, ['income_annual'])
        response['input_understanding'] = {'intent': 'REPEAT_REQUEST'}
        manager = ConversationManager(InMemorySessionStore(), text_matcher=AsyncMock(return_value=response))
        session = manager.start_session('REPEAT', 'en', 'en-IN')
        session.current_field = 'income_annual'
        decision = await manager.handle_turn('REPEAT', 'en', 'en-IN', speech='Please repeat')
        self.assertEqual(decision.field, 'income_annual')
        self.assertIn('repeat', decision.prompt)
        self.assertEqual(session.profile['activity'], 'dairy')

    def test_terminal_output_only_actual_eligible_schemes(self):
        bad = eligible_scheme('Not eligible')
        bad['eligibility']['status'] = 'NOT_ELIGIBLE'
        text = format_final_match('TEST', result('OK', {'activity': 'dairy'}, eligible=[bad, eligible_scheme('Actual scheme', 87)]))
        self.assertIn('Actual scheme', text)
        self.assertIn('Score: 87', text)
        self.assertNotIn('Not eligible', text)

    def test_empty_result_cannot_manufacture_recommendation(self):
        text = format_final_match('TEST', result('NO_MATCH', {}))
        self.assertIn('TOP RECOMMENDATION: None', text)
        self.assertIn('RETURNED: 0', text)

    def test_gather_is_twilio_text_not_sarvam_audio(self):
        transcript = read_gather_transcript({'SpeechResult': 'डेयरी', 'Confidence': '0.7'})
        self.assertEqual(transcript.provider, 'twilio')
        self.assertIsNone(transcript.stt_ms)
        self.assertEqual(transcript.text, 'डेयरी')

    async def test_callback_budget_never_fabricates_or_asks_past_limit(self):
        manager = ConversationManager(InMemorySessionStore(), text_matcher=AsyncMock(return_value=result('NEEDS_MORE_INFO', {}, ['activity'])))
        session = manager.start_session('LIMIT', 'en', 'en-IN')
        session.callbacks_used = 8
        decision = await manager.handle_turn('LIMIT', 'en', 'en-IN', speech='not sure')
        self.assertEqual(decision.action, 'terminate')
        self.assertIn('more information', decision.prompt)

    async def test_callsid_profile_survives_multiple_bhashini_text_turns(self):
        first_profile = {'age': 28, 'state': 'Rajasthan', 'activity': None}
        second_profile = {**first_profile, 'activity': 'dairy', 'activity_category': 'agriculture'}
        matcher = AsyncMock(side_effect=[
            result('NEEDS_MORE_INFO', first_profile, ['activity']),
            result('NEEDS_MORE_INFO', second_profile, ['project_cost']),
        ])
        manager = ConversationManager(InMemorySessionStore(), text_matcher=matcher)
        session = manager.start_session('CABHASHINI', 'hi', 'hi-IN')
        session.profile = {'age': 28}
        await manager.handle_turn('CABHASHINI', 'hi', 'hi-IN', speech='मैं राजस्थान से हूं', speech_provider='bhashini', stt_ms=20)
        await manager.handle_turn('CABHASHINI', 'hi', 'hi-IN', speech='डेयरी', speech_provider='bhashini', stt_ms=18)
        self.assertEqual(matcher.await_args_list[1].kwargs['profile']['age'], 28)
        self.assertEqual(matcher.await_args_list[1].kwargs['profile']['state'], 'Rajasthan')
