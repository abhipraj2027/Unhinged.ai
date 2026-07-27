"""Generate beautiful shareable roast cards as PNG images."""
from PIL import Image, ImageDraw, ImageFont
import io
import os
import textwrap

# Try to load good fonts, fall back to default
def _get_font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def generate_roast_card(score: float, roast: str, risk: str = "") -> bytes:
    """Generate a 1200x630 branded roast card PNG."""
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), "#0A0A0A")
    draw = ImageDraw.Draw(img)

    # Fonts
    font_huge = _get_font(88, bold=True)
    font_large = _get_font(28, bold=True)
    font_med = _get_font(18, bold=True)
    font_body = _get_font(17)
    font_small = _get_font(13)
    font_tiny = _get_font(11)

    # Colors based on score
    if score < 4:
        score_color = "#22C55E"
        bar_color = (34, 197, 94)
    elif score < 8:
        score_color = "#FACC15"
        bar_color = (250, 204, 21)
    else:
        score_color = "#EF4444"
        bar_color = (239, 68, 68)

    # ── Background effects ──
    # Subtle gradient overlay
    for y in range(H):
        alpha = int(20 * (1 - y / H))
        draw.line([(0, y), (W, y)], fill=(alpha, alpha, alpha))

    # Top accent line
    for x in range(200, 1000):
        intensity = int(255 * (1 - abs(x - 600) / 400))
        if intensity > 0:
            draw.point((x, 0), fill=(255, min(255, 92 + intensity // 4), 0))
            draw.point((x, 1), fill=(255, min(255, 92 + intensity // 4), 0))

    # ── Left panel: Brand + Score ──
    # Brand
    draw.text((50, 36), "🔥", font=_get_font(28), fill="#FF5C00")
    draw.text((88, 38), "UnHinged", font=font_large, fill="#FFFFFF")
    draw.text((88, 70), "EMAIL TONE CHECKER", font=font_tiny, fill="#52525B")

    # Score section
    draw.text((50, 140), "UNHINGED SCORE", font=font_small, fill="#71717A")

    # Large score
    score_text = f"{score:.1f}"
    draw.text((50, 170), score_text, font=font_huge, fill=score_color)
    # /10
    bbox = draw.textbbox((0, 0), score_text, font=font_huge)
    score_width = bbox[2] - bbox[0]
    draw.text((50 + score_width + 8, 210), "/10", font=font_large, fill="#3F3F46")

    # Fire emoji for high scores
    if score >= 8:
        draw.text((50 + score_width + 60, 185), "🔥", font=_get_font(40), fill="#FF5C00")

    # Score bar
    bar_x, bar_y, bar_w, bar_h = 50, 280, 400, 10
    # Background bar
    draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=5, fill="#1A1A1A")
    # Filled bar
    fill_w = int(bar_w * (score / 10))
    if fill_w > 0:
        draw.rounded_rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], radius=5, fill=bar_color)

    # Score labels
    draw.text((50, 300), "Safe", font=font_tiny, fill="#22C55E")
    draw.text((210, 300), "Spicy", font=font_tiny, fill="#FACC15")
    draw.text((390, 300), "Nuclear", font=font_tiny, fill="#EF4444")

    # ── Right panel: Roast ──
    # Roast box background
    draw.rounded_rectangle([500, 30, 1160, 460], radius=16, fill="#111113", outline="#1F1F23")

    # Roast label
    draw.text((530, 48), "▲ AI ROAST", font=font_small, fill="#FF5C00")

    # Roast text (word wrap)
    roast_clean = roast[:280] if len(roast) > 280 else roast
    wrapped = textwrap.fill(roast_clean, width=42)
    lines = wrapped.split("\n")[:8]  # Max 8 lines
    y_pos = 80
    for line in lines:
        draw.text((530, y_pos), line, font=font_body, fill="#E4E4E7")
        y_pos += 26

    # Risk section (if provided)
    if risk:
        risk_y = max(y_pos + 20, 300)
        draw.rounded_rectangle([500, risk_y, 1160, risk_y + 120], radius=12, fill="#0F0F11", outline="#1F1F23")
        draw.text((530, risk_y + 14), "⚠ RISK ASSESSMENT", font=font_small, fill="#FACC15")
        risk_clean = risk[:150] if len(risk) > 150 else risk
        risk_wrapped = textwrap.fill(risk_clean, width=44)
        risk_lines = risk_wrapped.split("\n")[:3]
        ry = risk_y + 40
        for line in risk_lines:
            draw.text((530, ry), line, font=font_body, fill="#A1A1AA")
            ry += 24

    # ── Bottom CTA bar ──
    draw.rectangle([0, 530, W, H], fill="#111113")
    draw.line([(0, 530), (W, 530)], fill="#1F1F23")

    # CTA text
    draw.text((50, 555), "How unhinged is YOUR email?", font=font_med, fill="#A1A1AA")
    draw.text((50, 585), "Try free →  unhinged.email", font=font_large, fill="#FF5C00")

    # Watermark
    draw.text((1020, 590), "unhinged.email", font=font_small, fill="#27272A")

    # ── Export ──
    buf = io.BytesIO()
    img.save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf.getvalue()
