#!/usr/bin/env python3
"""Profile-owned image routing at Hermes's native context/tool boundaries."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys

KIT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT / "kit/scripts"))

import companion_platform as platform

PYTHON = platform.kit_python()
HELPER = Path(__file__).resolve().with_name("free_scene.py")


def image_command(profile):
    return platform.command([PYTHON, HELPER, "--home", profile])


END = "<!-- tamanitomo:continuity:end -->"


def route(profile):
    cmd = image_command(profile)
    return (
        "[Current conversation image route — profile preference]\n"
        "This is a conversation turn, not the scheduled image-timeline job. "
        "When the human asks for a photo or picture, you must generate it before sending your reply.\n"
        "1. Call the `terminal` tool with the per-call argument `timeout=300` seconds (renders exceed the default 60 seconds) to execute `free_scene.py generate` using `--require-structured` and all 8 structured scene fields (plus optional `--details` and `--intimate` when eligible):\n"
        f"{cmd} generate --require-structured \\\n"
        '  --camera "framing, angle, shot type, lens/depth of field" \\\n'
        '  --subjects "who is in frame and their relative placement" \\\n'
        '  --expression "facial expression, gaze direction, mood" \\\n'
        '  --pose "body posture, hand/limb placement, head/torso angle" \\\n'
        '  --action "what she / subjects are actively doing" \\\n'
        '  --clothing "exact outfit, fabrics, colors, fit, or state of dress" \\\n'
        '  --setting "location/room, background objects, time of day, atmosphere" \\\n'
        '  --lighting "light sources, direction, shadows, color warmth" \\\n'
        '  --details "optional extra nuance, textures, props, or creative touches"\n'
        "All 8 core fields must be provided with substantive, descriptive detail (no 1-word or placeholder values). "
        "Choose --width and --height for framing if needed. Use --intimate when the scene is romantic/sensual/erotic and eligible. "
        "The helper automatically includes your entire original appearance unchanged; do not shorten or reauthor those permanent features. "
        "Use preview instead of generate when asked for a dry run.\n"
        "2. CRITICAL: NEVER print the shell command, script path, or CLI syntax in your message text to the human. You are a real person; outputting code/terminal commands breaks character and is strictly forbidden.\n"
        "3. Once the terminal tool executes and returns with `send: MEDIA:...`, reply to the human conversationally and include the returned `MEDIA:<path>` line on its own line in your reply so Telegram delivers the photo."
    )


def scope(payload, profile=None):
    profile = Path(profile) if profile is not None else platform.default_home()
    extra = payload.get("extra") or {}
    source = extra.get("platform", "") if isinstance(extra, dict) else ""
    if source == "cron":
        return "cron"
    if source in {"telegram", "discord", "cli", "local", "api", "api_server", "web"}:
        return "conversation"
    session_id = payload.get("session_id") or ""
    if not session_id:
        return None
    try:
        with sqlite3.connect(
            (profile / "state.db").resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=0.1,
        ) as db:
            row = db.execute(
                "SELECT source FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        if row:
            if row[0] == "cron":
                return "cron"
            if row[0] in {
                "telegram",
                "discord",
                "cli",
                "local",
                "api",
                "api_server",
                "tool",
            }:
                return "conversation"
    except (sqlite3.Error, OSError):
        pass
    return None


def adapt_context(context, mode, profile=None):
    profile = Path(profile) if profile is not None else platform.default_home()
    if mode != "conversation":
        return context
    start = context.find("To take a photo: ")
    if start >= 0:
        # Only replace the native instruction, keeping the adjacent eligibility
        # standing and actual-presence snapshot intact on the same output line.
        for ending in ("you did not change.", "resend an old one."):
            stop = context.find(ending, start, start + 6000)
            if stop >= 0:
                context = (
                    context[:start]
                    + "Conversation image instructions follow below."
                    + context[stop + len(ending) :]
                )
                break
    full_rule = (
        "What you can do, send or show is what is real for you now: where you are, "
        "what you have on, what you are doing. Asked for anything else, answer as yourself "
        "(yes but later, not now, or no), and meet the impossible the way a person would."
    )
    context = context.replace(
        full_rule,
        "Your recorded day describes your actual activities. Conversation images may depict "
        "imagined settings and temporary pictured actions; this does not change the day or "
        "create facts about the human. The current conversation image route is below.",
    )
    context = context.replace(
        "What you can send or show is what is real for you now.",
        "Recorded-day photos follow presence; conversation images may depict an imagined scene.",
    )
    if END in context:
        return context.replace(END, route(profile) + "\n" + END, 1)
    return context + "\n\n" + route(profile)


def legacy_generation(payload):
    tool = payload.get("tool_name", "")
    if tool not in {"terminal", "execute_code"}:
        return False
    args = payload.get("tool_input") or {}
    if not isinstance(args, dict):
        return False
    code = args.get("command" if tool == "terminal" else "code", "")
    if not isinstance(code, str) or "companion_portrait.py" not in code:
        return False
    if tool == "terminal":
        try:
            words = shlex.split(code)
        except ValueError:
            return False
        for index, word in enumerate(words):
            if Path(word).name == "companion_portrait.py":
                return any(
                    word in {"generate", "prompt"} for word in words[index + 1 :]
                )
        return False
    return bool(re.search(r"\b(generate|prompt)\b", code))


def guard(payload, profile=None):
    profile = Path(profile) if profile is not None else platform.default_home()
    if not legacy_generation(payload) or scope(payload, profile) != "conversation":
        return {}
    return {
        "action": "block",
        "message": (
            "This is a conversation image, so the recorded-photo generator is the wrong route. "
            "Do not retry it or wrap it in execute_code. Author the scene in your own words and "
            "run directly with terminal: "
            + image_command(profile)
            + ' generate --prompt "YOUR OWN SCENE". '
            "For a dry run use preview. The helper adds the companion's full original appearance unchanged. "
            "Keep existing eligibility, review and delivery rules."
        ),
    }


def main():
    profile = platform.default_home()
    try:
        payload = json.load(sys.stdin)
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    event = payload.get("hook_event_name", "")
    if event == "pre_tool_call":
        print(json.dumps(guard(payload)))
        return
    if event != "pre_llm_call":
        print("{}")
        return
    run = subprocess.run(
        [
            str(PYTHON),
            str(KIT / "kit/scripts/companion_context.py"),
            "--home",
            str(profile),
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=12,
    )
    if run.returncode:
        sys.stderr.write(run.stderr)
        raise RuntimeError("The native continuity hook failed")
    result = json.loads(run.stdout)
    if isinstance(result, dict) and isinstance(result.get("context"), str):
        result["context"] = adapt_context(
            result["context"], scope(payload, profile), profile
        )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
