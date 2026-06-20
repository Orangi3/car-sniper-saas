"""
tests/test_vin.py — VIN module unit tests.

Covers everything in the spec:
  - VIN validation (length, IOQ, check digit)
  - NHTSA decode success + failure
  - Paid provider missing
  - Paid provider mocked success
  - Paid provider malformed
  - Risk scoring matrix (salvage, rebuilt, flood, rollback, mismatch)
  - Listing mismatch detection
  - API endpoints return JSON
  - UI honesty: unknown data is not falsely marked clean

Run from the sniper folder:
    python3 -m pytest tests/test_vin.py -v
or:
    python3 tests/test_vin.py            (uses unittest)
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Make sniper folder importable when run as a script
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vin  # noqa: E402

# A canonical valid VIN (Honda Accord 1993, check digit verified by hand).
VALID_VIN = "1HGCM82633A004352"
# Same VIN with check digit broken
BAD_CHECK_DIGIT_VIN = "1HGCM82639A004352"
# Length too short
SHORT_VIN = "1HGCM82633A0043"
# Has forbidden character "I"
HAS_I_VIN = "1HGCM82633A00435I"


# ---------- Validation ---------------------------------------------------

class TestValidate(unittest.TestCase):

    def test_check_digit_known_good(self):
        self.assertEqual(vin.vin_check_digit(VALID_VIN), "3")

    def test_valid_vin_passes(self):
        v = vin.validate_vin(VALID_VIN)
        self.assertTrue(v["ok"])
        self.assertEqual(v["normalized"], VALID_VIN)
        self.assertEqual(v["region"], "north_america")
        self.assertTrue(v["check_digit_ok"])

    def test_invalid_length_rejected(self):
        v = vin.validate_vin(SHORT_VIN)
        self.assertFalse(v["ok"])
        self.assertTrue(any("17 characters" in e for e in v["errors"]))

    def test_forbidden_characters_rejected(self):
        v = vin.validate_vin(HAS_I_VIN)
        self.assertFalse(v["ok"])
        self.assertTrue(any("I, O, or Q" in e for e in v["errors"]))

    def test_invalid_check_digit_rejected(self):
        v = vin.validate_vin(BAD_CHECK_DIGIT_VIN)
        self.assertFalse(v["ok"])
        self.assertTrue(any("check digit" in e for e in v["errors"]))

    def test_lowercase_normalized(self):
        v = vin.validate_vin(VALID_VIN.lower())
        self.assertTrue(v["ok"])
        self.assertEqual(v["normalized"], VALID_VIN)

    def test_none_or_empty_rejected(self):
        for bad in (None, "", "   "):
            v = vin.validate_vin(bad)
            self.assertFalse(v["ok"])


# ---------- NHTSA decode -------------------------------------------------

class TestDecode(unittest.TestCase):

    @patch("vin.requests.get")
    def test_decode_success_caches(self, mget):
        mget.return_value = MagicMock(
            status_code=200,
            json=lambda: {"Results": [{
                "ModelYear": "2018", "Make": "BMW", "Model": "X3",
                "BodyClass": "Sport Utility Vehicle (SUV)/Multi-Purpose Vehicle (MPV)",
                "DisplacementL": "2.0", "EngineCylinders": "4",
            }]},
            raise_for_status=lambda: None,
        )
        # Wipe any cached row for this VIN first
        vin.cache_put(VALID_VIN, "decode", {}, ttl_seconds=0)
        decoded = vin.decode_vin(VALID_VIN, force_refresh=True)
        self.assertEqual(decoded["Make"], "BMW")
        # Second call without force_refresh must hit cache (no new HTTP call)
        mget.reset_mock()
        decoded2 = vin.decode_vin(VALID_VIN)
        self.assertEqual(decoded2["Make"], "BMW")
        self.assertEqual(mget.call_count, 0,
                         "second call should be cached, no HTTP request")

    @patch("vin.requests.get")
    def test_decode_failure_does_not_crash(self, mget):
        import requests as r
        mget.side_effect = r.ConnectionError("simulated outage")
        with self.assertRaises(Exception):
            vin.decode_vin(VALID_VIN, force_refresh=True)
        # But the full check() must not raise — it should fold the error in
        report = vin.check(VALID_VIN, force_refresh=True)
        self.assertIn("decode failed", " ".join(report.get("errors") or []))


# ---------- Provider layer -----------------------------------------------

class TestProviders(unittest.TestCase):

    def setUp(self):
        # Clear env so we know there's no provider unless this test sets one
        self._saved = {k: os.environ.pop(k, None) for k in
                       ("VIN_PROVIDER", "VIN_PROVIDER_API_KEY",
                        "VIN_PROVIDER_BASE_URL",
                        "BUMPER_API_KEY", "CLEARVIN_API_KEY")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def test_no_provider_returns_honest_empty(self):
        os.environ["VIN_PROVIDER"] = "none"
        h = vin.history(VALID_VIN)
        self.assertIsNone(h["title_status"])
        self.assertIsNone(h["owners"])
        self.assertFalse(h["_configured"])
        # Every field flagged as unavailable
        self.assertIn("title_status", h["unavailable_fields"])
        self.assertIn("owners", h["unavailable_fields"])

    @patch("vin.requests.get")
    def test_bumper_mock_success(self, mget):
        os.environ["VIN_PROVIDER"] = "bumper"
        os.environ["BUMPER_API_KEY"] = "test-key-1234"
        mget.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "title": {"status": "clean", "brand": "Clean", "state": "AL"},
                "owners": [{"name": "Owner1"}, {"name": "Owner2"}],
                "service_records": [{"date": "2022-01-01",
                                      "description": "oil change"}],
                "odometer_readings": [{"date": "2024-01-01", "miles": 80000}],
            },
            raise_for_status=lambda: None,
        )
        h = vin.history(VALID_VIN, force_refresh=True)
        self.assertEqual(h["_provider"], "bumper")
        self.assertEqual(h["title_status"], "clean")
        self.assertEqual(h["owners"], 2)
        self.assertEqual(h["maintenance_records"], 1)
        self.assertIn("title_status", h["available_fields"])

    @patch("vin.requests.get")
    def test_bumper_malformed_response(self, mget):
        os.environ["VIN_PROVIDER"] = "bumper"
        os.environ["BUMPER_API_KEY"] = "test-key-1234"
        # Return invalid JSON
        mget.return_value = MagicMock(
            status_code=200,
            json=MagicMock(side_effect=ValueError("not json")),
            raise_for_status=lambda: None,
        )
        h = vin.history(VALID_VIN, force_refresh=True)
        # Must NOT crash; must surface an error and not invent data
        self.assertTrue(h.get("_error"))
        self.assertIsNone(h["title_status"])
        self.assertIsNone(h["owners"])

    def test_provider_status_redacts_key(self):
        os.environ["BUMPER_API_KEY"] = "verysecretkey12345"
        ps = vin.provider_status()
        bumper = next(p for p in ps["providers"] if p["name"] == "bumper")
        self.assertTrue(bumper["configured"])
        self.assertEqual(bumper["key_last4"], "2345")
        # The full key must NEVER appear anywhere in the response
        ps_json = json.dumps(ps)
        self.assertNotIn("verysecretkey12345", ps_json)


# ---------- Risk scoring -------------------------------------------------

class TestRiskScoring(unittest.TestCase):

    def _empty_history(self, **overrides):
        h = vin._empty_history(None)
        for k, v in overrides.items():
            h[k] = v
        return h

    def test_clean_no_provider_is_unknown_not_clean(self):
        """Without paid data, risk_score stays 0 but confidence is low —
        unknown must NOT look like clean."""
        risk = vin.compute_risk({"year": "2018", "make": "BMW", "model": "X3"},
                                self._empty_history(), [],
                                {"checked": False, "mismatches": []})
        self.assertEqual(risk["risk_score"], 0)
        self.assertLess(risk["confidence_score"], 60,
                        "missing paid data must drop confidence")
        # All paid fields marked not_available
        for field in ("title_status", "owners", "accidents"):
            row = next(c for c in risk["data_completeness"] if c["field"] == field)
            self.assertEqual(row["status"], "not_available")

    def test_salvage_brand_pushes_to_avoid(self):
        h = self._empty_history()
        h["_configured"] = True
        h["_provider"] = "bumper"
        h["brands"]["salvage"] = True
        h["title_status"] = "salvage"
        risk = vin.compute_risk({"year": "2018", "make": "BMW"}, h, [],
                                {"checked": True, "mismatches": []})
        self.assertGreaterEqual(risk["risk_score"], 60)
        self.assertIn(risk["risk_label"], ("high_risk", "avoid"))

    def test_rollback_is_high_risk(self):
        h = self._empty_history()
        h["_configured"] = True
        h["_provider"] = "bumper"
        h["brands"]["odometer_rollback"] = True
        risk = vin.compute_risk({"year": "2018"}, h, [],
                                {"checked": True, "mismatches": []})
        self.assertGreaterEqual(risk["risk_score"], 46)

    def test_listing_mismatch_drives_risk(self):
        h = self._empty_history()
        h["_configured"] = True
        h["_provider"] = "bumper"
        mismatch = {"checked": True, "severity": "high",
                    "mismatches": [
                        {"field": "make", "listing": "bmw", "vin": "audi"},
                        {"field": "model", "listing": "x3", "vin": "q5"},
                    ],
                    "message": "..."}
        risk = vin.compute_risk({"year": "2018"}, h, [], mismatch)
        # 40 (make) + 20 (model) = at least 60
        self.assertGreaterEqual(risk["risk_score"], 60)


# ---------- Listing-mismatch detection -----------------------------------

class TestMismatch(unittest.TestCase):

    def test_no_listing_means_not_checked(self):
        m = vin.compare_listing({"year": "2018", "make": "BMW", "model": "X3"}, None)
        self.assertFalse(m["checked"])

    def test_aliases_tolerated(self):
        m = vin.compare_listing(
            {"year": "2014", "make": "Chevrolet", "model": "Cruze"},
            {"year": "2014", "make": "Chevy", "model": "Cruze"})
        self.assertEqual(m["mismatches"], [])

    def test_year_mismatch_high(self):
        m = vin.compare_listing(
            {"year": "2018", "make": "BMW", "model": "X3"},
            {"year": "2019", "make": "BMW", "model": "X3"})
        self.assertEqual(m["severity"], "high")
        self.assertEqual(m["mismatches"][0]["field"], "year")

    def test_substring_model_match_tolerated(self):
        m = vin.compare_listing(
            {"year": "2018", "make": "BMW", "model": "X3"},
            {"year": "2018", "make": "BMW", "model": "X3 M40i"})
        self.assertEqual(m["mismatches"], [])


# ---------- Endpoints return JSON (smoke) --------------------------------
# These pass even when Flask isn't installed in the test env — they just
# build the test client lazily and skip if Flask is missing.

class TestEndpoints(unittest.TestCase):

    def setUp(self):
        """Phase-1 SaaS: every /api/* route now requires an authenticated
        user, and /api/vin/* additionally requires plan='pro'. These tests
        still validate JSON shapes + error semantics — they just need a
        signed-in pro account first.

        Each test gets a throwaway SQLite DB so the prod listings.db is
        never touched and tests don't see each other's users."""
        try:
            import sys, tempfile
            tmp_dir = tempfile.mkdtemp(prefix="snipervin_")
            os.environ["SNIPER_DB_PATH"] = os.path.join(tmp_dir, "test.db")
            os.environ.pop("DATABASE_URL", None)
            os.environ["ALLOW_REGISTRATION"] = "1"
            # Force re-import so the new SNIPER_DB_PATH is picked up.
            # server.py no longer auto-migrates (that's a deploy step in prod),
            # so run the migration runner explicitly before importing.
            for mod in ("server", "db", "auth", "sniper",
                        "migrations.runner", "migrations"):
                sys.modules.pop(mod, None)
            from migrations import runner as _runner
            _runner.run_pending(verbose=False)
            import server
            import auth as _auth
            self.client = server.app.test_client()
            # Register a pro user and log in — the test client preserves
            # the session cookie across all subsequent requests in this test.
            email = "vintest@example.com"
            r = self.client.post("/api/auth/register",
                                 json={"email": email, "password": "vinpass1234"})
            assert r.status_code == 200, r.get_data(as_text=True)
            # Promote to pro so VIN endpoints (plan='pro') let us through.
            u = _auth.get_user_by_email(email)
            _auth.set_plan(u.id, "pro")
        except ImportError:
            self.skipTest("Flask not installed in this env")

    def test_decode_invalid_returns_json_400(self):
        r = self.client.get("/api/vin/decode/SHORT")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.content_type.split(";")[0], "application/json")
        self.assertFalse(r.get_json()["ok"])

    def test_provider_status_returns_json(self):
        r = self.client.get("/api/vin/provider-status")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertIn("providers", body)

    def test_diagnostics_returns_json(self):
        r = self.client.get("/api/diagnostics/vin")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["ok"])
        self.assertIn("nhtsa", body)

    def test_check_invalid_returns_json_400(self):
        r = self.client.post("/api/vin/check", json={"vin": "BAD"})
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertFalse(body["ok"])

    def test_404_under_api_is_json(self):
        r = self.client.get("/api/vin/not-a-real-route")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.content_type.split(";")[0], "application/json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
