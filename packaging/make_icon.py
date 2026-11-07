"""Draw the app icon: a mint pi on the Project5pi dark tile. Writes packaging/p5midi.ico."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

S = 256
img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([8, 8, S - 8, S - 8], radius=48, fill=(13, 18, 17, 255))
font = None
for name in ('georgiab.ttf', 'georgia.ttf', 'timesbd.ttf', 'DejaVuSerif-Bold.ttf'):
    try:
        font = ImageFont.truetype(name, 178)
        break
    except OSError:
        pass
font = font or ImageFont.load_default()
box = d.textbbox((0, 0), '\u03c0', font=font)
w, h = box[2] - box[0], box[3] - box[1]
d.text(((S - w) / 2 - box[0], (S - h) / 2 - box[1] - 6), '\u03c0', font=font, fill=(93, 230, 200, 255))
out = Path(__file__).with_name('p5midi.ico')
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(out.with_suffix('.png'))
print(out)
