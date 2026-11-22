"""Storage: credentials loading, no silent in-memory fallback, friendly failures, ignoring foreign documents.

Nothing here touches a real database: credentials are passed in explicitly and Firestore is faked.
"""

import base64
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import FORM, REAL_MAKE_STORE, appmod, fresh
from leaddesk import store as st
from leaddesk.store import Append, FirestoreStore, LeadGone, MemoryStore, StorageError, UnconfiguredStore

INFO = {"type": "service_account", "project_id": "demo-project", "client_email": "svc@demo-project.iam.example",
        "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"}


def b64(obj):
    return base64.b64encode(json.dumps(obj).encode()).decode()


class CredentialTests(unittest.TestCase):
    def test_base64_json_and_older_name_are_all_accepted(self):
        for env in ({"FIREBASE_CREDENTIALS_BASE64": b64(INFO)}, {"FIREBASE_CREDENTIALS": json.dumps(INFO)},
                    {"FIREBASE_SERVICE_ACCOUNT": json.dumps(INFO)}):
            info = st.load_credentials(env, cred_file=Path("does-not-exist.json"))
            self.assertEqual(info["project_id"], "demo-project", list(env))

    def test_base64_wins_and_a_broken_source_falls_through_to_the_next(self):
        other = {**INFO, "project_id": "from-json"}
        env = {"FIREBASE_CREDENTIALS_BASE64": b64(INFO), "FIREBASE_CREDENTIALS": json.dumps(other)}
        self.assertEqual(st.load_credentials(env, cred_file=Path("x"))["project_id"], "demo-project")
        env = {"FIREBASE_CREDENTIALS_BASE64": "%%% not base64 %%%", "FIREBASE_CREDENTIALS": json.dumps(other)}
        self.assertEqual(st.load_credentials(env, cred_file=Path("x"))["project_id"], "from-json")

    def test_file_source(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "firebase-service-account.json"
            f.write_text(json.dumps(INFO), encoding="utf-8")
            self.assertEqual(st.load_credentials({}, cred_file=f)["client_email"], INFO["client_email"])

    def test_incomplete_or_missing_credentials_give_none(self):
        self.assertIsNone(st.load_credentials({}, cred_file=Path("x")))
        self.assertIsNone(st.load_credentials({"FIREBASE_CREDENTIALS": json.dumps({"project_id": "p"})}, cred_file=Path("x")))
        self.assertIsNone(st.load_credentials({"FIREBASE_CREDENTIALS": "{broken"}, cred_file=Path("x")))

    def test_escaped_newlines_in_the_private_key_are_repaired(self):
        info = {**INFO, "private_key": "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----\\n"}
        key = st.load_credentials({"FIREBASE_CREDENTIALS": json.dumps(info)}, cred_file=Path("x"))["private_key"]
        self.assertIn("\nabc\n", key)

    def test_secrets_are_never_logged(self):
        with self.assertLogs("leaddesk.store", level="ERROR") as logs:
            st.load_credentials({"FIREBASE_CREDENTIALS": '{"private_key": "SECRET-VALUE-123", "client_email": "x"}'}, cred_file=Path("x"))
        self.assertNotIn("SECRET-VALUE-123", "\n".join(logs.output))


class MakeStoreTests(unittest.TestCase):
    def make(self, env):
        scrubbed = {k: v for k, v in os.environ.items() if not k.startswith("FIREBASE_") and k != "STORAGE"}
        with mock.patch.dict(os.environ, {**scrubbed, **env}, clear=True), \
                mock.patch.object(st, "CRED_FILE", Path("does-not-exist.json")):
            return REAL_MAKE_STORE()

    def test_no_credentials_means_no_storage_not_memory(self):
        s = self.make({})
        self.assertIsInstance(s, UnconfiguredStore)
        self.assertFalse(s.ready)

    def test_memory_is_opt_in(self):
        self.assertIsInstance(self.make({"STORAGE": "memory"}), MemoryStore)

    def test_credentials_give_firestore(self):
        sentinel = object()
        with mock.patch.object(st, "FirestoreStore", return_value=sentinel) as cls:
            self.assertIs(self.make({"FIREBASE_CREDENTIALS_BASE64": b64(INFO)}), sentinel)
        self.assertEqual(cls.call_args[0][0]["project_id"], "demo-project")

    def test_a_failing_firestore_start_does_not_crash_the_app(self):
        with mock.patch.object(st, "FirestoreStore", side_effect=RuntimeError("bad key")):
            self.assertIsInstance(self.make({"FIREBASE_CREDENTIALS_BASE64": b64(INFO)}), UnconfiguredStore)

    def test_default_collection_is_the_apps_own(self):
        self.assertEqual(st.COLLECTION, "leaddesk_leads")


class FakeDoc:
    def __init__(self, data):
        self._d = data
        self.exists = data is not None

    def to_dict(self):
        return self._d


class FakeCollection:
    def __init__(self, fail=False):
        self.docs, self.fail, self.streams = {}, fail, 0

    def _check(self):
        if self.fail:
            raise ConnectionError("firestore unreachable: secret-project-details")

    def stream(self):
        self._check()
        self.streams += 1
        return [FakeDoc(d) for d in self.docs.values()]

    def limit(self, n):
        return self

    def document(self, key):
        col = self

        class Ref:
            def get(self_inner):
                col._check()
                return FakeDoc(col.docs.get(key))

            def set(self_inner, data):
                col._check()
                col.docs[key] = data

            def delete(self_inner):
                col._check()
                col.docs.pop(key, None)

            def update(self_inner, payload):
                from google.api_core.exceptions import NotFound
                col._check()
                if key not in col.docs:
                    raise NotFound("no document")
                for field, value in payload.items():
                    if hasattr(value, "values"):                 # ArrayUnion
                        col.docs[key][field] = col.docs[key].get(field, []) + list(value.values)
                    else:
                        node = col.docs[key]
                        parts = field.split(".")
                        for part in parts[:-1]:
                            node = node.setdefault(part, {})
                        node[parts[-1]] = value
        return Ref()


def firestore_with(col, cache=None, meta=None):
    s = FirestoreStore.__new__(FirestoreStore)   # skip the real connection
    s._col, s._cache = col, cache or FakeCollection()
    s._meta = (meta or FakeCollection()).document("leads_version")
    s._listing = None
    s._reload_lock = threading.Lock()
    return s


class FirestoreStoreTests(unittest.TestCase):
    def test_roundtrip(self):
        s = firestore_with(FakeCollection())
        s.save({"id": "a", "x": 1})
        self.assertEqual(s.get("a"), {"id": "a", "x": 1})
        self.assertEqual(s.all(), [{"id": "a", "x": 1}])
        s.delete("a")
        self.assertIsNone(s.get("a"))
        self.assertTrue(s.ping())

    def test_database_failures_become_a_friendly_error_with_details_only_in_logs(self):
        s = firestore_with(FakeCollection(fail=True))
        for call in (s.all, lambda: s.get("a"), lambda: s.save({"id": "a"}), lambda: s.delete("a"), s.ping):
            with self.assertLogs("leaddesk.store", level="ERROR") as logs:
                with self.assertRaises(StorageError) as cm:
                    call()
            self.assertNotIn("secret-project-details", str(cm.exception))
            self.assertIn("ConnectionError", "\n".join(logs.output))

    def test_cache_entries_expire(self):
        s = firestore_with(FakeCollection())
        s.cache_set("k", '{"a": 1}')
        self.assertEqual(s.cache_get("k"), '{"a": 1}')
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        s._cache.docs["k"]["at"] = old
        self.assertIsNone(s.cache_get("k"))


class ListCacheTests(unittest.TestCase):
    """The lead list is only re-read from the database when something changed (cost doesn't grow with polling)."""

    def setUp(self):
        self.col, self.meta = FakeCollection(), FakeCollection()
        self.s = firestore_with(self.col, meta=self.meta)

    def test_an_unchanged_list_is_served_from_memory(self):
        self.s.save({"id": "a", "n": 1})
        self.assertEqual(len(self.s.all()), 1)
        streams = self.col.streams
        for _ in range(5):
            self.assertEqual(len(self.s.all()), 1)
        self.assertEqual(self.col.streams, streams, "no further full reads")

    def test_every_kind_of_write_invalidates_it(self):
        self.s.save({"id": "a", "n": 1})
        self.s.all()
        self.s.update("a", {"n": 2})
        self.assertEqual(self.s.all()[0]["n"], 2)
        self.s.save({"id": "b", "n": 3})
        self.assertEqual(len(self.s.all()), 2)
        self.s.delete("a")
        self.assertEqual([d["id"] for d in self.s.all()], ["b"])

    def test_a_write_by_another_server_instance_is_noticed(self):
        other = firestore_with(self.col, meta=self.meta)    # same database, separate memory
        self.s.save({"id": "a"})
        self.assertEqual(len(self.s.all()), 1)
        other.save({"id": "b"})
        self.assertEqual({d["id"] for d in self.s.all()}, {"a", "b"})

    def test_the_cache_expires_even_if_a_version_bump_was_missed(self):
        self.s.save({"id": "a"})
        self.s.all()
        self.col.docs["b"] = {"id": "b"}                    # written without bumping the version
        self.assertEqual(len(self.s.all()), 1)
        with mock.patch.object(st.time, "time", return_value=st.time.time() + st.LIST_CACHE_MAX_AGE_S + 1):
            self.assertEqual(len(self.s.all()), 2)

    def test_many_requests_missing_at_once_cause_a_single_read(self):
        for i in range(20):
            self.col.docs[f"l{i}"] = {"id": f"l{i}"}
        slow = self.col.stream

        def slow_stream():
            time.sleep(0.15)
            return slow()
        self.col.stream = slow_stream
        sizes = []
        threads = [threading.Thread(target=lambda: sizes.append(len(self.s.all()))) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sizes, [20] * 8)
        self.assertEqual(self.col.streams, 1, "one full read, not eight")

    def test_callers_get_independent_copies(self):
        self.s.save({"id": "a", "tags": ["x"]})
        first = self.s.all()
        first[0]["tags"].append("changed")
        self.assertEqual(self.s.all()[0]["tags"], ["x"])

    def test_missing_version_document_is_created(self):
        self.col.docs["a"] = {"id": "a"}                    # data from before versioning existed
        self.assertEqual(len(self.s.all()), 1)
        self.assertTrue(self.meta.docs, "version document was created")
        streams = self.col.streams
        self.s.all()
        self.assertEqual(self.col.streams, streams)

    def test_if_the_version_cannot_be_read_the_full_list_is_still_returned(self):
        self.col.docs["a"] = {"id": "a"}
        s = firestore_with(self.col, meta=FakeCollection(fail=True))
        self.assertEqual(len(s.all()), 1)
        s.save({"id": "b"})                                   # the write works even if the version can't be bumped
        self.assertEqual(len(s.all()), 2)


class PartialUpdateTests(unittest.TestCase):
    """update() writes only the fields it is given, so two people editing different parts of a lead don't clash."""

    def stores(self):
        return [MemoryStore(), firestore_with(FakeCollection())]

    def test_only_the_given_fields_change(self):
        for s in self.stores():
            s.save({"id": "a", "stage": "new", "form": {"name": "N", "phone": ""}, "chat": []})
            s.update("a", {"stage": "booked"})
            self.assertEqual(s.get("a"), {"id": "a", "stage": "booked", "form": {"name": "N", "phone": ""}, "chat": []}, s.mode)

    def test_dotted_paths_update_one_nested_field(self):
        for s in self.stores():
            s.save({"id": "a", "form": {"name": "N", "phone": ""}})
            s.update("a", {"form.phone": "98765"})
            self.assertEqual(s.get("a")["form"], {"name": "N", "phone": "98765"}, s.mode)

    def test_append_adds_to_the_stored_list(self):
        for s in self.stores():
            s.save({"id": "a", "chat": [{"m": 1}]})
            s.update("a", {"chat": Append([{"m": 2}])})
            s.update("a", {"chat": Append([{"m": 3}])})
            self.assertEqual([c["m"] for c in s.get("a")["chat"]], [1, 2, 3], s.mode)

    def test_updating_a_missing_lead_raises_leadgone_not_a_storage_error(self):
        for s in self.stores():
            with self.assertRaises(LeadGone):
                s.update("nope", {"stage": "booked"})

    def test_api_returns_404_for_a_lead_deleted_mid_action(self):
        c, ai = fresh()
        lid = c.post("/api/leads", json=FORM).get_json()["lead"]["id"]
        original = appmod.store.update

        def vanished(lead_id, fields):
            appmod.store.delete(lead_id)
            return original(lead_id, fields)
        with mock.patch.object(appmod.store, "update", side_effect=vanished):
            r = c.post(f"/api/leads/{lid}/stage", json={"stage": "booked"})
        self.assertEqual(r.status_code, 404)


class ApiStorageTests(unittest.TestCase):
    def setUp(self):
        self.c, self.ai = fresh()

    def test_unconfigured_storage_gives_friendly_503s_and_no_ai_calls(self):
        appmod.store = UnconfiguredStore()
        appmod._storage_check["at"] = 0
        for r in (self.c.get("/api/leads"), self.c.post("/api/leads", json=FORM), self.c.get("/api/leads/x")):
            self.assertEqual(r.status_code, 503)
            body = r.get_json()
            self.assertTrue(body["retryable"])
            self.assertNotRegex(body["error"].lower(), r"firebase|firestore|credential|key")
        self.assertEqual(self.ai.calls, [], "no AI quota is spent when the lead can't even be saved")
        self.assertFalse(self.c.get("/api/health").get_json()["storage_ready"])

    def test_health_reports_real_storage_status(self):
        self.assertTrue(self.c.get("/api/health").get_json()["storage_ready"])
        appmod.store = firestore_with(FakeCollection(fail=True))
        appmod._storage_check["at"] = 0
        self.assertFalse(self.c.get("/api/health").get_json()["storage_ready"])

    def test_storage_probe_is_not_run_on_every_health_call(self):
        appmod._storage_check["at"] = 0
        with mock.patch.object(appmod.store, "ping", wraps=appmod.store.ping) as ping:
            for _ in range(5):
                self.c.get("/api/health")
        self.assertEqual(ping.call_count, 1)

    def test_documents_from_other_tools_are_ignored_not_fatal(self):
        legacy = {"id": "L01", "name": "Old format", "message": "x", "score": 90, "battle_hook": "...", "stage": "new"}
        appmod.store.save(legacy)
        good = self.c.post("/api/leads", json=FORM).get_json()["lead"]
        r = self.c.get("/api/leads")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([l["id"] for l in r.get_json()["leads"]], [good["id"]])
        self.assertEqual(self.c.get("/api/leads/L01").status_code, 404)
        self.assertEqual(self.c.post("/api/leads/L01/chat", json={"message": "hi"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
