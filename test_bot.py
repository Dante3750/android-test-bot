#!/usr/bin/env python3
"""
Android Test Bot
----------------
Starts Android emulators, installs your app, runs your full test suite
(split across several emulators in parallel), and writes an HTML report.

Failed tests are rerun once. A test that fails and then passes is marked
"flaky" (it usually means timing problems, not a real bug).

Modes:
  instrument  - run your Espresso / JUnit androidTest tests (thousands is fine)
  maestro     - run a folder of Maestro YAML flows
  monkey      - random tap/swipe stress test (e.g. 4000 events)

Examples:
  python test_bot.py instrument --apk app-debug.apk --test-apk app-debug-androidTest.apk --avd Pixel_7 --emulators 4
  python test_bot.py maestro --apk app-debug.apk --flows ./flows --avd Pixel_7 --emulators 2
  python test_bot.py monkey  --apk app-debug.apk --avd Pixel_7 --events 4000

Exit codes: 0 = all passed (flaky allowed), 1 = tests failed, 3 = setup problem (device/install)
"""
import argparse, html, json, os, shutil, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path

# ---------- tool lookup ----------
SDK = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
EXE = ".exe" if os.name == "nt" else ""

def tool(name, subdir):
    found = shutil.which(name)
    if found:
        return found
    p = Path(SDK) / subdir / (name + EXE)
    if p.exists():
        return str(p)
    sys.exit(f"Can't find '{name}'. Install Android Studio and set ANDROID_HOME to your SDK folder.")

ADB = EMULATOR = None

class SetupError(Exception):
    """The device or install broke; no tests could run."""

def run(cmd, timeout=None, check=False):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        if check:
            raise
        return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s")
    if check and r.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r

def adb(serial, *args, timeout=None, check=False):
    return run([ADB, "-s", serial, *args], timeout=timeout, check=check)

def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)

FLAKY_PREFIX = "Failed the first time, passed on retry.\n\n"

def flaky_reason(details):
    """The first-try error of a flaky test (its details start with a fixed prefix)."""
    return (details or "").replace(FLAKY_PREFIX, "", 1)

def first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""

# ---------- emulators ----------
def start_emulators(avd, count, show_window, out_dir):
    procs, serials = [], []
    for i in range(count):
        port = 5554 + i * 2
        serial = f"emulator-{port}"
        cmd = [EMULATOR, "-avd", avd, "-port", str(port), "-read-only",
               "-no-snapshot-save", "-no-boot-anim", "-no-audio", "-gpu", "swiftshader_indirect"]
        if not show_window:
            cmd.append("-no-window")
        logf = open(out_dir / f"{serial}-emulator.log", "w")
        procs.append(subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT))
        serials.append(serial)
        log(f"Starting {serial}")
    for s in serials:
        wait_for_boot(s)
        # Turn off animations: makes UI tests faster and less flaky
        for key in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
            adb(s, "shell", "settings", "put", "global", key, "0")
        adb(s, "shell", "input", "keyevent", "82")  # unlock screen
    return procs, serials

def wait_for_boot(serial, timeout=600):
    log(f"Waiting for {serial} to boot...")
    start = time.time()
    run([ADB, "-s", serial, "wait-for-device"], timeout=timeout)
    while time.time() - start < timeout:
        if adb(serial, "shell", "getprop", "sys.boot_completed", timeout=30).stdout.strip() == "1":
            log(f"{serial} ready ({int(time.time() - start)}s)")
            return
        time.sleep(3)
    raise SetupError(f"{serial} did not boot in {timeout}s")

POPUP_WORDS = ("Not Responding", "isn't responding", "keeps stopping", "has stopped", "Application Error")

def current_focus(serial):
    """Names of the windows that have focus, e.g. 'Application Not Responding: com.google.android.gms'."""
    import re
    out = adb(serial, "shell", "dumpsys window | grep -E 'mCurrentFocus'", timeout=30).stdout
    names = re.findall(r"Window\{\S+ u\d+ ([^}]*)\}", out)
    return " | ".join(dict.fromkeys(names)) or " ".join(out.split())[:200]

def quiet_device(serial):
    """Stop "app isn't responding" and crash popups from appearing during tests.
    On slow emulators, Google's own apps often stop responding; their popup covers the app
    under test and every UI test fails with RootViewWithoutFocusException. Crashes of the
    app under test are still reported by the test runner and in logcat."""
    adb(serial, "shell", "settings", "put", "global", "hide_error_dialogs", "1", timeout=30)
    adb(serial, "shell", "settings", "put", "secure", "anr_show_background", "0", timeout=30)

def settle(serial, timeout=60):
    """Close system popups ("System UI isn't responding", "Launcher keeps stopping", ...)
    that steal focus from the app and make every UI test fail. Waits for the home screen."""
    end, closed = time.time() + timeout, 0
    while time.time() < end:
        focus = current_focus(serial)
        if any(w in focus for w in POPUP_WORDS):
            log(f"{serial}: closing system popup: {focus}")
            adb(serial, "shell", "am", "broadcast", "-a", "android.intent.action.CLOSE_SYSTEM_DIALOGS", timeout=30)
            adb(serial, "shell", "input", "keyevent", "4", timeout=30)   # BACK = "wait" on ANR popups
            closed += 1
        elif "auncher" in focus:   # Launcher / NexusLauncher: home screen is up and has focus
            return
        else:
            adb(serial, "shell", "input", "keyevent", "3", timeout=30)   # HOME
        time.sleep(2)
    log(f"{serial}: screen did not settle in {timeout}s (focus: {current_focus(serial)}); continuing")

def stop_emulators(procs, serials):
    for s in serials:
        adb(s, "emu", "kill", timeout=30)
    for p in procs:
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()

def third_party_packages(serial):
    out = adb(serial, "shell", "pm", "list", "packages", "-3", timeout=60).stdout
    return {l.replace("package:", "").strip() for l in out.splitlines() if l.strip()}

def install(serial, apk):
    """Install with a short timeout. If it hangs, wait for the device and try once more."""
    for attempt in (1, 2):
        r = adb(serial, "install", "-r", "-t", "-g", apk, timeout=120 if attempt == 1 else 240)
        if r.returncode == 0 and "Success" in r.stdout + r.stderr:
            return
        log(f"{serial}: install of {Path(apk).name} failed (try {attempt}): {first_line(r.stderr or r.stdout)}")
        if attempt == 1:
            run([ADB, "-s", serial, "wait-for-device"], timeout=120)
            wait_for_boot(serial, timeout=180)
    raise SetupError(f"Could not install {apk} on {serial}")

# ---------- failure videos ----------
class Recorder:
    """Records the screen (in 3-minute pieces, Android's limit) while failed tests rerun.
    The video is kept only if a test fails again, so it shows the real failure."""
    def __init__(self, serial, out_dir, name):
        self.serial, self.out_dir, self.name = serial, out_dir, name
        self.stopped, self.parts = threading.Event(), []
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        for i in range(6):   # up to 18 minutes
            if self.stopped.is_set():
                return
            remote = f"/sdcard/bot-{self.name}-{i}.mp4"
            self.parts.append(remote)
            adb(self.serial, "shell", "screenrecord", "--time-limit", "180", "--bit-rate", "2000000",
                remote, timeout=200)

    def stop(self, keep):
        time.sleep(1)  # let the last frames land
        self.stopped.set()
        adb(self.serial, "shell", "pkill -2 screenrecord || kill -2 $(pidof screenrecord)", timeout=30)
        self.thread.join(timeout=20)
        time.sleep(1)
        saved = []
        for i, remote in enumerate(self.parts):
            if keep:
                local = self.out_dir / f"{self.serial}-{self.name}-{i}.mp4"
                adb(self.serial, "pull", remote, str(local), timeout=120)
                if local.exists() and local.stat().st_size > 0:
                    saved.append(local)
            adb(self.serial, "shell", "rm", "-f", remote, timeout=30)
        if keep and saved:
            log(f"{self.serial}: saved video of the failing tests: {', '.join(p.name for p in saved)}")
        return saved

# ---------- quarantine ----------
def load_quarantine(path):
    """Known-flaky tests, one per line: com.x.LoginTest#signIn, com.x.LoginTest (whole class),
    or LoginTest#signIn. Lines starting with # are comments."""
    if not path or not Path(path).exists():
        return []
    entries = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split(" #")[0].strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries

def is_quarantined(name, entries):
    full = name.split("[")[0]
    cls = full.split("#")[0]
    for e in entries:
        if e in (full, cls) or full.endswith("." + e) or cls.endswith("." + e):
            return True
    return False

def apply_quarantine(results, entries):
    hits = 0
    for r in results:
        if r["status"] in ("failed", "error", "flaky") and is_quarantined(r["name"], entries):
            r["details"] = f"Quarantined (was {r['status']}): listed in the flaky-tests file, so it doesn't fail the run.\n\n" + r["details"]
            r["status"] = "quarantined"
            hits += 1
    if hits:
        log(f"{hits} quarantined test result(s) won't fail the run")

# ---------- instrumentation tests ----------
STATUS = {"0": "passed", "-1": "error", "-2": "failed", "-3": "skipped", "-4": "skipped"}

def parse_instrument(output):
    results, cur, key = [], {}, None
    for line in output.splitlines():
        if line.startswith("INSTRUMENTATION_STATUS: "):
            k, _, v = line[len("INSTRUMENTATION_STATUS: "):].partition("=")
            cur[k], key = v, k
        elif line.startswith("INSTRUMENTATION_STATUS_CODE: "):
            code = line.split(": ", 1)[1].strip()
            if code in STATUS and "test" in cur:
                results.append({"name": f"{cur.get('class', '')}#{cur['test']}",
                                "status": STATUS[code],
                                "details": cur.get("stack", "")})
            cur, key = {}, None
        elif line.startswith("INSTRUMENTATION_"):
            key = None
        elif key == "stack":
            cur["stack"] += "\n" + line
    return results

def find_runner(serial, app_pkgs):
    out = adb(serial, "shell", "pm", "list", "instrumentation", timeout=60).stdout
    for line in out.splitlines():
        # instrumentation:com.x.test/androidx.test.runner.AndroidJUnitRunner (target=com.x)
        if "target=" in line:
            target = line.split("target=")[1].rstrip(")").strip()
            if not app_pkgs or target in app_pkgs:
                return line.split("instrumentation:")[1].split(" ")[0]
    raise SetupError("No test runner found. Did you pass the androidTest APK with --test-apk?")

def instrument(serial, runner, args, out_file):
    out = adb(serial, "shell", "am", "instrument", "-r", "-w", *args, runner, timeout=6 * 3600).stdout
    out_file.write_text(out, encoding="utf-8")
    crashed = "INSTRUMENTATION_FAILED" in out or "Process crashed" in out
    return parse_instrument(out), crashed, out

def run_shard(serial, runner, index, total, extra, results, out_dir, video=False):
    log(f"{serial}: running shard {index + 1}/{total}")
    args = ["-e", "numShards", str(total), "-e", "shardIndex", str(index)]
    for e in extra:
        k, _, v = e.partition("=")
        args += ["-e", k, v]

    shard, crashed, out = instrument(serial, runner, args, out_dir / f"{serial}-instrument.txt")
    if crashed or not shard:
        log(f"{serial}: test run crashed or ran nothing, running the shard again")
        shard, crashed, out = instrument(serial, runner, args, out_dir / f"{serial}-instrument-retry.txt")

    # Rerun failed tests once. Pass on retry = flaky, not failed.
    failed = [r for r in shard if r["status"] in ("failed", "error")]
    if failed:
        # "Class#method[param]" -> "Class#method" (the runner can't pick a single parameter)
        if any("RootViewWithoutFocus" in r["details"] for r in failed):
            log(f"{serial}: app window lost focus; screen shows: {current_focus(serial)}")
        settle(serial, timeout=30)
        targets = sorted({r["name"].split("[")[0] for r in failed})
        log(f"{serial}: {len(failed)} failed, rerunning {len(targets)} test(s) once")
        rec = Recorder(serial, out_dir, "failure") if video else None
        rerun, _, _ = instrument(serial, runner, ["-e", "class", ",".join(targets)],
                                 out_dir / f"{serial}-instrument-rerun.txt")
        passed_on_retry = {r["name"] for r in rerun if r["status"] == "passed"}
        if rec:
            rec.stop(keep=any(r["name"] not in passed_on_retry for r in failed))
        for r in failed:
            if r["name"] in passed_on_retry:
                r["status"] = "flaky"
                r["details"] = FLAKY_PREFIX + r["details"]

    for r in shard:
        r["device"] = serial
    if crashed:
        shard.append({"name": f"shard {index + 1} crashed", "status": "error",
                      "details": out[-3000:], "device": serial})
    results.extend(shard)
    save_logcat(serial, out_dir)
    log(f"{serial}: shard done, {sum(r['status'] == 'passed' for r in shard)}/{len(shard)} passed, "
        f"{sum(r['status'] == 'flaky' for r in shard)} flaky")

# ---------- maestro ----------
def run_maestro(serials, flows, results, out_dir, part=0, parts=1):
    maestro = shutil.which("maestro") or sys.exit("Install Maestro first: https://maestro.mobile.dev")
    files = sorted(list(Path(flows).glob("**/*.yaml")) + list(Path(flows).glob("**/*.yml")))
    files = files[part::parts]  # this machine's share of the flows
    if not files:
        sys.exit(f"No .yaml flows found in {flows}")
    log(f"Found {len(files)} Maestro flows")
    queue, lock = list(files), threading.Lock()

    def worker(serial):
        while True:
            with lock:
                if not queue:
                    return
                f = queue.pop(0)
            r = run([maestro, "--device", serial, "test", str(f)], timeout=1800)
            status, details = "passed", ""
            if r.returncode != 0:
                details = (r.stdout + r.stderr)[-3000:]
                r2 = run([maestro, "--device", serial, "test", str(f)], timeout=1800)
                status = "flaky" if r2.returncode == 0 else "failed"
            results.append({"name": str(f), "status": status, "details": details, "device": serial})
            log(f"{serial}: {status.upper()} {f.name}")

    threads = [threading.Thread(target=worker, args=(s,)) for s in serials]
    [t.start() for t in threads]
    [t.join() for t in threads]

# ---------- monkey ----------
def run_monkey(serial, pkg, events, seed, results, out_dir):
    log(f"{serial}: sending {events} random events to {pkg}")
    out = adb(serial, "shell", "monkey", "-p", pkg, "-s", str(seed), "--throttle", "100",
              "--pct-syskeys", "0", "-v", str(events), timeout=6 * 3600).stdout
    (out_dir / f"{serial}-monkey.txt").write_text(out, encoding="utf-8")
    crashed = "// CRASH" in out or "ANR in" in out or "Monkey aborted" in out
    detail = ""
    if crashed:
        start = max(out.find("// CRASH"), out.find("ANR in"), 0)
        detail = out[start:start + 4000]
    results.append({"name": f"Monkey {events} events (seed {seed})",
                    "status": "failed" if crashed else "passed",
                    "details": detail, "device": serial})
    save_logcat(serial, out_dir)

def save_logcat(serial, out_dir):
    out = adb(serial, "logcat", "-d", "-b", "crash,main", timeout=120).stdout
    (out_dir / f"{serial}-logcat.txt").write_text(out, encoding="utf-8")

def screenshot(serial, out_dir):
    try:
        with open(out_dir / f"{serial}-screen.png", "wb") as f:
            subprocess.run([ADB, "-s", serial, "exec-out", "screencap", "-p"], stdout=f, timeout=30)
    except Exception:
        pass

# ---------- report ----------
ORDER = {"failed": 0, "error": 0, "flaky": 1, "quarantined": 2, "skipped": 3, "passed": 4}
STATUSES = ("passed", "flaky", "quarantined", "failed", "error", "skipped")

def write_report(results, out_dir, started, mode):
    counts = {s: sum(r["status"] == s for r in results) for s in STATUSES}
    (out_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    took = int(time.time() - started)
    shots = "".join(f"<figure><img src='{p.name}' width=240><figcaption>{p.stem}</figcaption></figure>"
                    for p in sorted(out_dir.glob("*-screen.png")))
    videos = "".join(f"<figure><video src='{p.name}' width=320 controls></video><figcaption>{p.stem}</figcaption></figure>"
                     for p in sorted(out_dir.glob("*.mp4")))
    rows = "".join(
        f"<tr class='{r['status']}'><td>{html.escape(r['name'])}</td><td>{r['status']}</td>"
        f"<td>{r['device']}</td><td><pre>{html.escape(r['details'][:3000])}</pre></td></tr>"
        for r in sorted(results, key=lambda r: ORDER.get(r["status"], 9)))
    page = f"""<!doctype html><meta charset=utf-8><title>Test Report</title>
<style>body{{font-family:system-ui;margin:24px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:6px;text-align:left;vertical-align:top}}
.failed,.error{{background:#fde2e2}}.flaky{{background:#fff4d6}}.quarantined{{background:#ede7f6}}.passed{{background:#e4f7e4}}.skipped{{background:#f2f2f2}}
pre{{white-space:pre-wrap;margin:0;font-size:12px;max-height:200px;overflow:auto}}figure{{display:inline-block;margin:8px}}</style>
<h1>Test Report ({mode})</h1>
<p><b>{counts['passed']}</b> passed, <b>{counts['flaky']}</b> flaky (passed on retry), <b>{counts['quarantined']}</b> quarantined, <b>{counts['failed']}</b> failed,
<b>{counts['error']}</b> errors, <b>{counts['skipped']}</b> skipped, {len(results)} total, took {took // 60}m {took % 60}s</p>
{('<h2>Video of the failing tests (rerun)</h2>' + videos) if videos else ''}
{('<h2>Screen at the end</h2>' + shots) if shots else ''}
<table><tr><th>Test</th><th>Status</th><th>Device</th><th>Details</th></tr>{rows}</table>"""
    (out_dir / "report.html").write_text(page, encoding="utf-8")
    write_junit(results, out_dir / "junit.xml", took)
    return counts

def write_junit(results, path, took):
    """Standard JUnit XML, readable by most CI tools and test dashboards."""
    from xml.sax.saxutils import quoteattr, escape
    by_device = {}
    for r in results:
        by_device.setdefault(r.get("device") or "device", []).append(r)
    suites = []
    for device, rs in by_device.items():
        fails = sum(r["status"] == "failed" for r in rs)
        errs = sum(r["status"] == "error" for r in rs)
        skips = sum(r["status"] in ("skipped", "quarantined") for r in rs)
        cases = []
        for r in rs:
            cls, _, name = r["name"].rpartition("#")
            attrs = f"classname={quoteattr(cls or 'bot')} name={quoteattr(name or r['name'])}"
            body = ""
            if r["status"] == "failed":
                body = f"<failure message={quoteattr(first_line(r['details']))}>{escape(r['details'])}</failure>"
            elif r["status"] == "error":
                body = f"<error message={quoteattr(first_line(r['details']))}>{escape(r['details'])}</error>"
            elif r["status"] == "skipped":
                body = "<skipped/>"
            elif r["status"] == "quarantined":
                body = f"<skipped message={quoteattr('quarantined: ' + first_line(r['details']))}/>"
            elif r["status"] == "flaky":
                body = f"<system-out>{escape('FLAKY: failed once, passed on retry.')}</system-out>"
            cases.append(f"    <testcase {attrs}>{body}</testcase>")
        suites.append(f"  <testsuite name={quoteattr(device)} tests=\"{len(rs)}\" failures=\"{fails}\" "
                      f"errors=\"{errs}\" skipped=\"{skips}\">\n" + "\n".join(cases) + "\n  </testsuite>")
    path.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuites time="{took}">\n'
                    + "\n".join(suites) + "\n</testsuites>\n", encoding="utf-8")

def print_problems(results, counts, label):
    bad = [r for r in results if r["status"] in ("failed", "error")]
    flaky = [r for r in results if r["status"] == "flaky"]
    for r in bad:
        log(f"FAILED: {r['name']}  ->  {first_line(r['details'])}")
    for r in flaky:
        log(f"FLAKY:  {r['name']}  ->  first try: {first_line(flaky_reason(r['details']))}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = [f"### {label}: {counts['passed']} passed, {counts['flaky']} flaky, "
                 f"{counts['quarantined']} quarantined, {counts['failed'] + counts['error']} failed"]
        lines += [f"- ❌ `{r['name']}`: {first_line(r['details'])}" for r in bad[:30]]
        lines += [f"- ⚠️ flaky `{r['name']}`: first try {first_line(flaky_reason(r['details']))}" for r in flaky[:30]]
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")

# ---------- main ----------
def main():
    global ADB, EMULATOR
    ap = argparse.ArgumentParser(description="Android test bot")
    ap.add_argument("mode", choices=["instrument", "maestro", "monkey"])
    ap.add_argument("--apk", required=True, help="your app APK")
    ap.add_argument("--test-apk", help="androidTest APK (instrument mode); ignored if the file doesn't exist")
    ap.add_argument("--flows", help="folder of Maestro .yaml flows (maestro mode)")
    ap.add_argument("--avd", help="emulator name (see: emulator -list-avds). Skip to use already-running devices")
    ap.add_argument("--emulators", type=int, default=2, help="how many emulators to run in parallel")
    ap.add_argument("--events", type=int, default=4000, help="random events per device (monkey mode)")
    ap.add_argument("--arg", action="append", default=[], help="extra instrument arg, e.g. --arg class=com.x.LoginTest")
    ap.add_argument("--show", action="store_true", help="show emulator windows")
    ap.add_argument("--shard", default="0/1", help="run only part i of n (0-based), e.g. 2/4; used by GitHub Actions")
    ap.add_argument("--label", default=os.environ.get("DEVICE_LABEL", "Results"), help="name shown in the summary")
    ap.add_argument("--quarantine", help="file listing known-flaky tests; their failures don't fail the run")
    ap.add_argument("--video", action="store_true", help="record the screen while failed tests rerun")
    ap.add_argument("--out", default="test-results")
    a = ap.parse_args()
    if a.test_apk and not Path(a.test_apk).exists():
        a.test_apk = None

    ADB = tool("adb", "platform-tools")
    started = time.time()
    root = Path(a.out)
    out_dir = root / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    part, parts = (int(x) for x in a.shard.split("/"))

    procs, serials = [], []
    try:
        if a.avd:
            EMULATOR = tool("emulator", "emulator")
            procs, serials = start_emulators(a.avd, a.emulators, a.show, out_dir)
        else:
            out = run([ADB, "devices"]).stdout.splitlines()[1:]
            serials = [l.split()[0] for l in out if l.strip().endswith("device")]
            if not serials:
                raise SetupError("No devices running. Pass --avd <name> or start an emulator first.")
            log(f"Using running devices: {', '.join(serials)}")

        before = third_party_packages(serials[0])
        for s in serials:
            log(f"{s}: installing app")
            install(s, a.apk)
            if a.test_apk:
                install(s, a.test_apk)
            adb(s, "logcat", "-c", timeout=30)
            quiet_device(s)
            settle(s)
        app_pkgs = third_party_packages(serials[0]) - before

        results = []
        if a.mode == "instrument":
            if not a.test_apk:
                sys.exit("instrument mode needs --test-apk")
            runner = find_runner(serials[0], app_pkgs)
            total = parts * len(serials)
            log(f"Test runner: {runner}; part {part + 1}/{parts}, split across {len(serials)} device(s)")
            threads = [threading.Thread(target=run_shard,
                       args=(s, runner, part * len(serials) + i, total, a.arg, results, out_dir, a.video))
                       for i, s in enumerate(serials)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        elif a.mode == "maestro":
            if not a.flows:
                sys.exit("maestro mode needs --flows <folder>")
            run_maestro(serials, a.flows, results, out_dir, part, parts)
        else:
            pkg = next((p for p in app_pkgs if not p.endswith(".test")), None)
            if not pkg:
                raise SetupError("Couldn't detect the app package (was it already installed?). Uninstall it and retry.")
            threads = [threading.Thread(target=run_monkey,
                       args=(s, pkg, a.events, 1000 + part * 100 + i, results, out_dir))
                       for i, s in enumerate(serials)]
            [t.start() for t in threads]
            [t.join() for t in threads]

        if not results:
            raise SetupError("The bot finished without running a single test.")
        apply_quarantine(results, load_quarantine(a.quarantine))
        if any(r["status"] in ("failed", "error") for r in results):
            for s in serials:
                screenshot(s, out_dir)

        counts = write_report(results, out_dir, started, a.mode)
        (root / "TESTS_RAN").write_text("yes")  # tells the workflow not to restart the emulator
        print_problems(results, counts, a.label)
        log(f"DONE: {counts['passed']} passed, {counts['flaky']} flaky, {counts['quarantined']} quarantined, "
            f"{counts['failed']} failed, {counts['error']} errors")
        log(f"Report: {out_dir / 'report.html'}")
        sys.exit(1 if counts["failed"] or counts["error"] else 0)
    except SetupError as e:
        log(f"SETUP PROBLEM: {e}")
        results = [{"name": "Setup problem", "status": "error", "details": str(e), "device": ", ".join(serials)}]
        write_report(results, out_dir, started, a.mode)
        sys.exit(3)
    finally:
        if procs:
            log("Shutting down emulators")
            stop_emulators(procs, serials)

if __name__ == "__main__":
    main()
