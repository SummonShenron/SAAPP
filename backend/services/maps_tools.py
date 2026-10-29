"""Sonic's two place tools, built on Google's Maps tools over MCP (backend/services/mcp_client.py): `find_places` and `travel_times`.

WHY THESE AND NOT THE SERVER'S OWN. Google's `search_places` returns about 1,400 characters a place (five links each, none of them an address) and its tool descriptions are long; its `compute_routes` makes the model
call it once per candidate. Both land in Sonic's prompt on every call, and we do not control them. So Sonic sees two small tools of ours instead:

- find_places(query, latitude?, longitude?, radius_meters?, count?): looks for places and returns each one as `[index] name, its coordinates, its Maps link`, then Google's summary of them (capped). That is a few
  hundred characters a place instead of 1,400.
- travel_times(origin, destinations[up to 5], mode?, depart_at?): asks Google for the route from ONE starting point to EACH destination in one tool call (the server calls happen here, with no model step between), and
  returns the distance and time to each. This is what answers "which of these is actually close to us": the search gives coordinates and no neighbourhood, so a district named from memory can be wrong (a bar called
  "west side" that was downtown), while a measured drive or walk from where the person is cannot.

WHAT IS NEVER USED: extended details (reviews and menus; a Pro-tier request that costs more and needs an allowlisted project), live-traffic routing (Pro), and polylines. WHAT THE TERMS REQUIRE: the source is named
and a place links to its Google Maps page (the descriptions tell Sonic to, and the results carry the links), walking times carry Google's beta warning, and Maps content is not to be stored or used to train a model
(see docs/mcp-client-plan notes in memory about our saved conversations). Results are text other people wrote (the summary quotes reviews), so they are untrusted data like any web page.

Configured by environment: MAPS_MCP_API_KEY (a Maps API key, not the Gemini key: Google answers a Gemini-style key with 401 "API keys are not supported by this API"), MAPS_MCP_URL (default Google's endpoint)."""
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.services import mcp_client as mc

SERVER = mc.McpServerConfig(name="maps", url=os.getenv("MAPS_MCP_URL", "https://mapstools.googleapis.com/mcp"), key_header="X-Goog-Api-Key", key_env="MAPS_MCP_API_KEY")

MAX_DESTINATIONS = 5
SUMMARY_CHARS = 1400
_LAT_LNG = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_MODES = ("DRIVE", "WALK", "BICYCLE", "TRANSIT")
_MODE_WORDS = {"DRIVE": "driving", "WALK": "walking", "BICYCLE": "cycling", "TRANSIT": "by transit"}
_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")


def _coordinate(value: Any, low: float, high: float, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not (low <= float(value) <= high):
        raise mc.McpError(f"{name} must be a number between {low:g} and {high:g}.")
    return float(value)


# ---- find_places ------------------------------------------------------------------------------------------------------------------------------

def _build_search(arguments: Dict[str, Any]) -> Dict[str, Any]:
    query = " ".join(str(arguments.get("query") or "").split())
    if len(query) < 3:
        raise mc.McpError("Say what to look for, with the city or area in it.")
    server: Dict[str, Any] = {"textQuery": query, "pageSize": max(1, min(int(arguments.get("count") or 5), 5))}
    lat, lng = arguments.get("latitude"), arguments.get("longitude")
    if (lat is None) != (lng is None):
        raise mc.McpError("Give both latitude and longitude, or neither.")
    if lat is not None:
        circle: Dict[str, Any] = {"center": {"latitude": _coordinate(lat, -90, 90, "latitude"), "longitude": _coordinate(lng, -180, 180, "longitude")}}
        radius = arguments.get("radius_meters")
        if radius is not None:
            circle["radiusMeters"] = float(max(100, min(int(radius), 50000)))
        server["locationBias"] = {"circle": circle}
    return server


def _clean_name(attribution: Any) -> str:
    title = str((attribution or {}).get("title") or "").strip() if isinstance(attribution, dict) else ""
    return re.sub(r"\s+-\s+Google Maps$", "", title) or "(unnamed place)"


def _render_places(text: str) -> str:
    data = json.loads(text)
    places = data.get("places") if isinstance(data, dict) else None
    if not isinstance(places, list) or not places:
        return "Google Maps found no places for that. Try a different or broader search."
    lines = ["Places from Google Maps (index, name, coordinates, Maps link). These have no street address or neighbourhood: use travel_times from where the person is to learn how far each one really is."]
    for index, place in enumerate(places[:5]):
        if not isinstance(place, dict):
            continue
        spot = place.get("location") if isinstance(place.get("location"), dict) else {}
        links = place.get("googleMapsLinks") if isinstance(place.get("googleMapsLinks"), dict) else {}
        where = f"{spot['latitude']:.5f},{spot['longitude']:.5f}" if isinstance(spot.get("latitude"), (int, float)) and isinstance(spot.get("longitude"), (int, float)) else "coordinates unknown"
        link = str(links.get("placeUrl") or (place.get("attribution") or {}).get("url") or "")
        lines.append(f"[{index}] {_clean_name(place.get('attribution'))} | {where} | {link}")
    summary = " ".join(str(data.get("summary") or "").split())
    if summary:
        lines.append("Google's summary of these places, by index (it quotes reviews, which are other people's words): " + (summary if len(summary) <= SUMMARY_CHARS else summary[:SUMMARY_CHARS].rstrip() + " [cut]"))
    lines.append("Source: Google Maps. Link each place you name to its Maps link.")
    return "\n".join(lines)


# ---- travel_times -----------------------------------------------------------------------------------------------------------------------------

def _waypoint(value: str, role: str) -> Dict[str, Any]:
    """A place as the route service takes it: coordinates written as "lat,lng", a Maps place id as "place:ID", or otherwise an address or a name with its city."""
    text = " ".join(str(value or "").split())
    if not text:
        raise mc.McpError(f"The {role} is empty.")
    found = _LAT_LNG.match(text)
    if found:
        return {"latLng": {"latitude": _coordinate(float(found.group(1)), -90, 90, "latitude"), "longitude": _coordinate(float(found.group(2)), -180, 180, "longitude")}}
    if text.lower().startswith("place:"):
        identifier = text[6:].strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-]{10,200}", identifier):
            raise mc.McpError("That is not a Maps place id.")
        return {"placeId": identifier}
    return {"address": text[:200]}


def _minutes(seconds: int) -> str:
    if seconds < 60:
        return "under a minute"
    minutes = round(seconds / 60)
    return f"{minutes} min" if minutes < 60 else (f"{minutes // 60} h" if minutes % 60 == 0 else f"{minutes // 60} h {minutes % 60} min")


def _render_route(label: str, text: str, mode: str) -> str:
    data = json.loads(text)
    routes = data.get("routes") if isinstance(data, dict) else None
    if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
        return f"- {label}: no route found."
    route = routes[0]
    meters = route.get("distanceMeters")
    duration = str(route.get("duration") or "")
    seconds = int(duration[:-1]) if re.fullmatch(r"\d+s", duration) else None
    if not isinstance(meters, (int, float)) or seconds is None:
        return f"- {label}: Google returned no distance or time."
    miles = meters / 1609.344
    distance = f"{meters / 1000:.1f} km ({miles:.1f} mi)" if meters >= 1000 else f"{int(meters)} m"
    link = str((route.get("attribution") or {}).get("url") or "") if isinstance(route.get("attribution"), dict) else ""
    return f"- {label}: {distance}, about {_minutes(seconds)} {_MODE_WORDS[mode]}" + (f" | {link}" if link else "")


def _travel_times(client: "mc.McpHttpClient", arguments: Dict[str, Any]) -> Tuple[str, bool]:
    origin_text = str(arguments.get("origin") or "")
    destinations: List[str] = [str(d) for d in arguments.get("destinations") or [] if str(d).strip()]
    if not destinations:
        raise mc.McpError("Give at least one destination.")
    if len(destinations) > MAX_DESTINATIONS:
        raise mc.McpError(f"At most {MAX_DESTINATIONS} destinations at a time.")
    mode = str(arguments.get("mode") or "DRIVE").upper()
    if mode not in _MODES:
        raise mc.McpError(f"The mode must be one of: {', '.join(_MODES)}.")
    depart = arguments.get("depart_at")
    if depart is not None and not _RFC3339.match(str(depart)):
        raise mc.McpError("depart_at must be a time like 2026-11-07T19:30:00-05:00.")
    origin = _waypoint(origin_text, "origin")
    lines, found, failed = [], 0, 0
    for destination in destinations:
        server: Dict[str, Any] = {"origin": origin, "destination": _waypoint(destination, "destination"), "travelMode": mode}
        if depart:
            server["departureTime"] = str(depart)
        label = " ".join(destination.split())[:80]
        try:
            text, errored = client.call_tool("compute_routes", server)
        except mc.McpError as problem:
            failed += 1
            lines.append(f"- {label}: could not be looked up ({problem}).")
            continue
        if errored:
            failed += 1
            lines.append(f"- {label}: could not be looked up.")
            continue
        try:
            lines.append(_render_route(label, text, mode))
            found += 1
        except (ValueError, TypeError):
            failed += 1
            lines.append(f"- {label}: Google's answer could not be read.")
    if found == 0:
        return "No route could be found for any of them. " + " ".join(lines)[:300], True
    head = f"Travel from {' '.join(origin_text.split())[:80]} ({_MODE_WORDS[mode]}), from Google Maps:"
    tail = ["Walking times are in beta and may miss some pedestrian paths." if mode == "WALK" else "", "Source: Google Maps."]
    return "\n".join([head, *lines, *[line for line in tail if line]]), False


# ---- the curated tools --------------------------------------------------------------------------------------------------------------------------

SERVER.tools = {
    "find_places": mc.ToolPolicy(
        description=(
            "Look up real places (bars, restaurants, cafes, shops, sights, hotels) on Google Maps. Put the city or area in the query, for example 'cocktail bars for a date night in downtown Portland, Oregon'; if the person "
            "did not say where, ask first. Returns up to 5 places, each with its coordinates and Maps link, and Google's summary of them. It gives NO street address or neighbourhood, so never say what part of town a "
            "place is in unless the summary says so; use travel_times from where the person is to measure how far each one is. Link each place you name to its Maps link and name Google Maps as the source."),
        arguments={
            "query": {"type": "string", "description": "what to look for, with the city or area in it"},
            "latitude": {"type": "number", "description": "optional: latitude of the area to favour (only with longitude)"},
            "longitude": {"type": "number", "description": "optional: longitude of the area to favour"},
            "radius_meters": {"type": "integer", "description": "optional: how far from that point to favour, 100 to 50000"},
            "count": {"type": "integer", "enum": [1, 2, 3, 4, 5], "description": "how many places, up to 5 (default 5)"},
        },
        required=("query",), max_result_chars=2600, daily_limit=60, server_name="search_places", build=_build_search, render=_render_places),
    "travel_times": mc.ToolPolicy(
        description=(
            "How far and how long it really is from ONE starting point to up to 5 places, by car, on foot, by bicycle or by transit, from Google Maps. Use it after find_places (give each place as its 'lat,lng' "
            "coordinates) to tell the person which are close to where they are or to where they are going, instead of guessing districts. The starting point is an address or a named place with its city, or 'lat,lng'. "
            "If you do not know where they are starting from, ask. Report the distance and time as given and name Google Maps as the source."),
        arguments={
            "origin": {"type": "string", "description": "where from: an address or named place with its city, or 'lat,lng'"},
            "destinations": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_DESTINATIONS, "description": "up to 5 places: 'lat,lng', an address or named place with its city"},
            "mode": {"type": "string", "enum": list(_MODES), "description": "DRIVE (default), WALK, BICYCLE or TRANSIT"},
            "depart_at": {"type": "string", "description": "optional: when they leave, like 2026-11-07T19:30:00-05:00 (default: now)"},
        },
        required=("origin", "destinations"), max_result_chars=2600, daily_limit=60, execute=_travel_times),
}


def tool_names() -> List[str]:
    return [mc.tool_name(SERVER, name) for name in SERVER.tools]


def available() -> bool:
    return SERVER.configured()


def declarations() -> List[Dict[str, Any]]:
    """The function declarations for the native engine: ours, never the server's."""
    return [mc.declaration(SERVER, name) for name in SERVER.tools]


def menu_lines() -> List[str]:
    """The same two tools as the old engine's text menu lines ("- name — description")."""
    lines = []
    for name, policy in SERVER.tools.items():
        args = ", ".join(f"{key} ({spec.get('description', '')})" for key, spec in policy.arguments.items())
        lines.append(f"- {mc.tool_name(SERVER, name)} — args: {args}. {policy.description}")
    return lines


def run(tool: str, arguments: Any, username: str = "") -> str:
    """One call of one of the two tools by the name Sonic uses (mcp__maps__find_places). Blocking: run it off the event loop. Failures come back as an ERROR line."""
    parts = mc.split_tool_name(tool)
    if parts is None or parts[0] != SERVER.name:
        return f"ERROR: {tool} is not an available tool."
    return mc.call(SERVER, parts[1], arguments, username)
