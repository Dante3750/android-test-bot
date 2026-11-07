# Android Test Bot

Tests your Android app on many Android versions and devices at once, on GitHub's servers, for free on public repos.

It starts emulators, installs your app, runs your **whole** test suite (thousands of tests is fine), reruns failures to separate real bugs from flaky tests, and gives you one table of results.

- **Android 7 to 16** (API 24–36) on phones, small phones, big phones and tablets
- **Your Espresso / JUnit tests**, random-tap stress tests, or Maestro flows
- **Big test suites split** across several emulators per device
- **Clear results:** ✅ passed, ⚠️ flaky, ❌ failed, with the failing test names right on the run page
- **A comment on every pull request** with the results, updated on each push
- **A video of each real failure**, recorded while the failed test reruns
- **Flaky-test history** across runs, and a quarantine list for tests you haven't fixed yet
- **Telegram, Slack or Discord messages** when tests fail

## Use it in your app's repo

Add this file to your app repo as `.github/workflows/android-tests.yml`:

```yaml
name: Android tests

on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: "30 21 * * *"   # every night at 03:00 India time (21:30 UTC)
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-java@v6
        with:
          distribution: temurin
          java-version: "17"
      - uses: gradle/actions/setup-gradle@v6
      - name: Build app and tests
        run: ./gradlew assembleDebug assembleDebugAndroidTest
      - uses: actions/upload-artifact@v7
        with:
          name: apks
          path: |
            app/build/outputs/apk/debug/*.apk
            app/build/outputs/apk/androidTest/debug/*.apk

  test:
    needs: build
    permissions:
      contents: write        # lets the bot keep the flaky-test history
      pull-requests: write   # lets the bot comment results on pull requests
    uses: Dante3750/android-test-bot/.github/workflows/android-test.yml@v1
    secrets: inherit         # passes your Telegram/Slack/Discord secrets, if you set any
    with:
      apks-artifact: apks
      title: My app
      # Pull requests: one phone, tests split 4 ways (fast). Main branch and nightly: 30 devices.
      devices: ${{ github.event_name == 'pull_request' && 'single' || 'quick' }}
      shards: ${{ github.event_name == 'pull_request' && 4 || 1 }}
```

If your app module isn't called `app`, change the two `app/build/...` paths.

That's it. Every push and pull request is tested, and every night the full quick set (30 devices) runs. Pull requests get a results comment, and GitHub emails you when a run fails.

### Options

| Option | Default | What it does |
|---|---|---|
| `apks-artifact` | (required) | Name of the artifact holding your APKs. The app APK and the `androidTest` APK are found automatically |
| `devices` | `quick` | `quick` = 30 devices, `full` = 104, `single` = one Pixel 6 at `api-level`, or your own JSON list, e.g. `'[{"api":34,"device":"pixel_6"},{"api":35,"device":"pixel_tablet"}]'` |
| `api-level` | `34` | Android version for `single` |
| `shards` | `1` | Split each device's tests into this many emulators running at the same time (2–8 for big suites) |
| `mode` | `instrument` | `instrument` (your androidTest tests), `monkey` (random taps), `maestro` (YAML flows) |
| `events` | `4000` | Random taps per device in `monkey` mode |
| `flows` | `flows` | Folder of Maestro flows in your repo |
| `test-args` | | Extra runner options, e.g. `annotation=com.example.Smoke` or `class=com.example.LoginTest` |
| `app-apk` / `test-apk` | auto | Exact APK file names, if auto-detection picks the wrong ones |
| `max-parallel` | `20` | Most emulators at the same time |
| `title` | `Android tests` | Name shown in results, PR comments and messages |
| `quarantine-file` | `.github/flaky-tests.txt` | Known-flaky tests; they still run but can't fail the build |
| `record-video` | `true` | Record a video while failed tests rerun |
| `pr-comment` | `true` | Comment results on pull requests |
| `history` | `true` | Keep the flaky-test history (main branch and nightly runs) |
| `notify-on` | `failure` | When to send chat messages: `failure`, `always`, `never` |
| `bot-ref` | `v1` | Bot version to use |

The workflow also gives back `result` (`passed`, `flaky` or `failed`) if a later job wants to use it.

### Quick vs full device lists

- **quick (30):** every Android version 7–16 on a Pixel 6, plus a small phone (Pixel) on 7/9/12/14/16, a big phone (Pixel 7) on 13–16, a budget phone (Pixel 3a) on 8/10, an old tablet (Pixel C) on 7/9, and a new tablet (Pixel Tablet) on 11/13/15/16.
- **full (104):** every Android version 7–16 × 8 device types.

A free account runs about 20 emulators at a time. Quick takes about 20–30 minutes, full about 1–2 hours.

## Pull request comments

On a pull request the bot posts one comment with the result, the problem tests and which devices they failed on, plus a link to the run. Later pushes update the same comment instead of adding new ones.

## Videos of failures

When a test fails, the bot reruns it while recording the screen. If it fails again, the video is kept in that device's report (`*-failure-*.mp4`, also playable inside `report.html`), so you can watch exactly what happened. If it passes on the rerun, it's just flaky, and the video is thrown away.

## Flaky-test history and quarantine

After each run on your main branch (including the nightly run), the bot updates an **`android-test-history`** branch in your repo. Open **`FLAKY.md`** on that branch to see:

- which tests had problems, in how many of the last 60 runs, and what to do about each (🔴 failing every run, 🟡 often flaky)
- the last 20 runs with their results

To stop a known-flaky test from blocking you while you fix it, add it to **`.github/flaky-tests.txt`**:

```
# one per line; a reason after " #" is fine
com.example.LoginTest#signInWithGoogle   # times out on slow emulators
com.example.CameraTest                    # whole class
```

Quarantined tests still run and still show up (as quarantined) in the results and history, but they can't fail the build. Remove them once they're fixed.

## Notifications

Add any of these as repository secrets (**Settings → Secrets and variables → Actions → New repository secret**):

| Chat app | Secrets | How to get them |
|---|---|---|
| Telegram | `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` | Message **@BotFather** → `/newbot` for the token. Send your new bot a message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id` |
| Slack | `SLACK_WEBHOOK` | Slack → Apps → **Incoming Webhooks** → add to a channel |
| Discord | `DISCORD_WEBHOOK` | Channel settings → Integrations → **Webhooks** → New webhook → Copy URL |

By default you get a message only when tests fail. Set `notify-on: always` to hear about every run.

## How it handles problems

- **Failed tests are rerun once.** If a test fails and then passes, it's marked ⚠️ *flaky*. That usually means a timing problem in the test, not a bug in your app. Flaky tests don't fail the run, but fix them when you can.
- **System popups are kept away.** On slow emulators, Google's own apps often stop responding, and their "isn't responding" popup covers your app so every UI test fails. The bot switches those popups off and closes any that are already open before tests start. Each emulator also gets 3 CPU cores instead of 2, so it stops responding less often. Crashes of *your* app are still reported.
- **Broken emulators are restarted.** If the emulator doesn't boot or the app install hangs (no tests ran), the job starts a fresh emulator and tries again once.
- **Stuck installs fail fast:** 2 minutes, then one more try.
- **Failing test names are shown** in the job log, in each job's summary, and in the results table.
- **Reports for every device** can be downloaded from the run page: `report.html`, `results.json`, `junit.xml` (the standard format other tools read), logs, failure videos, and a screenshot when something failed.

## Common failures and what they mean

| You see | It usually means |
|---|---|
| `NoMatchingViewException` only on Android 15+ | Android 15 draws apps edge-to-edge, under the status bar. Your top buttons are covered, so taps miss. Handle window insets (the sample app shows how) |
| `RootViewWithoutFocusException` on every UI test | Something covered your app. The bot switches off error popups, so look for your own dialogs or permission prompts; the job log names the window that had focus, and the failure video shows it |
| ⚠️ flaky on slow devices | The test depends on timing. Wait for the condition with Espresso idling resources, not fixed sleeps |
| ❌ did not start | The emulator never booted, even after a restart. Usually a GitHub problem; rerun the job |
| No PR comment or no history | The calling job is missing the `permissions:` block from the setup above |

## Try it in this repo

This repo has a small sample app (`sample-app/`, 406 tests). With no `apks/app.apk`, the workflows build and test it:

- **Android Tests:** the sample app on one Android version, split across 4 emulators. Runs on every push to `apks/` or `sample-app/`.
- **Android Matrix (many devices):** `quick` or `full`, from the Actions tab → Run workflow.

To test another app here without changing its repo, put `app.apk` (and `test.apk`) in `apks/` and push.

## Run it on your PC

Needs Android Studio (with an emulator created in Device Manager) and Python 3. Set `ANDROID_HOME` to your Android SDK folder.

```bash
# Your Espresso / JUnit tests, split across 4 emulators
python test_bot.py instrument --apk app.apk --test-apk app-androidTest.apk --avd Pixel_7 --emulators 4

# 4000 random taps/swipes to find crashes (no test code needed)
python test_bot.py monkey --apk app.apk --avd Pixel_7 --events 4000

# Maestro flows (put .yaml files in flows/)
python test_bot.py maestro --apk app.apk --flows flows --avd Pixel_7 --emulators 2
```

Leave out `--avd` to use an emulator or phone that's already running. Add `--show` to see the emulator windows. Run only some tests with `--arg class=com.example.LoginTest`.

Add `--video` to record failures, and `--quarantine flaky-tests.txt` to use a quarantine list.

Results go to `test-results/<date>/`. Exit code: `0` all passed (flaky allowed), `1` tests failed, `3` setup problem (device or install).

## Working on the bot

`python -m pytest tests -q` runs the bot's self-tests (a fake device, plus the summary, history and notification tools) in about 20 seconds. The **Bot self-check** workflow runs them, plus a lint of every workflow, on every push.

## Versions

Apps use `@v1`, a branch that always points at the latest tested 1.x version. To release a change: push it to `main`, wait for **Bot self-check** and a **Android Matrix (many devices)** run to pass, then move `v1` forward with `git push origin main:v1`. A change that would break existing callers goes to a new `v2` branch instead.
