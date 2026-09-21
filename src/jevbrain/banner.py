"""
The mark. A 5x7 pixel alphabet, packed two pixel rows to a character with half
blocks so each pixel is square, lit by a pink ramp from top to bottom.

    from jevbrain.banner import render
    print("\\n".join(render()))
"""

from __future__ import annotations

GLYPHS = {
    "J": ["..###", "....#", "....#", "....#", "....#", "#...#", ".###."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "V": ["#...#", "#...#", "#...#", "#...#", ".#.#.", ".#.#.", "..#.."],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "N": ["#...#", "##..#", "#.#.#", "#.#.#", "#..##", "#...#", "#...#"],
}
WORD = "JEVBRAIN"
#: xterm-256 pinks, one per terminal row (four rows carry the seven pixel rows)
RAMP = (125, 162, 205, 218)
DIM, MID = 238, 132
RESET = "\033[0m"


def ink(c: int, on: bool, bold: bool = False) -> str:
    return (f"\033[38;5;{c}m" + ("\033[1m" if bold else "")) if on else ""


def grid(text: str = WORD) -> list[str]:
    rows = ["" for _ in range(8)]
    for i, ch in enumerate(text):
        g = GLYPHS[ch] + ["....."]
        for r in range(8):
            rows[r] += ("." if i else "") + g[r]
    return rows


def word_rows(text: str = WORD, colour: bool = True) -> list[str]:
    g = grid(text)
    out = []
    for r in range(0, 8, 2):
        top, bot = g[r], g[r + 1]
        line = []
        for a, b in zip(top, bot):
            line.append("█" if a == "#" and b == "#" else "▀" if a == "#" else "▄" if b == "#" else " ")
        out.append(ink(RAMP[r // 2], colour, bold=r >= 4) + "".join(line) + (RESET if colour else ""))
    return out


def wiring(colour: bool = True) -> str:
    dim, mid, hot, end = ink(DIM, colour), ink(MID, colour), ink(205, colour, True), RESET if colour else ""
    arrow = f"{dim} ─▶ {end}"
    parts = [f"{mid}state{end}", f"{hot}brain{end}", f"{mid}gate{end}", f"{mid}act{end}", f"{mid}ledger{end}"]
    return arrow.join(parts)


WIRING_WIDTH = len("state ─▶ brain ─▶ gate ─▶ act ─▶ ledger")


def render(width: int = 80, colour: bool = True) -> list[str]:
    w = len(grid()[0])

    def centre(s: str, n: int) -> str:
        return " " * max(0, (width - n) // 2) + s

    out = [""] + [centre(r, w) for r in word_rows(WORD, colour)]
    out.append("")
    out.append(centre(wiring(colour), WIRING_WIDTH))
    out.append(centre(ink(DIM, colour) + "typed decisions · a gate in code · every answer on the record"
                      + (RESET if colour else ""), 61))
    return out
