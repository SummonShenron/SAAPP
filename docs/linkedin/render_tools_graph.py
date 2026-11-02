"""Renders docs/linkedin/tools-graph.html to one tall PNG (1080 px wide, drawn at 2x so text stays sharp on a phone).

Uses the Edge or Chrome already on the computer through Playwright (no browser download):
    .venv/Scripts/python.exe docs/linkedin/render_tools_graph.py
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "runner" / "src"))
from saapp_runner import browser as runner_browser  # noqa: E402  (only to find the installed browser)

found = runner_browser.find_browser()
if not found:
    sys.exit("No Chrome or Edge found on this computer.")

with sync_playwright() as p:
    chromium = p.chromium.launch(executable_path=found["path"], headless=True)
    page = chromium.new_page(viewport={"width": 1080, "height": 1600}, device_scale_factor=2)
    page.goto((HERE / "tools-graph.html").as_uri())
    page.wait_for_timeout(400)
    element = page.locator("#tall")
    box = element.bounding_box()
    out = HERE / "tools-graph-tall.png"
    element.screenshot(path=str(out))
    chromium.close()
print(f"wrote {out.name}: {box['width']:.0f} x {box['height']:.0f} px (at 2x)")
