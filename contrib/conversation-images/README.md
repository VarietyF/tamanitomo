# Authored conversation images (optional add-on)

Conversation pictures can follow an imagined scene, a temporary outfit or a
specific camera angle without changing the companion's recorded day. The helper
keeps the entire configured permanent appearance description and submits the
companion's scene through the existing ComfyUI compiler and renderer. Scheduled
photos continue to use the default recorded-photo workflow.

This is an opt-in add-on for a source checkout, not an automatically installed
release feature. Keep these files together under `contrib/conversation-images/`.
Use the checkout's Python environment. It needs a saved text-to-image ComfyUI
preset with a positive prompt mapping; reference-image presets are rejected.
Generation still uses native eligibility, review and held-image behavior. Prompt
fidelity depends on the selected image model; preserving the description does
not guarantee identical faces or exact poses in every generated image.

## Try without changing configuration

From the checkout, substitute the intended profile home:

```sh
.venv/bin/python contrib/conversation-images/free_scene.py --home /path/to/profile context
.venv/bin/python contrib/conversation-images/free_scene.py --home /path/to/profile preview \
  --prompt 'Two adults on a snowy city street, warm coats, walking side by side, wide shot from street level'
```

`context` returns the permanent appearance and optional recorded context.
`preview` verifies the exact compiled prompt and mapped workflow input without
rendering or writing presence. `generate` renders, saves provenance and returns
a real `MEDIA:` path; it does not send a message. With no `--home`, the helper
uses native `HERMES_HOME` discovery. Longer prompts can be supplied in a JSON
file using `--request`: `{"prompt": "...", "width": 1024, "height": 1536}`.
Per-render dimensions require corresponding preset mappings. `--intimate`
requests existing eligibility checks; it does not override them.

The full appearance is added automatically, unchanged. An exact copy pasted
into the authored scene is removed before appending the appearance once. A
missing appearance, incompatible preset or altered compiler output fails before
submission. The helper clears all preset/recorded positive parts, so wardrobe,
pose, setting, lighting and camera must be written in the scene itself. Negative
conditioning and other saved workflow settings remain native.

## Enable for one profile

1. Back up that profile's `config.yaml` and existing image instructions.
2. Render `SKILL.md.tmpl` and `owner-instructions.md.tmpl`, replacing
   `{{IMAGE_COMMAND}}` with the shell-quoted absolute command:
   `/path/to/checkout/.venv/bin/python /path/to/checkout/contrib/conversation-images/free_scene.py --home /path/to/profile`.
   Install the skill as `skills/conversation-image-scenes/SKILL.md` in the profile.
   Merge the owner instructions into its existing `agent.system_prompt`; retain
   unrelated instructions. Update conflicting image skills to use the same route
   for conversation images while retaining the scheduled default route.
3. In that profile's hooks, replace only the native `companion_context.py`
   `pre_llm_call` entry with the adapter below. Do not keep both context hooks,
   because the adapter calls the native hook itself. Append the tool guard;
   retain unrelated hooks and settings. Use absolute shell-quoted paths:

   ```yaml
   hooks:
     pre_llm_call:
       - command: /path/to/checkout/.venv/bin/python /path/to/checkout/contrib/conversation-images/image_routing.py
         timeout: 15
     pre_tool_call:
       - command: /path/to/checkout/.venv/bin/python /path/to/checkout/contrib/conversation-images/image_routing.py
         matcher: '^(terminal|execute_code)$'
         timeout: 3
   ```

4. Review and approve these two hook commands with Hermes's native hook-consent
   mechanism for the intended profile. Do not globally approve unrelated hooks.
   Ensure hook subprocesses receive that profile's `HERMES_HOME`, then check
   `hermes hooks doctor` and the running gateway's hook reconciliation.
5. Test a conversation preview through the actual caller and verify a cron turn
   still receives its native context. A successful render alone does not prove
   delivery. Ask for a generated picture only when a real send is intended.

The adapter replaces the native conversation instruction that otherwise keeps
suggesting the recorded-photo command. It retains the presence snapshot and
eligibility text. Cron and unknown-source turns pass through unchanged. The
guard redirects recognized legacy `companion_portrait.py prompt/generate` calls
in conversations; it leaves cron, review and unrelated tools alone. It is a
routing aid, not a command sandbox: Python wrapper detection is deliberately
conservative and can match quoted unused commands.

Undo by restoring the original context-hook entry, removing only this guard,
and restoring the previous profile instructions. No recorded day or shared
preset is rewritten. After native prompt repair or application updates, recheck
the optional integration rather than overwriting new native instructions.

## Operational fixes outside source control

For scheduled scripts missing Python dependencies, select the application's
dependency-equipped interpreter using the native cron job `interpreter` setting;
do not copy private job files or profile configuration into the repository.
Local model connectivity after a VM migration also depends on the host's actual
network route. Provider credentials, machine routes and personal profiles are
deployment configuration and are intentionally absent from this add-on.
