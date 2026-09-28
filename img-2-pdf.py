import os
import sys
import subprocess
import re

# ===== 1. Check / auto-install Pillow =====
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
    from PIL import Image, ImageDraw, ImageFont

# ===== 2. Default input folder (relative to this script) =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
folder = os.path.join(BASE_DIR, "captures")

# ===== 3. Output PDF =====
pdf_name = os.path.join(BASE_DIR, "result.pdf")

# ===== 4. Extract numeric page index from 'Page_X' =====
print("\n=== Image to PDF converter ===")
def page_number(filename: str) -> int:
    m = re.match(r"^Page_(\d+)", filename, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return 10**9


# ===== 5. List & sort images =====
print("Loading images...")
extensions = (".png", ".jpg", ".jpeg", ".bmp", ".webp")

try:
    all_files = os.listdir(folder)
except FileNotFoundError:
    print("Folder not found.")
    sys.exit(1)

files = [
    f for f in all_files
    if f.lower().startswith("page_") and f.lower().endswith(extensions)
]

files.sort(key=page_number)

if not files:
    print("No Page_X images found.")
    sys.exit(1)

page_count = len(files)

# ===== 6. Ask whether to stamp a page-number label onto each slide =====
# The white box behind the number is what makes it readable over the slide,
# so it's all-or-nothing: label (with box) or a clean, untouched slide.
answer = input(f"\nStamp a page-number label on each of the {page_count} slides? [Y/n] ").strip().lower()
add_label = answer in ("", "y", "yes")

# ===== 7. Build PDF (with optional overlay) =====
images = []

for index, file in enumerate(files, start=1):
    path = os.path.join(folder, file)
    img = Image.open(path)

    if img.mode != "RGB":
        img = img.convert("RGB")

    if add_label:
        draw = ImageDraw.Draw(img)
        text = f"{index} / {page_count}"

        try:
            font = ImageFont.truetype("arial.ttf", size=20)
        except IOError:
            font = ImageFont.load_default()

        # measure text
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        # box padding
        pad_x = 20
        pad_y = 10

        box_w = text_w + 2 * pad_x
        box_h = text_h + 2 * pad_y

        w, h = img.size

        # position centered bottom
        x0 = (w - box_w) // 2
        y0 = h - box_h - 10
        x1 = x0 + box_w
        y1 = y0 + box_h

        # white box for readability, then the number on top
        draw.rectangle([(x0, y0), (x1, y1)], fill="white")
        draw.text((x0 + pad_x, y0 + pad_y), text, fill="black", font=font)

    images.append(img)

print("Creating PDF")

images[0].save(
    pdf_name,
    save_all=True,
    append_images=images[1:],
    quality=100,
    resolution=300
)

print(f"PDF created: {pdf_name}")
print(f"Total pages: {page_count}")
