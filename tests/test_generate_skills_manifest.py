from box_agent.tools.skill_loader import SkillLoader
from scripts.generate_skills_manifest import (
    BUILTIN_SKILL_NAMES,
    SKILLS_DIR,
    _collect_skills,
)


EXPECTED_BUILTIN_SKILLS = {
    "browser-use": "browser-use/SKILL.md",
    "data-dashboard": "data-dashboard/SKILL.md",
    "docx": "document-skills/docx/SKILL.md",
    "html-templates": "html-templates/SKILL.md",
    "mcp-config": "mcp-config/SKILL.md",
    "memory-guide": "memory-guide/SKILL.md",
    "pdf": "document-skills/pdf/SKILL.md",
    "pptx": "document-skills/pptx/SKILL.md",
    "research-synthesis": "research-synthesis/SKILL.md",
    "roadmap": "roadmap/SKILL.md",
    "scheduled-task": "scheduled-task/SKILL.md",
    "xlsx": "document-skills/xlsx/SKILL.md",
}


def test_builtin_manifest_contains_exactly_the_core_skill_allowlist():
    entries = dict(_collect_skills())

    assert set(BUILTIN_SKILL_NAMES) == set(EXPECTED_BUILTIN_SKILLS)
    assert entries == EXPECTED_BUILTIN_SKILLS

    loader = SkillLoader(SKILLS_DIR)
    loader.discover_skills()
    assert set(loader.list_skills()) == set(EXPECTED_BUILTIN_SKILLS)

def test_roadmap_is_a_top_level_builtin_skill():
    entries = dict(_collect_skills())

    assert entries["roadmap"] == "roadmap/SKILL.md"
