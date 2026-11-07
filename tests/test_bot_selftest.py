"""
Self-tests for test_bot.py. They use a fake `adb` (tests/fake_adb.py), so they run in
seconds without any emulator. Run with:  python -m pytest tests -q
"""
import json, os, subprocess, sys, xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import test_bot  # noqa: E402

BOT = ROOT / "test_bot.py"
FAKE = Path(__file__).resolve().parent / "fake_adb.py"


def run_bot(tmp_path, scenario, *args, env_extra=None):
    """Run the bot against the fake device. Returns (exit code, output, results folder)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    adb = bindir / "adb"
    adb.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    adb.chmod(0o755)
    (tmp_path / "app.apk").write_text("x")
    (tmp_path / "test.apk").write_text("x")
    env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
               FAKE_SCENARIO=scenario, FAKE_STATE=str(tmp_path / "state"),
               GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"), **(env_extra or {}))
    env.pop("ANDROID_HOME", None)
    r = subprocess.run([sys.executable, str(BOT), *args, "--apk", "app.apk", "--out", "res"],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr, tmp_path / "res"


def results(res_dir):
    run_dirs = [p for p in res_dir.iterdir() if p.is_dir()]
    return json.loads((run_dirs[-1] / "results.json").read_text())


# ---------- parsing ----------
def test_parse_pass_fail_and_stack():
    out = "\n".join([
        "INSTRUMENTATION_STATUS: class=com.x.A", "INSTRUMENTATION_STATUS: test=ok", "INSTRUMENTATION_STATUS_CODE: 1",
        "INSTRUMENTATION_STATUS: class=com.x.A", "INSTRUMENTATION_STATUS: test=ok", "INSTRUMENTATION_STATUS_CODE: 0",
        "INSTRUMENTATION_STATUS: class=com.x.A", "INSTRUMENTATION_STATUS: test=bad",
        "INSTRUMENTATION_STATUS: stack=java.lang.AssertionError: nope", "\tat com.x.A.bad(A.kt:3)",
        "INSTRUMENTATION_STATUS_CODE: -2",
        "INSTRUMENTATION_STATUS: class=com.x.A", "INSTRUMENTATION_STATUS: test=skip", "INSTRUMENTATION_STATUS_CODE: -3",
        "INSTRUMENTATION_CODE: -1"])
    r = test_bot.parse_instrument(out)
    assert [(x["name"], x["status"]) for x in r] == [
        ("com.x.A#ok", "passed"), ("com.x.A#bad", "failed"), ("com.x.A#skip", "skipped")]
    assert "A.kt:3" in r[1]["details"]


def test_parse_parameterized_names():
    out = ("INSTRUMENTATION_STATUS: class=com.x.P\nINSTRUMENTATION_STATUS: test=add[a_1_2]\n"
           "INSTRUMENTATION_STATUS_CODE: 0\n")
    assert test_bot.parse_instrument(out)[0]["name"] == "com.x.P#add[a_1_2]"


def test_junit_xml_is_valid(tmp_path):
    rs = [{"name": "com.x.A#ok", "status": "passed", "details": "", "device": "emu"},
          {"name": "com.x.A#bad", "status": "failed", "details": "boom <&>", "device": "emu"},
          {"name": "com.x.A#wobbly", "status": "flaky", "details": "", "device": "emu"}]
    test_bot.write_junit(rs, tmp_path / "j.xml", 5)
    suite = ET.parse(tmp_path / "j.xml").getroot().find("testsuite")
    assert suite.get("tests") == "3" and suite.get("failures") == "1"
    assert suite.findall("testcase")[1].find("failure").get("message") == "boom <&>"


# ---------- full runs against the fake device ----------
def test_all_pass(tmp_path):
    code, out, res = run_bot(tmp_path, "all_pass", "instrument", "--test-apk", "test.apk")
    assert code == 0, out
    assert {r["status"] for r in results(res)} == {"passed"}
    assert (res / "TESTS_RAN").exists()


def test_flaky_and_real_failure(tmp_path):
    code, out, res = run_bot(tmp_path, "flaky", "instrument", "--test-apk", "test.apk", "--label", "API 34")
    assert code == 1, out
    by = {r["name"]: r["status"] for r in results(res)}
    assert by["com.demo.UiTest#tap"] == "flaky"
    assert by["com.demo.LogicTest#math"] == "failed"
    assert "FAILED: com.demo.LogicTest#math" in out
    summary = (tmp_path / "summary.md").read_text()
    assert "API 34" in summary and "LogicTest#math" in summary


def test_only_flaky_passes_the_run(tmp_path):
    code, out, res = run_bot(tmp_path, "only_flaky", "instrument", "--test-apk", "test.apk")
    assert code == 0, out
    assert "flaky" in {r["status"] for r in results(res)}


def test_crash_reruns_shard(tmp_path):
    code, out, res = run_bot(tmp_path, "crash_once", "instrument", "--test-apk", "test.apk")
    assert code == 0, out
    assert "running the shard again" in out


def test_install_failure_is_setup_problem(tmp_path):
    code, out, res = run_bot(tmp_path, "install_fails", "instrument", "--test-apk", "test.apk")
    assert code == 3, out
    assert not (res / "TESTS_RAN").exists()
    assert "SETUP PROBLEM" in out


def test_no_tests_is_setup_problem(tmp_path):
    code, out, res = run_bot(tmp_path, "no_tests", "instrument", "--test-apk", "test.apk")
    assert code == 3, out


def test_shard_arguments_are_passed(tmp_path):
    code, out, res = run_bot(tmp_path, "all_pass", "instrument", "--test-apk", "test.apk", "--shard", "2/4")
    assert code == 0, out
    calls = (tmp_path / "state.calls").read_text()
    assert "numShards 4" in calls and "shardIndex 2" in calls


def test_missing_test_apk_is_ignored_for_monkey(tmp_path):
    code, out, res = run_bot(tmp_path, "all_pass", "monkey", "--test-apk", "does-not-exist.apk", "--events", "50")
    assert code == 0, out
    assert results(res)[0]["name"].startswith("Monkey 50 events")


def test_monkey_crash_fails(tmp_path):
    code, out, res = run_bot(tmp_path, "monkey_crash", "monkey", "--events", "50")
    assert code == 1, out
    assert "CRASH" in results(res)[0]["details"]


def test_system_popup_is_closed_before_tests(tmp_path):
    code, out, res = run_bot(tmp_path, "anr_popup", "instrument", "--test-apk", "test.apk")
    assert code == 0, out
    assert "closing system popup" in out
    calls = (tmp_path / "state.calls").read_text()
    assert "keyevent 4" in calls and "CLOSE_SYSTEM_DIALOGS" in calls
