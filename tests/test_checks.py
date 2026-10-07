import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

from cmdbhealth.__main__ import main
from cmdbhealth.checks import load_csv, run_checks

DATA = Path(__file__).resolve().parent.parent / "sample_data"
TODAY = datetime(2026, 10, 1)


def server(sys_id, name="srv", serial="S1", seen="2026-09-30", **extra):
    row = {"sys_id": sys_id, "sys_class_name": "cmdb_ci_server", "name": name, "serial_number": serial,
           "ip_address": "10.0.0.1", "owned_by": "A", "support_group": "G", "environment": "Prod",
           "install_status": "Installed", "last_discovered": seen}
    row.update(extra)
    return row


class CheckTests(unittest.TestCase):
    def test_clean_ci_scores_100(self):
        report = run_checks([server("a")], [], TODAY)
        self.assertEqual((report.completeness, report.correctness), (100.0, 100.0))

    def test_missing_required_field_is_incomplete(self):
        report = run_checks([server("a", owned_by="")], [], TODAY)
        self.assertEqual(report.incomplete, [{"sys_id": "a", "name": "srv", "missing": ["owned_by"]}])

    def test_serial_duplicates_ignore_case(self):
        report = run_checks([server("a", serial="abc"), server("b", serial="ABC")], [], TODAY)
        self.assertEqual(report.duplicates[0]["sys_ids"], ["a", "b"])

    def test_name_duplicates_need_same_class(self):
        app = {"sys_id": "x", "sys_class_name": "cmdb_ci_app_server", "name": "API", "owned_by": "A", "support_group": "G", "environment": "P", "last_discovered": "2026-09-30"}
        db = dict(app, sys_id="y", sys_class_name="cmdb_ci_database")
        report = run_checks([app, db], [{"parent": "x", "child": "y"}], TODAY)
        self.assertEqual(report.duplicates, [])

    def test_stale_boundary(self):
        report = run_checks([server("a", serial="1", seen="2026-08-02"), server("b", serial="2", seen="2026-08-01")], [], TODAY, stale_days=60)
        self.assertEqual([s["sys_id"] for s in report.stale], ["b"])

    def test_retired_ci_is_not_stale(self):
        report = run_checks([server("a", seen="2024-01-01", install_status="Retired")], [], TODAY)
        self.assertEqual(report.stale, [])

    def test_bad_date_is_reported_not_raised(self):
        report = run_checks([server("a", seen="01/02/2026")], [], TODAY)
        self.assertIn("bad last_discovered", report.invalid_rows[0]["reason"])

    def test_relationship_to_unknown_ci_does_not_count(self):
        app = {"sys_id": "x", "sys_class_name": "cmdb_ci_app_server", "name": "API", "owned_by": "A", "support_group": "G", "environment": "P", "last_discovered": "2026-09-30"}
        report = run_checks([app], [{"parent": "x", "child": "ghost"}], TODAY)
        self.assertEqual([o["sys_id"] for o in report.orphans], ["x"])

    def test_empty_export(self):
        report = run_checks([], [], TODAY)
        self.assertEqual((report.total, report.completeness), (0, 100.0))


class SampleDataTests(unittest.TestCase):
    def setUp(self):
        self.report = run_checks(load_csv(DATA / "cis.csv"), load_csv(DATA / "relationships.csv"), TODAY)

    def test_expected_findings(self):
        r = self.report
        self.assertEqual(r.total, 11)
        self.assertEqual(len(r.invalid_rows), 1)
        self.assertEqual(sorted(i["sys_id"] for i in r.incomplete), ["ci004", "ci010"])
        self.assertEqual([d["sys_ids"] for d in r.duplicates], [["ci007", "ci011"], ["ci002", "ci003"]])
        self.assertEqual(sorted(s["sys_id"] for s in r.stale), ["ci005", "ci010"])
        self.assertEqual(sorted(o["sys_id"] for o in r.orphans), ["ci008", "ci010"])
        self.assertEqual((r.completeness, r.correctness), (81.8, 36.4))

    def test_cli_threshold_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "r.json")
            args = ["--cis", str(DATA / "cis.csv"), "--relationships", str(DATA / "relationships.csv"), "--today", "2026-10-01", "--json", out]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args + ["--min-score", "90"]), 1)
            with open(out, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["total_cis"], 11)

    def test_cli_missing_file_returns_2(self):
        with self.assertLogs("cmdbhealth", level="ERROR"):
            self.assertEqual(main(["--cis", "nope.csv", "--relationships", "nope.csv"]), 2)


if __name__ == "__main__":
    unittest.main()
