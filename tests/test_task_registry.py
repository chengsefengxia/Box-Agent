import json
from pathlib import Path

from box_agent.acp import _artifact_envelope
from box_agent.events import ArtifactEvent
from box_agent.task_context import TaskContext, normalize_task_id
from box_agent.task_registry import finish_task, register_artifact_revision


def _artifact(
    path: Path,
    *,
    rel_path: str = "report.md",
    tool_call_id: str = "tool-1",
) -> ArtifactEvent:
    return ArtifactEvent(
        tool_call_id=tool_call_id,
        kind="document",
        filename=path.name,
        rel_path=rel_path,
        abs_path=str(path),
        uri=path.as_uri(),
        size=path.stat().st_size,
        produced_at="2026-08-21T00:00:00+00:00",
    )


def test_task_id_normalization_rejects_path_traversal() -> None:
    assert normalize_task_id(" task-1 ") == "task-1"
    assert normalize_task_id("../task-1") is None


def test_registry_keeps_artifact_id_stable_and_versions_content(tmp_path: Path) -> None:
    output = tmp_path / "output" / "tasks" / "task-1"
    output.mkdir(parents=True)
    file_path = output / "report.md"
    context = TaskContext(session_id="session-1", task_id="task-1", turn_id="turn-1")

    file_path.write_text("first", encoding="utf-8")
    first = register_artifact_revision(
        tmp_path,
        context,
        _artifact(file_path),
        artifact_root_dir=output,
    )
    file_path.write_text("second", encoding="utf-8")
    second = register_artifact_revision(
        tmp_path,
        TaskContext(session_id="session-1", task_id="task-1", turn_id="turn-2"),
        _artifact(file_path),
        artifact_root_dir=output,
    )
    finish_task(
        tmp_path,
        context,
        execution_status="completed",
        delivery_status="incomplete",
        artifact_root_dir=output,
    )

    assert first.artifact_id == second.artifact_id
    assert first.artifact_revision_id != second.artifact_revision_id
    record = json.loads(Path(second.manifest_path).read_text(encoding="utf-8"))
    assert record["delivery_status"] == "incomplete"
    assert len(record["artifacts"][0]["revisions"]) == 2
    second_revision = record["artifacts"][0]["revisions"][1]
    assert second_revision["session_id"] == "session-1"
    assert second_revision["task_id"] == "task-1"
    assert second_revision["turn_id"] == "turn-2"
    assert second_revision["tool_call_id"] == "tool-1"


def test_registry_keeps_each_receipt_for_identical_revision(tmp_path: Path) -> None:
    output = tmp_path / "output" / "tasks" / "task-1"
    output.mkdir(parents=True)
    file_path = output / "report.md"
    file_path.write_text("same content", encoding="utf-8")

    first = register_artifact_revision(
        tmp_path,
        TaskContext(session_id="session-1", task_id="task-1", turn_id="turn-1"),
        _artifact(file_path, tool_call_id="call-1"),
        artifact_root_dir=output,
    )
    second = register_artifact_revision(
        tmp_path,
        TaskContext(session_id="session-1", task_id="task-1", turn_id="turn-2"),
        _artifact(file_path, tool_call_id="call-2"),
        artifact_root_dir=output,
    )

    assert first.artifact_revision_id == second.artifact_revision_id
    record = json.loads(Path(second.manifest_path).read_text(encoding="utf-8"))
    revision = record["artifacts"][0]["revisions"][0]
    assert revision["tool_call_id"] == "call-2"
    assert revision["receipts"] == [
        {
            "session_id": "session-1",
            "task_id": "task-1",
            "turn_id": "turn-1",
            "tool_call_id": "call-1",
            "produced_at": "2026-08-21T00:00:00+00:00",
        },
        {
            "session_id": "session-1",
            "task_id": "task-1",
            "turn_id": "turn-2",
            "tool_call_id": "call-2",
            "produced_at": "2026-08-21T00:00:00+00:00",
        },
    ]


def test_artifact_envelope_exposes_canonical_lineage(tmp_path: Path) -> None:
    file_path = tmp_path / "report.md"
    file_path.write_text("content", encoding="utf-8")
    artifact = _artifact(file_path)
    context = TaskContext(session_id="session-1", task_id="task-1", turn_id="turn-1")
    lineage = register_artifact_revision(
        tmp_path,
        context,
        artifact,
        artifact_root_dir=tmp_path,
    )

    payload = _artifact_envelope(
        artifact,
        str(tmp_path),
        session_id=context.session_id,
        task_id=context.task_id,
        turn_id=context.turn_id,
        lineage=lineage,
    )

    assert payload["artifact_id"] == lineage.artifact_id
    assert payload["artifact_revision_id"] == lineage.artifact_revision_id
    assert payload["task_id"] == "task-1"
