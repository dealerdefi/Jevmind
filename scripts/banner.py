#!/usr/bin/env python3
"""
Draw assets/banner.png from the same pixel alphabet the terminal prints.

    python scripts/banner.py

The README's banner and `jevbrain`'s banner are one drawing: the glyphs come out of
src/jevbrain/banner.py, so changing a letter there changes both. Development-only:
needs Playwright's Chromium.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jevbrain.banner import GLYPHS, WORD  # noqa: E402

#: The terminal's xterm pinks, as hex, top row to bottom.
ROWS = ["#87005f", "#af005f", "#d70087", "#ff0087", "#ff5faf", "#ff87d7", "#ffafd7"]

PAGE = """<!doctype html><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1280px;height:512px;background:#050305;overflow:hidden;
  font-family:"DejaVu Sans Mono",Menlo,monospace;color:#b0708f;position:relative}}
.glow{{position:absolute;inset:0;background:
  radial-gradient(640px 300px at 50% 46%,#3a0a26 0%,transparent 70%),
  repeating-linear-gradient(0deg,rgba(255,79,163,.035) 0 1px,transparent 1px 4px)}}
.c{{position:absolute;font-size:15px;letter-spacing:3px;line-height:1.9;color:#8a4d6d}}
.tl{{left:44px;top:34px}} .tr{{right:44px;top:34px;text-align:right}}
.bl{{left:44px;bottom:30px}} .br{{right:44px;bottom:30px;text-align:right}}
.c b{{color:#ff4fa3;font-weight:700}}
svg{{position:absolute;left:50%;top:150px;transform:translateX(-50%);
  filter:drop-shadow(0 0 22px rgba(255,79,163,.35))}}
.rule{{position:absolute;left:50%;top:318px;transform:translateX(-50%);width:560px;
  display:flex;align-items:center;gap:14px}}
.rule i{{flex:1;height:1px;background:#3a2233}} .rule s{{width:9px;height:9px;background:#ff5faf;
  transform:rotate(45deg)}}
.wire{{position:absolute;left:50%;top:352px;transform:translateX(-50%);text-align:center;
  font-size:24px;letter-spacing:2px;white-space:pre;line-height:1.5}}
.wire .a{{color:#8a4d6d}} .wire .d{{color:#ff87d7;font-weight:700}} .wire .x{{color:#4a3444}}
.wire small{{display:block;font-size:14px;color:#6e5264;margin-top:6px;letter-spacing:1px}}
</style>
<div class="glow"></div>
<div class="c tl">CHOICE<br><b>SCORE</b><br>NOUL</div>
<div class="c tr">ONE BRAIN<br>NINE HANDS<br>ON THE RECORD</div>
<div class="c bl">LOCAL · JEV · REPLAY</div>
<div class="c br">PYTHON · ZERO DEPENDENCIES · MCP</div>
{svg}
<div class="rule"><i></i><s></s><i></i></div>
<div class="wire"><span class="a">state</span><span class="x"> ─▶ </span><span class="d">brain</span><span class="x"> ─▶ </span><span class="a">gate</span><span class="x"> ─▶ </span><span class="a">act</span><span class="x"> ─▶ </span><span class="a">ledger</span><small>compact · navigate · review · route · canny · curate · walk · guard · arena</small></div>
"""


def svg(px: int = 16, gap: int = 2) -> str:
    cols = len(WORD) * 5 + (len(WORD) - 1)
    w, h = cols * px, 7 * px
    rects = []
    for i, ch in enumerate(WORD):
        glyph = GLYPHS[ch]
        x0 = i * 6
        for r, row in enumerate(glyph):
            for c, cell in enumerate(row):
                if cell == "#":
                    rects.append(f'<rect x="{(x0 + c) * px}" y="{r * px}" width="{px - gap}" '
                                 f'height="{px - gap}" fill="{ROWS[r]}"/>')
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{"".join(rects)}</svg>'


def main() -> int:
    from playwright.sync_api import sync_playwright

    out = ROOT / "assets" / "banner.png"
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 512}, device_scale_factor=2)
        pg.set_content(PAGE.format(svg=svg()))
        pg.wait_for_timeout(150)
        pg.screenshot(path=str(out))
        b.close()
    print(f"  {out.relative_to(ROOT)}  ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
