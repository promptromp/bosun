"""bosun.app's pictures, made from bosun's logo (docs/assets/logo.png): the app icon set that `iconutil` turns
into AppIcon.icns, and the menu bar's template image (docs/design/menu-bar.md); and the console's mark beside
its name in the top bar, which is also its tab's icon (console/logo.png).

The committed files are what this makes, once; run it again only when the logo changes (by hand, on any
machine: it needs Pillow, from the dev group, and nothing of macOS):

    uv run python tools/app_icons.py [--logo docs/assets/logo.png] [--out app/Resources]

It writes `<out>/AppIcon.iconset/icon_<n>x<n>[@2x].png` (the ten sizes `iconutil` takes) and
`<out>/MenuBarIcon.png` (18x18) and `<out>/MenuBarIcon@2x.png` (36x36), and `<console>/logo.png` (64x64: the
logo itself, in its colours on clear, for 32 points on a Retina screen; the console shows it at 20).

The app icon is the logo on a white rounded tile, with the margin macOS leaves round its icons. The menu bar
image is a template: black on clear, one colour with an alpha, which the menu bar tints for a light or a dark
bar. The logo is coloured, and its crossings are told apart by its dark outline: the template is the knot's
two ribbons in black, with that outline and the light fill of its loops left clear.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "docs" / "assets" / "logo.png"
RESOURCES = ROOT / "app" / "Resources"
CONSOLE = ROOT / "console"
CONSOLE_MARK = "logo.png"
CONSOLE_PIXELS = 64
ICONSET = "AppIcon.iconset"
# The sizes of an iconset, in points; each at 1x and 2x (the pixel size is points times the scale).
ICONSET_POINTS = (16, 32, 128, 256, 512)
MENU_BAR_POINTS = 18
INK_LIGHTNESS = 100  # of 255: the logo's outline is dark navy, its ribbons and loops are lighter
LOOP_RED = 120  # a loop's light blue fill has more red than this and more blue than LOOP_BLUE; the ribbons
LOOP_BLUE = 200  # (teal and amber) never have both
SOLID = 128  # of 255: the alpha from which a pixel of the logo is drawn
SUPERSAMPLE = 4  # the template and the tile are drawn this many times larger and scaled down
TILE_SHARE = 0.805  # of the icon's canvas, the tile macOS leaves inside its margin
TILE_RADIUS = 0.225  # of the tile's side
LOGO_SHARE = 0.72  # of the tile's side, the logo on it
BLACK = (0, 0, 0)
WHITE = (255, 255, 255, 255)


def read_logo(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGBA")


def iconset_names() -> list[tuple[str, int]]:
    """The file name and pixel size of each picture of the iconset."""
    return [
        (f"icon_{points}x{points}{suffix}.png", points * scale)
        for points in ICONSET_POINTS
        for suffix, scale in (("", 1), ("@2x", 2))
    ]


def _above(channel: Image.Image, level: int) -> Image.Image:
    return channel.point(lambda value: 255 if value > level else 0)


def outline(logo: Image.Image) -> Image.Image:
    """Where the logo is drawn in its dark outline: opaque and dark. An 'L' image, 255 for ink."""
    dark = logo.convert("L").point(lambda value: 255 if value < INK_LIGHTNESS else 0)
    return ImageChops.multiply(dark, logo.getchannel("A"))


def ribbons(logo: Image.Image) -> Image.Image:
    """Where the logo is drawn in the knot's ribbons: opaque, and neither its outline nor a loop's fill."""
    red, _, blue, alpha = logo.split()
    loops = ImageChops.multiply(_above(red, LOOP_RED), _above(blue, LOOP_BLUE))
    return ImageChops.subtract(_above(alpha, SOLID), ImageChops.lighter(loops, outline(logo)))


def template(logo: Image.Image, pixels: int) -> Image.Image:
    """The menu bar image, <pixels> square: black, its alpha the ribbons without the outline between them."""
    big = pixels * SUPERSAMPLE
    scaled = logo.resize((big, big), Image.Resampling.LANCZOS)
    knot = ImageChops.subtract(ribbons(scaled), outline(scaled))
    image = Image.new("RGBA", (pixels, pixels), (*BLACK, 0))
    image.putalpha(knot.resize((pixels, pixels), Image.Resampling.LANCZOS))
    return image


def app_icon(logo: Image.Image, pixels: int) -> Image.Image:
    """The app icon, <pixels> square: the logo on a white rounded tile inside the margin macOS leaves."""
    big = pixels * SUPERSAMPLE
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    side = round(big * TILE_SHARE)
    corner = round((big - side) / 2)
    tile = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, side - 1, side - 1), round(side * TILE_RADIUS), fill=WHITE)
    canvas.paste(tile, (corner, corner))
    mark_side = round(side * LOGO_SHARE)
    mark = logo.resize((mark_side, mark_side), Image.Resampling.LANCZOS)
    canvas.alpha_composite(mark, ((big - mark_side) // 2, (big - mark_side) // 2))
    return canvas.resize((pixels, pixels), Image.Resampling.LANCZOS)


def write_all(logo: Image.Image, out: Path) -> list[Path]:
    """Every picture, written under <out>; the paths."""
    iconset = out / ICONSET
    iconset.mkdir(parents=True, exist_ok=True)
    written = []
    for name, pixels in iconset_names():
        written.append(iconset / name)
        app_icon(logo, pixels).save(written[-1], optimize=True)
    for name, pixels in (
        ("MenuBarIcon.png", MENU_BAR_POINTS),
        ("MenuBarIcon@2x.png", MENU_BAR_POINTS * 2),
    ):
        written.append(out / name)
        template(logo, pixels).save(written[-1], optimize=True)
    return written


def console_mark(logo: Image.Image, pixels: int) -> Image.Image:
    """The logo as it is, in its colours on clear, at <pixels> square."""
    return logo.resize((pixels, pixels), Image.Resampling.LANCZOS)


def write_console_mark(logo: Image.Image, folder: Path) -> Path:
    """The console's mark, written in <folder>; its path."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / CONSOLE_MARK
    console_mark(logo, CONSOLE_PIXELS).save(path, optimize=True)
    return path


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Make bosun.app's icon set and menu bar image from the logo."
    )
    parser.add_argument("--logo", type=Path, default=LOGO)
    parser.add_argument("--out", type=Path, default=RESOURCES)
    parser.add_argument("--console-out", type=Path, default=CONSOLE)
    args = parser.parse_args(list(argv))
    logo = read_logo(args.logo)
    for path in [*write_all(logo, args.out), write_console_mark(logo, args.console_out)]:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
