"""Builds docs/linkedin/emotion-diagram.html: how Sonic reads the person's emotional state, holds its own energy at or below theirs (the energy ceiling), and checks what it says.

Everything shown is in backend/utils/emotion_utils.py, emotion_checks.py, identity_checks.py and the safety layer; the chart is an illustration of one imagined conversation, not data."""
import html
import pathlib

OUT = pathlib.Path(__file__).with_name("emotion-diagram.html")

COR, AMB, VIO, RED, GRY, TEA = "#f08a6b", "#e6c25c", "#a597f5", "#ef6b6b", "#9aa0aa", "#43c79a"

# name: (x, y, w, h, color, title, sub, tag)
ROW = [
    ("msg", 50, 128, 260, 120, GRY, "Your message", "what you just said, in your own words", None),
    ("read", 362, 128, 260, 120, COR, "Reads the room", "feeling, strength, what you want from the reply, what it is about", "MODEL"),
    ("carry", 674, 128, 260, 120, COR, "Carries it forward", "fades with time and turns; survives a change of topic", "CODE"),
    ("ceiling", 986, 128, 260, 120, AMB, "Sets the ceiling", "Sonic's own energy is capped at what you have shown", "CODE"),
    ("write", 1298, 128, 260, 120, GRY, "Writes the reply", "pace, register and length follow the ceiling and your need", "MODEL"),
    ("check", 1610, 128, 260, 120, VIO, "Checks the draft", "advice for someone venting? no real question about good news? one rewrite", "CODE"),
]
TIERS = [
    (1160, 290, 710, 134, AMB, "Subdued", "No lift shown, or you are frustrated. Sonic writes at your level: warm, plain prose, no exclamation marks or emoji. After a change of topic, one short line shows it has not forgotten."),
    (1160, 432, 710, 112, AMB, "Easing", "You have shown a small lift, a flicker of humor or relief. Sonic may match it, a touch of warmth, and no further."),
    (1160, 552, 710, 112, AMB, "Open", "No feeling is being carried, or you clearly recovered. The ceiling lifts and Sonic is back to its usual self. It never lifts the ceiling for you."),
]
BOTTOM = [
    (50, 800, 580, 178, RED, "Risk read", "Branches off the same reading", "Signs of someone in danger go to a separate safety layer. It starts with the people in your life, then professional help; it reaches for a hotline only when the situation is imminent. All in code, not left to the model.", "CODE"),
    (670, 800, 580, 178, VIO, "Feelings check", "On what Sonic says about itself", "No “I missed you”, “glad you're back”, “I'm so excited” or “I care about you”. A draft that does is rewritten once, saying what Sonic will do instead.", "CODE"),
    (1290, 800, 580, 178, GRY, "What stays allowed", "Honest, not cold", "Caring about getting the work right, an apology for a real mistake, “happy to help”, and the kind things people say that carry the sentiment, like “I'm here for you”.", None),
]


def box(name, x, y, w, h, color, title, sub, tag):
    tag_html = f'<span class="tag {tag.lower()}">{tag}</span>' if tag else ""
    return (f'<div class="box" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;--c:{color}">{tag_html}'
            f'<b>{html.escape(title)}</b><i>{html.escape(sub)}</i></div>')


def wide(x, y, w, h, color, title, kicker, body, tag):
    tag_html = f'<span class="tag {tag.lower()}">{tag}</span>' if tag else ""
    return (f'<div class="box wide" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;--c:{color}">{tag_html}'
            f'<b>{html.escape(title)}</b><em>{html.escape(kicker)}</em><i>{html.escape(body)}</i></div>')


def tier(x, y, w, h, color, title, body):
    return (f'<div class="tier" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;--c:{color}"><h3>{html.escape(title)}</h3>'
            f'<p>{html.escape(body)}</p></div>')


def arrow(d, color, dashed=False):
    dash = ' stroke-dasharray="9 7"' if dashed else ""
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"{dash} marker-end="url(#a-{color[1:]})"/>'


def marker(color):
    return (f'<marker id="a-{color[1:]}" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="5.5" markerHeight="5.5" orient="auto">'
            f'<path d="M1.5 1.5 L8.5 5 L1.5 8.5" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></marker>')


ARROWS = [
    arrow("M310 188 H360", GRY), arrow("M622 188 H672", COR), arrow("M934 188 H984", COR), arrow("M1246 188 H1296", AMB), arrow("M1558 188 H1608", GRY),
    arrow("M1116 250 V288", AMB),
]

# ---- the chart: one imagined conversation -----------------------------------------------------------------------------------------------
X0, DX = 70, 70
Y0, SPAN = 335, 285


def px(t):
    return X0 + (t - 1) * DX


def py(e):
    return Y0 - e * SPAN


USER = [0.60, 0.62, 0.58, 0.12, 0.10, 0.14, 0.10, 0.12, 0.30, 0.30, 0.38, 0.72, 0.80]
SONIC = [0.70, 0.72, 0.68, 0.17, 0.16, 0.19, 0.16, 0.17, 0.36, 0.36, 0.40, 0.84, 0.90]
FEELING = {4: 0.80, 5: 0.77, 6: 0.73, 7: 0.69, 8: 0.65, 9: 0.62, 10: 0.58, 11: 0.54, 12: 0.0}


def poly(values, step=False):
    pts = []
    for i, v in enumerate(values, start=1):
        if step and pts:
            pts.append(f"{px(i):.0f},{pts[-1].split(',')[1]}")
        pts.append(f"{px(i):.0f},{py(v):.0f}")
    return " ".join(pts)


FEEL_PTS = " ".join(f"{px(t):.0f},{py(v):.0f}" for t, v in FEELING.items())
LEFT, RIGHT = px(1) - 30, px(13) + 30

CHART = f"""<svg viewBox="0 0 1000 410" width="1080" height="440" style="display:block">
  <rect x="{LEFT:.0f}" y="40" width="{px(3.5)-LEFT:.0f}" height="295" fill="{TEA}" opacity="0.08"/>
  <rect x="{px(3.5):.0f}" y="40" width="{px(8.5)-px(3.5):.0f}" height="295" fill="{AMB}" opacity="0.07"/>
  <rect x="{px(8.5):.0f}" y="40" width="{px(11.5)-px(8.5):.0f}" height="295" fill="{AMB}" opacity="0.13"/>
  <rect x="{px(11.5):.0f}" y="40" width="{RIGHT-px(11.5):.0f}" height="295" fill="{TEA}" opacity="0.08"/>
  <text x="{LEFT+10:.0f}" y="62" class="band" fill="{TEA}">NORMAL</text>
  <text x="{px(3.5)+10:.0f}" y="62" class="band" fill="{AMB}">SUBDUED</text>
  <text x="{px(8.5)+10:.0f}" y="62" class="band" fill="{AMB}">EASING</text>
  <text x="{px(11.5)+10:.0f}" y="62" class="band" fill="{TEA}">OPEN</text>
  <line x1="{LEFT:.0f}" y1="{Y0}" x2="{RIGHT:.0f}" y2="{Y0}" stroke="#4a4b53" stroke-width="2"/>
  <line x1="{LEFT:.0f}" y1="40" x2="{LEFT:.0f}" y2="{Y0}" stroke="#4a4b53" stroke-width="2"/>
  <text x="{LEFT+4:.0f}" y="34" class="axis">energy</text>
  <text x="{RIGHT-62:.0f}" y="{Y0+24}" class="axis">time &rarr;</text>
  <polyline points="{FEEL_PTS}" fill="none" stroke="{COR}" stroke-width="3.5" stroke-dasharray="3 9" stroke-linecap="round"/>
  <polyline points="{poly(USER)}" fill="none" stroke="#e8e8ea" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>
  <polyline points="{poly(SONIC, step=True)}" fill="none" stroke="{AMB}" stroke-width="5.5" stroke-linejoin="round" stroke-linecap="round"/>
  <path d="M{px(6.2):.0f} {py(0.17):.0f} q8 -40 24 -30 q8 8 6 30" fill="none" stroke="{AMB}" stroke-width="2.5" stroke-dasharray="4 5"/>
  <text x="{px(5.2):.0f}" y="{py(0.17)-46:.0f}" class="note" fill="{AMB}">rare: a small joke it is sure will land</text>
  <text x="{px(1)-6:.0f}" y="{py(0.70)-16:.0f}" class="key" fill="{AMB}">Sonic</text>
  <text x="{px(1)-6:.0f}" y="{py(0.60)+30:.0f}" class="key" fill="#e8e8ea">what you show</text>
  <text x="{px(4)+14:.0f}" y="{py(0.80)-12:.0f}" class="key" fill="{COR}">the feeling being carried</text>
  <text x="{px(4)+14:.0f}" y="{py(0.50):.0f}" class="note" fill="{AMB}">Sonic drops with you,</text>
  <text x="{px(4)+14:.0f}" y="{py(0.50)+20:.0f}" class="note" fill="{AMB}">in the same reply</text>
  <text x="{px(8)-6:.0f}" y="{py(0.79):.0f}" class="note" fill="{AMB}">it follows you up, never ahead of you</text>
  <g class="ev">
    <text x="{px(1)-26:.0f}" y="{Y0+52}">an ordinary chat</text>
    <text x="{px(3.7)-20:.0f}" y="{Y0+52}">you open up about</text><text x="{px(3.7)-20:.0f}" y="{Y0+70}">something painful</text>
    <text x="{px(6.2)-30:.0f}" y="{Y0+52}">you move on to</text><text x="{px(6.2)-30:.0f}" y="{Y0+70}">another question</text>
    <text x="{px(9)-40:.0f}" y="{Y0+52}">a first flicker</text><text x="{px(9)-40:.0f}" y="{Y0+70}">of humor</text>
    <text x="{RIGHT:.0f}" y="{Y0+52}" text-anchor="end">you clearly recover:</text><text x="{RIGHT:.0f}" y="{Y0+70}" text-anchor="end">the cap lifts</text>
  </g>
  <g fill="#e8e8ea"><circle cx="{px(1):.0f}" cy="{Y0}" r="5"/><circle cx="{px(4):.0f}" cy="{Y0}" r="5"/><circle cx="{px(6.2):.0f}" cy="{Y0}" r="5"/><circle cx="{px(9):.0f}" cy="{Y0}" r="5"/><circle cx="{px(12):.0f}" cy="{Y0}" r="5"/></g>
</svg>"""

PAGE = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>How Sonic reads the room</title>
<style>
  :root {{ --bg:#131416; --card:#1f2024; --line:#383940; --text:#efeff1; --muted:#a0a0a8; --vio:#a597f5; }}
  * {{ box-sizing: border-box; margin: 0; }}
  html, body {{ width: 1920px; height: 1080px; background: var(--bg); color: var(--text); font-family: "Segoe UI", "Inter", Arial, sans-serif; overflow: hidden; }}
  .canvas {{ position: relative; width: 1920px; height: 1080px; }}
  h1 {{ position: absolute; left: 50px; top: 18px; font-size: 40px; font-weight: 700; letter-spacing: -0.6px; }}
  .sub {{ position: absolute; left: 50px; top: 66px; font-size: 21px; color: var(--muted); }}
  .legend {{ position: absolute; right: 50px; top: 30px; display: flex; gap: 18px; font-size: 17px; color: var(--muted); white-space: nowrap; align-items: center; }}
  svg.layer {{ position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; pointer-events: none; }}
  .box {{ position: absolute; border-radius: 14px; background: var(--card); border: 1.5px solid var(--line); border-left: 6px solid var(--c); padding: 10px 14px; display: flex; flex-direction: column; justify-content: center; }}
  .box b {{ font-size: 23px; font-weight: 600; line-height: 1.12; }}
  .box i {{ font-style: normal; font-size: 17.5px; color: var(--muted); margin-top: 5px; line-height: 1.25; }}
  .box em {{ font-style: normal; font-size: 17px; color: var(--c); margin-top: 2px; }}
  .box.wide {{ padding: 14px 20px; }}
  .box.wide b {{ font-size: 27px; color: var(--c); }}
  .box.wide i {{ font-size: 19.5px; margin-top: 8px; line-height: 1.32; color: #c9c9cf; }}
  .tag {{ position: absolute; top: -11px; right: 10px; font-size: 13px; font-weight: 700; letter-spacing: 0.6px; padding: 2px 8px; border-radius: 6px; }}
  .tag.model {{ background: var(--vio); color: #17132e; }}
  .tag.code {{ background: var(--bg); border: 1.5px solid var(--muted); color: var(--muted); }}
  .panel {{ position: absolute; left: 50px; top: 290px; width: 1080px; height: 474px; border: 2px solid var(--line); border-radius: 18px; background: #17181b; padding: 8px 0 0 0; }}
  .panel h2 {{ position: absolute; left: 22px; top: -17px; background: var(--bg); padding: 0 12px; font-size: 22px; color: #efeff1; }}
  .panel h2 small {{ font-size: 18px; color: var(--muted); font-weight: 400; margin-left: 8px; }}
  svg text {{ font-family: "Segoe UI", "Inter", Arial, sans-serif; }}
  .band {{ font-size: 17px; font-weight: 700; letter-spacing: 1.2px; }}
  .axis {{ font-size: 16px; fill: #8e8e95; }}
  .key {{ font-size: 19px; font-weight: 600; }}
  .note {{ font-size: 16px; }}
  .ev text {{ font-size: 16.5px; fill: #c9c9cf; }}
  .tier {{ position: absolute; border: 2px solid var(--c); border-radius: 14px; background: #1c1b17; padding: 12px 18px; }}
  .tier h3 {{ font-size: 27px; font-weight: 700; color: var(--c); }}
  .tier p {{ font-size: 18px; line-height: 1.3; color: #d0d0d6; margin-top: 4px; }}
  .gate {{ position: absolute; left: 1160px; top: 674px; width: 710px; height: 90px; border: 1.5px dashed var(--line); border-radius: 12px; padding: 10px 16px; font-size: 18px; color: #c9c9cf; line-height: 1.28; }}
  .gate b {{ color: var(--text); }}
  .foot {{ position: absolute; left: 50px; right: 50px; bottom: 16px; display: flex; justify-content: space-between; font-size: 20px; color: var(--muted); }}
  .foot b {{ color: var(--text); font-weight: 600; }}
</style></head>
<body><div class="canvas">
<h1>How Sonic handles emotional distress</h1>
<div class="sub">It matches your energy on the way down, stays steady while you are low, and follows you back up, never ahead of you.</div>
<div class="legend"><span><span class="tag model" style="position:static;margin-right:6px">MODEL</span>a small model decides</span><span><span class="tag code" style="position:static;margin-right:6px">CODE</span>plain code decides</span></div>

<svg class="layer" viewBox="0 0 1920 1080"><defs>{marker(GRY)}{marker(COR)}{marker(AMB)}</defs>{''.join(ARROWS)}</svg>

{''.join(box(*spec) for spec in ROW)}

<div class="panel"><h2>Sonic's energy follows yours<small>one imagined conversation</small></h2>{CHART}</div>

{''.join(tier(*spec) for spec in TIERS)}
<div class="gate"><b>The tier gates more than tone.</b> Follow-ups, offers and callbacks stay quiet unless it is open, concern for you is allowed only while a feeling is on the table, and the mascot's mood follows it.</div>

{''.join(wide(*spec) for spec in BOTTOM)}

<div class="foot"><span><b>Sonic Assistant</b></span><span>Warm without claiming feelings. Steady without being cold.</span></div>
</div></body></html>
"""

OUT.write_text(PAGE, encoding="utf-8")
print("wrote", OUT)
