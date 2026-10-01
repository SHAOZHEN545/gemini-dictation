"""Convert the supplied artwork into platform icons (requires Pillow for development).

Run from any directory: python scripts/generate-icons.py
The desktop/mobile apps use the generated files and do not require Pillow.
"""

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "gemini_live_dictation" / "assets"
BACKGROUND = "#fff8df"


def canvas(source: Image.Image, size: int, fraction: float, background=None) -> Image.Image:
    result = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
    artwork = source.resize((round(size * fraction),) * 2, Image.Resampling.LANCZOS)
    offset = (size - artwork.width) // 2
    result.alpha_composite(artwork, (offset, offset))
    return result


def main() -> None:
    source = Image.open(ASSETS / "app-icon.png").convert("RGBA")
    source.save(ASSETS / "app-icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    web = ROOT / "web"
    source.save(web / "favicon.ico", sizes=[(s, s) for s in (16, 32, 48)])
    for size in (192, 512):
        canvas(source, size, 1).save(web / f"icon-{size}.png")
    canvas(source, 180, 0.88, BACKGROUND).convert("RGB").save(web / "icon-180.png")
    canvas(source, 512, 0.78, BACKGROUND).convert("RGB").save(web / "icon-maskable-512.png")

    res = ROOT / "android" / "app" / "src" / "main" / "res"
    for density, scale in (("mdpi", 1), ("hdpi", 1.5), ("xhdpi", 2), ("xxhdpi", 3), ("xxxhdpi", 4)):
        folder = res / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        legacy = canvas(source, round(48 * scale), 0.78, BACKGROUND)
        legacy.save(folder / "ic_launcher.png")
        legacy.save(folder / "ic_launcher_round.png")
        # Keep the entire duck within Android's central 66dp adaptive-icon safe area.
        canvas(source, round(108 * scale), 66 / 108).save(folder / "ic_launcher_foreground.png")


if __name__ == "__main__":
    main()
