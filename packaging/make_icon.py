"""Gera o ícone do StudioClips (assets/studioclips.ico e .png).

    python packaging/make_icon.py      (precisa do Pillow: pip install pillow)

Desenho: quadrado escuro arredondado com um "clipe vertical" (9:16) em rosa, botão de play branco e
marcas de corte nos cantos. Desenhado grande e reduzido, pra ficar nítido até em 16 px.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
S = 1024
BG_TOP, BG_BOTTOM = (28, 28, 40), (8, 8, 12)
PINK, PINK_DARK = (255, 46, 99), (196, 20, 70)
WHITE = (255, 255, 255)


def vertical_gradient(size, top, bottom):
    w, h = size
    grad = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return grad.resize((w, h))


def rounded_mask(size, box, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle(box, radius=radius, fill=255)
    return m


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # fundo: quadrado arredondado escuro
    bg_box = (40, 40, S - 40, S - 40)
    img.paste(vertical_gradient((S, S), BG_TOP, BG_BOTTOM), (0, 0), rounded_mask((S, S), bg_box, 220))

    # clipe vertical 9:16 (com brilho rosa atrás)
    cw, ch = 430, 700
    cx0, cy0 = (S - cw) // 2, (S - ch) // 2 + 10
    clip_box = (cx0, cy0, cx0 + cw, cy0 + ch)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(clip_box, radius=90, fill=PINK + (150,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(60)))
    card = vertical_gradient((S, S), PINK, PINK_DARK).convert("RGBA")
    img.paste(card, (0, 0), rounded_mask((S, S), clip_box, 90))

    d = ImageDraw.Draw(img)
    # play
    mx, my = S // 2 + 22, cy0 + ch // 2
    r = 150
    d.polygon([(mx - r * 0.75, my - r), (mx - r * 0.75, my + r), (mx + r, my)], fill=WHITE)

    # marcas de corte nos cantos (enquadramento)
    m, L, t = 150, 120, 34
    for (x, y, dx, dy) in [(m, m, 1, 1), (S - m, m, -1, 1), (m, S - m, 1, -1), (S - m, S - m, -1, -1)]:
        d.rounded_rectangle(sorted_box(x, y, x + dx * L, y + dy * t), radius=t // 2, fill=WHITE)
        d.rounded_rectangle(sorted_box(x, y, x + dx * t, y + dy * L), radius=t // 2, fill=WHITE)
    return img


def sorted_box(x0, y0, x1, y1):
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


if __name__ == "__main__":
    big = draw()
    out = ROOT / "assets"
    big.resize((256, 256), Image.LANCZOS).save(out / "studioclips.png")
    big.save(out / "studioclips.ico", sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
                                             (64, 64), (128, 128), (256, 256)])
    print("ok:", out / "studioclips.ico")
