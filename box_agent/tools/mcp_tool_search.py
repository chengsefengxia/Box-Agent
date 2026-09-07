"""Session-scoped MCP discovery and deferred tool exposure."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from .base import Tool, ToolResult, build_tool_name_index
from .mcp_tool_catalog import MCPToolCatalog

TOOL_SEARCH_NAME = "tool_search"
_ACTIVATION_COMPANIONS: dict[str, tuple[str, ...]] = {
    # Playwright navigation returns metadata before the page-body snapshot.
    # Exposing one without the other creates a runtime state the model cannot
    # complete once a workflow requires exact-page evidence.
    "managed_browser_navigate": ("managed_browser_snapshot",),
}


@dataclass(frozen=True, slots=True)
class ActivatedMCPTool:
    tool_id: str
    model_name: str
    generation: int


@dataclass(frozen=True, slots=True)
class ToolExposure:
    tools: list[Tool]
    offered_names: frozenset[str]
    mcp_generations: dict[str, int]


class ToolSearchTool(Tool):
    """Search the MCP catalog and activate usable hits for this session."""

    reserved_deferred_mcp_search = True

    def __init__(
        self,
        catalog: MCPToolCatalog,
        activated: OrderedDict[str, ActivatedMCPTool],
        *,
        protected_names_provider: Callable[[], frozenset[str]] | None = None,
        allowed_connector_ids_provider: Callable[[], frozenset[str]] | None = None,
        readiness_timeout: float = 15.0,
    ) -> None:
        self._catalog = catalog
        self._activated = activated
        self._protected_names_provider = protected_names_provider
        self._allowed_connector_ids_provider = allowed_connector_ids_provider
        self._readiness_timeout = readiness_timeout

    def _entry_is_allowed(self, entry) -> bool:
        if entry.connector_id is None:
            return True
        if self._allowed_connector_ids_provider is None:
            return True
        return entry.connector_id in self._allowed_connector_ids_provider()

    @property
    def name(self) -> str:
        return TOOL_SEARCH_NAME

    @property
    def description(self) -> str:
        return (
            "Search the connected deferred MCP catalog by capability. Every hit "
            "returned by this call is immediately activated for this session and "
            "only those activated hits are added to the next model step; other "
            "deferred catalog tools are not exposed, while alwaysLoad tools remain "
            "visible without search. Use query for one keyword search, queries for "
            "independent bilingual or synonymous searches, or tool_names to activate "
            "only exact catalog IDs, model-facing names, or unique original MCP names. "
            "Passing only server_name activates all tools from that one enabled server. "
            "Provide at least one non-empty query, queries, tool_names, server_name, or "
            "connector input; they may be combined. Prefer short capability, server, "
            "connector, or tool "
            "keywords; task-specific operands are tolerated but should be omitted "
            "when possible. Passing only connector activates all tools owned by "
            "the uniquely matched enabled connector. Set top_k to however many "
            "matching tool schemas the task actually needs, including ten or more "
            "when appropriate. "
            "A query hit may activate a protocol-required companion, such as the "
            "snapshot paired with managed browser navigation, in addition to top_k. "
            "The response reports catalog_tool_count for the applied server scope, "
            "matched_count after query limits, and activated_count after conflict "
            "filtering; never infer the catalog total from top_k or matched_count. "
            "This search activates tools but does not execute them."
        )

    @property
    def parameters(self) -> dict:
        # Some providers reject top-level unions. execute() checks that at
        # least one search input is non-empty before accessing the catalog.
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "One capability, action, server, or tool keyword query. Kept "
                        "for compatibility; prefer queries for independent alternatives."
                    ),
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": (
                        "Independent keyword queries merged by each tool's best "
                        "relevance. Use separate Chinese, English, synonym, or exact "
                        "capability phrases. Prefix matches are supported and "
                        "unmatched task operands are tolerated."
                    ),
                },
                "tool_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": (
                        "Exact tool IDs, model-facing names, or unique original MCP names "
                        "to activate, such as mcp:server/tool, server/tool, or an "
                        "MCP tools/list name. No fuzzy fallback is used, and unlisted "
                        "catalog tools remain hidden."
                    ),
                },
                "server_name": {
                    "type": "string",
                    "description": (
                        "Exact internal MCP server name. On its own, activates all "
                        "tools from that enabled server; with query, connector, or "
                        "tool_names, it narrows the normal discovery scope."
                    ),
                },
                "connector": {
                    "type": "string",
                    "description": (
                        "Connector ID or unique display-name text from "
                        "<connector-status>, such as pkulaw or 北大法宝. On its "
                        "own, activates all tools owned by that enabled connector; "
                        "with query, server_name, or tool_names, it narrows the "
                        "normal discovery scope."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1,
                    "description": (
                        "Exact maximum number of query matches to return and activate; "
                        "ignored for tool_names and server-only activation. Choose any positive count required by "
                        "the task; there is no small fixed cap. Protocol-required "
                        "companions may be activated in addition to these query matches. "
                        "Other unreturned catalog tools remain hidden."
                    ),
                },
            },
            "additionalProperties": False,
        }

    async def execute(
        self,
        query: str | None = None,
        queries: list[str] | None = None,
        tool_names: list[str] | None = None,
        server_name: str | None = None,
        connector: str | None = None,
        top_k: int = 1,
    ) -> ToolResult:
        normalized_server_name = (
            server_name.strip()
            if isinstance(server_name, str) and server_name.strip()
            else None
        )
        normalized_queries = [
            item
            for item in ([query] if query else []) + (queries or [])
            if isinstance(item, str) and item.strip()
        ]
        normalized_tool_names = [
            item
            for item in tool_names or []
            if isinstance(item, str) and item.strip()
        ]
        normalized_connector = (
            connector.strip()
            if isinstance(connector, str) and connector.strip()
            else None
        )
        search_input = {
            "query": query,
            "queries": normalized_queries,
            "tool_names": normalized_tool_names,
            "server_name": normalized_server_name,
            "connector": normalized_connector,
        }
        connector_direct = bool(
            normalized_connector
            and not normalized_server_name
            and not normalized_queries
            and not normalized_tool_names
        )
        server_direct = bool(
            normalized_server_name and not normalized_queries and not normalized_tool_names
        )
        if (
            not normalized_queries
            and not normalized_tool_names
            and not server_direct
            and not connector_direct
        ):
            payload = {
                "success": False,
                **search_input,
                "catalog_tool_count": None,
                "matched_count": 0,
                "companion_count": 0,
                "activated_count": 0,
                "activated": [],
                "conflicts": [],
                "missing": [],
                "notice": "Provide query, queries, exact tool_names, server_name, or connector.",
            }
            return ToolResult(
                success=False,
                content=json.dumps(payload, ensure_ascii=False),
                error="Tool search input is empty.",
            )

        if not await self._catalog.wait_until_ready(self._readiness_timeout):
            payload = {
                "success": False,
                **search_input,
                "state": "catalog_loading",
                "catalog_tool_count": None,
                "matched_count": 0,
                "companion_count": 0,
                "activated_count": 0,
                "activated": [],
                "conflicts": [],
                "missing": [],
                "notice": (
                    "The MCP catalog is still loading. Retry tool_search after the "
                    "runtime readiness update; no empty-catalog conclusion was made."
                ),
            }
            return ToolResult(
                success=False,
                content=json.dumps(payload, ensure_ascii=False),
                error="MCP catalog is still loading; retry tool_search shortly.",
            )

        resolved_connector_id = (
            self._catalog.resolve_connector_id(
                normalized_connector,
                entry_filter=self._entry_is_allowed,
            )
            if normalized_connector
            else None
        )

        def entry_is_in_scope(entry) -> bool:
            if not self._entry_is_allowed(entry):
                return False
            if normalized_connector and (
                resolved_connector_id is None
                or entry.connector_id != resolved_connector_id
            ):
                return False
            return normalized_server_name is None or entry.server_name == normalized_server_name

        search_input["resolved_connector_id"] = resolved_connector_id
        catalog_tool_count = sum(
            1 for entry in self._catalog.snapshot() if entry_is_in_scope(entry)
        )
        missing: list[str] = []
        if connector_direct or server_direct:
            hits = [
                entry
                for entry in self._catalog.snapshot()
                if entry_is_in_scope(entry)
            ]
        elif normalized_tool_names:
            hits, missing = self._catalog.lookup_exact(
                normalized_tool_names,
                server_name=normalized_server_name,
                entry_filter=entry_is_in_scope,
            )
        else:
            hits = self._catalog.search_many(
                normalized_queries,
                server_name=normalized_server_name,
                top_k=top_k,
                entry_filter=entry_is_in_scope,
            )
        query_matched_count = len(hits)
        hit_ids = {entry.tool_id for entry in hits}
        companion_entries = []
        if not normalized_tool_names and not server_direct and not connector_direct:
            for entry in tuple(hits):
                for companion_name in _ACTIVATION_COMPANIONS.get(entry.model_name, ()):
                    companions, _ = self._catalog.lookup_exact(
                        [companion_name],
                        server_name=entry.server_name,
                        entry_filter=entry_is_in_scope,
                    )
                    for companion in companions:
                        if companion.tool_id in hit_ids:
                            continue
                        hit_ids.add(companion.tool_id)
                        hits.append(companion)
                        companion_entries.append(companion)
        activated_results = []
        conflicts = []
        protected_names = (
            self._protected_names_provider()
            if self._protected_names_provider is not None
            else frozenset()
        )
        for entry in hits:
            if (
                entry.name_conflict
                or entry.model_name == TOOL_SEARCH_NAME
                or entry.model_name in protected_names
            ):
                conflicts.append(
                    {
                        "tool_id": entry.tool_id,
                        "name": entry.model_name,
                        "server_name": entry.server_name,
                        "error": (
                            "reserved deferred-search tool name"
                            if entry.model_name == TOOL_SEARCH_NAME
                            else (
                                "conflicts with stable core tool"
                                if entry.model_name in protected_names
                                else "duplicate model-facing tool name"
                            )
                        ),
                    }
                )
                continue
            previous = self._activated.get(entry.tool_id)
            already_active = (
                previous is not None and previous.generation == entry.generation
            )
            self._activated[entry.tool_id] = ActivatedMCPTool(
                tool_id=entry.tool_id,
                model_name=entry.model_name,
                generation=entry.generation,
            )
            activated_results.append(
                {
                    "name": entry.model_name,
                    "server_name": entry.server_name,
                    "connector_id": entry.connector_id,
                    "connector_name": entry.connector_name,
                    "remote_name": entry.remote_name,
                    "description": entry.description,
                    "already_active": already_active,
                }
            )
        payload = {
            "success": not conflicts or bool(activated_results),
            **search_input,
            "catalog_tool_count": catalog_tool_count,
            "matched_count": query_matched_count,
            "companion_count": len(companion_entries),
            "activated_count": len(activated_results),
            "activated": activated_results,
            "conflicts": conflicts,
            "missing": missing,
            "notice": (
                f"Activated {len(activated_results)} returned or required companion "
                "tool(s) for this session. Only these hits, their protocol-required "
                "companions, and explicit eager tools are callable by their real name "
                "on the next step; all other catalog tools remain hidden."
                if activated_results
                else (
                    "Matching tools have conflicting model-facing names."
                    if conflicts
                    else (
                        "No exact MCP tools found for the requested tool_names."
                        if normalized_tool_names
                        else "No matching MCP tools found."
                    )
                )
            ),
        }
        conflict_error = None
        if conflicts and not activated_results:
            conflict_error = (
                "MCP tool name conflict; adjust MCP configuration before activation."
            )
        return ToolResult(
            success=conflict_error is None,
            content=json.dumps(payload, ensure_ascii=False),
            error=conflict_error,
        )


class MCPToolExposureManager:
    """Build one step's visible tool set from a session activation store."""

    def __init__(
        self,
        catalog: MCPToolCatalog,
        activated: OrderedDict[str, ActivatedMCPTool],
        allowed_connector_ids_provider: Callable[[], frozenset[str]] | None = None,
    ) -> None:
        self._catalog = catalog
        self._activated = activated
        self._allowed_connector_ids_provider = allowed_connector_ids_provider

    def _entry_is_allowed(self, entry) -> bool:
        if entry.connector_id is None:
            return True
        if self._allowed_connector_ids_provider is None:
            return True
        return entry.connector_id in self._allowed_connector_ids_provider()

    def prepare_tools(self, candidates: list[Tool]) -> ToolExposure:
        # ``candidates`` is the session's stable core-tool registry. Ordinary
        # MCP tools live only in the process catalog and are appended here
        # after an explicit activation (or alwaysLoad). Keeping the two stores
        # separate makes it impossible for a loaded MCP schema to leak into a
        # provider request merely because it was connected successfully.
        visible: OrderedDict[str, Tool] = OrderedDict()
        generations: dict[str, int] = {}
        for tool in candidates:
            if getattr(tool, "mcp_tool_id", None) is None:
                visible[tool.name] = tool
        protected_names = frozenset(build_tool_name_index(visible.values()))

        for entry in self._catalog.snapshot():
            if not self._entry_is_allowed(entry):
                continue
            if (
                entry.name_conflict
                or entry.model_name == TOOL_SEARCH_NAME
                or entry.model_name in protected_names
            ):
                continue
            activation = self._activated.get(entry.tool_id)
            activated = activation is not None and activation.generation == entry.generation
            if not entry.always_load and not activated:
                continue
            visible[entry.model_name] = entry.tool
            generations[entry.model_name] = entry.generation
        tools = list(visible.values())
        return ToolExposure(tools, frozenset(visible), generations)

    def inherited_tools(self, tool_map: dict[str, Tool]) -> dict[str, Tool]:
        """Give child agents only the parent's currently visible real tools."""
        exposure = self.prepare_tools(list(tool_map.values()))
        return {
            tool.name: tool
            for tool in exposure.tools
            if tool.name != TOOL_SEARCH_NAME
        }

    def validate_call(
        self,
        name: str,
        offered_generation: int | None,
        target_tool: Tool | None = None,
    ) -> str | None:
        if offered_generation is None:
            return None
        current = self._catalog.get_by_model_name(name)
        if current is None:
            return f"MCP tool '{name}' is unavailable or has a name conflict; search again."
        if not self._entry_is_allowed(current):
            return f"MCP tool '{name}' is not enabled for this conversation; search again."
        if current.generation != offered_generation:
            return f"MCP tool '{name}' changed after it was offered; search again."
        if (
            target_tool is not None
            and getattr(target_tool, "mcp_generation", None) != offered_generation
        ):
            return f"MCP tool '{name}' execution target changed after it was offered; search again."
        return None
