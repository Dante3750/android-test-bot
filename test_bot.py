#!/usr/bin/env python3
"""
Android Test Bot
----------------
Starts Android emulators, installs your app, runs your full test suite
(split across several emulators in parallel), and writes an HTML report.

Modes:
  instrument  - run your Espresso / JUnit androidTest tests (thousands is fine)
  maestro     - run a folder of Maestro YAML flows
  monkey      - random tap/swipe stress test (e.g. 4000 events)

Examples:
  python test_bot.py instrument --apk app-debug.apk --test-apk app-debug-androidTest.apk --avd Pixel_7 --emulators 4
  python test_bot.py maestro --apk app-debug.apk --flows ./flows --avd Pixel_7 --emulators 2
  python test_bot.py monkey  --apk app-debug.apk --avd Pixel_7 --events 4000
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

def run(cmd, timeout=None, check=False):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r

def adb(serial, *args, timeout=None, check=False):
    return run([ADB, "-s", serial, *args], timeout=timeout, check=check)

def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)

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
        if adb(serial, "shell", "getprop", "sys.boot_completed").stdout.strip() == "1":
            log(f"{serial} ready ({int(time.time() - start)}s)")
            return
        time.sleep(3)
    raise TimeoutError(f"{serial} did not boot in {timeout}s")

def stop_emulators(procs, serials):
    for s in serials:
        adb(s, "emu", "kill")
    for p in procs:
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            p.kill()

def third_party_packages(serial):
    out = adb(serial, "shell", "pm", "list", "packages", "-3").stdout
    return {l.replace("package:", "").strip() for l in out.splitlines() if l.strip()}

def install(serial, apk):
    adb(serial, "install", "-r", "-t", "-g", apk, timeout=600, check=True)

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
    out = adb(serial, "shell", "pm", "list", "instrumentation").stdout
    for line in out.splitlines():
        # instrumentation:com.x.test/androidx.test.runner.AndroidJUnitRunner (target=com.x)
        if "target=" in line:
            target = line.split("target=")[1].rstrip(")").strip()
            if not app_pkgs or target in app_pkgs:
                return line.split("instrumentation:")[1].split(" ")[0]
    sys.exit("No test runner found. Did you pass the androidTest APK with --test-apk?")

def run_shard(serial, runner, index, total, extra, results, out_dir):
    log(f"{serial}: running shard {index + 1}/{total}")
    cmd = ["shell", "am", "instrument", "-r", "-w",
           "-e", "numShards", str(total), "-e", "shardIndex", str(index)]
    for e in extra:
        k, _, v = e.partition("=")
        cmd += ["-e", k, v]
    out = adb(serial, *cmd, runner, timeout=6 * 3600).stdout
    (out_dir / f"{serial}-instrument.txt").write_text(out, encoding="utf-8")
    shard = parse_instrument(out)
    for r in shard:
        r["device"] = serial
    if "INSTRUMENTATION_FAILED" in out or "Process crashed" in out:
        shard.append({"name": f"shard {index + 1} crashed", "status": "error",
                      "details": out[-3000:], "device": serial})
    results.extend(shard)
    save_logcat(serial, out_dir)
    log(f"{serial}: shard done, {sum(r['status'] == 'passed' for r in shard)}/{len(shard)} passed")

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
            ok = r.returncode == 0
            results.append({"name": str(f), "status": "passed" if ok else "failed",
                            "details": "" if ok else (r.stdout + r.stderr)[-3000:], "device": serial})
            log(f"{serial}: {'PASS' if ok else 'FAIL'} {f.name}")

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
    results.append({"name": f"Monkey {events} events (seed {seed})",
                    "status": "failed" if crashed else "passed",
                    "details": out[-4000:] if crashed else "", "device": serial})
    save_logcat(serial, out_dir)

def save_logcat(serial, out_dir):
    out = adb(serial, "logcat", "-d", "-b", "crash,main", timeout=120).stdout
    (out_dir / f"{serial}-logcat.txt").write_text(out, encoding="utf-8")

# ---------- report ----------
def write_report(results, out_dir, started, mode):
    counts = {s: sum(r["status"] == s for r in results) for s in ("passed", "failed", "error", "skipped")}
    (out_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    took = int(time.time() - started)
    rows = "".join(
        f"<tr class='{r['status']}'><td>{html.escape(r['name'])}</td><td>{r['status']}</td>"
        f"<td>{r['device']}</td><td><pre>{html.escape(r['details'][:3000])}</pre></td></tr>"
        for r in sorted(results, key=lambda r: r["status"] == "passed"))
    page = f"""<!doctype html><meta charset=utf-8><title>Test Report</title>
<style>body{{font-family:system-ui;margin:24px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:6px;text-align:left;vertical-align:top}}
.failed,.error{{background:#fde2e2}}.passed{{background:#e4f7e4}}.skipped{{background:#f2f2f2}}
pre{{white-space:pre-wrap;margin:0;font-size:12px;max-height:200px;overflow:auto}}</style>
<h1>Test Report ({mode})</h1>
<p><b>{counts['passed']}</b> passed, <b>{counts['failed']}</b> failed, <b>{counts['error']}</b> errors,
<b>{counts['skipped']}</b> skipped, {len(results)} total, took {took // 60}m {took % 60}s</p>
<table><tr><th>Test</th><th>Status</th><th>Device</th><th>Details</th></tr>{rows}</table>"""
    (out_dir / "report.html").write_text(page, encoding="utf-8")
    return counts

# ---------- main ----------
def main():
    global ADB, EMULATOR
    ap = argparse.ArgumentParser(description="Android test bot")
    ap.add_argument("mode", choices=["instrument", "maestro", "monkey"])
    ap.add_argument("--apk", required=True, help="your app APK")
    ap.add_argument("--test-apk", help="androidTest APK (instrument mode)")
    ap.add_argument("--flows", help="folder of Maestro .yaml flows (maestro mode)")
    ap.add_argument("--avd", help="emulator name (see: emulator -list-avds). Skip to use already-running devices")
    ap.add_argument("--emulators", type=int, default=2, help="how many emulators to run in parallel")
    ap.add_argument("--events", type=int, default=4000, help="random events per device (monkey mode)")
    ap.add_argument("--arg", action="append", default=[], help="extra instrument arg, e.g. --arg class=com.x.LoginTest")
    ap.add_argument("--show", action="store_true", help="show emulator windows")
    ap.add_argument("--shard", default="0/1", help="run only part i of n, e.g. 2/4 (used by GitHub Actions to split tests)")
    ap.add_argument("--out", default="test-results")
    a = ap.parse_args()

    ADB = tool("adb", "platform-tools")
    started = time.time()
    out_dir = Path(a.out) / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    procs, serials = [], []
    try:
        if a.avd:
            EMULATOR = tool("emulator", "emulator")
            procs, serials = start_emulators(a.avd, a.emulators, a.show, out_dir)
        else:
            out = run([ADB, "devices"]).stdout.splitlines()[1:]
            serials = [l.split()[0] for l in out if l.strip().endswith("device")]
            if not serials:
                sys.exit("No devices running. Pass --avd <name> or start an emulator first.")
            log(f"Using running devices: {', '.join(serials)}")

        before = third_party_packages(serials[0])
        for s in serials:
            log(f"{s}: installing app")
            install(s, a.apk)
            if a.test_apk:
                install(s, a.test_apk)
            adb(s, "logcat", "-c")
        app_pkgs = third_party_packages(serials[0]) - before

        results = []
        if a.mode == "instrument":
            if not a.test_apk:
                sys.exit("instrument mode needs --test-apk")
            runner = find_runner(serials[0], app_pkgs)
            part, parts = (int(x) for x in a.shard.split("/"))
            total = parts * len(serials)
            log(f"Test runner: {runner}; part {part + 1}/{parts}, split across {len(serials)} device(s)")
            threads = [threading.Thread(target=run_shard,
                       args=(s, runner, part * len(serials) + i, total, a.arg, results, out_dir))
                       for i, s in enumerate(serials)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        elif a.mode == "maestro":
            if not a.flows:
                sys.exit("maestro mode needs --flows <folder>")
            part, parts = (int(x) for x in a.shard.split("/"))
            run_maestro(serials, a.flows, results, out_dir, part, parts)
        else:
            pkg = next((p for p in app_pkgs if not p.endswith(".test")), None)
            if not pkg:
                sys.exit("Couldn't detect the app package (was it already installed?). Uninstall it and retry.")
            threads = [threading.Thread(target=run_monkey,
                       args=(s, pkg, a.events, 1000 + int(a.shard.split("/")[0]) * 100 + i, results, out_dir))
                       for i, s in enumerate(serials)]
            [t.start() for t in threads]
            [t.join() for t in threads]

        if not results:
            results.append({"name": "No tests ran", "status": "error", "device": ", ".join(serials),
                            "details": "The bot finished without running a single test. Check the logs in this folder."})
        counts = write_report(results, out_dir, started, a.mode)
        log(f"DONE: {counts['passed']} passed, {counts['failed']} failed, {counts['error']} errors")
        log(f"Report: {out_dir / 'report.html'}")
        sys.exit(1 if counts["failed"] or counts["error"] else 0)
    finally:
        if procs:
            log("Shutting down emulators")
            stop_emulators(procs, serials)

if __name__ == "__main__":
    main()
