"""Renders docs/linkedin/tools-diagram.html to PNGs: one per slide (1080 x 1620, drawn at 2x so text stays sharp on a phone), the three stacked into one tall picture, and a PDF of the slides.

Uses the Edge or Chrome already on the computer through Playwright (no browser download):
    .venv/Scripts/python.exe docs/linkedin/render_tools_diagram.py
"""
import pathlib
import sys

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "runner" / "src"))
from saapp_runner import browser as runner_browser  # noqa: E402  (only to find the installed browser)

found = runner_browser.find_browser()
if not found:
    sys.exit("No Chrome or Edge found on this computer.")

SCALE = 2
slides = []
with sync_playwright() as p:
    chromium = p.chromium.launch(executable_path=found["path"], headless=True)
    page = chromium.new_page(viewport={"width": 1080, "height": 1620}, device_scale_factor=SCALE)
    page.goto((HERE / "tools-diagram.html").as_uri())
    page.wait_for_timeout(400)
    for index in (1, 2, 3, 4, 5):
        element = page.locator(f"#p{index}")
        box = element.bounding_box()
        overflow = page.evaluate(f"(() => {{ const p = document.getElementById('p{index}'); return p.scrollHeight - p.clientHeight; }})()")
        print(f"slide {index}: {box['width']:.0f} x {box['height']:.0f} px, content overflow {overflow} px")
        out = HERE / f"tools-slide-{index}.png"
        element.screenshot(path=str(out))
        slides.append(out)
    chromium.close()

images = [Image.open(path).convert("RGB") for path in slides]
tall = Image.new("RGB", (images[0].width, sum(image.height for image in images)), (19, 20, 22))
y = 0
for image in images:
    tall.paste(image, (0, y))
    y += image.height
tall = tall.resize((tall.width // SCALE, tall.height // SCALE), Image.LANCZOS)      # the tall picture at 1x: five slides at 2x would be 16,000 px of height
tall.save(HERE / "tools-diagram-tall.png", optimize=True)
images[0].save(HERE / "tools-carousel.pdf", save_all=True, append_images=images[1:], resolution=144)
print("wrote", [path.name for path in slides], "tools-diagram-tall.png", "tools-carousel.pdf", tall.size)
