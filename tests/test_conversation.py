import unittest
from unittest.mock import AsyncMock

from conversation_manager import (
    ConversationManager,
    DREProtocolError,
    build_spoken_recommendation,
    contextualize_answer,
)
from numeric_answers import parse_numeric_answer, parse_numeric_dtmf
from session_store import InMemorySessionStore


def result(status, profile, missing=None, eligible=None, summary_text=""):
    return {
        "status": status,
        "normalized_profile": profile,
        "missing_fields": missing or [],
        "eligible_schemes": eligible or [],
        "summary_text": summary_text,
    }


def eligible_scheme(name="Kisan Credit Card Scheme", score=97, emi=2905):
    return {
        "rank": 1,
        "scheme": {"name": name},
        "score": {"total_score": score},
        "eligibility": {"status": "ELIGIBLE"},
        "simulation": {"available": True, "emi_monthly": emi},
    }


class FailedResolver:
    async def resolve(self, _pincode):
        return None


class SuccessfulResolver:
    async def resolve(self, pincode):
        return {"pincode": pincode, "district": "Jaipur", "state": "Rajasthan"}


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    def manager(self, text_results=None, profile_results=None, resolver=None):
        store = InMemorySessionStore()
        text_matcher = AsyncMock(side_effect=text_results or [])
        profile_matcher = AsyncMock(side_effect=profile_results or [])
        manager = ConversationManager(store, text_matcher, profile_matcher, resolver or FailedResolver())
        return manager, store, text_matcher, profile_matcher

    async def test_complete_first_utterance_speaks_structured_eligible_results(self):
        profile = {
            "age": 28, "gender": "F", "social_category": "SC", "state": "Rajasthan",
            "income_annual": 120000, "activity": "dairy", "activity_category": "agriculture",
            "existing_business": False, "project_cost": 200000, "loan_required": 120000,
        }
        manager, store, _, _ = self.manager([
            result("OK", profile, eligible=[eligible_scheme()])
        ])

        decision = await manager.handle_turn("CA1", "en", "en-IN", speech="complete profile")

        self.assertEqual(decision.action, "complete")
        self.assertIn("Kisan Credit Card Scheme", decision.prompt)
        self.assertIn("2,905 rupees", decision.prompt)
        self.assertIsNone(store.get("CA1"))

    async def test_partial_profile_asks_one_field_and_preserves_previous_values(self):
        partial = {"age": 28, "state": "Rajasthan", "activity": "dairy", "existing_business": False,
                   "income_annual": None}
        complete = {**partial, "income_annual": 200000}
        manager, store, text_matcher, profile_matcher = self.manager(
            [result("NEEDS_MORE_INFO", partial, ["income_annual", "gender"]),
             result("NEEDS_MORE_INFO", complete, ["gender"])],
            [result("OK", complete, eligible=[eligible_scheme()])],
        )

        first = await manager.handle_turn("CA2", "en", "en-IN", speech="initial details")
        self.assertEqual(first.field, "gender")
        self.assertNotIn("income annual, gender", first.prompt)

        session = store.get("CA2")
        session.current_field = "income_annual"
        session.current_input = "speech"
        store.save(session)
        second = await manager.handle_turn("CA2", "en", "en-IN", speech="Two lakh", confidence="0.5")
        self.assertEqual(second.action, "complete")
        sent_profile = profile_matcher.await_args.kwargs["profile"]
        self.assertEqual(sent_profile["age"], 28)
        self.assertEqual(sent_profile["state"], "Rajasthan")
        self.assertEqual(profile_matcher.await_args.kwargs["profile"]["income_annual"], 200000)

    async def test_amount_followup_is_contextualized_as_annual_income(self):
        updated = {"activity": "dairy", "income_annual": 200000}
        manager, store, text_matcher, profile_matcher = self.manager(
            profile_results=[result("NEEDS_MORE_INFO", updated, ["project_cost"])]
        )
        session = manager.start_session("CA3", "en", "en-IN")
        session.profile = {"activity": "dairy", "income_annual": None}
        session.current_field = "income_annual"
        store.save(session)

        decision = await manager.handle_turn("CA3", "en", "en-IN", speech="Two lakh")

        self.assertEqual(decision.action, "ask")
        self.assertEqual(profile_matcher.await_args.kwargs["profile"]["income_annual"], 200000)
        text_matcher.assert_not_awaited()

    async def test_existing_business_dtmf_two_sets_false_and_reruns_dre(self):
        profile = {"activity": "dairy", "existing_business": False, "project_cost": None}
        manager, store, _, profile_matcher = self.manager(
            profile_results=[result("NEEDS_MORE_INFO", profile, ["project_cost"])]
        )
        session = manager.start_session("CA4", "en", "en-IN")
        session.profile = {"activity": "dairy", "existing_business": None}
        session.current_field = "existing_business"
        session.current_input = "dtmf"
        store.save(session)

        decision = await manager.handle_turn("CA4", "en", "en-IN", digits="2")

        self.assertFalse(profile_matcher.await_args.kwargs["profile"]["existing_business"])
        self.assertEqual(decision.field, "project_cost")

    async def test_empty_speech_reasks_same_question_without_ending_call(self):
        manager, store, text_matcher, _ = self.manager()
        session = manager.start_session("CA5", "en", "en-IN")
        session.current_field = "income_annual"
        store.save(session)

        decision = await manager.handle_turn("CA5", "en", "en-IN", speech="")

        self.assertEqual(decision.action, "ask")
        self.assertEqual(decision.field, "income_annual")
        self.assertEqual(decision.input_type, "numeric_dtmf")
        self.assertIn("keypad", decision.prompt)
        text_matcher.assert_not_awaited()
        self.assertIsNotNone(store.get("CA5"))

    async def test_unrecognized_activity_is_reasked_then_contextualized(self):
        missing_activity = {"age": 28, "activity": None}
        dairy = {"age": 28, "activity": "dairy", "activity_category": "agriculture", "project_cost": None}
        manager, _, text_matcher, _ = self.manager([
            result("NEEDS_MORE_INFO", missing_activity, ["activity"]),
            result("NEEDS_MORE_INFO", dairy, ["project_cost"]),
        ])

        first = await manager.handle_turn("CA6", "en", "en-IN", speech="I am a forward")
        self.assertEqual(first.field, "activity")
        second = await manager.handle_turn("CA6", "en", "en-IN", speech="Dairy farming")
        self.assertEqual(second.field, "project_cost")
        self.assertEqual(text_matcher.await_args.kwargs["text"], "My business activity is Dairy farming.")

    async def test_explicit_correction_carries_old_profile_and_accepts_new_value(self):
        corrected = {"income_annual": 250000, "activity": "dairy", "project_cost": None}
        manager, store, text_matcher, _ = self.manager([
            result("NEEDS_MORE_INFO", corrected, ["project_cost"])
        ])
        session = manager.start_session("CA7", "en", "en-IN")
        session.profile = {"income_annual": 200000, "activity": "dairy"}
        store.save(session)

        decision = await manager.handle_turn(
            "CA7", "en", "en-IN", speech="Actually my annual income is two lakh fifty thousand"
        )

        self.assertEqual(decision.field, "project_cost")
        self.assertEqual(text_matcher.await_args.kwargs["profile"]["income_annual"], 200000)
        self.assertEqual(store.get("CA7").profile["income_annual"], 250000)

    async def test_hinglish_partial_input_continues_instead_of_crashing(self):
        profile = {"state": "Rajasthan", "activity": "dairy", "loan_required": 200000, "age": None}
        manager, store, _, _ = self.manager([
            result("NEEDS_MORE_INFO", profile, ["age", "project_cost"])
        ])

        decision = await manager.handle_turn(
            "CA8", "hi", "hi-IN", speech="Main Rajasthan se hoon aur dairy ke liye do lakh chahiye"
        )

        self.assertEqual(decision.action, "ask")
        self.assertEqual(decision.field, "age")
        self.assertEqual(store.get("CA8").profile["activity"], "dairy")

    async def test_failed_pin_lookup_falls_back_to_state_speech(self):
        manager, store, _, _ = self.manager(resolver=FailedResolver())
        session = manager.start_session("CA9", "en", "en-IN")
        session.current_field = "state"
        session.current_input = "pincode"
        store.save(session)

        decision = await manager.handle_turn("CA9", "en", "en-IN", digits="000000")

        self.assertEqual(decision.action, "ask")
        self.assertEqual(decision.input_type, "speech")
        self.assertIn("could not verify", decision.prompt)
        self.assertTrue(store.get("CA9").location_speech_fallback)

    async def test_successful_pin_lookup_updates_state_and_district(self):
        located = {"state": "Rajasthan", "district": "Jaipur", "activity": "dairy", "project_cost": None}
        manager, store, _, profile_matcher = self.manager(
            profile_results=[result("NEEDS_MORE_INFO", located, ["project_cost"])],
            resolver=SuccessfulResolver(),
        )
        session = manager.start_session("CAPIN", "en", "en-IN")
        session.current_field = "state"
        session.current_input = "pincode"
        store.save(session)

        decision = await manager.handle_turn("CAPIN", "en", "en-IN", digits="302001")

        sent = profile_matcher.await_args.kwargs["profile"]
        self.assertEqual(sent["state"], "Rajasthan")
        self.assertEqual(sent["district"], "Jaipur")
        self.assertEqual(store.get("CAPIN").pincode, "302001")
        self.assertEqual(decision.field, "project_cost")

    async def test_repeated_invalid_dtmf_terminates_without_looping(self):
        manager, store, _, profile_matcher = self.manager()
        session = manager.start_session("CADTMF", "en", "en-IN")
        session.current_field = "gender"
        session.current_input = "dtmf"
        store.save(session)

        first = await manager.handle_turn("CADTMF", "en", "en-IN", digits="9")
        second = await manager.handle_turn("CADTMF", "en", "en-IN", digits="9")
        third = await manager.handle_turn("CADTMF", "en", "en-IN", digits="9")

        self.assertEqual(first.action, "ask")
        self.assertEqual(second.action, "ask")
        self.assertEqual(third.action, "terminate")
        self.assertIsNone(store.get("CADTMF"))
        profile_matcher.assert_not_awaited()

    async def test_malformed_dre_response_is_rejected(self):
        manager, _, _, _ = self.manager([{"status": "OK", "missing_fields": []}])
        with self.assertRaises(DREProtocolError):
            await manager.handle_turn("CABAD", "en", "en-IN", speech="dairy")

    async def test_low_confidence_reasks_without_calling_dre(self):
        manager, store, text_matcher, _ = self.manager()
        session = manager.start_session("CA10", "en", "en-IN")
        session.current_field = "activity"
        store.save(session)

        decision = await manager.handle_turn("CA10", "en", "en-IN", speech="forward", confidence="0.1")

        self.assertEqual(decision.field, "activity")
        text_matcher.assert_not_awaited()

    async def test_unambiguous_numeric_speech_beats_low_confidence_without_semantic_call(self):
        cases = [
            ("age", "28", "0.12", 28),
            ("income_annual", "120000 rupees", "0.18", 120000),
            ("income_annual", "120000", "0.13", 120000),
            ("project_cost", "2 lakh", "0.10", 200000),
            ("income_annual", "one lakh twenty thousand", "0.10", 120000),
        ]
        for index, (field, speech, confidence, expected) in enumerate(cases):
            profile = {field: expected, "gender": None}
            manager, store, text_matcher, profile_matcher = self.manager(
                profile_results=[result("NEEDS_MORE_INFO", profile, ["gender"])]
            )
            call_sid = f"CANUM{index}"
            session = manager.start_session(call_sid, "en", "en-IN")
            session.current_field = field
            session.current_fields = [field]
            store.save(session)

            decision = await manager.handle_turn(
                call_sid, "en", "en-IN", speech=speech, confidence=confidence
            )

            self.assertEqual(profile_matcher.await_args.kwargs["profile"][field], expected)
            self.assertEqual(decision.field, "gender")
            self.assertNotEqual(decision.action, "confirm")
            text_matcher.assert_not_awaited()

    async def test_ambiguous_numeric_speech_switches_to_variable_length_dtmf(self):
        manager, store, text_matcher, profile_matcher = self.manager()
        session = manager.start_session("CAAMB", "en", "en-IN")
        session.current_field = "income_annual"
        session.current_fields = ["income_annual"]
        store.save(session)

        decision = await manager.handle_turn(
            "CAAMB", "en", "en-IN", speech="100000 20000", confidence="0.48"
        )

        self.assertEqual(decision.action, "ask")
        self.assertEqual(decision.input_type, "numeric_dtmf")
        self.assertIn("keypad", decision.prompt)
        self.assertEqual(store.get("CAAMB").retry_count, 1)
        text_matcher.assert_not_awaited()
        profile_matcher.assert_not_awaited()

    async def test_numeric_dtmf_updates_profile_and_reruns_dre(self):
        updated = {"income_annual": 120000, "gender": None}
        manager, store, text_matcher, profile_matcher = self.manager(
            profile_results=[result("NEEDS_MORE_INFO", updated, ["gender"])]
        )
        session = manager.start_session("CADIGITS", "en", "en-IN")
        session.current_field = "income_annual"
        session.current_fields = ["income_annual"]
        session.current_input = "numeric_dtmf"
        session.retry_count = 1
        store.save(session)

        decision = await manager.handle_turn("CADIGITS", "en", "en-IN", digits="120000#")

        self.assertEqual(profile_matcher.await_args.kwargs["profile"]["income_annual"], 120000)
        self.assertEqual(decision.field, "gender")
        text_matcher.assert_not_awaited()

    def test_numeric_parser_supports_hindi_and_rejects_multiple_values(self):
        self.assertEqual(parse_numeric_answer("income_annual", "एक लाख बीस हजार"), 120000)
        self.assertIsNone(parse_numeric_answer("income_annual", "100000 20000"))
        self.assertIsNone(parse_numeric_answer("income_annual", "-120000"))
        self.assertEqual(parse_numeric_dtmf("income_annual", "120000#"), 120000)

    def test_contextualizer_does_not_change_the_answer_value(self):
        self.assertEqual(contextualize_answer("project_cost", "two lakh"), "My project cost is two lakh.")

    def test_recommendations_exclude_noneligible_items(self):
        eligible = eligible_scheme()
        ineligible = eligible_scheme("Ineligible Scheme", 100)
        ineligible["eligibility"]["status"] = "NOT_ELIGIBLE"
        spoken = build_spoken_recommendation({"eligible_schemes": [ineligible, eligible]})
        self.assertNotIn("Ineligible Scheme", spoken)
        self.assertIn("Kisan Credit Card Scheme", spoken)
