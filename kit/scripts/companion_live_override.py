#!/usr/bin/env python3
"""On-the-fly schedule and presence override for mid-conversation plan changes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# If executed as script under a foreign python (like Hermes python3.14 runner), re-exec using project venv
if __name__ == "__main__":
    _VENV_PY = Path(__file__).resolve().parents[2] / ".venv/bin/python"
    if _VENV_PY.is_file() and sys.executable != str(_VENV_PY):
        _clean_env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
        _clean_env["PATH"] = f"{_VENV_PY.parent}:{_clean_env.get('PATH', '')}"
        os.execve(str(_VENV_PY), [str(_VENV_PY)] + sys.argv, _clean_env)

# Scrub foreign PYTHONPATH/VIRTUAL_ENV and foreign site-packages from sys.path
os.environ.pop("PYTHONPATH", None)
os.environ.pop("VIRTUAL_ENV", None)
_current_py = f"python{sys.version_info.major}.{sys.version_info.minor}"
sys.path = [
    p for p in sys.path
    if not ("site-packages" in p and _current_py not in p)
]

import argparse
import datetime as dt
import json
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion_config as cc
import companion_plan as plan
import companion_presence as presence


def override(
    c,
    activity: str,
    setting: str = "",
    reason: str = "",
    pose: str = "",
    mood: str = "",
    duration: int = 60,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> dict:
    """Displace conflicting plan items and transition presence immediately."""
    if not activity or not activity.strip():
        raise ValueError("Activity cannot be empty")
    activity = activity.strip()
    setting = (setting or "").strip()
    reason = (reason or f"Agreed mid-conversation with {c.human}").strip()
    mood = (mood or "relaxed").strip()
    duration = max(15, int(duration or 60))

    tz = ZoneInfo(c.timezone)
    now = now or dt.datetime.now(tz)
    today = now.date()

    start_hhmm = now.strftime("%H:%M")
    end_dt = now + dt.timedelta(minutes=duration)
    end_hhmm = end_dt.strftime("%H:%M")

    new_item = {
        "what": activity,
        "setting": setting or "home",
        "start": start_hhmm,
        "end": end_hhmm,
        "kind": "other",
        "status": "planned",
        "reason": reason,
    }
    candidate = plan.clean_item(new_item)

    current_plan = plan.read(c, today) or plan.empty(today)
    clashes = [
        item for item in current_plan.get("items", [])
        if item.get("status") == "planned" and plan.overlaps(item, candidate)
    ]
    displaced_ids = [item["id"] for item in clashes]

    if not dry_run:
        with plan.file_lock(plan.folder(c) / ".lock"):
            p = plan.read(c, today) or plan.empty(today)
            for item in p.get("items", []):
                if item["id"] in displaced_ids and item.get("status") == "planned":
                    item["status"] = "moved"
                    item["reason"] = f"displaced by {candidate['what'][:80]}"[:300]
            p["items"] = [x for x in p.get("items", []) if x["id"] != candidate["id"]] + [candidate]
            note = f"live override: {candidate['what'][:40]}"
            if displaced_ids:
                note += f" (displaced {len(displaced_ids)})"
            plan._write(c, today, p, note=note, now=now)

        privacy_setting = setting if setting in ("private", "public") else None
        room = setting.strip().title() if setting and setting not in ("private", "public") else ""
        loc = f"home, {setting.strip().lower()}" if room else ""
        narrative = f"{activity.capitalize()}."
        try:
            presence_result = presence.transition_scene(
                c,
                activity=activity,
                reason=reason,
                text_=narrative,
                location=loc,
                room=room,
                setting=privacy_setting,
                pose=pose,
                mood=mood,
                duration=duration,
                now=now,
            )
        except ValueError:
            # If no prior scene was ever recorded, write the initial state
            initial_rec = {
                "activity": activity,
                "setting": privacy_setting or "private",
                "transition": reason,
                "text": narrative,
                "pose": pose,
                "mood": mood,
                "duration_minutes": duration,
                "previous_id": None,
                "outfit": ["towel"],
                "location": loc or "home",
            }
            presence_result = presence.update(c, initial_rec, now=now)
    else:
        presence_result = {"dry_run": True, "activity": activity, "setting": setting}

    return {
        "status": "ok",
        "activity": activity,
        "setting": setting,
        "duration": duration,
        "start": start_hhmm,
        "end": end_hhmm,
        "displaced_count": len(displaced_ids),
        "displaced_ids": displaced_ids,
        "presence": presence_result,
        "plan_item": candidate,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="nell", help="Companion profile name")
    parser.add_argument("--home", type=Path, help="Override companion home directory")
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes to disk")

    sub = parser.add_subparsers(dest="subcommand")
    trans_p = sub.add_parser("transition", help="Transition live presence and override active plan")
    trans_p.add_argument("--activity", required=True, help="New activity description")
    trans_p.add_argument("--setting", default="", help="Setting / room / environment")
    trans_p.add_argument("--reason", default="", help="Reason for transition/override")
    trans_p.add_argument("--pose", default="", help="Physical posture / pose")
    trans_p.add_argument("--mood", default="", help="Companion mood / expression")
    trans_p.add_argument("--duration", type=int, default=60, help="Duration in minutes")
    trans_p.add_argument("--dry-run", action="store_true", help="Do not write changes to disk")

    args = parser.parse_args()
    if not args.subcommand:
        parser.print_help()
        sys.exit(1)

    home = args.home
    if not home and args.profile:
        candidate = Path.home() / f".hermes/profiles/{args.profile}"
        if candidate.is_dir():
            home = candidate
    c = cc.load(home)
    result = override(
        c,
        activity=args.activity,
        setting=args.setting,
        reason=args.reason,
        pose=args.pose,
        mood=args.mood,
        duration=args.duration,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
