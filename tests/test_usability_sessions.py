"""F29-09 (#893): the checks and the scoring behind the usability report.

tests/acceptance/f29_09.py holds the plan's tasks and measures as data
(docs/usability-test-plan.md, F29-08). These tests pin the scoring to the plan
and the refusals that keep personal data and incomplete records out. They also
keep the report's generated results in step with the committed records.
"""

from __future__ import annotations

import copy
import re
import tomllib
from datetime import date
from pathlib import Path

import pytest

from tests.acceptance import f29_09
from tests.acceptance.f29_09 import RecordError

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs/usability-test-plan.md"
REPORT_TEXT = f29_09.REPORT.read_text(encoding="utf-8")
_TOML = re.compile(r"```toml\n(.*?)```", re.DOTALL)


def _examples() -> tuple[dict, dict]:
    session, findings = _TOML.findall(REPORT_TEXT)[:2]
    return tomllib.loads(session), tomllib.loads(findings)


def _session(**changes: object) -> dict:
    raw = copy.deepcopy(_examples()[0])
    for key, value in changes.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    return raw


def _parse(raw: dict, name: str | None = None):
    return f29_09.parse_session(
        name or f"{raw['participant']}-{raw['client']}.toml", raw
    )


def _write(directory: Path, raw: dict) -> None:
    sessions = directory / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    lines = []
    tables = []
    for key, value in raw.items():
        if isinstance(value, dict):
            tables.append((key, value))
        else:
            lines.append(f"{key} = {_toml(value)}")
    for key, value in tables:
        if key == "tasks":
            for task, fields in value.items():
                lines.append(f"[tasks.{task}]")
                lines += [f"{k} = {_toml(v)}" for k, v in fields.items()]
        else:
            lines.append(f"[{key}]")
            lines += [f"{k} = {_toml(v)}" for k, v in value.items()]
    path = sessions / f"{raw['participant']}-{raw['client']}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toml(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return str(value)


# --- The plan, as data ---------------------------------------------------------


def test_every_task_id_of_the_plan_is_known_and_none_is_invented():
    plan = PLAN.read_text(encoding="utf-8")
    in_plan = set(re.findall(r"\| ([ADM]-[A-Z]?\d) \|", plan))
    known = {t for _, _, tasks in f29_09.ROLES.values() for t in tasks}
    known |= set(f29_09.CONNECTION_TASKS) | set(f29_09.METRICS)
    assert in_plan == known


def test_android_participants_also_do_the_connection_tasks_and_desktop_ones_do_not():
    assert f29_09.task_order("android", "STORE_MANAGER")[-5:] == (
        f29_09.CONNECTION_TASKS
    )
    assert not set(f29_09.task_order("desktop", "ANALYST")) & set(
        f29_09.CONNECTION_TASKS
    )


@pytest.mark.parametrize(
    ("answers", "score"),
    [
        ([5, 1, 5, 1, 5, 1, 5, 1, 5, 1], 100.0),
        ([1, 5, 1, 5, 1, 5, 1, 5, 1, 5], 0.0),
        ([3] * 10, 50.0),
        ([4, 2, 4, 1, 4, 2, 5, 2, 4, 2], 80.0),
    ],
)
def test_sus_is_scored_the_standard_way(answers, score):
    assert f29_09.sus_score(answers) == score


@pytest.mark.parametrize(
    "answers", [[3] * 9, [3] * 11, [0] + [3] * 9, [6] + [3] * 9, [True] + [3] * 9]
)
def test_a_sus_that_is_not_ten_answers_of_one_to_five_is_refused(answers):
    with pytest.raises(RecordError, match="ten answers"):
        f29_09.sus_score(answers)


# --- The runbook's examples ----------------------------------------------------


def test_the_runbooks_example_records_are_valid():
    session, findings = _examples()
    parsed = _parse(session)
    assert parsed.score == 80.0
    assert parsed.tasks["A-S2"].outcome == "assisted"
    both = [parsed, _parse(_session(participant="P-2"))]
    assert f29_09.parse_findings(findings, both)[0].fixed_by == 1210


# --- Refusals ------------------------------------------------------------------


def test_a_session_missing_a_task_is_refused_unless_it_was_stopped():
    raw = _session()
    del raw["tasks"]["A-E5"]
    with pytest.raises(RecordError, match=r"missing \['A-E5'\]"):
        _parse(raw)
    raw["stopped"] = True
    assert _parse(raw).stopped


def test_a_stopped_session_records_only_the_tasks_before_the_stop():
    raw = _session(stopped=True)
    del raw["tasks"]["A-S2"]
    with pytest.raises(RecordError, match="before the stop"):
        _parse(raw)


def test_a_stopped_session_may_have_no_sus_and_a_finished_one_may_not():
    with pytest.raises(RecordError, match="sus is required"):
        _parse(_session(sus=None))
    assert _parse(_session(sus=None, stopped=True)).score is None


def test_a_task_of_another_role_is_refused():
    raw = _session()
    raw["tasks"]["A-M1"] = {"outcome": "complete", "seconds": 10, "errors": 0}
    with pytest.raises(RecordError, match="not tasks of SALES_ASSOCIATE"):
        _parse(raw)


def test_a_role_is_tested_on_its_own_client():
    with pytest.raises(RecordError, match="ANALYST is tested on desktop"):
        _parse(_session(role="ANALYST"))


def test_the_five_minute_cap_is_a_failure():
    raw = _session()
    raw["tasks"]["A-S1"] = {"outcome": "complete", "seconds": 300, "errors": 0}
    with pytest.raises(RecordError, match="cap"):
        _parse(raw)
    raw["tasks"]["A-S1"]["seconds"] = 301
    with pytest.raises(RecordError, match="0 to 300"):
        _parse(raw)


def test_an_assisted_task_says_what_the_hint_was_and_only_an_assisted_one_has_one():
    raw = _session()
    raw["tasks"]["A-S2"]["hint"] = ""
    with pytest.raises(RecordError, match="hint is required"):
        _parse(raw)
    raw = _session()
    raw["tasks"]["A-S1"]["hint"] = "Showed the search."
    with pytest.raises(RecordError, match="assisted or failed, not complete"):
        _parse(raw)


def test_a_hint_that_did_not_get_the_participant_there_is_a_failed_task():
    raw = _session()
    raw["tasks"]["A-S4"]["hint"] = "Pointed at the explanation."
    assert _parse(raw).tasks["A-S4"].outcome == "failed"


def test_no_consent_to_take_notes_means_no_record():
    with pytest.raises(RecordError, match="consent to take notes"):
        _parse(_session(consent={"notes": False, "recording": False}))


def test_a_participant_who_built_the_client_is_refused():
    with pytest.raises(RecordError, match="built_this_client"):
        _parse(_session(built_this_client=True))


def test_the_moderator_does_not_read_their_own_notes():
    with pytest.raises(RecordError, match="second team member"):
        _parse(_session(second_reader="github-login"))
    with pytest.raises(RecordError, match="second team member"):
        _parse(_session(second_reader="GitHub-Login"))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("name", "Ana"),
        ("email", "x"),
        ("age", 34),
    ],
)
def test_a_field_the_record_does_not_define_is_refused(key, value):
    with pytest.raises(RecordError, match="unknown keys"):
        _parse(_session(**{key: value}))


@pytest.mark.parametrize(
    "text",
    [
        "Call me at ana.p@example.com",
        "Her number is +52 55 1234 5678",
        "8112345678",
    ],
)
def test_free_text_that_looks_like_contact_details_is_refused(text):
    with pytest.raises(RecordError, match="email address or a phone"):
        _parse(_session(hardest=text))


def test_ordinary_numbers_in_free_text_are_not_mistaken_for_a_phone():
    assert _parse(_session(hardest="Took 120 - 180 s on 2026-12-01, store 12"))


def test_a_participant_is_only_ever_p_n():
    with pytest.raises(RecordError, match="P-<n>"):
        _parse(_session(participant="Ana"), "Ana-android.toml")


def test_the_file_is_named_after_the_participant_and_client():
    with pytest.raises(RecordError, match="named P-1-android"):
        _parse(_session(), "P-2-android.toml")


def test_desktop_over_web_is_for_desktop_sessions():
    with pytest.raises(RecordError, match="desktop sessions"):
        _parse(_session(desktop_over_web="Comparing versions."))


def test_every_metric_question_is_recorded_even_when_not_asked():
    raw = _session()
    del raw["metrics"]["M-3"]
    with pytest.raises(RecordError, match="M-1 to M-5"):
        _parse(raw)


def test_a_finding_must_name_a_participant_who_had_that_client():
    session, findings = _examples()
    findings["finding"][0]["seen_by"] = ["P-9"]
    with pytest.raises(RecordError, match="seen_by"):
        f29_09.parse_findings(findings, [_parse(session)])


def test_a_finding_marked_not_fixing_gives_a_reason():
    session, findings = _examples()
    entry = findings["finding"][0]
    del entry["fixed_by"]
    entry["outcome"] = "not-fixing"
    both = [_parse(session), _parse(_session(participant="P-2"))]
    with pytest.raises(RecordError, match="reason is required"):
        f29_09.parse_findings(findings, both)


# --- Rendering and the final check ------------------------------------------------


def _results(tmp_path: Path) -> f29_09.Results:
    session, findings = _examples()
    _write(tmp_path, session)
    stand_in = _session(participant="P-2", standing="stand-in")
    stand_in["sus"] = [3] * 10
    _write(tmp_path, stand_in)
    desktop = {
        "participant": "P-6",
        "client": "desktop",
        "role": "AUDITOR",
        "standing": "real",
        "built_this_client": False,
        "date": date(2026, 12, 2),
        "moderator": "github-login",
        "second_reader": "another-login",
        "consent": {"notes": True, "recording": True},
        "tasks": {"D-U1": {"outcome": "complete", "seconds": 61, "errors": 0}},
        "metrics": {m: "not-asked" for m in f29_09.METRICS},
        "sus": [5, 1, 5, 1, 5, 1, 5, 1, 5, 2],
        "desktop_over_web": "The audit trail in one place.",
    }
    _write(tmp_path, desktop)
    (tmp_path / "findings.toml").write_text(
        _TOML.findall(REPORT_TEXT)[1], encoding="utf-8"
    )
    return f29_09.load(tmp_path)


def test_stand_ins_are_reported_apart_from_real_participants(tmp_path):
    text = f29_09.render(_results(tmp_path))
    real, stand_in = text.split("#### Results per task, stand-ins")
    # A-S1: P-1 is real, P-2 stands in. Each table counts one.
    assert "| A-S1 | 1 | 1 of 1 |" in real.split("#### Results per task, real")[1]
    assert "| A-S1 | 1 | 1 of 1 |" in stand_in
    assert "P-2 50 (stand-in)" in text
    assert "| Android | 2 | P-1 80, P-2 50 (stand-in) | 65.0 | 80.0 |" in text


def test_coverage_names_roles_with_stand_ins_only_and_roles_not_tested(tmp_path):
    text = f29_09.render(_results(tmp_path))
    assert "Store seller (`SALES_ASSOCIATE`) | Android | 2 | P-1 | P-2 |" in text
    assert "| Analyst (`ANALYST`) | Desktop | 2 | — | — | **not tested** |" in text


def test_the_render_carries_medians_findings_and_quotes(tmp_path):
    text = f29_09.render(_results(tmp_path))
    assert "| D-U1 | 1 | 1 of 1 | 0 | 0 | 61 | 0 | 0 |" in text
    assert "| F-1 | major | Android | A-E4 | P-1, P-2 |" in text
    assert "| #1201 | fixed in #1210 |" in text
    assert "“The audit trail in one place.”" in text


def test_the_final_check_wants_an_issue_for_every_blocker_and_major(tmp_path):
    results = _results(tmp_path)
    report = f29_09.write_block(
        f"{f29_09.BEGIN}\n{f29_09.END}\n", f29_09.render(results)
    )
    assert f29_09.final_problems(results, report) == []
    (tmp_path / "findings.toml").write_text(
        '[[finding]]\nid = "F-1"\nseverity = "blocker"\nclient = "desktop"\n'
        'tasks = ["D-U1"]\nseen_by = ["P-6"]\nsummary = "x"\n',
        encoding="utf-8",
    )
    results = f29_09.load(tmp_path)
    problems = f29_09.final_problems(results, report + "‹fill›")
    assert "F-1 is blocker and has no issue (plan §4.3)" in problems
    assert any("placeholders" in p for p in problems)
    assert any("stale" in p for p in problems)


# --- The committed report --------------------------------------------------------


def test_the_committed_records_are_valid_and_the_report_shows_them():
    """The report's results are exactly what the committed records render."""
    results = f29_09.load()
    assert f29_09.generated_block(REPORT_TEXT) == f29_09.render(results)
