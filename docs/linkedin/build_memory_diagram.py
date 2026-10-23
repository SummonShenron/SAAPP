"""Builds docs/linkedin/memory-diagram.html (shareable) and prints nothing else. Render with Edge headless; see the commands in the project notes.

Two looks from one source: the plain one, and `?study` with the file each step lives in (a study aid, not for sharing)."""
import html
import pathlib

OUT = pathlib.Path(__file__).with_name("memory-diagram.html")


def box(title, sub, tag=None, kind="", file=None, cls=""):
    tag_html = f'<span class="tag {tag.lower()}">{tag}</span>' if tag else ""
    file_html = f'<em class="file">{html.escape(file)}</em>' if file else ""
    return f'<div class="box {kind} {cls}">{tag_html}<b>{html.escape(title)}</b><i>{html.escape(sub)}</i>{file_html}</div>'


def down(color="var(--c)"):
    return f'<div class="dn" style="color:{color}">&darr;</div>'


# ---- the write path -------------------------------------------------------------------------------------------------
write = [
    box("Message arrives", "saved to the conversation first", None, "gray", "chat_sessions, save_conversation_turn"),
    down("var(--gray)"),
    box("Router decides", "does this message state something worth keeping?", "MODEL", "gray", "reasoner_node"),
    down("var(--vio)"),
    box("Extractor", "category, part of life, one clear statement", "MODEL", "vio", "memory_save_node"),
    down("var(--vio)"),
    box("Honest writes", "drops any time, number, weekday, name or day you never said", "CODE", "vio hot", "memory_grounding.py"),
    down("var(--vio)"),
    box("Dates resolved", "“tonight” becomes a real date, in your timezone", "CODE", "vio", "date_resolve.py"),
    down("var(--vio)"),
    box("Duplicate, update or new?", "similar facts are shortlisted, a small model judges", "MODEL", "vio", "save_user_fact"),
    down("var(--vio)"),
    box("Link and file it", "related facts bound together, then saved", "MODEL", "vio", "fact_links.py"),
]
write_notes = [
    box("After every reply", "a background job, off the response path", None, "teal dash", "index_conversation_turn"),
    down("var(--teal)"),
    box("One-sentence summary", "of what just happened, then the same honesty check", "MODEL", "teal", "summarize_turn"),
]

# ---- stores ---------------------------------------------------------------------------------------------------------------------
stores = [
    '''<div class="store gray"><h3>Ephemeral</h3><p class="tagline">this conversation</p>
       <div class="chips"><span>last 10 messages go to the graph</span><span>graph checkpoints per conversation</span><span>expires, pruned every turn</span></div>
       <em class="file">chat_sessions, MongoDBSaver</em></div>''',
    '''<div class="store vio"><h3>Durable facts</h3><p class="tagline">what is true now</p>
       <div class="chips"><span>category</span><span>part of life: job, own, personal, general</span><span>source: stated, inferred, pattern, edited</span><span>confidence</span><span>linked facts</span><span>packed embedding</span></div>
       <em class="file">user_memory_facts + user_memory_fact_embeddings</em></div>''',
    '''<div class="store teal"><h3>Semantic notes</h3><p class="tagline">what happened</p>
       <div class="chips"><span>one grounded sentence per turn</span><span>embedded in a vector index</span><span>filtered to one person</span><span>merged over time</span></div>
       <em class="file">user_memory_chunks (Atlas vector index)</em></div>''',
]

# ---- the read path ---------------------------------------------------------------------------------------------------------------
read = [
    box("Recent messages", "the last few, trimmed to fit", None, "gray", "format_history"),
    box("Facts block", "who you are, always; then what is most relevant and fresh", "CODE", "vio", "fetch_relevant_user_facts"),
    box("Note recall", "closest notes for this person, fresh ones only, high bar", "CODE", "teal", "retrieve_relevant_memory_items"),
    box("Second look", "almost-matches get one strict model check", "MODEL", "teal", "judge_near_misses"),
    box("Agent searches memory", "a tool the tool agent calls when it needs to", "MODEL", "teal hot", "search_for_agent"),
    box("Guard rules", "remembered text can never trigger a query or a fetch", "CODE", "gray", "MemoryTurnGuard"),
]

# ---- upkeep ----------------------------------------------------------------------------------------------------------------------------------------
upkeep = [
    box("Decay", "old facts fade when read; nothing is deleted; identity never fades", "CODE", "vio", "effective_confidence"),
    box("Reinforcement", "seeing a fact again raises trust in it", "CODE", "vio", "save_user_fact"),
    box("Pruning", "at capacity the weakest go first, into an archive, never lost", "CODE", "vio", "_prune_excess_facts"),
    box("Compaction", "similar notes merge into a summary; originals archived first", "MODEL", "teal", "memory_compaction.py"),
    box("Self-replacing summaries", "summaries re-merge and replace themselves, so storage stays bounded", "MODEL", "teal", "compact_meta_memory"),
    box("Facts from notes", "lasting facts found in summaries are saved, marked inferred", "MODEL", "vio", "_run_compaction"),
    box("Patterns", "themes that several facts agree on, never a single one", "MODEL", "vio", "extract_user_patterns"),
    box("Staleness", "a corrected fact hides the old notes that said otherwise", "CODE", "teal", "flag_stale_chunks"),
    box("Dated things", "an event gets a date only if your words gave one", "CODE", "amb", "open_loops.py"),
    box("You are in control", "see, edit and delete anything; your wording outranks a model's", "CODE", "amb", "Memory page"),
    box("Forget everything", "facts, notes and archives removed together", "CODE", "amb", "memory_erase.py"),
    box("Logs hold counts", "never what you said", "CODE", "amb", "log_hygiene"),
]

PAGE = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>How Sonic remembers</title>
<style>
  :root {{ --bg:#131416; --panel:#17181b; --card:#1f2024; --line:#383940; --text:#efeff1; --muted:#a0a0a8; --gray:#9aa0aa; --vio:#a597f5; --teal:#43c79a; --amb:#e6c25c; --or:#e0955f; }}
  * {{ box-sizing: border-box; margin: 0; }}
  html, body {{ width: 1920px; height: 1200px; background: var(--bg); color: var(--text); font-family: "Segoe UI", "Inter", Arial, sans-serif; overflow: hidden; }}
  .page {{ height: 1200px; padding: 22px 36px 12px; display: flex; flex-direction: column; gap: 10px; }}
  header {{ display: flex; justify-content: space-between; align-items: flex-end; }}
  header h1 {{ font-size: 44px; font-weight: 700; letter-spacing: -0.8px; }}
  header p {{ font-size: 22px; color: var(--muted); margin-top: 3px; }}
  .legend {{ display: flex; gap: 20px; font-size: 18px; white-space: nowrap; color: var(--muted); align-items: center; }}
  .legend span.sw {{ display: inline-block; width: 16px; height: 16px; border-radius: 4px; margin-right: 8px; vertical-align: -2px; }}
  .main {{ display: grid; grid-template-columns: 560px 78px 1fr 78px 560px; flex: none; height: 730px; }}
  .zone {{ border: 2px solid var(--line); border-radius: 20px; background: var(--panel); padding: 10px 14px 12px; display: flex; flex-direction: column; gap: 4px; }}
  .zone > h2 {{ font-size: 27px; font-weight: 700; display: flex; justify-content: space-between; align-items: baseline; }}
  .zone > h2 small {{ font-size: 19px; font-weight: 400; color: var(--muted); }}
  .box {{ position: relative; background: var(--card); border: 1.5px solid var(--line); border-radius: 12px; padding: 5px 12px 6px; }}
  .box b {{ display: block; font-size: 21px; font-weight: 600; line-height: 1.12; padding-right: 64px; }}
  .box i {{ display: block; font-style: normal; font-size: 16.5px; color: var(--muted); margin-top: 2px; line-height: 1.2; }}
  .tag {{ position: absolute; top: 8px; right: 9px; font-size: 13px; font-weight: 700; letter-spacing: 0.6px; padding: 2px 8px; border-radius: 6px; }}
  .tag.model {{ background: var(--vio); color: #17132e; }}
  .tag.code {{ border: 1.5px solid var(--muted); color: var(--muted); }}
  .gray {{ --c: var(--gray); }} .vio {{ --c: var(--vio); }} .teal {{ --c: var(--teal); }} .amb {{ --c: var(--amb); }}
  .box.gray, .box.vio, .box.teal, .box.amb {{ border-left: 6px solid var(--c); }}
  .box.hot {{ background: #24252b; box-shadow: 0 0 0 1.5px var(--c) inset; }}
  .box.dash {{ border-style: dashed; border-left-style: solid; }}
  .dn {{ text-align: center; font-size: 20px; line-height: 0.5; height: 9px; }}
  .gut {{ display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 14px; font-size: 40px; color: var(--muted); }}
  .store {{ border: 2px solid var(--c); border-radius: 20px; background: var(--panel); padding: 9px 18px 10px; flex: 1; display: flex; flex-direction: column; }}
  .store h3 {{ font-size: 28px; font-weight: 700; color: var(--c); }}
  .store .tagline {{ font-size: 20px; color: var(--muted); margin-bottom: 6px; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 7px; }}
  .chips span {{ font-size: 17px; padding: 2px 10px; border: 1.5px solid var(--c); border-radius: 999px; color: var(--text); }}
  .mid {{ display: flex; flex-direction: column; gap: 8px; min-height: 0; }}
  .links {{ display: flex; gap: 10px; }}
  .links .link {{ flex: 1; }}
  .link {{ font-size: 18px; color: var(--muted); text-align: center; border: 1.5px dashed var(--line); border-radius: 10px; padding: 5px 8px; }}
  .readcol {{ display: flex; flex-direction: column; gap: 7px; }}
  .readcol .box {{ flex: 1; display: flex; flex-direction: column; justify-content: center; }}
  .out {{ background: #2a2517; border: 2px solid var(--amb); border-radius: 12px; padding: 8px 12px; }}
  .out b {{ font-size: 23px; color: var(--amb); display: block; }}
  .out i {{ font-style: normal; font-size: 17.5px; color: var(--muted); display: block; }}
  .band {{ border: 2px solid var(--amb); border-radius: 20px; background: var(--panel); padding: 10px 16px 14px; flex: 1; display: flex; flex-direction: column; gap: 8px; min-height: 0; }}
  .band h2 {{ font-size: 27px; font-weight: 700; color: var(--amb); display: flex; justify-content: space-between; align-items: baseline; }}
  .band h2 small {{ font-size: 19px; font-weight: 400; color: var(--muted); }}
  .grid {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; flex: 1; }}
  .grid .box {{ display: flex; flex-direction: column; justify-content: center; }}
  .grid .box b {{ font-size: 20px; padding-right: 62px; }}
  .grid .box i {{ font-size: 16px; }}
  footer {{ display: flex; justify-content: space-between; font-size: 20px; color: var(--muted); }}
  footer b {{ color: var(--text); font-weight: 600; }}
  .file {{ display: none; font-style: normal; font-family: "Cascadia Mono", Consolas, monospace; font-size: 14px; color: var(--c, var(--muted)); margin-top: 4px; opacity: .95; }}
  body.study .file {{ display: block; }}
  html.study, html.study body {{ height: 1440px; }}
  html.study .page {{ height: 1440px; }}
  html.study .main {{ height: 880px; }}
  .store .file {{ margin-top: auto; padding-top: 6px; }}
</style></head>
<body><div class="page">
<header>
  <div><h1>How Sonic remembers</h1>
  <p>Three layers, one prompt: what is saved, checked, recalled and kept healthy.</p></div>
  <div class="legend">
    <span><span class="sw" style="background:var(--gray)"></span>Ephemeral</span>
    <span><span class="sw" style="background:var(--vio)"></span>Durable facts</span>
    <span><span class="sw" style="background:var(--teal)"></span>Semantic notes</span>
    <span><span class="tag model" style="position:static;margin-right:6px">MODEL</span>a small model decides</span>
    <span><span class="tag code" style="position:static;margin-right:6px">CODE</span>plain code decides</span>
  </div>
</header>

<div class="main">
  <div class="zone">
    <h2>Writing <small>what gets remembered</small></h2>
    {''.join(write)}
    <div style="height:4px"></div>
    {''.join(write_notes)}
  </div>

  <div class="gut"><div style="color:var(--vio)">&rarr;</div><div style="color:var(--teal)">&rarr;</div></div>

  <div class="mid">
    {stores[0]}
    {stores[1]}
    <div class="links"><div class="link">a corrected fact hides the notes that said otherwise &uarr;</div><div class="link">compaction promotes lasting facts &darr;</div></div>
    {stores[2]}
  </div>

  <div class="gut"><div style="color:var(--vio)">&rarr;</div><div style="color:var(--teal)">&rarr;</div></div>

  <div class="zone">
    <h2>Reading <small>what comes back</small></h2>
    <div class="readcol">{''.join(read)}</div>
    <div class="out"><b>Reply prompt</b><i>persona + facts + recalled notes + recent messages</i></div>
    <div class="out" style="border-color:var(--gray);background:#1d1e22"><b style="color:var(--text)">Reply streams, then the turn is remembered</b><i>the summary job on the left runs in the background</i></div>
  </div>
</div>

<div class="band">
  <h2>Upkeep <small>how it stays accurate, bounded and yours</small></h2>
  <div class="grid">{''.join(upkeep)}</div>
</div>

<footer><span><b>Sonic Assistant</b></span><span>Facts are what is true now. Notes are what happened. Nothing is saved that you did not say.</span></footer>
</div>
<script>if (location.search.indexOf('study') > -1) {{ document.body.classList.add('study'); document.documentElement.classList.add('study'); }}</script>
</body></html>
"""

OUT.write_text(PAGE, encoding="utf-8")
print("wrote", OUT)
