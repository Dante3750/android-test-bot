"""
A fake `adb` for the bot's self-tests. It pretends to be one booted emulator.
FAKE_SCENARIO picks the behaviour; FAKE_STATE is a path prefix for small state files.
"""
import os, sys
from pathlib import Path

args = sys.argv[1:]
if args[:1] == ["-s"]:
    args = args[2:]
line = " ".join(args)
scenario = os.environ.get("FAKE_SCENARIO", "all_pass")
state = Path(os.environ.get("FAKE_STATE", "/tmp/fake_adb_state"))
installed = state.with_suffix(".installed")
runs = state.with_suffix(".runs")
with open(state.with_suffix(".calls"), "a") as f:
    f.write(line + "\n")


def test(cls, name, code, msg="java.lang.AssertionError: boom"):
    print(f"INSTRUMENTATION_STATUS: class={cls}\nINSTRUMENTATION_STATUS: test={name}\nINSTRUMENTATION_STATUS_CODE: 1")
    print(f"INSTRUMENTATION_STATUS: class={cls}\nINSTRUMENTATION_STATUS: test={name}")
    if code != 0:
        print(f"INSTRUMENTATION_STATUS: stack={msg} in {name}\n\tat {cls}.{name}(X.kt:1)")
    print(f"INSTRUMENTATION_STATUS_CODE: {code}")


if line == "devices":
    print("List of devices attached\nemulator-5554\tdevice")
elif "dumpsys window" in line:
    seen = state.with_suffix(".focus")
    n = int(seen.read_text()) + 1 if seen.exists() else 1
    seen.write_text(str(n))
    if scenario == "anr_popup" and n <= 2:
        print("  mCurrentFocus=Window{1a2b u0 Application Not Responding: com.android.systemui}")
    else:
        print("  mCurrentFocus=Window{3c4d u0 com.google.android.apps.nexuslauncher/.NexusLauncherActivity}")
elif "getprop sys.boot_completed" in line:
    print("1")
elif "pm list packages" in line:
    if installed.exists():
        print("package:com.demo\npackage:com.demo.test")
elif args[:1] == ["install"]:
    if scenario == "install_fails":
        print("Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]")
        sys.exit(1)
    installed.touch()
    print("Success")
elif "pm list instrumentation" in line:
    print("instrumentation:com.demo.test/androidx.test.runner.AndroidJUnitRunner (target=com.demo)")
elif "am instrument" in line:
    n = int(runs.read_text()) + 1 if runs.exists() else 1
    runs.write_text(str(n))
    rerun = "-e class" in line
    if scenario == "no_tests":
        pass
    elif scenario == "crash_once" and n == 1:
        test("com.demo.UiTest", "ok", 0)
        print("INSTRUMENTATION_RESULT: shortMsg=Process crashed.\nINSTRUMENTATION_CODE: 0")
        sys.exit(0)
    elif scenario == "flaky":
        test("com.demo.UiTest", "tap", 0 if rerun else -2)
        test("com.demo.LogicTest", "math", -2)
        if not rerun:
            test("com.demo.UiTest", "ok", 0)
    elif scenario == "only_flaky":
        test("com.demo.UiTest", "tap", 0 if rerun else -2)
        if not rerun:
            test("com.demo.UiTest", "ok", 0)
    else:
        test("com.demo.UiTest", "ok", 0)
        test("com.demo.P", "add[a_1_2]", 0)
    print("INSTRUMENTATION_CODE: -1")
elif "monkey" in line:
    if scenario == "monkey_crash":
        print("// CRASH: com.demo (pid 123)\n// Short Msg: java.lang.NullPointerException")
        print("** Monkey aborted due to error.")
    else:
        print("Events injected: 50\n// Monkey finished")
elif args[:2] == ["shell", "screenrecord"]:
    import time; time.sleep(0.2)
elif args[:1] == ["pull"]:
    Path(args[2]).write_bytes(b"fake mp4")
elif "screencap" in line:
    sys.stdout.buffer.write(b"\x89PNG fake")
# everything else (settings, logcat, input, emu kill, wait-for-device): succeed quietly
