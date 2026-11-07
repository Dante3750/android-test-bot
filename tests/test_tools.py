"""Self-tests for tools/summary.py, tools/history.py and tools/notify.py."""
import json, os, subprocess, sys, threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import summary, history, notify  # noqa: E402


def make_reports(base, devices):
    """devices: {"api34-pixel_6-p0": [(name, status), ...] or None for 'did not start'}"""
    for job, tests in devices.items():
        d = base / f"report-{job}" / "20261107-120000"
        d.mkdir(parents=True)
        if tests is not None:
            (d / "results.json").write_text(json.dumps(
                [{"name": n, "status": s, "details": f"java.lang.AssertionError: {n} broke" if s == "failed" else "",
                  "device": "emulator-5554"} for n, s in tests]))
        else:
            d.rmdir()
            (base / f"report-{job}").mkdir(exist_ok=True)
    return base


OK = [("com.x.A#one", "passed"), ("com.x.A#two", "passed")]


def test_summary_counts_and_problem_tests(tmp_path):
    rep = make_reports(tmp_path / "all", {
        "api24-pixel_6-p0": OK,
        "api34-pixel_6-p0": [("com.x.A#one", "passed"), ("com.x.A#two", "flaky")],
        "api35-pixel_7-p0": [("com.x.A#one", "failed"), ("com.x.A#two", "passed")],
        "api36-pixel_7-p0": [("com.x.A#one", "failed"), ("com.x.A#two", "quarantined")],
        "api27-pixel_6-p0": None,
    })
    out = summary.build(summary.load(str(rep)), "https://run", "My app")
    assert out["result"] == "failed"
    assert out["totals"] == {"ok": 1, "flaky": 1, "bad": 2, "dead": 1}
    assert "`A#one`: ❌ on 2: 35 pixel_7, 36 pixel_7" in out["page"]
    assert "A#one broke" in out["page"]
    assert "(emulator did not start)" in out["page"]
    assert out["comment"].startswith(summary.MARKER)
    assert "https://run" in out["comment"] and "https://run" in out["notify"]
    assert out["notify"].startswith("❌ My app: 1 passed, 1 flaky, 3 failed on 5 devices.")


def test_summary_shards_are_merged_per_device(tmp_path):
    rep = make_reports(tmp_path / "all", {
        "api34-pixel_6-p0": [("com.x.A#one", "passed")],
        "api34-pixel_6-p1": [("com.x.A#two", "passed")],
    })
    out = summary.build(summary.load(str(rep)))
    assert out["result"] == "passed" and out["totals"]["ok"] == 1
    assert "| 34 | pixel_6 | ✅ | 2 |" in out["page"]


def test_summary_missing_shard_counts_as_dead(tmp_path):
    rep = make_reports(tmp_path / "all", {"api34-pixel_6-p0": OK, "api34-pixel_6-p1": None})
    assert summary.build(summary.load(str(rep)))["result"] == "failed"


def test_summary_cli_writes_files(tmp_path):
    rep = make_reports(tmp_path / "all", {"api34-pixel_6-p0": OK})
    env = dict(os.environ, GITHUB_OUTPUT=str(tmp_path / "out.txt"), GITHUB_STEP_SUMMARY=str(tmp_path / "sum.md"))
    subprocess.run([sys.executable, str(ROOT / "tools/summary.py"), "--reports", str(rep), "--out", str(tmp_path / "o")],
                   check=True, env=env, capture_output=True)
    assert "result=passed" in (tmp_path / "out.txt").read_text()
    assert (tmp_path / "o/comment.md").exists() and (tmp_path / "o/notify.txt").exists()


def run_history(tmp_path, i, devices):
    rep = make_reports(tmp_path / f"all{i}", devices)
    subprocess.run([sys.executable, str(ROOT / "tools/history.py"), "--history", str(tmp_path / "hist"),
                    "--reports", str(rep), "--sha", f"abc{i}def", "--run-url", f"https://run/{i}"],
                   check=True, capture_output=True)


def test_history_tracks_flaky_and_broken_tests(tmp_path):
    for i in range(4):
        run_history(tmp_path, i, {
            "api34-pixel_6-p0": [("com.x.A#wobbly", "flaky" if i != 1 else "passed"),
                                 ("com.x.A#broken", "failed" if i >= 1 else "passed"),
                                 ("com.x.A#fine", "passed")],
        })
    data = json.loads((tmp_path / "hist/history.json").read_text())
    assert len(data["runs"]) == 4
    assert "com.x.A#fine" not in json.dumps(data)  # only problem tests are stored
    md = (tmp_path / "hist/FLAKY.md").read_text()
    assert "`com.x.A#wobbly` | 3 of 4 | 3 | 0" in md and "often flaky" in md
    assert "`com.x.A#broken` | 3 of 4 | 0 | 3" in md and "failing every recent run" in md
    assert "[" in md and "](https://run/3)" in md


def test_history_keeps_only_recent_runs(tmp_path):
    rep = make_reports(tmp_path / "all", {"api34-pixel_6-p0": OK})
    for i in range(5):
        subprocess.run([sys.executable, str(ROOT / "tools/history.py"), "--history", str(tmp_path / "h"),
                        "--reports", str(rep), "--keep", "3"], check=True, capture_output=True)
    assert len(json.loads((tmp_path / "h/history.json").read_text())["runs"]) == 3
    assert "Every test passed first time" in (tmp_path / "h/FLAKY.md").read_text()


def test_notify_rules():
    assert notify.should_send("failed", "failure")
    assert not notify.should_send("flaky", "failure")
    assert not notify.should_send("passed", "failure")
    assert notify.should_send("passed", "always")
    assert not notify.should_send("failed", "never")


def test_notify_sends_to_every_channel(tmp_path):
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    (tmp_path / "msg.txt").write_text("❌ My app: 3 failed")
    env = dict(os.environ, RESULT="failed", NOTIFY_ON="failure", TELEGRAM_TOKEN="T0K", TELEGRAM_CHAT_ID="42",
               TELEGRAM_API=base, SLACK_WEBHOOK=base + "/slack", DISCORD_WEBHOOK=base + "/discord",
               NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    r = subprocess.run([sys.executable, str(ROOT / "tools/notify.py"), str(tmp_path / "msg.txt")],
                       env=env, capture_output=True, text=True)
    srv.shutdown()
    assert r.returncode == 0, r.stdout + r.stderr
    paths = {p: body for p, body in got}
    assert paths["/botT0K/sendMessage"] == {"chat_id": "42", "text": "❌ My app: 3 failed", "disable_web_page_preview": True}
    assert paths["/slack"] == {"text": "❌ My app: 3 failed"}
    assert paths["/discord"] == {"content": "❌ My app: 3 failed"}


def test_notify_never_fails_the_build(tmp_path):
    (tmp_path / "msg.txt").write_text("x")
    env = dict(os.environ, RESULT="failed", SLACK_WEBHOOK="http://127.0.0.1:9/nothing-here",
               NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
    r = subprocess.run([sys.executable, str(ROOT / "tools/notify.py"), str(tmp_path / "msg.txt")],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "Could not send to Slack" in r.stdout


def test_summary_shows_why_flaky_tests_failed_first(tmp_path):
    d = tmp_path / "all/report-api34-pixel_6-p0/x"
    d.mkdir(parents=True)
    (d / "results.json").write_text(json.dumps([{"name": "com.x.A#tap", "status": "flaky", "device": "e",
        "details": "Failed the first time, passed on retry.\n\nandroidx.test.espresso.NoMatchingViewException: nope\n\tat x"}]))
    out = summary.build(summary.load(str(tmp_path / "all")))
    assert "NoMatchingViewException: nope" in out["page"]
