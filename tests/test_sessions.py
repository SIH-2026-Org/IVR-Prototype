import unittest

from session_store import InMemorySessionStore


class SessionStoreTests(unittest.TestCase):
    def test_preserves_session_by_call_sid_and_expires_it_after_ttl(self):
        now = [100.0]
        store = InMemorySessionStore(ttl_seconds=20, clock=lambda: now[0])
        session = store.get_or_create("CA123", "en", "en-IN")
        session.profile["age"] = 28
        store.save(session)

        now[0] = 119.0
        self.assertEqual(store.get("CA123").profile["age"], 28)

        now[0] = 140.0
        self.assertIsNone(store.get("CA123"))
        self.assertEqual(len(store), 0)

    def test_delete_removes_completed_call(self):
        store = InMemorySessionStore()
        store.get_or_create("CA456", "hi", "hi-IN")
        store.delete("CA456")
        self.assertIsNone(store.get("CA456"))
