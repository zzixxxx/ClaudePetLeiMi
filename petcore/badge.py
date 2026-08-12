"""petcore.badge — 托盘/菜单栏用量数字徽章 (PIL 绘制, 双端共用)."""
import sys

from PIL import Image, ImageDraw, ImageFont

from .common import usage_color

# 粗体数字字体候选 (按平台顺序尝试)
_FONTS = (["arialbd.ttf"] if sys.platform == "win32" else
          ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
           "/System/Library/Fonts/Helvetica.ttc"])


def _font(size):
    for path in _FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_badge(pct5, pct7):
    """单个徽章: 上半 5h 用量, 下半 7d 用量, 各自按红黄绿分档底色."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 63, 30), fill=usage_color(pct5))
    d.rectangle((0, 33, 63, 63), fill=usage_color(pct7))
    font = _font(26)
    d.text((32, 15), str(min(round(pct5), 99)), font=font, fill="white",
           anchor="mm")
    d.text((32, 48), str(min(round(pct7), 99)), font=font, fill="white",
           anchor="mm")
    mask = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 63, 63), radius=12,
                                           fill=255)
    return Image.composite(img, Image.new("RGBA", (64, 64), (0, 0, 0, 0)),
                           mask)
