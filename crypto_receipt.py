"""AMBAR crypto-payment receipt → a branded PDF built from our OWN verified
on-chain data (no Tronscan scraping). Consumed by the /api/crypto/receipt route.

Self-contained: `python3 crypto_receipt.py` renders a sample to /tmp for preview.
Requires fpdf2 (`pip install fpdf2`) + the bundled DejaVu fonts in ./fonts.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE   = Path(__file__).resolve().parent
_FONTS  = _HERE / "fonts"
_DUBAI  = timezone(timedelta(hours=4))

# ── palette (AMBAR dark-gold) ────────────────────────────────────────────────
BG    = (11, 11, 20)
PANEL = (21, 21, 32)
PAYBG = (28, 25, 16)
GOLD  = (201, 169, 110)
GOLDL = (231, 206, 150)
TEXT  = (236, 236, 242)
SUB   = (148, 148, 165)
GREEN = (95, 196, 144)
LINE  = (58, 53, 40)


def _fmt_dt(ts) -> str:
    if not ts:
        return "—"
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(_DUBAI).strftime("%d.%m.%Y · %H:%M")
    except Exception:
        return str(ts)


def _amt(v) -> str:
    try:
        f = float(v)
        return f"{f:.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(v)


def build_receipt(order: dict, to_address: str = "", from_address: str = "") -> bytes:
    """Render the receipt for one crypto order. `order` mirrors the DB doc:
    order_id, timestamp, items[], total, crypto_txid, crypto_amount_usdt."""
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="A5")  # 148 × 210 mm
    pdf.set_auto_page_break(False)
    pdf.add_font("D", "",  str(_FONTS / "DejaVuSans.ttf"))
    pdf.add_font("D", "B", str(_FONTS / "DejaVuSans-Bold.ttf"))
    pdf.add_font("M", "",  str(_FONTS / "DejaVuSansMono.ttf"))
    pdf.add_page()

    W, ML, MR = 148, 14, 14
    cw = W - ML - MR
    pdf.set_fill_color(*BG); pdf.rect(0, 0, W, 210, style="F")

    def line(x1, y1, x2, color=LINE, w=0.2):
        pdf.set_draw_color(*color); pdf.set_line_width(w); pdf.line(x1, y1, x2, y1)

    # ── header: brand logo (pin + AMBAR + PREMIUM SPIRITS) ─────────────────────
    _logo = _HERE / "LOGOS" / "ambar_logo.png"
    if _logo.exists():
        lw = 42.0
        hy = 9 + lw * 994.0 / 1093.0             # logo bottom (height auto-scales)
        pdf.image(str(_logo), x=(W - lw) / 2.0, y=9, w=lw)
    else:                                         # fallback: text wordmark
        pdf.set_font("D", "B", 27); pdf.set_text_color(*GOLDL); pdf.set_char_spacing(3.2)
        pdf.set_xy(ML, 14); pdf.cell(cw, 12, "AMBAR", align="C"); pdf.set_char_spacing(0)
        pdf.set_font("D", "", 7); pdf.set_text_color(*GOLD); pdf.set_char_spacing(4.5)
        pdf.set_xy(ML, 27); pdf.cell(cw, 4, "PREMIUM SPIRITS", align="C"); pdf.set_char_spacing(0)
        hy = 38.0
    line(ML + 38, hy + 3, W - MR - 38, color=GOLD, w=0.4)
    pdf.set_font("D", "B", 13); pdf.set_text_color(*TEXT)
    pdf.set_xy(ML, hy + 6); pdf.cell(cw, 7, "Чек оплаты", align="C")

    # ── order + date ───────────────────────────────────────────────────────────
    y = 63
    pdf.set_font("D", "B", 11); pdf.set_text_color(*GOLD)
    pdf.set_xy(ML, y); pdf.cell(cw / 2, 6, f"№ {order.get('order_id', '—')}")
    pdf.set_font("D", "", 9); pdf.set_text_color(*SUB)
    pdf.set_xy(ML + cw / 2, y); pdf.cell(cw / 2, 6, _fmt_dt(order.get("timestamp")), align="R")

    # ── items + total panel ────────────────────────────────────────────────────
    y += 9
    items = order.get("items", []) or []
    ph = 7 + 6.5 * len(items) + 13
    pdf.set_fill_color(*PANEL); pdf.rect(ML, y, cw, ph, style="F")
    iy = y + 6
    for it in items:
        nm = str(it.get("name", ""))
        qty = it.get("qty", 1)
        lt = it.get("line_total", it.get("price", 0) * qty)
        pdf.set_font("D", "", 9); pdf.set_text_color(*TEXT)
        pdf.set_xy(ML + 5, iy); pdf.cell(cw * 0.6, 5, nm[:36])
        pdf.set_font("D", "", 9); pdf.set_text_color(*SUB)
        pdf.set_xy(ML + cw * 0.55, iy); pdf.cell(cw * 0.4 - 5, 5, f"×{qty}    {lt} AED", align="R")
        iy += 6.5
    iy += 1
    line(ML + 5, iy, ML + cw - 5)
    iy += 3
    pdf.set_font("D", "B", 11); pdf.set_text_color(*TEXT)
    pdf.set_xy(ML + 5, iy); pdf.cell(cw * 0.5, 6, "Итого")
    pdf.set_font("D", "B", 12); pdf.set_text_color(*GOLDL)
    pdf.set_xy(ML + cw * 0.5 - 5, iy); pdf.cell(cw * 0.5, 6, f"{order.get('total', 0)} AED", align="R")

    # ── payment block ──────────────────────────────────────────────────────────
    y += ph + 6
    payh = 27
    pdf.set_fill_color(*PAYBG); pdf.rect(ML, y, cw, payh, style="F")
    pdf.set_draw_color(*GOLD); pdf.set_line_width(0.35); pdf.rect(ML, y, cw, payh)
    pdf.set_font("D", "B", 9.5); pdf.set_text_color(*GOLD)
    pdf.set_xy(ML + 6, y + 4.5); pdf.cell(cw - 12, 5, "ОПЛАЧЕНО КРИПТОЙ")
    pdf.set_font("D", "B", 17); pdf.set_text_color(*GOLDL)
    pdf.set_xy(ML + 6, y + 10); pdf.cell(cw - 12, 9, f"{_amt(order.get('crypto_amount_usdt'))} USDT")
    pdf.set_font("D", "", 8.5); pdf.set_text_color(*GREEN)
    pdf.set_xy(ML + 6, y + 19.5); pdf.cell(cw - 12, 5, "✓  Подтверждено · TRON (TRC-20)")

    # ── transaction details ────────────────────────────────────────────────────
    y += payh + 7
    pdf.set_font("D", "B", 9); pdf.set_text_color(*GOLD)
    pdf.set_xy(ML, y); pdf.cell(cw, 5, "Детали транзакции")
    y += 7

    def kv(label, val, mono=True):
        nonlocal y
        pdf.set_font("D", "", 7); pdf.set_text_color(*SUB)
        pdf.set_xy(ML, y); pdf.cell(cw, 3.6, label); y += 3.8
        pdf.set_font("M" if mono else "D", "", 8); pdf.set_text_color(*TEXT)
        pdf.set_xy(ML, y); pdf.multi_cell(cw, 4, str(val)); y = pdf.get_y() + 2.3

    kv("Хэш транзакции (TXID)", order.get("crypto_txid", "—"))
    if from_address:
        kv("Отправитель", from_address)
    kv("Получатель", to_address or "—")

    # ── footer ─────────────────────────────────────────────────────────────────
    fy = 192
    line(ML, fy, W - MR)
    pdf.set_font("D", "", 7); pdf.set_text_color(*SUB)
    pdf.set_xy(ML, fy + 3); pdf.cell(cw, 4, "Проверить в блокчейне на tronscan.org", align="C")
    pdf.set_font("D", "B", 8.5); pdf.set_text_color(*GOLD)
    pdf.set_xy(ML, fy + 9.5); pdf.cell(cw, 4, "AMBAR · Спасибо за заказ", align="C")

    return bytes(pdf.output())


if __name__ == "__main__":
    sample = {
        "order_id": "AMB2882150",
        "timestamp": "2026-06-10T16:38:00+00:00",
        "items": [
            {"name": "Absolut 1 ltr", "qty": 1, "line_total": 95},
            {"name": "Beluga 0.7 ltr", "qty": 2, "line_total": 480},
        ],
        "total": 575,
        "crypto_amount_usdt": 1.01,
        "crypto_txid": "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
    }
    out = build_receipt(
        sample,
        to_address="TVqC6bZvsTuBtvLxF9GGJnVYGLBcg7biNj",
        from_address="TJYeasZ7yvN5h8sGq2c4rN9oH1k2m3P4Qx",
    )
    Path("/tmp/receipt_sample.pdf").write_bytes(out)
    print(f"wrote /tmp/receipt_sample.pdf ({len(out)} bytes)")
