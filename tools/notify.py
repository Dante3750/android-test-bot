#!/usr/bin/env python3
"""
Send the results message to Telegram, Slack and/or Discord.

Usage: notify.py MESSAGE_FILE
Reads from the environment:
  RESULT            passed | flaky | failed
  NOTIFY_ON         failure (default) | always | never
  TELEGRAM_TOKEN + TELEGRAM_CHAT_ID, SLACK_WEBHOOK, DISCORD_WEBHOOK   (any you set up)
A channel that isn't set up is skipped. A failed send prints a warning; it never fails the build.
"""
import json, os, sys, urllib.request


def should_send(result, notify_on):
    if notify_on == "never":
        return False
    if notify_on == "always":
        return True
    return result == "failed"


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "android-test-bot"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def targets(env, text):
    out = []
    if env.get("TELEGRAM_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        out.append(("Telegram", f"{env.get('TELEGRAM_API', 'https://api.telegram.org')}/bot{env['TELEGRAM_TOKEN']}/sendMessage",
                    {"chat_id": env["TELEGRAM_CHAT_ID"], "text": text, "disable_web_page_preview": True}))
    if env.get("SLACK_WEBHOOK"):
        out.append(("Slack", env["SLACK_WEBHOOK"], {"text": text}))
    if env.get("DISCORD_WEBHOOK"):
        out.append(("Discord", env["DISCORD_WEBHOOK"], {"content": text[:1900]}))
    return out


def main():
    text = open(sys.argv[1], encoding="utf-8").read().strip()
    env = os.environ
    result, notify_on = env.get("RESULT", "failed"), env.get("NOTIFY_ON", "failure")
    if not should_send(result, notify_on):
        print(f"Not sending (result={result}, notify-on={notify_on}).")
        return
    chans = targets(env, text)
    if not chans:
        print("No notification channel set up (see README: Notifications).")
        return
    for name, url, payload in chans:
        try:
            post(url, payload)
            print(f"Sent to {name}.")
        except Exception as e:  # never fail the build because a chat app is down
            print(f"::warning::Could not send to {name}: {e}")


if __name__ == "__main__":
    main()
