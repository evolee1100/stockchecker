#!/usr/bin/env python3
"""產生 app 圖示。

設計沿用網站本身的視覺語言：上升的價格線 + 強調的端點圓點，
跟展開個股時看到的走勢圖是同一套語彙，不是隨便找個通用圖表圖示。
深綠底是為了在手機主畫面的淺色桌布上夠顯眼。
"""

import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
GREEN = (6, 112, 63)          # 比介面的綠再深一點，小尺寸才壓得住
LINE = (255, 255, 255)
FILL = (255, 255, 255, 46)    # 線下的淡填色
DOT = (240, 180, 20)          # 端點用金色：跟線不同色，縮到 20px 才不會糊成一團，
                              # 同時呼應介面上「最愛」那顆星

# 走勢線的形狀：先震盪後拉升，最後一段回檔再創高——像真的股價。
# 起點刻意設在 x=0（被圓角切掉），這樣線下的填色左邊會齊到圖示邊緣，
# 不會出現一條突兀的垂直接縫；右邊在圓點處收邊，就是「今天」。
PATH = [(0.00, 0.70), (0.12, 0.62), (0.22, 0.67), (0.33, 0.50),
        (0.44, 0.56), (0.56, 0.38), (0.66, 0.44), (0.78, 0.29)]


def draw_icon(size, radius_ratio=0.225, pad=0.0):
    S = size * 4                       # 先畫 4 倍再縮，等於做抗鋸齒
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    inset = int(S * pad)
    box = [inset, inset, S - inset - 1, S - inset - 1]
    d.rounded_rectangle(box, radius=int(S * radius_ratio), fill=GREEN)

    pts = [(inset + (S - 2 * inset) * x, inset + (S - 2 * inset) * y) for x, y in PATH]

    # 線下方的淡填色，讓圖示不會只是一條細線
    area = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(area).polygon(
        pts + [(pts[-1][0], S), (0, S)], fill=FILL)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=int(S * radius_ratio), fill=255)
    img = Image.alpha_composite(img, Image.composite(
        area, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask))

    d = ImageDraw.Draw(img)
    w = max(2, int(S * 0.046))
    d.line(pts, fill=LINE, width=w, joint="curve")
    # 端點圓點——跟網頁圖表上標最新價的那顆一致
    r = int(S * 0.066)
    cx, cy = pts[-1]
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=DOT)

    return img.resize((size, size), Image.LANCZOS)


def svg():
    pts = " ".join("{:.1f},{:.1f}".format(x * 512, y * 512) for x, y in PATH)
    last = PATH[-1]
    return '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="115" fill="#06703f"/>
  <polygon points="{pts} {lx:.1f},512 0,512" fill="#fff" fill-opacity=".2"/>
  <polyline points="{pts}" fill="none" stroke="#fff" stroke-width="24"
            stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="{lx:.1f}" cy="{ly:.1f}" r="34" fill="#f0b414"/>
</svg>
'''.format(pts=pts, lx=last[0] * 512, ly=last[1] * 512)


if __name__ == "__main__":
    for size, name in [(512, "icon-512.png"), (192, "icon-192.png"),
                       (180, "apple-touch-icon.png"), (32, "favicon-32.png"),
                       (16, "favicon-16.png")]:
        draw_icon(size).save(os.path.join(HERE, name))
        print("  ", name)
    with open(os.path.join(HERE, "icon.svg"), "w", encoding="utf-8") as fh:
        fh.write(svg())
    print("   icon.svg")
