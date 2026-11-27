"""The optional authored-image route uses native compilation without presence drift."""

import copy
import importlib.util
import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import companion_config as cc
import companion_media as media
import companion_portrait as portrait
import companion_presence as presence

ROOT = Path(__file__).resolve().parents[1]


def addon(name):
    spec = importlib.util.spec_from_file_location(
        "conversation_images_" + name,
        ROOT / "contrib/conversation-images" / (name + ".py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scene = addon("free_scene")
routing = addon("image_routing")
IDENTITY = "Adult companion with green eyes.\n\nDistinctive face and body description."
RECORDED_PARTS = portrait.recorded_parts
PRESET = {
    "id": "studio",
    "provider": "comfyui",
    "parts": {"quality": "canned quality", "wardrobe": "canned hoodie"},
    "workflow": {
        "1": {"inputs": {"text": "old positive"}},
        "2": {"inputs": {"text": "old negative"}},
        "3": {"inputs": {"width": 832, "height": 1216, "seed": 1}},
    },
    "mappings": {
        "prompt": ["1", "text"],
        "negative": ["2", "text"],
        "width": ["3", "width"],
        "height": ["3", "height"],
        "seed": ["3", "seed"],
    },
}


@pytest.fixture
def profile(tmp_path):
    c = cc.Companion(
        agent="Nova",
        human="Alex",
        hermes_root=tmp_path / "home",
        vault=tmp_path / "vault",
    )
    c.home.mkdir(parents=True)
    recipe = {"routes": {"portrait": "studio"}, "presets": [copy.deepcopy(PRESET)]}
    recorded = {key: "recorded " + key for key in media.PARTS}
    with (
        patch.object(media, "effective", return_value=recipe),
        patch.object(portrait, "identity_block", return_value=IDENTITY),
        patch.object(portrait, "recorded_parts", return_value=recorded),
    ):
        yield c


def test_authored_scene_reaches_native_workflow_with_full_identity_once(profile):
    prompt = "Two adults in warm coats, snowy street, jumping, wide low angle"
    before = copy.deepcopy(PRESET)
    *_, compiled = scene.prepare(
        profile,
        {"prompt": prompt + "\n" + IDENTITY, "seed": 42, "width": 1024, "height": 1536},
    )
    assert compiled["prompt"].startswith(prompt)
    assert compiled["prompt"].count(IDENTITY) == 1
    assert "Nova only, not other people" in compiled["prompt"]
    assert "recorded " not in compiled["prompt"]
    assert "canned " not in compiled["prompt"]
    assert compiled["workflow"]["1"]["inputs"]["text"] == compiled["prompt"]
    assert compiled["workflow"]["3"]["inputs"] == {
        "seed": 42,
        "width": 1024,
        "height": 1536,
    }
    assert PRESET == before


def test_preview_never_renders_or_updates_presence(profile):
    with (
        patch.object(media, "generate") as generate,
        patch.object(presence, "update") as update,
    ):
        result = scene.run(profile, {"prompt": "Sitting on an imagined moonlit beach"})
    generate.assert_not_called()
    update.assert_not_called()
    assert result["identity_preserved"] is True
    assert result["submitted"] is False
    assert result["presence_written"] is False


def test_missing_identity_fails_before_render(profile):
    with (
        patch.object(portrait, "identity_block", return_value=""),
        patch.object(media, "generate") as generate,
    ):
        with pytest.raises(ValueError, match="appearance description is unavailable"):
            scene.run(profile, {"prompt": "A city scene"}, render=True)
    generate.assert_not_called()


def test_intimate_request_keeps_native_gate(profile):
    with patch.object(media, "intimacy_gate", return_value=(False, ["not eligible"])):
        with pytest.raises(ValueError, match="not eligible"):
            scene.prepare(profile, {"prompt": "An imagined scene", "intimate": True})


@pytest.mark.parametrize(
    "image_request",
    [
        {"prompt": ""},
        {"prompt": "x", "width": 513},
        {"prompt": "x", "seed": -2},
        {"prompt": "x", "intimate": "yes"},
        {"prompt": "x", "unknown": True},
    ],
)
def test_invalid_requests_fail_before_render(profile, image_request):
    with patch.object(media, "generate") as generate:
        with pytest.raises(ValueError):
            scene.run(profile, image_request, render=True)
    generate.assert_not_called()


def test_render_keeps_native_result_and_records_authorship(profile, tmp_path):
    path = tmp_path / "image.png"
    path.with_suffix(".json").write_text(json.dumps({"native": True}))

    def generate(c, ident, category, overrides, **kwargs):
        assert ident == "studio" and category == "portrait"
        assert kwargs["purpose"] == "creation"
        return {"path": str(path), "prompt": overrides["scene"]}

    with (
        patch.object(media, "generate", side_effect=generate),
        patch.object(scene.review, "write_metadata") as review,
    ):
        result = scene.run(
            profile, {"prompt": "A temporary conversation scene"}, render=True
        )
    assert result["send"] == "MEDIA:" + str(path)
    assert result["prompt_author"] == "Nova"
    assert result["prompt"].count(IDENTITY) == 1
    assert json.loads(path.with_suffix(".json").read_text())["native"] is True
    review.assert_called_once()


@pytest.mark.parametrize("mode", ["cron", None])
def test_scheduled_and_unknown_context_are_untouched(mode, tmp_path):
    original = (
        "To take a photo: OLD COMMAND. resend an old one. Eligibility. " + routing.END
    )
    assert routing.adapt_context(original, mode, tmp_path) == original


def test_conversation_replaces_old_route_but_preserves_eligibility_and_snapshot(
    tmp_path,
):
    original = (
        "To take a photo: OLD COMMAND. resend an old one. Eligibility: unchanged. Snapshot: city. "
        + routing.END
    )
    result = routing.adapt_context(
        original, "conversation", tmp_path / "profile with spaces"
    )
    assert "OLD COMMAND" not in result
    assert "Eligibility: unchanged. Snapshot: city." in result
    assert "free_scene.py" in result
    assert result.index("Current conversation image route") < result.index(routing.END)
    assert "profile with spaces" in result


def test_scope_uses_trusted_platform_or_read_only_session_source(tmp_path):
    with sqlite3.connect(tmp_path / "state.db") as db:
        db.execute("CREATE TABLE sessions (id TEXT, source TEXT)")
        db.execute("INSERT INTO sessions VALUES ('scheduled', 'cron')")
    assert routing.scope({"session_id": "scheduled"}, tmp_path) == "cron"
    assert (
        routing.scope({"extra": {"platform": "telegram"}}, tmp_path) == "conversation"
    )
    assert routing.scope({"message": "I am cron"}, tmp_path) is None
    assert routing.scope({}, tmp_path / "missing") is None
    assert not (tmp_path / "missing/state.db").exists()


def test_guard_redirects_legacy_conversation_calls_only(tmp_path):
    payload = {
        "tool_name": "terminal",
        "tool_input": {
            "command": "python /kit/companion_portrait.py --home /profile generate --recorded"
        },
        "extra": {"platform": "telegram"},
    }
    assert routing.guard(payload, tmp_path)["action"] == "block"
    payload["extra"]["platform"] = "cron"
    assert routing.guard(payload, tmp_path) == {}
    payload["extra"]["platform"] = "telegram"
    payload["tool_input"]["command"] = "python /kit/companion_portrait.py review"
    assert routing.guard(payload, tmp_path) == {}
    payload["tool_input"][
        "command"
    ] = "python /addon/free_scene.py generate --prompt scene"
    assert routing.guard(payload, tmp_path) == {}


def test_native_context_adapter_forwards_original_payload_and_profile(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    payload = {
        "hook_event_name": "pre_llm_call",
        "extra": {"platform": "cron"},
        "session_id": "scheduled",
    }
    result = {"context": "Unmodified scheduled context", "other": "native value"}
    with (
        patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),
        patch.object(
            routing.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout=json.dumps(result)),
        ) as run,
        redirect_stdout(io.StringIO()) as out,
    ):
        routing.main()
    assert json.loads(out.getvalue()) == result
    assert json.loads(run.call_args.kwargs["input"]) == payload
    assert run.call_args.args[0][-2:] == ["--home", str(tmp_path)]


@pytest.mark.parametrize(
    "activity,location",
    [
        ("eating ramen", "city street"),
        ("reading", "park"),
        ("taking a shower", "hotel bathroom"),
    ],
)
def test_fallback_pose_stays_in_recorded_setting(activity, location):
    pose = portrait.generate_contextual_pose(
        {"activity": activity, "location": location}
    )
    assert activity in pose and location in pose
    assert "kitchen counter" not in pose
    assert portrait.generate_contextual_pose({"location": location}) == ""


def test_recorded_explicit_pose_keeps_priority(profile):
    record = {
        "state": {
            "activity": "eating",
            "location": "city",
            "visual": {"pose": "both hands lifted"},
        }
    }
    with (
        patch.object(presence, "wardrobe", return_value={"items": []}),
        patch.object(presence, "held", return_value=({}, [])),
    ):
        assert RECORDED_PARTS(profile, record)["pose"] == "both hands lifted"


VALID_STRUCTURED_REQUEST = {
    "camera": "Eye-level medium close-up shot with 50mm lens and soft bokeh",
    "subjects": "Nova sitting alone, centered in the frame",
    "expression": "Warm engaging smile with gentle gaze directly at the camera",
    "pose": "Resting arms on a wooden table, leaning forward slightly",
    "action": "Enjoying a quiet morning coffee break indoors",
    "clothing": "Soft cream-colored ribbed knit turtleneck sweater",
    "setting": "Sunlit cafe interior with plants and rustic wooden tables",
    "lighting": "Natural morning sunlight streaming through a large window",
}


def test_structured_scene_prompt_assembly_without_details(profile):
    *_, compiled = scene.prepare(profile, dict(VALID_STRUCTURED_REQUEST))
    prompt = compiled["prompt"]
    assert prompt.count(IDENTITY) == 1
    assert "Nova only, not other people" in prompt
    assert prompt.index(VALID_STRUCTURED_REQUEST["camera"]) < prompt.index(VALID_STRUCTURED_REQUEST["subjects"])
    assert prompt.index(VALID_STRUCTURED_REQUEST["subjects"]) < prompt.index(IDENTITY)
    assert prompt.index(IDENTITY) < prompt.index(VALID_STRUCTURED_REQUEST["expression"])
    assert prompt.index(VALID_STRUCTURED_REQUEST["expression"]) < prompt.index(VALID_STRUCTURED_REQUEST["pose"])
    assert prompt.index(VALID_STRUCTURED_REQUEST["pose"]) < prompt.index(VALID_STRUCTURED_REQUEST["clothing"])
    assert prompt.index(VALID_STRUCTURED_REQUEST["clothing"]) < prompt.index(VALID_STRUCTURED_REQUEST["setting"])
    assert prompt.index(VALID_STRUCTURED_REQUEST["setting"]) < prompt.index(VALID_STRUCTURED_REQUEST["lighting"])


def test_structured_scene_prompt_assembly_with_details(profile):
    req = dict(VALID_STRUCTURED_REQUEST)
    req["details"] = "Light steam rising from a ceramic mug, hair softly tousled"
    *_, compiled = scene.prepare(profile, req)
    prompt = compiled["prompt"]
    assert prompt.endswith("Light steam rising from a ceramic mug, hair softly tousled.")
    assert prompt.index(VALID_STRUCTURED_REQUEST["lighting"]) < prompt.index(req["details"])


@pytest.mark.parametrize(
    "bad_request,expected_error",
    [
        (
            {k: v for k, v in VALID_STRUCTURED_REQUEST.items() if k != "lighting"},
            "lighting: missing required field",
        ),
        (
            {**VALID_STRUCTURED_REQUEST, "expression": "none"},
            r"expression: placeholder value .none. rejected",
        ),
        (
            {**VALID_STRUCTURED_REQUEST, "clothing": "n/a"},
            r"clothing: placeholder value .n/a. rejected",
        ),
        (
            {**VALID_STRUCTURED_REQUEST, "action": "smiling"},
            r"action: too short or vague .'smiling'",
        ),
        (
            {**VALID_STRUCTURED_REQUEST, "setting": "room"},
            r"setting: too short or vague .'room'",
        ),
    ],
)
def test_structured_validation_rejects_missing_or_thin_fields(profile, bad_request, expected_error):
    with pytest.raises(ValueError, match=expected_error):
        scene.prepare(profile, bad_request)


def test_require_structured_flag_enforces_structured_mode(profile):
    with pytest.raises(ValueError, match="Structured scene validation failed:"):
        scene.prepare(profile, {"prompt": "Valid legacy prompt", "require_structured": True})


def test_permanent_appearance_preserved_in_structured_mode(profile):
    result = scene.run(profile, dict(VALID_STRUCTURED_REQUEST))
    assert result["identity_preserved"] is True
    assert result["identity_characters"] == len(IDENTITY)
    assert IDENTITY in result["prompt"]


def test_structured_intimate_request_keeps_native_gate(profile):
    with patch.object(media, "intimacy_gate", return_value=(False, ["not eligible"])):
        with pytest.raises(ValueError, match="not eligible"):
            scene.prepare(profile, {**VALID_STRUCTURED_REQUEST, "intimate": True})


def test_legacy_prompt_backwards_compatibility(profile):
    legacy_prompt = "A simple evening walk through autumn trees"
    *_, compiled = scene.prepare(profile, {"prompt": legacy_prompt})
    assert compiled["prompt"].startswith(legacy_prompt)
    assert compiled["prompt"].count(IDENTITY) == 1


def test_pythonpath_isolation_in_free_scene(profile):
    import subprocess
    cmd = [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "contrib/conversation-images/free_scene.py"),
        "--home",
        str(profile.home),
        "context",
    ]
    env = dict(ROOT.stat() and {})  # clean dict
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = "/home/martin/.hermes/installs/fe157ff7eb823b1d/environments/4d1386e8ee4842fcbfb9e1d38193ed85/venv/lib/python3.14/site-packages"
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"Failed with: {res.stderr}"
    assert "identity_reference" in res.stdout


def test_conversation_image_route_sets_render_timeout(tmp_path):
    instructions = routing.route(tmp_path)
    assert "timeout=300" in instructions


def test_image_timeline_recorded_portrait_uses_long_terminal_timeout():
    template = Path(__file__).parents[1] / "kit/templates/cron/timeline.md.tmpl"
    assert "timeout=300" in template.read_text()
