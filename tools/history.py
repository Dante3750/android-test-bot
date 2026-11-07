#!/usr/bin/env python3
"""
Keep a history of test runs so you can see which tests are getting flaky over time.

It updates two files in a history folder (the workflow keeps them on an
'android-test-history' branch of your repo):
  history.json  - the last runs (only problem tests are stored, so it stays small)
  FLAKY.md      - a readable page: flakiest tests, broken tests, recent runs

Usage: history.py --history DIR --reports DIR [--sha SHA] [--ref BRANCH] [--run-url URL] [--keep 60]
"""
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summary  # noqa: E402


def record(devices, sha="", ref="", run_url="", event="", now=None):
    built = summary.build(devices)
    problems, names = {}, set()
    for (api, dname), dev in devices.items():
        for r in dev["res"]:
            names.add(r["name"])
            if r["status"] in ("failed", "error", "flaky", "quarantined"):
                kind = "failed" if r["status"] in ("failed", "error") else r["status"]
                p = problems.setdefault(r["name"], {"failed": 0, "flaky": 0, "quarantined": 0})
                p[kind] += 1
    return {"time": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC"),
            "sha": sha[:7], "ref": ref, "event": event, "url": run_url,
            "result": built["result"], "devices": len(devices), "totals": built["totals"],
            "tests": len(names), "problems": problems}


def analyse(runs):
    """Per test: in how many runs it had problems, and a suggestion."""
    stats = {}
    for i, run in enumerate(runs):
        for name, p in run["problems"].items():
            s = stats.setdefault(name, {"runs": 0, "flaky_runs": 0, "failed_runs": 0, "quarantined_runs": 0,
                                        "last": "", "recent_failed": []})
            s["runs"] += 1
            s["flaky_runs"] += p.get("flaky", 0) > 0
            s["failed_runs"] += p.get("failed", 0) > 0
            s["quarantined_runs"] += p.get("quarantined", 0) > 0
            s["last"] = run["time"]
    last3 = runs[-3:]
    for name, s in stats.items():
        broken_now = len(last3) == 3 and all(r["problems"].get(name, {}).get("failed", 0) > 0 for r in last3)
        if broken_now:
            s["advice"] = "🔴 failing every recent run: fix it"
        elif s["quarantined_runs"]:
            s["advice"] = "quarantined: fix, then remove it from the list"
        elif s["flaky_runs"] >= 3:
            s["advice"] = "🟡 often flaky: fix it or add it to the quarantine list"
        elif s["failed_runs"] and not s["flaky_runs"]:
            s["advice"] = "failed sometimes: look at the videos"
        else:
            s["advice"] = ""
    return stats


def render(runs, repo=""):
    stats = analyse(runs)
    ranked = sorted(stats.items(), key=lambda kv: (-kv[1]["runs"], -kv[1]["failed_runs"], kv[0]))
    md = [f"# Android test history{f' for {repo}' if repo else ''}", "",
          f"Updated {runs[-1]['time'] if runs else '-'}, from the last {len(runs)} run(s) "
          "on the main branch and nightly. Pull requests aren't counted.", ""]
    md += ["## Tests with problems", ""]
    if ranked:
        md += ["| Test | Runs with problems | Flaky | Failed | Quarantined | Last problem | What to do |",
               "|---|---|---|---|---|---|---|"]
        for name, s in ranked[:50]:
            md.append(f"| `{name}` | {s['runs']} of {len(runs)} | {s['flaky_runs']} | {s['failed_runs']} | "
                      f"{s['quarantined_runs']} | {s['last']} | {s['advice']} |")
    else:
        md.append("None. Every test passed first time in every run. 🎉")
    md += ["", "## Recent runs", "", "| When | Commit | Result | Devices | ✅ | ⚠️ | ❌ | Tests |",
           "|---|---|---|---|---|---|---|---|"]
    for r in reversed(runs[-20:]):
        t = r["totals"]
        icon = {"passed": "✅", "flaky": "⚠️", "failed": "❌"}[r["result"]]
        when = f"[{r['time']}]({r['url']})" if r.get("url") else r["time"]
        md.append(f"| {when} | `{r['sha']}` | {icon} {r['result']} | {r['devices']} | {t['ok']} | {t['flaky']} | "
                  f"{t['bad'] + t['dead']} | {r['tests']} |")
    md += ["", "To quarantine a test, add its name (like `com.example.LoginTest#signIn`) to "
           "`.github/flaky-tests.txt` in your repo. It still runs, but can't fail the build."]
    top = [f"- `{n.split('.')[-1]}`: problems in {s['runs']} of {len(runs)} runs. {s['advice']}"
           for n, s in ranked[:5] if s["runs"] >= 2]
    snippet = ("### Trends (last %d runs)\n\n" % len(runs) + "\n".join(top) + "\n") if top else ""
    return "\n".join(md) + "\n", snippet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True)
    ap.add_argument("--reports", required=True)
    ap.add_argument("--sha", default="")
    ap.add_argument("--ref", default="")
    ap.add_argument("--event", default="")
    ap.add_argument("--run-url", default="")
    ap.add_argument("--repo", default="")
    ap.add_argument("--keep", type=int, default=60)
    a = ap.parse_args()

    h = Path(a.history)
    h.mkdir(parents=True, exist_ok=True)
    path = h / "history.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"runs": []}
    devices = summary.load(a.reports)
    if not devices:
        print("No reports found; history not changed.")
        return
    data["runs"].append(record(devices, a.sha, a.ref, a.run_url, a.event))
    data["runs"] = data["runs"][-a.keep:]
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    md, snippet = render(data["runs"], a.repo)
    (h / "FLAKY.md").write_text(md, encoding="utf-8")
    (h / "README.md").write_text("Test history kept by the Android Test Bot. See [FLAKY.md](FLAKY.md).\n", encoding="utf-8")
    if snippet and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n" + snippet)
    print(md)


if __name__ == "__main__":
    main()
