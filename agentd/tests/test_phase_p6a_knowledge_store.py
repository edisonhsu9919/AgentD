"""Phase P6-A — Knowledge Store Skeleton tests.

Tests cover:
- Directory structure creation
- Frontmatter schema building and validation
- Knowledge document read/write with YAML frontmatter
- Permission-based listing
- Raw file storage
- Document-to-source path mapping
"""

import os

import pytest

from knowledge.store import (
    build_frontmatter,
    ensure_knowledge_dirs,
    generate_doc_id,
    get_doc_source_path,
    get_files_dir,
    get_knowledge_root,
    get_raw_dir,
    get_raw_file_path,
    list_knowledge_docs,
    parse_knowledge_doc,
    read_knowledge_doc,
    save_raw_file,
    validate_frontmatter,
    write_knowledge_doc,
    REQUIRED_FRONTMATTER,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def knowledge_root(tmp_path, monkeypatch):
    """Point knowledge store to a temp directory."""
    monkeypatch.setattr("knowledge.store.settings.workspace_root", str(tmp_path))
    return str(tmp_path / "knowledge")


# ── Directory structure ──────────────────────────────────────────────────


class TestDirectoryStructure:
    def test_ensure_creates_dirs(self, knowledge_root):
        ensure_knowledge_dirs()
        assert os.path.isdir(os.path.join(knowledge_root, "files"))
        assert os.path.isdir(os.path.join(knowledge_root, "raw"))

    def test_idempotent(self, knowledge_root):
        ensure_knowledge_dirs()
        ensure_knowledge_dirs()  # second call should not fail
        assert os.path.isdir(knowledge_root)

    def test_paths(self, knowledge_root):
        assert get_knowledge_root().endswith("/knowledge")
        assert get_files_dir().endswith("/knowledge/files")
        assert get_raw_dir().endswith("/knowledge/raw")


# ── Document ID ──────────────────────────────────────────────────────────


class TestDocId:
    def test_generate_doc_id(self):
        doc_id = generate_doc_id()
        assert len(doc_id) == 12
        assert doc_id.isalnum()

    def test_unique(self):
        ids = {generate_doc_id() for _ in range(100)}
        assert len(ids) == 100


# ── Frontmatter ──────────────────────────────────────────────────────────


class TestFrontmatter:
    def test_build_complete(self):
        fm = build_frontmatter(
            title="Test Doc",
            description="A test document",
            kind="pdf",
            owner="user-1",
            permission="public",
            tags=["test", "sample"],
            author="Alice",
            source_file="test.pdf",
            source_path="knowledge/raw/test.pdf",
            file_size=12345,
        )
        assert fm["title"] == "Test Doc"
        assert fm["permission"] == "public"
        assert fm["tags"] == ["test", "sample"]
        assert fm["created_at"]  # auto-populated
        assert fm["file_size"] == 12345

    def test_build_defaults(self):
        fm = build_frontmatter(
            title="Minimal",
            description="Minimal doc",
            kind="text",
            owner="user-2",
        )
        assert fm["permission"] == "private"
        assert fm["tags"] == []
        assert fm["author"] == ""

    def test_description_capped(self):
        fm = build_frontmatter(
            title="Long desc",
            description="x" * 1000,
            kind="pdf",
            owner="user-1",
        )
        assert len(fm["description"]) <= 500

    def test_validate_valid(self):
        fm = build_frontmatter(title="T", description="D", kind="pdf", owner="u")
        errors = validate_frontmatter(fm)
        assert errors == []

    def test_validate_missing_fields(self):
        errors = validate_frontmatter({})
        assert len(errors) >= len(REQUIRED_FRONTMATTER)

    def test_validate_bad_permission(self):
        fm = build_frontmatter(title="T", description="D", kind="pdf", owner="u", permission="admin")
        errors = validate_frontmatter(fm)
        assert any("permission" in e for e in errors)


# ── Knowledge document read/write ────────────────────────────────────────


class TestKnowledgeDoc:
    def test_write_and_read(self, knowledge_root):
        fm = build_frontmatter(title="Test", description="Desc", kind="pdf", owner="u1")
        path = write_knowledge_doc("doc001", fm, "# Content\nHello world")

        assert os.path.isfile(path)
        assert path.endswith("doc001.md")

        result = read_knowledge_doc("doc001")
        assert result is not None
        loaded_fm, body = result
        assert loaded_fm["title"] == "Test"
        assert "Hello world" in body

    def test_read_nonexistent(self, knowledge_root):
        ensure_knowledge_dirs()
        assert read_knowledge_doc("nonexistent") is None

    def test_parse_knowledge_doc(self):
        content = "---\ntitle: Test\nkind: pdf\n---\n\n# Body\nContent here"
        result = parse_knowledge_doc(content)
        assert result is not None
        fm, body = result
        assert fm["title"] == "Test"
        assert "Content here" in body

    def test_parse_no_frontmatter(self):
        assert parse_knowledge_doc("Just plain text") is None

    def test_parse_invalid_yaml(self):
        assert parse_knowledge_doc("---\n: bad: yaml: [\n---\nBody") is None


# ── Permission-based listing ─────────────────────────────────────────────


class TestListKnowledgeDocs:
    def _setup_docs(self, knowledge_root):
        ensure_knowledge_dirs()
        # Public doc
        fm1 = build_frontmatter(title="Public Doc", description="Visible to all",
                                kind="pdf", owner="user-a", permission="public")
        write_knowledge_doc("pub001", fm1, "Public content")

        # Private doc owned by user-a
        fm2 = build_frontmatter(title="Private A", description="Only user-a",
                                kind="docx", owner="user-a", permission="private")
        write_knowledge_doc("priv_a", fm2, "Private content A")

        # Private doc owned by user-b
        fm3 = build_frontmatter(title="Private B", description="Only user-b",
                                kind="pdf", owner="user-b", permission="private")
        write_knowledge_doc("priv_b", fm3, "Private content B")

    def test_user_a_sees_public_and_own(self, knowledge_root):
        self._setup_docs(knowledge_root)
        docs = list_knowledge_docs(user_id="user-a")
        titles = {d["title"] for d in docs}
        assert "Public Doc" in titles
        assert "Private A" in titles
        assert "Private B" not in titles

    def test_user_b_sees_public_and_own(self, knowledge_root):
        self._setup_docs(knowledge_root)
        docs = list_knowledge_docs(user_id="user-b")
        titles = {d["title"] for d in docs}
        assert "Public Doc" in titles
        assert "Private A" not in titles
        assert "Private B" in titles

    def test_no_user_sees_only_public(self, knowledge_root):
        self._setup_docs(knowledge_root)
        docs = list_knowledge_docs(user_id=None)
        titles = {d["title"] for d in docs}
        assert "Public Doc" in titles
        assert "Private A" not in titles

    def test_doc_id_in_results(self, knowledge_root):
        self._setup_docs(knowledge_root)
        docs = list_knowledge_docs(user_id="user-a")
        assert all("doc_id" in d for d in docs)

    def test_empty_knowledge(self, knowledge_root):
        ensure_knowledge_dirs()
        assert list_knowledge_docs(user_id="anyone") == []


# ── Raw file storage ─────────────────────────────────────────────────────


class TestRawFile:
    def test_save_and_get(self, knowledge_root):
        ensure_knowledge_dirs()
        path = save_raw_file("test.pdf", b"fake pdf content")
        assert os.path.isfile(path)
        assert path.endswith("test.pdf")

        found = get_raw_file_path("test.pdf")
        assert found == path

    def test_get_nonexistent(self, knowledge_root):
        ensure_knowledge_dirs()
        assert get_raw_file_path("missing.pdf") is None


# ── Source path mapping ──────────────────────────────────────────────────


class TestSourceMapping:
    def test_doc_to_source(self, knowledge_root):
        ensure_knowledge_dirs()
        save_raw_file("report.pdf", b"pdf bytes")
        fm = build_frontmatter(
            title="Report", description="A report", kind="pdf", owner="u1",
            source_file="report.pdf", source_path="knowledge/raw/report.pdf",
        )
        write_knowledge_doc("rpt001", fm, "# Report\nAnalysis...")

        source = get_doc_source_path("rpt001")
        assert source is not None
        assert source.endswith("report.pdf")

    def test_doc_without_source(self, knowledge_root):
        ensure_knowledge_dirs()
        fm = build_frontmatter(title="Note", description="A note", kind="text", owner="u1")
        write_knowledge_doc("note001", fm, "Just a note")

        assert get_doc_source_path("note001") is None

    def test_nonexistent_doc(self, knowledge_root):
        ensure_knowledge_dirs()
        assert get_doc_source_path("missing") is None
