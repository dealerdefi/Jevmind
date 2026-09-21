#!/usr/bin/env python3
"""
Record assets/arena.gif: one real run of `jevmind arena`, frame by frame.

Every frame is drawn from the same `render()` the terminal uses, and the status
line is the brain's actual answer on that tick. Development-only: needs Pillow
and ffmpeg. jevmind itself needs neither.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from jevmind.mind import Mind  # noqa: E402
from jevmind.skills import arena  # noqa: E402

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
BG, GROUND, PINK, WALKER, MUTE, HOT = "#070307", "#5c1238", "#ff4fa3", "#8ea3c8", "#8a6a7c", "#ffd24a"
W, CELL_W, CELL_H = 56, 16, 30


def frame(body, level, status: list[tuple[str, str]], font, bold) -> Image.Image:
    img = Image.new("RGB", (W * CELL_W + 40, 5 * CELL_H + 92), BG)
    d = ImageDraw.Draw(img)
    left = max(0, body.x - 10)
    for yy in range(4, -1, -1):
        for i, x in enumerate(range(left, left + W)):
            px, py = 20 + i * CELL_W, 20 + (4 - yy) * CELL_H
            if yy == 0 and not level.pit_at(x):
                d.rectangle([px, py + 4, px + CELL_W - 2, py + 12], fill=GROUND)
            h = level.pipes.get(x, 0)
            if h and 1 <= yy <= h:
                d.rectangle([px, py, px + CELL_W - 2, py + CELL_H - 2], fill=GROUND)
            if yy == 1 and any(w.x == x for w in level.walkers):
                d.ellipse([px + 1, py + 8, px + CELL_W - 3, py + CELL_H - 4], fill=WALKER)
            if x == body.x and yy == body.y + 1:
                d.rectangle([px + 1, py + 2, px + CELL_W - 3, py + CELL_H - 4], fill=PINK if body.alive else MUTE)
                d.rectangle([px + 8, py + 8, px + 11, py + 12], fill=BG)
            if x == level.length and yy >= 1:
                d.rectangle([px, py, px + 4, py + CELL_H - 2], fill=PINK)
    x = 20
    for text, colour in status:
        d.text((x, 5 * CELL_H + 40), text, font=font, fill=colour)
        x += d.textlength(text, font=font)
    return img


def main() -> int:
    font, bold = ImageFont.truetype(FONT, 20), ImageFont.truetype(BOLD, 22)
    level, body = arena.make_level(4), arena.Body()
    mind = Mind(record=False)
    frames: list[Image.Image] = []
    tick = 0
    while body.alive and not body.won and tick < 400:
        s = arena.ahead(body, level)
        d = mind.ask("arena", "gif", {"ahead": s}, {
            "move": arena.Choice("What should the runner do this tick?", arena.MOVES),
            "danger": arena.Noul("If the runner keeps running straight on, it dies within two ticks."),
        }, arena.REFLEXES, drive="move", act_when=lambda a: True, threshold=0.3)
        move = str(d["move"].value)
        arena.step(body, level, move)
        tick += 1
        danger = d["danger"].p
        frames.append(frame(body, level, [
            (f"tick {tick:>3}  ", MUTE), (f"x {body.x:>3}/{level.length}  ", MUTE),
            (f"{move:<5}", PINK if move != "run" else MUTE),
            (f"  danger {danger:.2f}", HOT if danger >= 0.5 else MUTE),
            (f"  conf {d['move'].confidence:.2f}  local {d.thought.latency_ms:.2f}ms", MUTE)], font, bold))
    tmp = Path(tempfile.mkdtemp())
    for i, f in enumerate(frames):
        f.save(tmp / f"{i:04d}.png")
    out = ROOT / "assets" / "arena.gif"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "14", "-i", str(tmp / "%04d.png"),
                    "-vf", "split[a][b];[a]palettegen=max_colors=32[p];[b][p]paletteuse=dither=none",
                    str(out)], check=True)
    subprocess.run(["gifsicle", "-O3", "--lossy=30", "-b", str(out)], check=False)
    print(f"  {out.relative_to(ROOT)}  {len(frames)} frames  ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
