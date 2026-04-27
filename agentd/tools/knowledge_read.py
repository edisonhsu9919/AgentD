"""knowledge_read tool (Phase P6-B).

Reads a specific knowledge document with permission check and
partial read support (offset/limit) to avoid loading entire
documents into the conversation context.
"""

import json
from typing import Any

from tools.base import BaseTool, ToolContext, ToolMetadata


class KnowledgeReadTool(BaseTool):
    @property
    def name(self) -> str:
        return "knowledge_read"

    @property
    def description(self) -> str:
        return (
            "Read a knowledge document by doc_id. Supports partial read "
            "via offset and limit to avoid loading large documents entirely. "
            "Only reads documents the current user has access to."
        )

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            default_permission="allow",
            is_read_only=True,
            is_destructive=False,
            is_concurrency_safe=True,
            can_run_in_background=True,
            result_compressibility="medium",
            access_scope="system_scoped",
            mutates_session_state=False,
            max_result_size_chars=30_000,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Document ID from knowledge_catalog or knowledge_search.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Start reading from this line (1-based). Default: 1.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum lines to read. Default: 100.",
                },
            },
            "required": ["doc_id"],
        }

    def canonicalize_args(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {
            "doc_id": kwargs.get("doc_id"),
            "offset": max(kwargs.get("offset") or 1, 1),
            "limit": min(kwargs.get("limit") or 100, 500),
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        from knowledge.store import read_knowledge_doc, list_knowledge_docs

        doc_id = kwargs["doc_id"]
        offset = max(kwargs.get("offset") or 1, 1)
        limit = min(kwargs.get("limit") or 100, 500)

        # Permission check: verify user can see this doc
        visible_ids = {d["doc_id"] for d in list_knowledge_docs(user_id=ctx.user_id)}
        if doc_id not in visible_ids:
            return {
                "output": json.dumps({
                    "error": "Document not found or access denied",
                    "doc_id": doc_id,
                }),
                "is_error": True,
            }

        result = read_knowledge_doc(doc_id)
        if result is None:
            return {
                "output": json.dumps({"error": "Document not found", "doc_id": doc_id}),
                "is_error": True,
            }

        fm, body = result
        lines = body.split("\n")
        total_lines = len(lines)

        # Apply offset/limit
        start = offset - 1  # 0-based
        selected = lines[start:start + limit]
        content = "\n".join(selected)

        output = {
            "doc_id": doc_id,
            "title": fm.get("title", ""),
            "kind": fm.get("kind", ""),
            "total_lines": total_lines,
            "offset": offset,
            "limit": limit,
            "lines_returned": len(selected),
            "has_more": (start + limit) < total_lines,
            "content": content,
            "source_file": fm.get("source_file", ""),
        }
        return {"output": json.dumps(output, ensure_ascii=False), "is_error": False}
