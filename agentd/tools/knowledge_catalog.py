"""knowledge_catalog tool (Phase P6-B).

Returns metadata of all knowledge documents visible to the current user.
Permission filtering happens at the tool layer — the model never sees
documents the user doesn't have access to.
"""

import json
from typing import Any

from tools.base import BaseTool, ToolContext, ToolMetadata


class KnowledgeCatalogTool(BaseTool):
    @property
    def name(self) -> str:
        return "knowledge_catalog"

    @property
    def description(self) -> str:
        return (
            "List all knowledge documents available to the current user. "
            "Returns metadata (title, description, tags, kind, author) "
            "for each document. Use this to discover what knowledge is "
            "available before searching or reading."
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
                "tag_filter": {
                    "type": "string",
                    "description": "Optional: filter by tag (case-insensitive substring match).",
                },
            },
            "required": [],
        }

    async def execute(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        from knowledge.store import list_knowledge_docs

        tag_filter = (kwargs.get("tag_filter") or "").lower().strip()

        docs = list_knowledge_docs(user_id=ctx.user_id)

        if tag_filter:
            docs = [
                d for d in docs
                if any(tag_filter in t.lower() for t in d.get("tags", []))
            ]

        # Return concise metadata only
        items = []
        for d in docs:
            items.append({
                "doc_id": d.get("doc_id", ""),
                "title": d.get("title", ""),
                "description": d.get("description", ""),
                "tags": d.get("tags", []),
                "kind": d.get("kind", ""),
                "author": d.get("author", ""),
                "permission": d.get("permission", ""),
            })

        result = {"count": len(items), "documents": items}
        return {"output": json.dumps(result, ensure_ascii=False), "is_error": False}
