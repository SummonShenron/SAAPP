"""Builds docs/linkedin/memory-diagram-15.html: the same memory system as memory-diagram.html, as 15 boxes with the flows drawn between them.

Boxes are placed by hand (x, y, w, h in a 1920 x 1080 canvas) and the arrows are one SVG layer over them, so an arrow always starts and ends on a box edge."""
import html
import pathlib

OUT = pathlib.Path(__file__).with_name("memory-diagram-15.html")

# name: (x, y, w, h, kind, title, sub, tag)
BOXES = {
    "message":   (50, 490, 176, 116, "gray", "Message arrives", "saved to the conversation first", None),
    "router":    (260, 490, 176, 116, "gray", "Router", "is this worth keeping?", "MODEL"),
    "extractor": (470, 490, 176, 116, "vio", "Extractor", "one clear statement, with its category", "MODEL"),
    "honest":    (680, 490, 176, 116, "vio hot", "Honest writes", "drops what you never said", "CODE"),
    "dedupe":    (890, 480, 176, 136, "vio", "New, update or same?", "a small model judges it against what is stored", "MODEL"),
    "summary":   (890, 137, 176, 116, "teal", "Turn summary", "one sentence, same honesty check", "MODEL"),
    "recall":    (1454, 137, 180, 116, "teal", "Note recall", "closest notes, a high bar, then a second look", "CODE"),
    "factsblock": (1454, 490, 180, 116, "vio", "Facts block", "who you are, then what is most relevant", "CODE"),
    "agent":     (1454, 316, 180, 104, "teal hot", "Agent searches memory", "asked for, mid-task", "MODEL"),
    "compaction": (1100, 310, 180, 104, "amb", "Compaction", "merges notes, promotes lasting facts", "MODEL"),
    "decay":     (1100, 690, 320, 84, "amb", "Decay and pruning", "old facts fade, the weakest are archived, never lost", "CODE"),
}

STORES = {
    "notes": (1100, 122, 320, 150, "teal", "Semantic notes", "what happened", ["one sentence per turn", "searched by meaning"]),
    "facts": (1100, 440, 320, 210, "vio", "Durable facts", "what is true now", ["category", "part of life", "stated or inferred", "confidence", "linked facts"]),
    "convo": (50, 790, 330, 120, "gray", "Conversation", "the last few messages, gone when it ends", []),
}


def box(name, x, y, w, h, kind, title, sub, tag):
    tag_html = f'<span class="tag {tag.lower()}">{tag}</span>' if tag else ""
    return (f'<div class="box {kind}" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px">{tag_html}'
            f'<b>{html.escape(title)}</b><i>{html.escape(sub)}</i></div>')


def store(x, y, w, h, kind, title, sub, chips):
    chip_html = "".join(f"<span>{html.escape(c)}</span>" for c in chips)
    return (f'<div class="store {kind}" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px"><h3>{html.escape(title)}</h3>'
            f'<p>{html.escape(sub)}</p><div class="chips">{chip_html}</div></div>')


def arrow(d, color, dashed=False):
    dash = ' stroke-dasharray="9 7"' if dashed else ""
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"{dash} marker-end="url(#a-{color[1:]})"/>'


G, V, T, A = "#9aa0aa", "#a597f5", "#43c79a", "#e6c25c"
ARROWS = [
    # the write path, left to right
    arrow("M226 548 H258", G), arrow("M436 548 H468", V), arrow("M646 548 H678", V), arrow("M856 548 H888", V), arrow("M1066 548 H1098", V),
    # the message is also kept in the conversation
    arrow("M138 606 V788", G),
    # the notes written after a reply
    arrow("M1066 195 H1098", T),
    # reading
    arrow("M1420 548 H1452", V), arrow("M1634 548 H1666", V),
    arrow("M1420 195 H1452", T), arrow("M1634 195 H1666", T),
    # the agent asks both stores
    arrow("M1420 255 H1437 V345 H1452", T), arrow("M1420 470 H1437 V392 H1452", V), arrow("M1634 368 H1666", T),
    # the conversation goes straight to the prompt
    arrow("M380 850 H1666", G),
    # upkeep
    arrow("M1175 274 V308", A), arrow("M1175 416 V438", A), arrow("M1260 688 V652", A),
    # a corrected fact hides old notes
    arrow("M1305 438 V274", V, dashed=True),
    # after the reply, the turn is remembered
    arrow("M1769 123 V106 H978 V135", T, dashed=True),
]


def marker(color):
    return (f'<marker id="a-{color[1:]}" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5.5" markerHeight="5.5" orient="auto">'
            f'<path d="M1.5 1.5 L8.5 5 L1.5 8.5" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker>')


PAGE = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>How Sonic remembers (15 boxes)</title>
<style>
  :root {{ --bg:#131416; --card:#1f2024; --line:#383940; --text:#efeff1; --muted:#a0a0a8; --gray:#9aa0aa; --vio:#a597f5; --teal:#43c79a; --amb:#e6c25c; }}
  * {{ box-sizing: border-box; margin: 0; }}
  html, body {{ width: 1920px; height: 1080px; background: var(--bg); color: var(--text); font-family: "Segoe UI", "Inter", Arial, sans-serif; overflow: hidden; }}
  .canvas {{ position: relative; width: 1920px; height: 1080px; }}
  h1 {{ position: absolute; left: 50px; top: 18px; font-size: 40px; font-weight: 700; letter-spacing: -0.6px; }}
  .sub {{ position: absolute; left: 50px; top: 66px; font-size: 21px; color: var(--muted); }}
  .legend {{ position: absolute; right: 50px; top: 28px; display: flex; gap: 18px; font-size: 17px; color: var(--muted); white-space: nowrap; align-items: center; }}
  .legend .sw {{ display: inline-block; width: 15px; height: 15px; border-radius: 4px; margin-right: 7px; vertical-align: -2px; }}
  svg {{ position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; }}
  .box, .store {{ position: absolute; border-radius: 14px; background: var(--card); border: 1.5px solid var(--line); }}
  .box {{ padding: 8px 12px; display: flex; flex-direction: column; justify-content: center; }}
  .box b {{ font-size: 21px; font-weight: 600; line-height: 1.12; padding-right: 0; }}
  .box i {{ font-style: normal; font-size: 16.5px; color: var(--muted); margin-top: 4px; line-height: 1.2; }}
  .tag {{ position: absolute; top: -11px; right: 10px; font-size: 13px; font-weight: 700; letter-spacing: 0.6px; padding: 2px 8px; border-radius: 6px; }}
  .tag.model {{ background: var(--vio); color: #17132e; }}
  .tag.code {{ background: var(--bg); border: 1.5px solid var(--muted); color: var(--muted); }}
  .gray {{ --c: var(--gray); }} .vio {{ --c: var(--vio); }} .teal {{ --c: var(--teal); }} .amb {{ --c: var(--amb); }}
  .box.gray, .box.vio, .box.teal, .box.amb {{ border-left: 6px solid var(--c); }}
  .box.hot {{ background: #25262c; box-shadow: 0 0 0 1.5px var(--c) inset; }}
  .store {{ border: 2.5px solid var(--c); background: #17181b; padding: 12px 18px; }}
  .store h3 {{ font-size: 29px; font-weight: 700; color: var(--c); }}
  .store p {{ font-size: 20px; color: var(--muted); margin: 1px 0 8px; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .chips span {{ font-size: 16.5px; padding: 2px 10px; border: 1.5px solid var(--c); border-radius: 999px; }}
  .prompt {{ position: absolute; left: 1668px; top: 125px; width: 202px; height: 785px; border: 2.5px solid var(--amb); border-radius: 16px; background: #231f12; padding: 16px 16px; }}
  .prompt h3 {{ font-size: 29px; color: var(--amb); }}
  .prompt p {{ font-size: 18px; color: var(--muted); margin-top: 3px; }}
  .prompt ul {{ list-style: none; padding: 0; margin-top: 16px; display: flex; flex-direction: column; gap: 12px; }}
  .prompt li {{ font-size: 21px; border-left: 4px solid var(--c); padding: 4px 0 4px 10px; }}
  .prompt .end {{ position: absolute; left: 16px; right: 16px; bottom: 16px; font-size: 18px; color: var(--muted); border-top: 1.5px dashed var(--line); padding-top: 10px; }}
  .read {{ position: absolute; left: 50px; top: 140px; width: 800px; display: flex; flex-direction: column; gap: 22px; }}
  .read .r {{ display: flex; gap: 18px; align-items: flex-start; }}
  .read .r > span {{ flex: none; width: 46px; height: 46px; border-radius: 50%; background: var(--c); color: #111; font-size: 27px; font-weight: 700; display: flex; align-items: center; justify-content: center; }}
  .read b {{ font-size: 28px; font-weight: 600; display: block; }}
  .read p {{ font-size: 21px; color: var(--muted); margin-top: 4px; line-height: 1.3; }}
  .cap {{ position: absolute; font-size: 15.5px; color: var(--muted); line-height: 1.2; }}
  .foot {{ position: absolute; left: 50px; right: 50px; bottom: 16px; display: flex; justify-content: space-between; font-size: 20px; color: var(--muted); }}
  .foot b {{ color: var(--text); font-weight: 600; }}
</style></head>
<body><div class="canvas">
<h1>How Sonic remembers</h1>
<div class="sub">Three layers, one prompt, and the checks in between.</div>
<div class="legend">
  <span><span class="sw" style="background:var(--gray)"></span>Ephemeral</span>
  <span><span class="sw" style="background:var(--vio)"></span>Durable facts</span>
  <span><span class="sw" style="background:var(--teal)"></span>Semantic notes</span>
  <span><span class="tag model" style="position:static;margin-right:6px">MODEL</span>a small model decides</span>
  <span><span class="tag code" style="position:static;margin-right:6px">CODE</span>plain code decides</span>
</div>

<svg viewBox="0 0 1920 1080"><defs>{marker(G)}{marker(V)}{marker(T)}{marker(A)}</defs>{''.join(ARROWS)}</svg>

{''.join(box(name, *spec) for name, spec in BOXES.items())}
{''.join(store(*spec) for spec in STORES.values())}

<div class="prompt" style="--c:var(--amb)"><h3>Reply prompt</h3><p>everything the model sees</p>
  <ul><li style="--c:var(--gray)">persona and rules</li><li style="--c:var(--vio)">who you are</li><li style="--c:var(--teal)">what it recalled</li><li style="--c:var(--gray)">recent messages</li><li style="--c:var(--amb)">your question</li></ul>
  <div class="end">the reply streams, then the turn is remembered</div></div>

<div class="cap" style="left:1314px;top:340px;width:100px">a correction hides old notes</div>
<div class="read">
  <div class="r"><span style="--c:var(--vio)">1</span><div><b>Only what you said is kept</b><p>Every write passes a check in plain code that drops any time, number, name or day you never gave.</p></div></div>
  <div class="r"><span style="--c:var(--teal)">2</span><div><b>Two stores, two jobs</b><p>Facts hold what is true now and get corrected. Notes hold what happened and get merged over time.</p></div></div>
  <div class="r"><span style="--c:var(--amb)">3</span><div><b>A small slice comes back</b><p>Each reply gets who you are plus only what is relevant. The agent can also search memory itself.</p></div></div>
</div>
<div class="cap" style="left:1180px;top:80px;width:420px;text-align:center;font-size:16px">after the reply, the turn is summarized in the background</div>
<div class="cap" style="left:152px;top:690px;width:200px">kept first, before anything else happens</div>
<div class="cap" style="left:400px;top:822px;width:640px;font-size:17px">The conversation goes straight into the prompt; it is never searched, and it expires.</div>

<div class="foot"><span><b>Sonic Assistant</b></span><span>Facts are what is true now. Notes are what happened. Nothing is saved that you did not say.</span></div>
</div></body></html>
"""

OUT.write_text(PAGE, encoding="utf-8")
print("wrote", OUT)
