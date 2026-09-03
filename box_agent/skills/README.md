# Box-Agent built-in Skills

This directory is the runtime-owned built-in Skill catalog.

Only host contracts and core Office workflows declared in `_manifest.json`
belong here. Marketplace, partner, community, and user-authored Skills must not
be stored here or bundled as hidden runtime files.

Skill sources are intentionally separated:

- `box_agent/skills/`: manifest-declared built-ins shipped with the runtime.
- `~/.box-agent/connectors/skills/`: host-managed connector companion Skills.
- `~/.box-agent/skills/`: user-installed Skills, including SkillHub packages.

User Skills keep precedence over built-ins. Connector Skills have their own
source/owner metadata and are not exposed in the user Skill management UI.

To change the built-in catalog:

1. Add or remove the Skill under this directory.
2. Update `BUILTIN_SKILL_NAMES` in
   `scripts/generate_skills_manifest.py`.
3. Regenerate `_manifest.json`.
4. Run `tests/test_generate_skills_manifest.py` and the runtime build-helper
   tests.

The manifest generator rejects undeclared Skill entry points, and both runtime
builders package only manifest-declared Skill directories.
