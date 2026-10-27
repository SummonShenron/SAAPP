# Load testing without real model calls (planned, not built)

Decided 2026-10-27 (Jack): "we should make a note that it needs to be done eventually now that I know we can." Nothing here is built.

## Why this, and why without the model
The question it answers is "how well does the app tier hold up under many people at once": the event loop, the database, memory per open stream, the background tasks. Real model calls are the wrong tool for that: they cost real money (the $5 a day cap), and they hit the provider's rate limits, so the result measures Google's limits and not this server. Standing in for the model is ordinary service virtualization. A small real-model sample is still worth taking afterwards to calibrate the fake's delays.

## What exists today
`LazyLLM` (backend/models/models.py) has a `dev_mode` that returns a fixed "[DEV_MODE] Mock LLM Response." with no delay and no streaming. Nothing in the app turns it on, and it is not realistic enough for load. Embeddings (`embed_text`, backend/utils/embedding_utils.py) are a real Google call and need a stand-in too.

## Design
- **A load-test switch at the two choke points**, off by default: the model (`LazyLLM`: `invoke`, `ainvoke`, `astream`, `ainvoke_tools`) and `embed_text`. Everything else is the real stack: sign-in, routing, the graph, memory recall, SSE streaming, database reads and writes, background tasks.
- **Realistic shape.** The fake waits a sampled time before the first token and streams a canned answer at a sampled speed. Take the distributions from the dashboard's recorded time-to-first-token and total times. It reports fake token usage so metering and the dashboard work. Fake embeddings are a stable vector from the text, with a small delay.
- **Two guards so it can never reach production by accident:** it turns on only when two settings are present, and refuses to turn on when the environment is production.
- **Tool turns.** A plain fake answer exercises the chat path. To load the tool loop, script a short sequence of tool calls followed by an answer.

## Where to run it
A separate staging instance and a test database, never production: the Atlas tier is already throttled, and production has real people and real data. Use test accounts. The per-user and daily spend guard would trip on the fake usage, so turn it off in staging or give the test users room.

## JMeter / Azure Load Testing notes
- JMeter's "Latency" is time to first byte. The app sends a progress event almost at once, so that is the first EVENT, not the first answer token. For time to first token, parse the stream (a JSR223 sampler, or Locust or k6).
- Ramp slowly: 1, 5, 10, 25 concurrent users, each holding a stream open for several seconds. The instance has roughly 195 MB free at idle.

## What to watch
p95 total time and error rate; event-loop stalls (the app already logs them); memory against concurrent streams (Sonic Health, Server memory card); database operations per turn (the counters); Atlas's own metrics (operations, bytes in and out, connections).

## What it will not tell you
Whether the model provider's limits would hold. That is quota arithmetic from the published limits, plus the small real-model sample.

## What would block running more than one instance (found 2026-10-27, so the test results make sense)
Live-turn steering and the wait for an edit approval are held in one process's memory, so a request landing on another worker finds nothing to deliver to; the connected folder's server copy is on one instance's disk. Scheduled jobs already claim work in the database, and the conversation cache reloads from the database. See the scaling discussion in the interview notes.
