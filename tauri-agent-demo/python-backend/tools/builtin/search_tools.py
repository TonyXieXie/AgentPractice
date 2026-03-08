import logging
from typing import Any, Dict, List

import httpx

from ..base import Tool, ToolParameter
from ..config import get_tool_config
from .common import _parse_json_input


logger = logging.getLogger(__name__)

_SUPPORTED_SEARCH_PROVIDERS = {"tavily", "gemini"}
_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


def _normalize_provider_name(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _normalize_provider_order(raw: Any) -> List[str]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        items = [part.strip() for part in raw.split(",")]
    else:
        items = []

    normalized: List[str] = []
    for item in items:
        provider = _normalize_provider_name(item)
        if provider and provider not in normalized:
            normalized.append(provider)
    return normalized


def _format_http_error(exc: httpx.HTTPStatusError) -> str:
    detail = ""
    try:
        detail = exc.response.text.strip()
    except Exception:
        detail = ""
    if len(detail) > 400:
        detail = f"{detail[:397]}..."
    suffix = f": {detail}" if detail else ""
    return f"HTTP {exc.response.status_code}{suffix}"


def _format_provider_error(provider: str, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{provider}: {_format_http_error(exc)}"
    return f"{provider}: {exc}"


class SearchTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "search"
        self.description = "Search the web using configured providers with automatic fallback."
        self.parameters = [
            ToolParameter(name="query", type="string", description="Search query.", required=True),
            ToolParameter(name="max_results", type="number", description="Max results.", required=False),
        ]

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        query = str(data.get("query") or input_data or "").strip()
        if not query:
            raise ValueError("Missing query.")

        search_cfg = get_tool_config().get("search", {})
        max_results = data.get("max_results")
        max_results = int(max_results) if max_results is not None else int(search_cfg.get("max_results", 5))
        provider_order = self._get_provider_order(data, search_cfg)
        if not provider_order:
            return "Search provider not configured. Set search.providers or search.provider in tools_config.json."

        failures: List[str] = []
        for provider in provider_order:
            try:
                return await self._execute_provider(provider, query, max_results, search_cfg)
            except Exception as exc:
                failure = _format_provider_error(provider, exc)
                logger.warning("Search provider failed", extra={"provider": provider, "query": query, "error": failure})
                failures.append(failure)

        lines = ["All configured search providers failed."]
        lines.extend(f"- {failure}" for failure in failures)
        return "\n".join(lines)

    def _get_provider_order(self, data: Dict[str, Any], search_cfg: Dict[str, Any]) -> List[str]:
        override = data.get("providers")
        if override is None and data.get("provider") is not None:
            override = [data.get("provider")]

        providers = _normalize_provider_order(override)
        if not providers:
            providers = _normalize_provider_order(search_cfg.get("providers"))
        if not providers:
            providers = _normalize_provider_order(search_cfg.get("provider"))
        if not providers:
            providers = ["tavily"]
        return providers

    async def _execute_provider(
        self,
        provider: str,
        query: str,
        max_results: int,
        search_cfg: Dict[str, Any],
    ) -> str:
        provider_name = _normalize_provider_name(provider)
        if provider_name == "tavily":
            return await self._search_with_tavily(query, max_results, search_cfg)
        if provider_name == "gemini":
            return await self._search_with_gemini(query, max_results, search_cfg)
        if provider_name in _SUPPORTED_SEARCH_PROVIDERS:
            raise RuntimeError(f"Provider implementation missing for {provider_name}")
        raise RuntimeError(
            f"Unsupported search provider: {provider_name}. Supported providers: {', '.join(sorted(_SUPPORTED_SEARCH_PROVIDERS))}"
        )

    async def _search_with_tavily(self, query: str, max_results: int, search_cfg: Dict[str, Any]) -> str:
        api_key = str(search_cfg.get("tavily_api_key") or "").strip()
        if not api_key:
            raise RuntimeError("Tavily API key not configured. Set TAVILY_API_KEY or tools_config.json.")

        search_depth = search_cfg.get("search_depth", "basic")
        min_score = search_cfg.get("min_score", 0.4)
        try:
            min_score = float(min_score)
        except (TypeError, ValueError):
            min_score = 0.4

        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": search_depth,
            "include_answer": False,
            "include_raw_content": False,
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post("https://api.tavily.com/search", json=payload)
            response.raise_for_status()
            result_data = response.json()

        results = result_data.get("results", []) if isinstance(result_data, dict) else []
        filtered_results = []
        for item in results:
            score = item.get("score") if isinstance(item, dict) else None
            if score is not None:
                try:
                    score = float(score)
                except (TypeError, ValueError):
                    score = None
            if score is not None and score < min_score:
                continue
            filtered_results.append(item)

        if not filtered_results:
            return "Search results (provider: tavily):\nNo results."

        lines = ["Search results (provider: tavily):"]
        for idx, item in enumerate(filtered_results, start=1):
            title = item.get("title", "")
            url = item.get("url", "")
            snippet = item.get("content", "")
            lines.append(f"{idx}. {title}")
            if url:
                lines.append(url)
            if snippet:
                lines.append(snippet)
            lines.append("")

        return "\n".join(lines).strip()

    async def _search_with_gemini(self, query: str, max_results: int, search_cfg: Dict[str, Any]) -> str:
        api_key = str(search_cfg.get("gemini_api_key") or "").strip()
        if not api_key:
            raise RuntimeError("Gemini API key not configured. Set GEMINI_API_KEY or tools_config.json.")

        model = str(search_cfg.get("gemini_model") or _DEFAULT_GEMINI_MODEL).strip() or _DEFAULT_GEMINI_MODEL
        prompt = (
            "Search the web using Google Search grounding for the query below. "
            "Provide a concise factual summary based on the search results.\n\n"
            f"Query: {query}"
        )
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "tools": [{"google_search": {}}],
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": api_key},
                json=payload,
            )
            response.raise_for_status()
            result_data = response.json()

        return self._format_gemini_result(result_data, max_results)

    def _format_gemini_result(self, result_data: Dict[str, Any], max_results: int) -> str:
        candidates = result_data.get("candidates", []) if isinstance(result_data, dict) else []
        candidate = next((item for item in candidates if isinstance(item, dict)), None)
        if not candidate:
            raise RuntimeError("Gemini returned no candidates.")

        content = candidate.get("content", {}) if isinstance(candidate, dict) else {}
        parts = content.get("parts", []) if isinstance(content, dict) else []
        summary_parts: List[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = str(part.get("text") or "").strip()
            if text:
                summary_parts.append(text)
        summary = "\n".join(summary_parts).strip()

        grounding = candidate.get("groundingMetadata", {}) if isinstance(candidate, dict) else {}
        raw_queries = grounding.get("webSearchQueries", []) if isinstance(grounding, dict) else []
        queries = [str(item).strip() for item in raw_queries if str(item).strip()]

        chunks = grounding.get("groundingChunks", []) if isinstance(grounding, dict) else []
        sources: List[Dict[str, str]] = []
        seen_urls = set()
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            web = chunk.get("web")
            if not isinstance(web, dict):
                continue
            url = str(web.get("uri") or "").strip()
            title = str(web.get("title") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            sources.append({"title": title or url, "url": url})
            if len(sources) >= max_results:
                break

        if not summary and not sources:
            raise RuntimeError("Gemini returned no grounded search content.")

        lines = ["Search results (provider: gemini):"]
        if summary:
            lines.append("Summary:")
            lines.append(summary)
            lines.append("")
        if queries:
            lines.append("Search queries:")
            for item in queries[:max_results]:
                lines.append(f"- {item}")
            lines.append("")
        if sources:
            lines.append("Sources:")
            for idx, source in enumerate(sources, start=1):
                lines.append(f"{idx}. {source['title']}")
                lines.append(source["url"])
                lines.append("")
        elif not summary:
            lines.append("No grounded sources returned.")

        return "\n".join(lines).strip()


__all__ = ["SearchTool"]
