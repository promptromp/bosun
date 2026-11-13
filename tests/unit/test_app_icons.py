"""tools/app_icons.py and the pictures it made, which are committed (app/Resources): the menu bar image is a
template (black on clear, one colour with an alpha, 18x18 and 36x36), and the icon set is the ten sizes
`iconutil` takes. What the tool makes is checked on bosun's real logo, in a scratch folder."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import app_icons


RESOURCES = Path(__file__).resolve().parents[2] / "app" / "Resources"
ICONSET = RESOURCES / "AppIcon.iconset"
EXPECTED_SIZES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def pixels_of(image: Image.Image) -> list[tuple[int, ...]]:
    assert image.mode == "RGBA", image.mode
    return [tuple(pixel) for pixel in image.get_flattened_data()]  # type: ignore[arg-type]


def pixels(path: Path) -> list[tuple[int, ...]]:
    with Image.open(path) as image:
        return pixels_of(image)


def assert_template(case: unittest.TestCase, path: Path, side: int) -> None:
    with Image.open(path) as image:
        case.assertEqual(image.size, (side, side), path.name)
    found = pixels(path)
    alphas = {alpha for *_, alpha in found}
    case.assertEqual(
        {tuple(rgb) for *rgb, alpha in found if alpha}, {(0, 0, 0)}, f"{path.name}: only black is drawn"
    )
    case.assertIn(0, alphas, f"{path.name}: has clear pixels")
    case.assertIn(255, alphas, f"{path.name}: has solid pixels")
    case.assertGreater(len(alphas), 2, f"{path.name}: has an alpha, not just on and off")
    ink = sum(alpha for *_, alpha in found) / 255 / len(found)  # the share of it that is black
    case.assertTrue(0.15 < ink < 0.6, f"{path.name}: {ink:.0%} of it is black, a knot and not a blob")


class CommittedPicturesTest(unittest.TestCase):
    def test_the_menu_bar_image_is_a_template_at_eighteen_points(self) -> None:
        assert_template(self, RESOURCES / "MenuBarIcon.png", 18)

    def test_and_at_twice_that(self) -> None:
        assert_template(self, RESOURCES / "MenuBarIcon@2x.png", 36)

    def test_the_icon_set_is_the_ten_sizes_iconutil_takes_and_nothing_else(self) -> None:
        self.assertEqual({path.name for path in ICONSET.iterdir()}, set(EXPECTED_SIZES))
        for name, side in EXPECTED_SIZES.items():
            with self.subTest(name), Image.open(ICONSET / name) as image:
                self.assertEqual(image.size, (side, side))
                self.assertEqual(image.mode, "RGBA")

    def test_each_icon_has_the_margin_macos_leaves_and_a_picture_inside_it(self) -> None:
        for name in EXPECTED_SIZES:
            with self.subTest(name), Image.open(ICONSET / name) as image:
                side = image.width
                self.assertEqual(image.getpixel((0, 0))[3], 0)  # type: ignore[index]
                self.assertLess(image.getpixel((side // 2, 0))[3], 16)  # type: ignore[index]
                self.assertEqual(image.getpixel((side // 2, side // 2))[3], 255)  # type: ignore[index]


class ToolTest(unittest.TestCase):
    temp: tempfile.TemporaryDirectory[str]
    out: Path
    written: list[Path]

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temp.name)
        cls.written = app_icons.write_all(app_icons.read_logo(app_icons.LOGO), cls.out)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_it_writes_the_iconset_and_the_two_template_images(self) -> None:
        names = sorted(str(path.relative_to(self.out)) for path in self.written)
        expected = sorted(
            [f"AppIcon.iconset/{name}" for name in EXPECTED_SIZES] + ["MenuBarIcon.png", "MenuBarIcon@2x.png"]
        )
        self.assertEqual(names, expected)

    def test_what_it_makes_is_what_is_committed_in_size_and_kind(self) -> None:
        assert_template(self, self.out / "MenuBarIcon.png", 18)
        assert_template(self, self.out / "MenuBarIcon@2x.png", 36)
        for name, side in EXPECTED_SIZES.items():
            with Image.open(self.out / "AppIcon.iconset" / name) as image:
                self.assertEqual(image.size, (side, side), name)

    def test_the_template_is_the_logo_s_outline_and_not_its_colours(self) -> None:
        logo = app_icons.read_logo(app_icons.LOGO)
        found = pixels_of(app_icons.template(logo, 36))
        self.assertEqual({tuple(rgb) for *rgb, alpha in found if alpha}, {(0, 0, 0)})

    def test_it_runs_as_a_command_and_says_what_it_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(app_icons.main(["--out", folder]), 0)
            self.assertTrue((Path(folder) / "MenuBarIcon.png").is_file())


if __name__ == "__main__":
    unittest.main()
