from pathlib import Path

from box_agent.tools.skill_loader import SKILL_SLOT_SENTINEL, SkillLoader, SkillSelector


def _write_connector_skill(root: Path, *, disabled: bool = False) -> Path:
    skill_dir = root / "connector-pkulaw"
    skill_dir.mkdir(parents=True)
    disable_line = "disable: true\n" if disabled else ""
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\n"
        "name: pkulaw\n"
        "description: PKULaw connector guidance\n"
        f"{disable_line}"
        "---\n\n"
        "Always retrieve before answering.\n",
        encoding="utf-8",
    )
    return skill_path


def test_connector_source_exposes_owner_metadata(tmp_path: Path) -> None:
    connector_root = tmp_path / "connectors" / "skills"
    skill_path = _write_connector_skill(connector_root)
    loader = SkillLoader(sources=[(connector_root, "connector")])

    loader.discover_skills()

    skill = loader.get_skill("pkulaw")
    assert skill is not None
    assert skill.source == "connector"
    assert skill.owner_id == "pkulaw"
    assert skill.skill_path == skill_path
    assert skill.to_metadata_dict()["ownerId"] == "pkulaw"


def test_disabled_connector_skill_stays_out_of_model_catalog(tmp_path: Path) -> None:
    connector_root = tmp_path / "connectors" / "skills"
    _write_connector_skill(connector_root, disabled=True)
    loader = SkillLoader(sources=[(connector_root, "connector")])

    loader.discover_skills()

    assert loader.get_skill("pkulaw") is None
    disabled = loader.get_skill("pkulaw", include_disabled=True)
    assert disabled is not None
    assert disabled.disabled is True


def test_connector_skill_requires_connection_for_new_matching_but_stays_after_loading(
    tmp_path: Path,
) -> None:
    connector_root = tmp_path / "connectors" / "skills"
    _write_connector_skill(connector_root)
    loader = SkillLoader(sources=[(connector_root, "connector")])
    loader.discover_skills()
    connected_connectors: set[str] = set()
    selector = SkillSelector(
        loader,
        skill_filter=lambda skill: skill.source != "connector"
        or skill.owner_id in connected_connectors,
    )
    selector.bind(f"prefix\n\n{SKILL_SLOT_SENTINEL}\n\nsuffix")

    assert "pkulaw" not in (selector.update("PKULaw legal search") or "")

    connected_connectors.add("pkulaw")
    assert "pkulaw" in (selector.update("PKULaw legal search") or "")

    connected_connectors.clear()
    selector.update("continue")
    assert "pkulaw" in selector.matched_skill_names


def test_connector_disable_flag_does_not_change_existing_user_skill_behavior(
    tmp_path: Path,
) -> None:
    user_root = tmp_path / "skills"
    skill_dir = user_root / "ordinary-user-skill"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "---\n"
        "name: ordinary-user-skill\n"
        "description: Existing user skill\n"
        "disable: true\n"
        "---\n\n"
        "Existing behavior remains unchanged.\n",
        encoding="utf-8",
    )
    loader = SkillLoader(sources=[(user_root, "user")])

    loader.discover_skills()

    skill = loader.get_skill("ordinary-user-skill")
    assert skill is not None
    assert skill.disabled is False
