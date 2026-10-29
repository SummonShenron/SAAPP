# SAAPP as an MCP client (Google Maps tools)

Sonic can use tools offered by an MCP server. The first is Google's Maps service, for real places and real distances. The client is `backend/services/mcp_client.py`; Sonic's two place tools are in
`backend/services/maps_tools.py`; the decision to attach them to a turn is `backend/utils/place_questions.py`.

## Why it exists

Asked for bars for a date, Sonic named real places but put them in the wrong part of town ("west side" when it was downtown). Google's search returns coordinates and links but **no address or neighbourhood**, so a
district named from memory can be wrong. Measuring the real distance or travel time from where the person is cannot be.

## Setup

1. In the Google Cloud console enable **Maps Experience agent tools** (formerly Maps Grounding Lite) for a project with billing on. The first 10,000 requests a month are free, then $7 per 1,000.
2. Create an **API key** (Credentials, Create credentials, API key; it starts with `AIza`) and restrict it to that API. A Gemini key will not work: Google answers it with 401 "API keys are not supported by this API".
3. Set `MAPS_MCP_API_KEY` in `.env` (and in the host's environment). `MAPS_MCP_URL` overrides the address (default `https://mapstools.googleapis.com/mcp`).

Without the key nothing is offered and nothing changes.

## What Sonic sees (curated, not the server's)

| Tool | Does | Calls on Google's side |
|---|---|---|
| `mcp__maps__find_places` | up to 5 places for a query with the city or area in it: name, coordinates, Maps link, and Google's summary (capped) | one `search_places` |
| `mcp__maps__travel_times` | the distance and time from ONE starting point to up to 5 places, by car, on foot, by bike or by transit | one `compute_routes` per destination, made here with no model step between |

A third party's tool descriptions and results would land in Sonic's prompt on every call and we do not control them, so: the server is fixed by us (https only, no redirects), each tool is allowlisted with OUR description
and only the arguments we list (anything else the model sends is dropped), results are rewritten to a small size (about 1,700 characters for three places instead of about 4,300), there is a daily limit per person,
extended details (reviews, menus), live traffic and polylines are never requested, and the tools are attached **only to a turn that is about places or getting somewhere**.

## Rules from Google's terms, and what is done about them

- The source is named and each place links to its Maps page (the descriptions say so and the results carry the links). Walking times carry Google's beta warning.
- Maps content must not be stored by or used to train the model. Sonic's saved conversations and memory summaries may count as storing it: **decide before relying on this in production** (an option is to keep place details out of memory).
- The summary quotes reviews written by other people, so results are untrusted data like a web page: a turn that used them cannot propose a commit.

## Not built yet

A saved "where you live" setting. Today the tool descriptions tell Sonic to ask where the person is starting from when it does not know.
