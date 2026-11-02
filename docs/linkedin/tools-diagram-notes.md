# Talking through the tool-calling diagram (interview notes)

Five slides, in order: the route, the menu, the loop, the budget, the brakes. Every number below is in the code; where it comes from is in brackets.

## The 60-second version
"My assistant, Sonic, has 45 tools, but a message only reaches them if it needs them. A cheap reading step and plain rules route each message: chat, memory, documents, a change that needs approval, or the tool loop. Inside the loop, the model never sees all 45 tools: the menu is built each turn from who you are, what's connected and what you asked. The loop runs on a step budget, and the design idea is that reading spends steps while *acting earns them back*, so it can edit, test and fix without running away. And anything that changes something is a proposal you click, checked twice."

## Reading the two graph slides
- **Slide 1 is the real LangGraph.** `START → coordinator_node`, then a queue of agents (`_AGENT_NODE_MAP`: conversational, memory_save/recall, retrieve→grade→rewrite, pr_summary, propose_write/execute_write, image_card/image_gen, tool_agent) and every path ends at `formatter_node → generate_node` (`create_workflow`). Not drawn: summarizer and progress-note nodes.
- **Order the coordinator checks** (`build_agent_plan`): an approval reply goes to execute_write first; commit/merge wording goes to the tool loop; PR/issue/event/email requests go to propose_write; picture, progress-note and "about you" questions are caught by code; then the reasoner's flags queue memory, documents, summary, tool loop, PR summary; nothing matched means chat.
- **Slide 2 is inside one node.** `propose_local_edits`, `propose_commit` and `propose_merge` are *tools* the tool loop can call, not graph nodes. `propose_write_node` is a different thing: a graph node that drafts a PR, issue, event or email. If asked "why two ways to open a PR?", the node is the older direct path for a PR between branches that already differ; commit-and-push or a new branch goes through the tool loop.
- The group counts on slide 2 add up to 45; only some tool names are shown per group.

## Numbers worth remembering
- **45** tools in the native registry (`native_tools.REGISTRY`); 7 groups: 9 read code, 12 accounts, 4 library and memory, 3 web and compute, 6 your machine, 7 propose changes, 4 operate the app.
- **Budget:** 7 tool steps a turn (14 in Deep mode), hard ceiling 12 in the native engine, at least 6 for a change request (`TOOL_AGENT_MAX_ITERATIONS`, `_DEEP`, `native_loop.MAX_STEPS`, `TOOL_AGENT_LOCAL_EDIT_MIN_STEPS`).
- **Checkpoints** at one third and two thirds of a budget of 9 or more steps; "2 steps left" and "last step" notices (`native_loop.checkpoint_after`, `steps_left_notice`).
- **Combo:** a landed action (an applied edit, a check that ran) hands back **4** steps; the same result again 2, then 1, then 0; a new failing check is the "counter-hit"; the chain drops the moment a step doesn't land; at most **30** steps handed back per turn (`backend/utils/combo.py`). The idea came from "a combo in a fighting game".
- **Second reader:** after an applied edit a cheap model reviews it; a blocking finding hands steps back (4, then 2), two rounds at most (`diff_review.py`).
- **Low-power mode:** past the spend limit a turn gets 4 steps (`budget_guard.THROTTLED_TOOL_STEPS`).
- **Older results shrink** after 2 steps; a repeated identical call is refused after 3.

## Likely questions, honest answers
- **Why not give the model all the tools?** Cost and accuracy: every description is paid for on every step, and a smaller menu is a smaller blast radius. Gates are plain code, so they can be tested.
- **How do you stop an agent looping or burning money?** The step budget, the combo that only rewards real actions, checkpoints that make "continue" justify itself, spend caps, and a low-power mode. I found one trap myself: in low power an edit request got only 4 steps and the model wrote code in its reply instead of editing; I now know never to judge it on an over-budget run.
- **How do you handle prompt injection?** Results are labelled as data; after a turn reads outside text (web, mail, logs, a page it looked at), commits are refused for that turn; and commands are checked on the server and again on the user's own machine by the same rules file.
- **How do you know the model picks the right tool?** I measure it. Example from this week: for git requests the new "steps" tool was chosen 0 of 18 times with the original wording (the model always reached for the one-click commit tool, or looked and answered in words). After rewriting the descriptions it ended on the right tool 24 of 27 times on the default model, and a "merge the pull request" request went straight to the merge card. It was a small run (nothing executed, canned results), so I'd call it a signal, not a benchmark. One of my own mistakes showed up here: the model reads a separate description table for some tools, so my first edits changed the wrong place.
- **What would you improve next?** A bigger, repeatable tool-choice eval across models; counting a visual check as a "check" for the combo and the "you claimed it passed" guard (today only test runs count); and an allow list for pages that need an outside script to render.
- **What went wrong?** (Good stories.) A hidden browser I built blocked every outside request, including `127.0.0.1` itself, so the first real use failed; my tests used `localhost` and a LAN address but not the IP literal. I reproduced it, fixed it, added tests for every loopback spelling, and made the error message stop blaming the user's setup. The model had also invented an explanation instead of reporting the failure, which is a weakness I track.

## What not to claim
- The "45 tools" count is the native registry; map tools and a couple of internal ones (`continue_working`, `save_draft`) are extra.
- The per-turn tool menu size varies; I haven't measured an average, so don't quote one.
- The step budget numbers are defaults that can be changed by environment settings.
