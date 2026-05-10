"""Knowledge permission regression tests.

These tests lock the intended contract:
- regular users may publish public knowledge;
- public knowledge is visible to other users;
- invalid permission values are rejected before durable write.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from knowledge.importer import run_import_task
from knowledge.router import KnowledgeImportRequest, _can_access_doc
from knowledge.store import ensure_knowledge_dirs, list_knowledge_docs, read_knowledge_doc


@pytest.fixture(autouse=True)
def knowledge_root(tmp_path, monkeypatch):
    monkeypatch.setattr("knowledge.store.settings.workspace_root", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_regular_user_can_publish_public_knowledge_visible_to_others(tmp_path):
    ensure_knowledge_dirs()
    session_dir = tmp_path / "sessions" / "session-a"
    session_dir.mkdir(parents=True)
    raw_path = tmp_path / "source.txt"
    raw_path.write_text("Shared policy content", encoding="utf-8")

    result = await run_import_task(
        task_id="task-public",
        session_id="session-a",
        session_dir=str(session_dir),
        user_id="user-a",
        source_path=str(raw_path),
        raw_path=str(raw_path),
        metadata={
            "title": "Shared Policy",
            "description": "Published by a regular user",
            "tags": ["policy"],
            "permission": "public",
            "kind": "text",
        },
    )

    assert result["success"] is True
    docs_for_other_user = list_knowledge_docs(user_id="user-b")
    published = next(d for d in docs_for_other_user if d["title"] == "Shared Policy")
    assert published["permission"] == "public"
    assert published["owner"] == "user-a"


def test_invalid_import_permission_is_rejected_by_request_schema():
    with pytest.raises(ValidationError):
        KnowledgeImportRequest(
            session_id="session-a",
            source_path="source.txt",
            title="Bad Permission",
            permission="team",
        )


def test_admin_can_read_private_doc_owned_by_another_user():
    from knowledge.store import build_frontmatter, write_knowledge_doc

    ensure_knowledge_dirs()
    fm = build_frontmatter(
        title="Private",
        description="Private doc",
        kind="text",
        owner="user-a",
        permission="private",
    )
    write_knowledge_doc("private001", fm, "secret")
    stored, _ = read_knowledge_doc("private001")

    admin = SimpleNamespace(id="admin-user", role="admin")
    regular = SimpleNamespace(id="user-b", role="user")
    assert _can_access_doc(stored, admin) is True
    assert _can_access_doc(stored, regular) is False
