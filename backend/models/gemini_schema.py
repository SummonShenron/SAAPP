"""Tool declarations that Gemini will accept.

WHY. Gemini's function-declaration schema (google-genai `Schema`) only takes `enum` values that are STRINGS. Claude and OpenAI take any JSON enum. The Google Maps tool declared `"count": {"type": "integer",
"enum": [1, 2, 3, 4, 5]}`; whenever a tool turn ran on Gemini, binding the tools raised "5 validation errors for Schema ... Input should be a valid string" and the whole turn died. It stayed hidden because the tool agent
runs on Claude for people who chose it: the first time it showed was 2026-11-20, when Anthropic answered 401 and every turn fell back to Gemini (docs/launch-probe-2026-11-19.md carries the story).

WHAT. Before tools are bound to a Gemini client, an enum that is not all strings is taken out of the schema and its values are said in the description instead ("one of: 1, 2, 3, 4, 5"). Nothing is lost: the
server still checks the value against the tool's own declaration when the tool is called (backend/services/mcp_client.py), and the type stays as declared. The declarations the caller passed are never changed.
"""
from typing import Any, Dict, List


def _safe(node: Any) -> Any:
    if isinstance(node, list):
        return [_safe(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _safe(value) for key, value in node.items()}
    options = out.get("enum")
    if isinstance(options, list) and any(not isinstance(option, str) for option in options):
        del out["enum"]
        said = f"one of: {', '.join(str(option) for option in options)}"
        out["description"] = f"{out['description']} ({said})" if out.get("description") else said
    return out


def gemini_safe_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """A COPY of a JSON schema (every dict and list is rebuilt on the way, so the original is never touched) with every non-string enum, at any depth, replaced by a sentence in its description. Pure."""
    return _safe(schema)


def gemini_safe_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copies of the loop's function declarations ({name, description, parameters}) that Gemini's converter accepts. A declaration with nothing to change comes back equal to what went in. Pure."""
    safe = []
    for tool in tools or []:
        declared = dict(tool)
        for key in ("parameters", "input_schema"):
            if isinstance(declared.get(key), dict):
                declared[key] = gemini_safe_schema(declared[key])
        safe.append(declared)
    return safe
