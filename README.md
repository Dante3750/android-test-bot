# Android Test Bot

A bot that starts Android emulators, installs your app, runs your **whole** test suite (thousands of tests is fine), and gives you an HTML report.

It can run on your own PC, or on GitHub's servers with 4 emulators at once.

## Run it on GitHub (no setup on your PC)

1. Build your app: `./gradlew assembleDebug assembleDebugAndroidTest`
2. Copy the APKs into the `apks/` folder of this repo:
   - `app/build/outputs/apk/debug/app-debug.apk` → `apks/app.apk`
   - `app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk` → `apks/test.apk` (only if you have tests)
3. Commit and push. The tests start automatically.
   You can also run them any time: **Actions** tab → **Android Tests** → **Run workflow**, then pick a mode.
4. When it finishes, open the run and download the **test-report-part-0…3** files. Open `report.html` inside.

Your tests are split across 4 emulators, so 4,000 tests run as 4 groups of about 1,000 at the same time.
To use more, edit `part: [0, 1, 2, 3]` and `PARTS: 4` in `.github/workflows/android-tests.yml`.

## Test on 104 devices at once

The **Android Matrix (104 devices)** workflow runs your app on 13 Android versions
(Android 7 to 16, API 24–36) × 8 device types (small and large phones, tablets).
Actions tab → **Android Matrix (104 devices)** → **Run workflow**.

When it's done, the run page shows one table with ✅ or ❌ for every device, and you can download each device's report.
A free account runs about 20 emulators at a time, so all 104 take roughly 1–2 hours.

## Run it on your PC

Needs Android Studio (with an emulator created in Device Manager) and Python 3.
Set `ANDROID_HOME` to your Android SDK folder.

```bash
# Your Espresso / JUnit tests, split across 4 emulators
python test_bot.py instrument --apk apks/app.apk --test-apk apks/test.apk --avd Pixel_7 --emulators 4

# 4000 random taps/swipes to find crashes (no test code needed)
python test_bot.py monkey --apk apks/app.apk --avd Pixel_7 --events 4000

# Maestro flows (put .yaml files in flows/)
python test_bot.py maestro --apk apks/app.apk --flows flows --avd Pixel_7 --emulators 2
```

Leave out `--avd` to use an emulator or phone that's already running.
Add `--show` to see the emulator windows.
Run only some tests: `--arg class=com.example.LoginTest`

Results go to `test-results/<date>/`: `report.html`, `results.json`, and logs for each device.

## Modes

| Mode | What it does | Needs |
|---|---|---|
| `instrument` | Runs your androidTest tests (Espresso, UI Automator, JUnit) | app APK + test APK |
| `monkey` | Random taps and swipes; fails if the app crashes or freezes | app APK only |
| `maestro` | Runs simple YAML test flows | app APK + `flows/*.yaml`, [Maestro](https://maestro.mobile.dev) |

## Note on GitHub minutes

Public repos get free Actions minutes. Private repos have a monthly free limit, and 4 emulators use 4× the minutes, so check **Settings → Billing** if you run it a lot.
