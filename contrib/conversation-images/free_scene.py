#!/usr/bin/env python3
"""Profile-owned free-prompt scenes using Tamanitomo's existing image renderer."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

KIT = Path(__file__).resolve().parents[2] / "kit/scripts"
sys.path.insert(0, str(KIT))

import companion_config as cc
import companion_media as media
import companion_media_review as review
import companion_portrait as portrait
from companion_platform import atomic_write


def with_permanent_identity(c, authored_prompt):
    identity = portrait.identity_block(c)
    if not identity.strip():
        raise ValueError(
            "The full appearance description is unavailable; no image was submitted"
        )
    # The source description is never summarized, rewritten or model-authored.
    # Remove an exact pasted copy from the scene to avoid repeating the same block.
    scene = authored_prompt.replace(identity, "").strip()
    if not scene:
        raise ValueError(
            "Write the scene for this picture separately from the permanent appearance"
        )
    submitted = (
        scene
        + f"\n\nThe following permanent appearance describes {c.agent} only, not other people in the image:\n"
        + identity
        + f"\n\nKeep {c.agent}'s permanent face, body, hair and physical features exactly as described. "
        "If scene wording conflicts with those permanent features, the permanent description wins. "
        "The scene determines this picture's clothing, pose, activity, temporary expression, gaze "
        "and camera, overriding only customary presentation details in the description."
    )
    return identity, submitted


def prepare(c, request):
    if not isinstance(request, dict):
        raise ValueError("The request must be a JSON object")
    permitted = {"prompt", "preset", "intimate", "seed", "width", "height", "purpose"}
    unknown = set(request) - permitted
    if unknown:
        raise ValueError("Unknown request fields: " + ", ".join(sorted(unknown)))
    prompt = request.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Write the complete image prompt in prompt")
    # This is the normalization performed by the native comma-join compiler.
    prompt = prompt.strip().rstrip(",")
    if not prompt or len(prompt) > 24000:
        raise ValueError("The image prompt must contain 1 to 24000 characters")
    identity, submitted_prompt = with_permanent_identity(c, prompt)
    intimate = request.get("intimate", False)
    if not isinstance(intimate, bool):
        raise ValueError("intimate must be true or false")
    purpose = request.get("purpose", "creation")
    if purpose not in ("creation", "capture"):
        raise ValueError("purpose must be creation or capture")
    recipe = media.effective(c)
    ident = (
        request.get("preset")
        or recipe.get("routes", {}).get("portrait")
        or recipe.get("default_preset")
    )
    if not isinstance(ident, str):
        raise ValueError("preset must name a saved preset")
    preset = next((p for p in recipe.get("presets", []) if p.get("id") == ident), None)
    if preset is None:
        raise ValueError("Choose a saved image preset first")
    if preset.get("provider") != "comfyui" or not preset.get("mappings", {}).get(
        "prompt"
    ):
        raise ValueError(
            "Free scenes need a saved ComfyUI preset with a complete prompt mapping"
        )
    draft = copy.deepcopy(preset)
    for key in ("width", "height"):
        if key in request:
            value = request[key]
            if type(value) is not int or value < 512 or value > 2048 or value % 64:
                raise ValueError(key + " must be a multiple of 64 between 512 and 2048")
            if key not in draft.get("mappings", {}):
                raise ValueError("This preset has no " + key + " mapping")
            draft[key] = value
    # Explicit blanks override native presence and preset fallbacks in every box.
    # The authored scene and full permanent identity reach positive conditioning.
    # No canned scene, pose, wardrobe or saved preset is edited or added.
    overrides = {part: "" for part in media.PARTS}
    overrides["scene"] = submitted_prompt
    if "seed" in request:
        seed = request["seed"]
        if type(seed) is not int or seed < -1 or seed >= 2**63:
            raise ValueError("seed must be -1 or a nonnegative 63-bit integer")
        overrides["seed"] = seed
    compiled = media.compile(c, ident, "portrait", overrides, draft, intimate)
    if compiled["reference_image"]:
        raise ValueError(
            "This preset uses a reference image; choose a text-to-image preset for a free scene"
        )
    if compiled["prompt"] != submitted_prompt:
        raise ValueError(
            "The installed compiler changed the scene/identity prompt; nothing was submitted"
        )
    node, field = draft["mappings"]["prompt"]
    if compiled["workflow"][str(node)]["inputs"][field] != submitted_prompt:
        raise ValueError(
            "The scene and permanent identity did not reach the workflow input"
        )
    return prompt, ident, draft, overrides, intimate, compiled


def run(c, request, render=False):
    prompt, ident, draft, overrides, intimate, compiled = prepare(c, request)
    if not render:
        return {
            "status": "preview",
            "authored_prompt": prompt,
            "prompt": compiled["prompt"],
            "preset": ident,
            "identity_preserved": True,
            "identity_characters": len(portrait.identity_block(c)),
            "seed": compiled["seed"],
            "intimate": intimate,
            "width": draft.get("width"),
            "height": draft.get("height"),
            "presence_written": False,
            "submitted": False,
        }
    # Rendering, normal eligibility checks, review preferences, file validation
    # and any existing held-image retry remain owned by the official renderer.
    result = media.generate(
        c,
        ident,
        "portrait",
        overrides,
        draft=draft,
        intimate=intimate,
        purpose=request.get("purpose", "creation"),
    )
    path = Path(result["path"])
    actual = result["prompt"]
    prompts = {"structured": actual, "tags": actual}
    provenance = {
        "image_mode": "conversation-scene",
        "prompt_author": c.agent,
        "authored_prompt": prompt,
        "prompt": actual,
        "prompts": prompts,
        "active_prompt_type": "tags",
        "presence_written": False,
        "identity_preserved": True,
        "identity_characters": len(portrait.identity_block(c)),
    }
    metadata_path = path.with_suffix(".json")
    original = json.loads(metadata_path.read_text())
    atomic_write(metadata_path, json.dumps({**original, **provenance}, indent=2))
    review.write_metadata(path, provenance)
    # No messaging client or outbox call: the caller decides whether to share.
    return {
        **(
            {"send": "MEDIA:" + str(path)}
            if request.get("purpose", "creation") == "creation"
            else {}
        ),
        **result,
        **provenance,
        "status": "generated",
        "submitted": True,
    }


def context(c, capture_id=None):
    import companion_presence as presence

    if capture_id:
        import re
        import companion_timeline as timeline

        if not re.fullmatch(r"[a-f0-9]{24}", capture_id):
            raise ValueError("Invalid capture ID")
        record = json.loads(timeline.capture_path(c, capture_id).read_text())["scene"]
    else:
        record = presence.current(c) or {}
    state = record.get("state", {})
    closet = presence.wardrobe(c).get("items", [])
    kind, worn = presence.visible_outfit(state, closet)
    appearance = portrait.identity_block(c)
    return {
        "identity_reference": appearance,
        "identity_added_automatically": True,
        "recorded_context_is_optional_for_imagined_images": True,
        "recorded": {
            "at": record.get("recorded_at"),
            "activity": state.get("activity"),
            "location": state.get("location"),
            "visible_clothing": worn,
            "clothing_kind": kind,
            "visual": state.get("visual", {}),
        },
        "image_style": c.image_style,
        "adult_images_ready": media.adult_ready(c)[0],
        "note": "Author the scene, clothing, activity, pose and camera freely. The full original appearance is added automatically and must not be shortened or changed. Do not invent real facts about the human.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument("command", choices=["context", "preview", "generate"])
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--request",
        type=Path,
        help="JSON file containing a complete prompt and optional settings",
    )
    source.add_argument(
        "--prompt", help="Complete authored prompt; use a JSON file for longer text"
    )
    parser.add_argument(
        "--intimate",
        action="store_true",
        help="Choose an intimate image using existing eligibility checks",
    )
    parser.add_argument("--preset")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--capture",
        action="store_true",
        help="Render a scheduled capture without adding a creation or a send instruction",
    )
    parser.add_argument(
        "--capture-id", help="Read the frozen scene of a prepared capture in context"
    )
    args = parser.parse_args()
    c = cc.load(args.home)
    if args.command == "context":
        result = context(c, args.capture_id)
    else:
        if args.request:
            if args.request.stat().st_size > 150000:
                raise ValueError("The request file is too large")
            request = json.loads(args.request.read_text())
        elif args.prompt:
            request = {"prompt": args.prompt}
        else:
            raise ValueError("Provide --prompt or --request")
        if args.intimate:
            if not isinstance(request, dict):
                raise ValueError("The request must be a JSON object")
            request["intimate"] = True
        for key in ("preset", "width", "height", "seed"):
            if getattr(args, key) is not None:
                request[key] = getattr(args, key)
        if args.capture:
            request["purpose"] = "capture"
        result = run(c, request, render=args.command == "generate")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        sys.exit(1)
