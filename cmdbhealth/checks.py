"""CMDB health checks modelled on the three ServiceNow CMDB Health KPIs:
completeness, correctness (duplicates, orphans, stale CIs) and compliance.

Input is a CI export and a relationship export. All sample data is synthetic.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

REQUIRED_BY_CLASS = {
    "cmdb_ci_server": ["name", "serial_number", "ip_address", "owned_by", "support_group", "environment"],
    "cmdb_ci_app_server": ["name", "owned_by", "support_group", "environment"],
    "cmdb_ci_database": ["name", "owned_by", "support_group", "environment"],
}
DEFAULT_REQUIRED = ["name", "owned_by", "support_group"]
# Classes that should never sit alone: an application or database with no relationship is an orphan.
MUST_HAVE_RELATIONSHIP = {"cmdb_ci_app_server", "cmdb_ci_database"}
DATE_FORMAT = "%Y-%m-%d"


@dataclass
class HealthReport:
    total: int = 0
    incomplete: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    stale: list[dict] = field(default_factory=list)
    orphans: list[dict] = field(default_factory=list)
    invalid_rows: list[dict] = field(default_factory=list)

    def _pct(self, failing_ids: set[str]) -> float:
        return 100.0 if self.total == 0 else round(100.0 * (self.total - len(failing_ids)) / self.total, 1)

    @property
    def completeness(self) -> float:
        return self._pct({i["sys_id"] for i in self.incomplete})

    @property
    def correctness(self) -> float:
        failing = {s for d in self.duplicates for s in d["sys_ids"]}
        failing |= {i["sys_id"] for i in self.stale} | {i["sys_id"] for i in self.orphans}
        return self._pct(failing)

    def to_dict(self) -> dict:
        return {
            "total_cis": self.total,
            "completeness_pct": self.completeness,
            "correctness_pct": self.correctness,
            "incomplete": self.incomplete,
            "duplicates": self.duplicates,
            "stale": self.stale,
            "orphans": self.orphans,
            "invalid_rows": self.invalid_rows,
        }


def load_csv(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(handle)]


def run_checks(cis: list[dict], relationships: list[dict], today: datetime, stale_days: int = 60) -> HealthReport:
    report = HealthReport()
    valid = []
    for line, ci in enumerate(cis, start=2):
        if not ci.get("sys_id") or not ci.get("sys_class_name"):
            report.invalid_rows.append({"line": line, "reason": "missing sys_id or sys_class_name"})
            continue
        valid.append(ci)
    report.total = len(valid)

    # Completeness: required attributes per class.
    for ci in valid:
        required = REQUIRED_BY_CLASS.get(ci["sys_class_name"], DEFAULT_REQUIRED)
        missing = [f for f in required if not ci.get(f)]
        if missing:
            report.incomplete.append({"sys_id": ci["sys_id"], "name": ci.get("name", ""), "missing": missing})

    # Duplicates: same serial number, else same class + name (case-insensitive), like an identification rule.
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for ci in valid:
        if ci.get("serial_number"):
            groups[("serial_number", ci["serial_number"].upper())].append(ci)
        elif ci.get("name"):
            groups[("class+name", ci["sys_class_name"], ci["name"].lower())].append(ci)
    for key, members in sorted(groups.items()):
        if len(members) > 1:
            report.duplicates.append({"rule": key[0], "value": key[-1], "sys_ids": [m["sys_id"] for m in members]})

    # Staleness: not seen by Discovery inside the window. A missing date counts as stale.
    cutoff = today - timedelta(days=stale_days)
    for ci in valid:
        if ci.get("install_status", "").lower() == "retired":
            continue
        raw = ci.get("last_discovered", "")
        try:
            seen = datetime.strptime(raw, DATE_FORMAT) if raw else None
        except ValueError:
            report.invalid_rows.append({"sys_id": ci["sys_id"], "reason": f"bad last_discovered '{raw}'"})
            continue
        if seen is None or seen < cutoff:
            report.stale.append({"sys_id": ci["sys_id"], "name": ci.get("name", ""), "last_discovered": raw or None})

    # Orphans: classes that must have at least one relationship to a CI that exists.
    known = {ci["sys_id"] for ci in valid}
    related: set[str] = set()
    for rel in relationships:
        parent, child = rel.get("parent", ""), rel.get("child", "")
        if parent in known and child in known:
            related.update((parent, child))
    for ci in valid:
        if ci["sys_class_name"] in MUST_HAVE_RELATIONSHIP and ci["sys_id"] not in related:
            report.orphans.append({"sys_id": ci["sys_id"], "name": ci.get("name", "")})
    return report
