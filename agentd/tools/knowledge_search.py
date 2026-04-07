"""knowledge_search tool (Phase P6-B).

Searches knowledge document content within the user's authorized set.
Uses simple text matching (grep-style) on Markdown bodies.
Permission filtering at the tool layer — only searches visible documents.
"""

import json
import os
import re
from typing import Any

from tools.base import BaseTool, ToolContext, ToolMetadata


class KnowledgeSearchTool(BaseTool):
    @property
    def name(self) -> str:
        return "knowledge_search"

    @property
    def description(self) -> str:
        return (
            "Search knowledge document content for a text pattern. "
            "Only searches documents the current user has access to. "
            "Returns matching excerpts with document metadata and line context."
        )

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            default_permission="allow",
            is_read_only=True,
            is_destructive=False,
            is_concurrency_safe=True,
            can_run_in_background=True,
            result_compressibility="high",
            access_scope="system_scoped",
            mutates_session_state=False,
        )

    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (text or regex pattern).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of matching documents to return (default: 10).",
                },
            },
            "required": ["query"],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        from knowledge.store import list_knowledge_docs, read_knowledge_doc

        query = kwargs["query"]
        max_results = kwargs.get("max_results") or 10

        if not query.strip():
            return {"output": json.dumps({"error": "Empty query"}), "is_error": True}

        # Get visible docs
        visible = list_knowledge_docs(user_id=ctx.user_id)

        # Search each document's body
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            # Fallback to literal search
            pattern = re.compile(re.escape(query), re.IGNORECASE)

        matches = []
        for doc_meta in visible:
            doc_id = doc_meta.get("doc_id", "")
            result = read_knowledge_doc(doc_id)
            if result is None:
                continue

            fm, body = result
            lines = body.split("\n")

            doc_matches = []
            for line_num, line in enumerate(lines, 1):
                if pattern.search(line):
                    doc_matches.append({
                        "line": line_num,
                        "text": line.strip()[:200],
                    })

            if doc_matches:
                matches.append({
                    "doc_id": doc_id,
                    "title": fm.get("title", ""),
                    "kind": fm.get("kind", ""),
                    "match_count": len(doc_matches),
                    "excerpts": doc_matches[:5],  # Cap excerpts per doc
                })

            if len(matches) >= max_results:
                break

        result = {
            "query": query,
            "total_matches": len(matches),
            "results": matches,
        }
        return {"output": json.dumps(result, ensure_ascii=False), "is_error": False}
