"""Tests for active-chat timeline skip, outbox hold, and live scene override."""

import datetime as dt
import json
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "kit/scripts"))

import companion_config as cc
import companion_dispatch as dispatch
import companion_live_override as live_override
import companion_plan as plan
import companion_presence as presence
import companion_timeline as timeline


class ActiveChatTimelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "companion.json").write_text(
            json.dumps({"name": "nell", "human": "Martin", "timezone": "Europe/London", "image_timeline": True})
        )
        self.c = cc.load(self.home)
        self.now = dt.datetime(2026, 11, 27, 15, 0, tzinfo=ZoneInfo("Europe/London"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_timeline_skip_when_chat_active_within_30_minutes(self):
        # 12 minutes since Martin spoke (< 0.5h)
        mock_thread = {"available": True, "hours_since_human": 0.2}
        with patch("companion_thread.read", return_value=mock_thread):
            refusal, _, _ = timeline._refusal(self.c, self.now)
            self.assertIsNotNone(refusal)
            self.assertFalse(refusal["ready"])
            self.assertIn("Active conversation in progress", refusal["reason"])

            gate_res = timeline.gate(self.c, self.now)
            self.assertFalse(gate_res["wakeAgent"])

            fp = timeline.fingerprint(self.c, self.now)
            self.assertIn("active conversation", fp)

    def test_timeline_not_skipped_when_chat_idle_over_30_minutes(self):
        # 45 minutes since Martin spoke (>= 0.5h)
        mock_thread = {"available": True, "hours_since_human": 0.75}
        with patch("companion_thread.read", return_value=mock_thread),              patch("companion_sleep.status", return_value={"asleep": False}),              patch("companion_timeline.current", return_value={"state": {"activity": "reading"}}),              patch("companion_timeline.provider_down", return_value=None):
            refusal, _, _ = timeline._refusal(self.c, self.now)
            # Not refused by active conversation
            if refusal:
                self.assertNotIn("Active conversation in progress", refusal.get("reason", ""))


class ActiveChatOutboxHoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / "companion.json").write_text(
            json.dumps({
                "name": "nell",
                "human": "Martin",
                "timezone": "Europe/London",
                "quiet_start": "23:00",
                "quiet_end": "07:00",
                "outreach_spacing_minutes": 0,
            })
        )
        self.c = cc.load(self.home)
        self.now = dt.datetime(2026, 11, 27, 15, 0, tzinfo=ZoneInfo("Europe/London"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_unprompted_timeline_image_held_when_chat_active(self):
        entry = {
            "kind": "image",
            "body": "Look what I made!",
            "media_path": "/tmp/photo.png",
            "source": "timeline",
            "priority": "normal",
        }
        mock_thread = {"available": True, "hours_since_human": 0.1}
        with patch("companion_thread.read", return_value=mock_thread):
            res = dispatch.verdict(self.c, entry, self.now)
            self.assertEqual(res["action"], "hold")
            self.assertIn("active conversation in progress", res["reason"])

    def test_high_priority_or_urgent_bypasses_active_chat_hold(self):
        entry = {
            "kind": "image",
            "body": "Here is the photo you asked for!",
            "media_path": "/tmp/photo.png",
            "source": "conversation",
            "priority": "high",
        }
        mock_thread = {"available": True, "hours_since_human": 0.1}
        with patch("companion_thread.read", return_value=mock_thread),              patch("companion_dispatch.wearable_says", return_value=(False, False)),              patch("companion_outreach.in_quiet_hours", return_value=False),              patch("companion_outreach.decide", return_value={"allowed": True, "reason": "ok"}):
            res = dispatch.verdict(self.c, entry, self.now)
            self.assertNotEqual(res["action"], "hold")

    def test_idle_chat_releases_timeline_hold(self):
        entry = {
            "kind": "image",
            "body": "Afternoon photo",
            "media_path": "/tmp/photo.png",
            "source": "timeline",
            "priority": "normal",
        }
        mock_thread = {"available": True, "hours_since_human": 1.2}
        with patch("companion_thread.read", return_value=mock_thread),              patch("companion_dispatch.wearable_says", return_value=(False, False)),              patch("companion_outreach.in_quiet_hours", return_value=False),              patch("companion_outreach.decide", return_value={"allowed": True, "reason": "ok"}):
            res = dispatch.verdict(self.c, entry, self.now)
            self.assertEqual(res["action"], "send")


class LiveSceneOverrideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        vault = self.home / "vault"
        vault.mkdir(parents=True, exist_ok=True)
        self.c = cc.Companion(
            profile="nell",
            human="Martin",
            timezone="Europe/London",
            hermes_root=self.home,
            vault=vault,
        )
        self.now = dt.datetime(2026, 11, 27, 15, 0, tzinfo=ZoneInfo("Europe/London"))

        # Seed an existing plan with an errand from 14:30 to 16:30
        import companion_places
        companion_places.add(
            self.c,
            {"home": True, "name": "home", "description": "our apartment", "rooms": ["Living room", "Kitchen", "Office"]},
            self.now,
        )
        presence.update_wardrobe(
            self.c,
            [{"id": "loungewear", "description": "cozy sweatpants and sweater", "use": "casual"}],
        )
        presence.update(
            self.c,
            {
                "activity": "working",
                "setting": "private",
                "room": "Office",
                "mood": "focused",
                "transition": "started working",
                "previous_id": None,
                "outfit": ["loungewear"],
                "location": "home, office",
                "text": "working quietly",
            },
            now=self.now - dt.timedelta(hours=1),
        )
        today = self.now.date()
        p = plan.empty(today)
        p["items"].append({
            "id": "errand-1",
            "what": "running errands at market",
            "setting": "downtown",
            "start": "14:30",
            "end": "16:30",
            "kind": "other",
            "status": "planned",
        })
        plan._write(self.c, today, p)

    def tearDown(self):
        self.tmp.cleanup()

    def test_override_displaces_active_plan_and_updates_presence(self):
        res = live_override.override(
            self.c,
            activity="cuddling on the sofa",
            setting="living room",
            reason="Martin suggested we stay in",
            pose="curled up beside Martin",
            duration=90,
            now=self.now,
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["activity"], "cuddling on the sofa")
        self.assertEqual(res["displaced_count"], 1)
        self.assertIn("errand-1", res["displaced_ids"])

        # Check plan on disk
        updated_plan = plan.read(self.c, self.now.date())
        displaced = [x for x in updated_plan["items"] if x["id"] == "errand-1"][0]
        self.assertEqual(displaced["status"], "moved")
        self.assertIn("displaced by", displaced["reason"])

        new_item = [x for x in updated_plan["items"] if x["status"] == "planned"][0]
        self.assertEqual(new_item["what"], "cuddling on the sofa")
        self.assertEqual(new_item["start"], "15:00")
        self.assertEqual(new_item["end"], "16:30")

        # Check presence on disk
        cur = presence.current(self.c)
        self.assertEqual(cur["state"]["activity"], "cuddling on the sofa")
        self.assertEqual(cur["state"]["room"], "Living room")
        self.assertEqual(cur["state"]["visual"]["pose"], "curled up beside Martin")

    def test_override_dry_run_leaves_disk_unchanged(self):
        res = live_override.override(
            self.c,
            activity="baking cookies",
            setting="kitchen",
            duration=45,
            now=self.now,
            dry_run=True,
        )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["displaced_count"], 1)

        # Confirm plan on disk was NOT mutated
        p = plan.read(self.c, self.now.date())
        errand = [x for x in p["items"] if x["id"] == "errand-1"][0]
        self.assertEqual(errand["status"], "planned")


if __name__ == "__main__":
    unittest.main()
