"""Generate the Monster-ww application icons from the source artwork.

Source artwork: .github/assets/app_icon_source.webp (256x256 RGBA portrait).
Outputs (all derived from one 512x512 master image):

  icons/icon.png      300x300 RGBA  - PyAppify app icon, Qt window icon, docs logo
  icons/icon.ico      256x256 PNG   - Windows icon resource
  icons/launcher.ico  16..128 DIB   - /win32icon for the optional C# launcher

Small ICO entries use a face crop so the icon stays readable in the taskbar.
Run from the repository root:

  python .github/scripts/generate_app_icons.py
"""

from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".github" / "assets" / "app_icon_source.webp"
ICONS = ROOT / "icons"

MASTER = 512
RADIUS = 112
TOP_COLOR = (38, 82, 140)
BOTTOM_COLOR = (17, 37, 69)
PNG_SIZE = 300
FACE_BOX = (106, 110, 406, 410)  # square face crop used for tiny ICO entries
ART_SIZE = 452
ART_TOP = 30


def resize_rgba(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize with premultiplied alpha to avoid dark fringes."""
    if image.size == size:
        return image.copy()
    return image.convert("RGBa").resize(size, Image.LANCZOS).convert("RGBA")


def build_master(source: Image.Image) -> Image.Image:
    canvas = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))

    # Vertical gradient background.
    gradient = Image.new("RGB", (MASTER, MASTER))
    draw = ImageDraw.Draw(gradient)
    for y in range(MASTER):
        t = y / (MASTER - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(TOP_COLOR, BOTTOM_COLOR))
        draw.line([(0, y), (MASTER, y)], fill=color)

    mask = Image.new("L", (MASTER, MASTER), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, MASTER - 1, MASTER - 1], radius=RADIUS, fill=255
    )
    canvas.paste(gradient, (0, 0), mask)

    # Soft cool highlight behind the head, clipped to the rounded square.
    glow = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [-60, -250, MASTER + 60, 270], fill=(150, 205, 255, 90)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(95))
    canvas = Image.composite(Image.alpha_composite(canvas, glow), canvas, mask)

    art = resize_rgba(source.convert("RGBA"), (ART_SIZE, ART_SIZE))
    canvas.alpha_composite(art, ((MASTER - ART_SIZE) // 2, ART_TOP))
    canvas.putalpha(ImageChops.multiply(canvas.getchannel("A"), mask))
    return canvas


def dib_entry(image: Image.Image) -> bytes:
    """Encode one 32bpp BMP/DIB icon image (csc /win32icon compatible)."""
    width, height = image.size
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    xor = bytearray()
    for y in range(height - 1, -1, -1):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            xor += bytes((blue, green, red, alpha))
    row_bytes = ((width + 31) // 32) * 4
    and_mask = bytes(row_bytes * height)  # 32bpp alpha is authoritative
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height * 2,
        1,
        32,
        0,
        len(xor) + len(and_mask),
        0,
        0,
        0,
        0,
    )
    return header + bytes(xor) + and_mask


def write_ico(path: Path, images: dict[int, Image.Image]) -> None:
    sizes = sorted(images)
    count = len(sizes)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    directory = bytearray()
    blobs = bytearray()
    for size in sizes:
        data = dib_entry(images[size])
        directory += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        blobs += data
        offset += len(data)
    path.write_bytes(header + bytes(directory) + bytes(blobs))


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Source artwork missing: {SOURCE}")
    ICONS.mkdir(exist_ok=True)
    master = build_master(Image.open(SOURCE))

    resize_rgba(master, (PNG_SIZE, PNG_SIZE)).save(ICONS / "icon.png", "PNG", optimize=True)
    resize_rgba(master, (256, 256)).save(ICONS / "icon.ico", "ICO", sizes=[(256, 256)])

    face = master.crop(FACE_BOX)
    sizes = (16, 24, 32, 48, 64, 128)
    entries = {}
    for size in sizes:
        entry = resize_rgba(face if size <= 48 else master, (size, size))
        # Keep the rounded-square silhouette at every size, including the
        # face crops used for the taskbar.
        corner = Image.new("L", (size, size), 0)
        ImageDraw.Draw(corner).rounded_rectangle(
            [0, 0, size - 1, size - 1],
            radius=max(2, round(size * RADIUS / MASTER)),
            fill=255,
        )
        entry.putalpha(ImageChops.multiply(entry.getchannel("A"), corner))
        entries[size] = entry
    write_ico(ICONS / "launcher.ico", entries)

    for name in ("icon.png", "icon.ico", "launcher.ico"):
        path = ICONS / name
        print(f"  {name:16s} {path.stat().st_size:8d} B")
    print(f"  master          {master.width}x{master.height} RGBA")


if __name__ == "__main__":
    main()
