"""Подготавливает nova.ico из утверждённого логотипа assets/icons/nova.png."""
from __future__ import annotations

from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICON_PNG = ROOT / "assets" / "icons" / "nova.png"
ICON_ICO = ROOT / "assets" / "icons" / "nova.ico"


def main() -> None:
    if not ICON_PNG.is_file():
        raise FileNotFoundError(f"Не найден логотип NOVA: {ICON_PNG}")
    with Image.open(ICON_PNG) as source:
        image = source.convert("RGBA")
        if image.width != image.height:
            side = min(image.size)
            left = (image.width - side) // 2
            top = (image.height - side) // 2
            image = image.crop((left, top, left + side, top + side))
        image.resize((256, 256), Image.Resampling.LANCZOS).save(
            ICON_ICO, format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
    print(f"NOVA icon ready: {ICON_ICO}")


if __name__ == "__main__":
    main()
