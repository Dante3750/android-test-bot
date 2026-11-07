#!/usr/bin/env python3
"""
Combine the per-device reports of one workflow run into:
  - the results table shown on the run page (GITHUB_STEP_SUMMARY)
  - comment.md  (the pull request comment)
  - notify.txt  (the short message sent to Telegram / Slack / Discord)
  - result=passed|flaky|failed  (GITHUB_OUTPUT)

Usage: summary.py --reports DIR --out DIR [--run-url URL] [--title TEXT]
DIR holds one folder per job, named report-api<API>-<device>-p<part>.
"""
import argparse, glob, json, os, re
from pathlib import Path

MARKER = "<!-- android-test-bot -->"
BAD = ("failed", "error")


def load(reports):
    """{(api, device): {"res": [results], "missing": parts that produced nothing}}"""
    devices = {}
    for d in glob.glob(os.path.join(reports, "report-api*")):
        m = re.match(r"report-api(\d+)-(.+)-p(\d+)$", os.path.basename(d))
        if not m:
            continue
        files = sorted(glob.glob(os.path.join(d, "**", "results.json"), recursive=True))
        dev = devices.setdefault((int(m.group(1)), m.group(2)), {"res": [], "missing": 0})
        if files:
            dev["res"] += json.load(open(files[-1], encoding="utf-8"))
        else:
            dev["missing"] += 1
    return devices


def device_status(dev):
    res = dev["res"]
    if dev["missing"] or not res:
        return "dead"
    if any(r["status"] in BAD for r in res):
        return "bad"
    if any(r["status"] in ("flaky", "quarantined") for r in res):
        return "flaky"
    return "ok"


ICON = {"ok": "✅", "flaky": "⚠️ flaky", "bad": "❌", "dead": "❌ did not start"}


def short(name):
    return name.split(".")[-1]


def build(devices, run_url="", title="Android tests"):
    totals = {"ok": 0, "flaky": 0, "bad": 0, "dead": 0}
    rows, problems = [], {}
    for (api, dname), dev in sorted(devices.items()):
        st = device_status(dev)
        totals[st] += 1
        res = dev["res"]
        count = {s: sum(r["status"] == s for r in res) for s in ("passed", "flaky", "quarantined")}
        bad = [r for r in res if r["status"] in BAD]
        shown = bad or [r for r in res if r["status"] in ("flaky", "quarantined")]
        names = ", ".join(f"`{short(r['name'])}`" for r in shown[:3])
        if len(shown) > 3:
            names += f" +{len(shown) - 3} more"
        rows.append(f"| {api} | {dname} | {ICON[st]} | {count['passed']} | {count['flaky']} | "
                    f"{count['quarantined']} | {len(bad)} | {names} |")
        if st == "dead":
            problems.setdefault("(emulator did not start)", {"failed": [], "flaky": [], "first": ""})["failed"].append(f"{api} {dname}")
        for r in res:
            if r["status"] in BAD + ("flaky",):
                p = problems.setdefault(r["name"], {"failed": [], "flaky": [], "first": ""})
                p["failed" if r["status"] in BAD else "flaky"].append(f"{api} {dname}")
                if r["status"] in BAD and not p["first"]:
                    p["first"] = next((l.strip() for l in r.get("details", "").splitlines() if l.strip()), "")[:160]

    failed = totals["bad"] + totals["dead"]
    result = "failed" if failed else ("flaky" if totals["flaky"] else "passed")
    headline = (f"**{totals['ok']} ✅ passed, {totals['flaky']} ⚠️ flaky, {failed} ❌ failed** "
                f"on {len(devices)} device{'s' if len(devices) != 1 else ''}")

    # Problem tests across all devices: real failures first, then the flakiest
    prob = sorted(problems.items(), key=lambda kv: (-len(kv[1]["failed"]), -len(kv[1]["flaky"]), kv[0]))
    prob_lines = []
    for name, p in prob[:15]:
        where = []
        if p["failed"]:
            where.append(f"❌ on {len(p['failed'])}: {', '.join(p['failed'][:4])}{' …' if len(p['failed']) > 4 else ''}")
        if p["flaky"]:
            where.append(f"⚠️ flaky on {len(p['flaky'])}")
        line = f"- `{short(name)}`: {'; '.join(where)}"
        if p["first"]:
            line += f"  \n  <sub>{p['first']}</sub>"
        prob_lines.append(line)
    if len(prob) > 15:
        prob_lines.append(f"- …and {len(prob) - 15} more (see the run)")

    table = ("| API | Device | Result | Passed | Flaky | Quarantined | Failed | Problem tests |\n"
             "|---|---|---|---|---|---|---|---|\n" + "\n".join(rows))
    link = f"\n\n[Open the run, reports and videos]({run_url})" if run_url else ""

    page = f"## {title}\n\n{headline}\n\n"
    if prob_lines:
        page += "### Problem tests\n\n" + "\n".join(prob_lines) + "\n\n"
    page += "### Every device\n\n" + table + "\n"

    comment = f"{MARKER}\n### {ICON['ok'] if result == 'passed' else ('⚠️' if result == 'flaky' else '❌')} {title}\n\n{headline}\n\n"
    if prob_lines:
        comment += "\n".join(prob_lines) + "\n\n"
    comment += f"<details><summary>Every device</summary>\n\n{table}\n\n</details>{link}\n"

    top = "; ".join(f"{short(n)} ({len(p['failed'])} ❌)" if p["failed"] else f"{short(n)} (flaky)" for n, p in prob[:3])
    icon = {"passed": "✅", "flaky": "⚠️", "failed": "❌"}[result]
    notify = f"{icon} {title}: {totals['ok']} passed, {totals['flaky']} flaky, {failed} failed on {len(devices)} devices."
    if top:
        notify += f"\nProblems: {top}"
    if run_url:
        notify += f"\n{run_url}"
    return {"result": result, "page": page, "comment": comment, "notify": notify, "totals": totals}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--run-url", default="")
    ap.add_argument("--title", default="Android tests")
    a = ap.parse_args()
    out = build(load(a.reports), a.run_url, a.title)
    o = Path(a.out)
    o.mkdir(parents=True, exist_ok=True)
    (o / "comment.md").write_text(out["comment"], encoding="utf-8")
    (o / "notify.txt").write_text(out["notify"], encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(out["page"] + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"result={out['result']}\n")
    print(out["page"])


if __name__ == "__main__":
    main()
