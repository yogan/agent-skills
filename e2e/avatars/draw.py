#!/usr/bin/env python3
"""Draws the demo people's avatars: 16x16 pixel art, scaled to the 192x192 GitLab asks for.

    uv run --with pillow e2e/avatars/draw.py

The PNGs are committed, so a setup needs none of this; run it only to change a drawing.
`frank.png` is the presenter's own avatar, not drawn here. GitLab shows every avatar in a
circle, so each drawing keeps to the middle and its background fills the corners.
"""
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SCALE = 12

OUTLINE = "#2b2d42"

SPRITES = {
    # Alice Agent: a little robot.
    "alice": ("#f9d5e5", {"K": OUTLINE, "G": "#7fd1c7", "W": "#ffffff", "E": OUTLINE,
                          "P": "#ff8fab", "A": "#ffb703"}, [
        ".......AA.......",
        ".......KK.......",
        "...KKKKKKKKKK...",
        "..KGGGGGGGGGGK..",
        "..KGWWWWWWWWGK..",
        "..KGWEEWWEEWGK..",
        "..KGWEEWWEEWGK..",
        "..KGWWWWWWWWGK..",
        "..KGPWWKKWWPGK..",
        "..KGWWWWWWWWGK..",
        "..KGGGGGGGGGGK..",
        "...KKKKKKKKKK...",
        ".....KGGGGK.....",
        "...KKGGGGGGKK...",
        "..KGGGGAAGGGGK..",
        "..KGGGGGGGGGGK..",
    ]),
    # Bob Builder: a hard hat and a vest with reflective stripes.
    "bob": ("#cdeffd", {"K": OUTLINE, "Y": "#ffd23f", "O": "#f4a261", "S": "#f2c6a0",
                        "H": "#6b4226", "E": OUTLINE, "P": "#ff8fab"}, [
        "......KKKK......",
        "....KKYYYYKK....",
        "...KYYYKKYYYK...",
        "..KYYYYKKYYYYK..",
        ".KOOOOOOOOOOOOK.",
        "..KSSSSSSSSSSK..",
        "..KSHHSSSSHHSK..",
        "..KSSESSSSESSK..",
        "..KSSESSSSESSK..",
        "..KSPSSSSSSPSK..",
        "..KSSSKSSKSSSK..",
        "..KSSSSKKSSSSK..",
        "...KSSSSSSSSK...",
        "....KKKKKKKK....",
        "...KOOOOOOOOK...",
        "..KOOYOOOOYOOK..",
    ]),
    # Clara Checker: a cat.
    "clara": ("#d8f3dc", {"K": OUTLINE, "F": "#b8a1e3", "R": "#ff8fab", "E": OUTLINE,
                          "W": "#ffffff", "P": "#ff8fab"}, [
        "................",
        "...K........K...",
        "...KK......KK...",
        "...KFK....KFK...",
        "..KKRFKKKKFRKK..",
        ".KFFFFFFFFFFFFK.",
        ".KFFFFFFFFFFFFK.",
        ".KFFEEFFFFEEFFK.",
        ".KFFEWFFFFEWFFK.",
        ".KFPFFFWWFFFPFK.",
        ".KFFFFWKKWFFFFK.",
        ".KFFFFFWWFFFFFK.",
        "..KFFFFFFFFFFK..",
        "...KKFFFFFFKK...",
        ".....KKKKKK.....",
        "................",
    ]),
}


def draw(background, colours, rows):
    assert len(rows) == 16 and all(len(r) == 16 for r in rows), "a sprite is 16x16"
    img = Image.new("RGB", (16, 16), background)
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            if c != ".":
                img.putpixel((x, y), Image.new("RGB", (1, 1), colours[c]).getpixel((0, 0)))
    return img.resize((16 * SCALE, 16 * SCALE), Image.NEAREST)


if __name__ == "__main__":
    for name, (background, colours, rows) in SPRITES.items():
        path = os.path.join(HERE, f"{name}.png")
        draw(background, colours, rows).save(path, optimize=True)
        print(path)
