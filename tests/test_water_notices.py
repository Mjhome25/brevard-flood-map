#!/usr/bin/env python3
"""Water-source boundaries and lifecycle checks; no network or production state writes."""
import copy
import datetime as dt
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import update_water_notices as W

NOW = dt.datetime(2026, 11, 3, 15, 0, tzinfo=dt.timezone.utc)
ISSUE = "A precautionary boil water notice has been issued for all City of Melbourne water customers, including City of West Melbourne water customers."
LIFT = "The precautionary boil water notice for all City of Melbourne water customers has been rescinded."
GEOM = {"type": "Polygon", "coordinates": [[[-80.7, 28.0], [-80.6, 28.0], [-80.6, 28.1], [-80.7, 28.0]]]}
RAW = json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": GEOM}]})


def page(statement, day="November 03, 2026"):
    return '<main id="main-content"><div class="col-m-8"><h1>Utility notice</h1><p>Published on ' + day + '</p><p>' + statement + '</p></div></main>'


class WaterTests(unittest.TestCase):
    def get(self, index="", extra=None):
        pages = {W.INDEX: index, W.UPDATE: page(ISSUE), W.NOTICE: page(ISSUE, "November 02, 2026"), W.BOUNDARY: RAW}
        pages.update(extra or {})
        return lambda u: pages[u]

    def test_real_scope_and_geometry_survive(self):
        data = W.refresh({}, self.get(), NOW)
        n = data["notices"][0]
        self.assertEqual((n["status"], n["geometry"]), ("active", GEOM))
        self.assertEqual(n["publishedDate"], "2026-11-03")
        self.assertEqual(n["checkedAt"], W.iso(NOW))

    def test_partial_older_negated_and_future_lifts_do_not_remove(self):
        old = W.refresh({}, self.get(), NOW)
        cases = ["The precautionary boil water notice for Coral Way has been rescinded.",
                 "The precautionary boil water notice for all City of Melbourne water customers has not been rescinded.",
                 "The precautionary boil water notice for all City of Melbourne water customers will be lifted tomorrow.",
                 "Exercise scenario: " + LIFT]
        url = W.INDEX + "/Rescission-Test"
        for text in cases:
            result = W.refresh(old, self.get('<a href="'+url+'">Update</a>', {url: page(text)}), NOW)
            self.assertEqual(result["notices"][0]["status"], "active", text)
        self.assertFalse(W.statements(page(LIFT, "November 04, 2026"), url, "2026-11-03"))
        self.assertFalse(W.statements(page(LIFT, "November 01, 2026"), url, "2026-11-03"))

    def test_full_system_lift_records_evidence_and_keeps_boundary(self):
        old = W.refresh({}, self.get(), NOW)
        url = W.INDEX + "/Rescission-All-Customers"
        new = W.refresh(old, self.get('<a href="'+url+'">Rescission</a><a href="'+W.UPDATE+'">Older update</a>', {url: page(LIFT)}), NOW)
        self.assertEqual(new["notices"][0]["status"], "rescinded")
        self.assertEqual(new["history"][-1]["sourceUrl"], url)
        self.assertEqual(new["notices"][0]["geometry"], GEOM)
        self.assertEqual(old["notices"][0]["status"], "active")

    def test_later_same_day_issue_is_not_overridden_by_older_lift(self):
        url = W.INDEX + "/Rescission-All-Customers"
        index = '<a href="'+W.UPDATE+'">New issue</a><a href="'+url+'">Older lift</a>'
        new = W.refresh({}, self.get(index, {url: page(LIFT)}), NOW)
        self.assertEqual(new["notices"][0]["status"], "active")

    def test_failure_never_drops_or_refreshes_previous_warning(self):
        old = W.refresh({}, self.get(), NOW)
        later = NOW + dt.timedelta(days=2)
        def down(_):
            raise OSError("unavailable")
        new = W.refresh(old, down, later)
        self.assertEqual(new["notices"], old["notices"])
        self.assertTrue(new["errors"])

    def test_failed_current_source_cannot_reactivate_old_issue(self):
        url = W.INDEX + "/Rescission-All-Customers"
        old = W.refresh({}, self.get('<a href="'+url+'">Rescission</a>', {url: page(LIFT)}), NOW)
        new = W.refresh(old, self.get(), NOW)
        self.assertEqual(new["notices"][0]["status"], "rescinded")
        self.assertTrue(new["errors"])

    def test_invalid_boundary_rejected_and_last_boundary_kept(self):
        for bad in [dict(type="FeatureCollection", features=[]),
                    {"type":"FeatureCollection","features":[{"geometry":{"type":"Point","coordinates":[0,0]}}]}]:
            with self.assertRaises(ValueError):
                W.geometry(json.dumps(bad))
        old = W.refresh({}, self.get(), NOW)
        later = NOW + dt.timedelta(days=1)
        new = W.refresh(old, self.get(extra={W.BOUNDARY: '{"features":[]}'}), later)
        self.assertEqual(new["notices"][0]["geometry"], old["notices"][0]["geometry"])
        self.assertTrue(new["errors"])

    def test_sidebar_statement_and_off_host_link_are_not_evidence(self):
        body = '<main id="main-content"><div class="col-m-8"><p>Published on November 03, 2026</p><p>No utility statement.</p></div><div class="col-m-4"><p>'+LIFT+'</p></div></main>'
        self.assertFalse(W.statements(body, W.UPDATE, "2026-11-03"))
        links = W.Links()
        links.feed('<a href="https://evil.example/Rescission">bad</a><a href="'+W.UPDATE+'">good</a>')
        self.assertEqual(links.urls, [W.UPDATE])

    def test_future_rescission_context_is_not_current_clearance(self):
        body = page("This change is effective tomorrow.</p><p>"+LIFT)
        self.assertFalse(W.statements(body, W.UPDATE, "2026-11-03"))


if __name__ == "__main__":
    unittest.main()
