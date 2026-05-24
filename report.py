from __future__ import annotations
"""Monthly PDF spending report — generation, upload, delivery."""
import io
import json
import os
import threading
from datetime import datetime, date
from collections import Counter

import config
import database
import sheets

# ── Colours & constants ─────────────────────────────────────────────────────

_GREEN  = "#27AE60"
_RED    = "#C0392B"
_AMBER  = "#E67E22"
_NAVY   = "#1A2744"
_LIGHT  = "#F4F6F9"
_WHITE  = "#FFFFFF"
_GREY   = "#7F8C8D"
_BLACK  = "#2C3E50"

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


# ── Public entry points ─────────────────────────────────────────────────────

def trigger_report_async(year: int, month: int):
    """
    Send an immediate 'generating…' WhatsApp ack, then build and deliver
    the PDF in a background thread.
    """
    _notify_ava(f"On it — generating your {_month_label(month, year)} spending report. "
                f"I'll send you the link in a moment.")
    t = threading.Thread(target=_run_report, args=(year, month), daemon=True)
    t.start()


def generate_and_deliver(year: int, month: int):
    """Blocking version — used by the scheduler (already in a background job)."""
    _run_report(year, month)


# ── Internal orchestrator ───────────────────────────────────────────────────

def _run_report(year: int, month: int):
    try:
        data         = _gather_data(year, month)
        observations = _generate_observations(data)
        pdf_bytes    = _build_pdf(data, observations)
        filename     = f"Spending_Report_{_month_label(month, year).replace(' ', '_')}.pdf"

        download_url = _upload_supabase(pdf_bytes, filename)
        _upload_drive(pdf_bytes, filename)
        _write_sheet_actuals(year, month, data["totals"])

        label   = _month_label(month, year)
        net     = data["total_spent"] - data["total_budget"]
        verdict = (f"under budget by ${abs(net):.0f}" if net <= 0
                   else f"over budget by ${net:.0f}")

        if download_url:
            msg = (f"Your {label} spending report is ready.\n"
                   f"{download_url}\n\n"
                   f"You came in {verdict} this month.")
        else:
            msg = (f"Your {label} spending report was generated but the upload failed "
                   f"(check your Supabase 'reports' bucket). "
                   f"You came in {verdict} this month.")
        _notify_ava(msg)
    except Exception as e:
        print(f"Report generation error: {e}")
        _notify_ava("Something went wrong generating your report. Check the server logs.")


# ── Data gathering ──────────────────────────────────────────────────────────

def _gather_data(year: int, month: int) -> dict:
    rows        = database.get_monthly_spend(year, month)
    totals: dict[str, float] = {}
    for r in rows:
        totals[r["category"]] = totals.get(r["category"], 0.0) + float(r["amount"])

    # Previous month
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    prev_rows  = database.get_monthly_spend(prev_year, prev_month)
    prev_totals: dict[str, float] = {}
    for r in prev_rows:
        prev_totals[r["category"]] = prev_totals.get(r["category"], 0.0) + float(r["amount"])
    has_prev = bool(prev_rows)

    # Top 10 transactions
    top10 = sorted(rows, key=lambda r: float(r["amount"]), reverse=True)[:10]

    total_budget = sum(config.BUDGET_TARGETS.values())
    total_spent  = sum(totals.values())

    # Budget health score
    scored = [c for c in config.BUDGET_TARGETS if totals.get(c, 0) > 0]
    if scored:
        health = round(
            sum(min(100, 100 * config.BUDGET_TARGETS[c] / totals[c])
                for c in scored) / len(scored)
        )
    else:
        health = 100

    # Merchant flags
    merchant_counts: Counter = Counter(r["description"] for r in rows if r["description"])
    repeat_merchants = {m: n for m, n in merchant_counts.items() if n >= 3}

    big_single: list[dict] = []
    for r in rows:
        budget = config.BUDGET_TARGETS.get(r["category"], 0)
        if budget and float(r["amount"]) > 0.15 * budget:
            big_single.append(r)

    culprits: list[dict] = []
    for cat, budget in config.BUDGET_TARGETS.items():
        if totals.get(cat, 0) > budget:
            cat_rows = [r for r in rows if r["category"] == cat]
            if cat_rows:
                worst = max(cat_rows, key=lambda r: float(r["amount"]))
                culprits.append({"category": cat, "transaction": worst})

    return {
        "year": year, "month": month,
        "rows": rows, "totals": totals,
        "prev_totals": prev_totals, "has_prev": has_prev,
        "top10": top10,
        "total_budget": total_budget, "total_spent": total_spent,
        "health_score": health,
        "repeat_merchants": repeat_merchants,
        "big_single": big_single,
        "culprits": culprits,
    }


# ── Claude observations ─────────────────────────────────────────────────────

def _generate_observations(data: dict) -> list[str]:
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    lines = []
    for cat, budget in config.BUDGET_TARGETS.items():
        actual = data["totals"].get(cat, 0)
        if actual:
            pct = actual / budget * 100
            lines.append(f"  {cat.replace('_', ' ').title()}: ${actual:.0f} / ${budget} ({pct:.0f}%)")

    flag_lines = []
    for r in data["big_single"][:3]:
        flag_lines.append(
            f"  ${float(r['amount']):.0f} at {r['description']} "
            f"({r['category'].replace('_', ' ')}) was >15% of category budget"
        )
    for merchant, count in list(data["repeat_merchants"].items())[:3]:
        flag_lines.append(f"  {merchant} appeared {count} times")
    for c in data["culprits"][:2]:
        t = c["transaction"]
        flag_lines.append(
            f"  Largest charge in over-budget {c['category']}: "
            f"${float(t['amount']):.0f} at {t['description']}"
        )

    prompt = (
        f"Here is Ava's spending data for {_month_label(data['month'], data['year'])}:\n\n"
        f"Category actuals vs budget:\n" + "\n".join(lines) + "\n\n"
        f"Notable flags:\n" + ("\n".join(flag_lines) if flag_lines else "  None") + "\n\n"
        f"Total spent: ${data['total_spent']:.0f} of ${data['total_budget']} budget.\n\n"
        f"Write 3–5 specific, direct observations about this spending. "
        f"Name actual merchants and dollar amounts. "
        f"Frame as observations not judgments. "
        f"Tone: direct, like a financially responsible older sister. "
        f"Return only the bullet points, one per line, each starting with '• '."
    )

    resp = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text
    bullets = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("•")]
    return bullets or ["• No observations generated."]


# ── PDF builder ─────────────────────────────────────────────────────────────

def _build_pdf(data: dict, observations: list[str]) -> bytes:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether,
    )
    from reportlab.platypus.flowables import HRFlowable
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.graphics import renderPDF

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )

    # ── Styles ──────────────────────────────────────────────────────────────
    styles = getSampleStyleSheet()
    def sty(name, **kw):
        s = styles["Normal"].clone(name)
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    navy_c   = colors.HexColor(_NAVY)
    green_c  = colors.HexColor(_GREEN)
    red_c    = colors.HexColor(_RED)
    amber_c  = colors.HexColor(_AMBER)
    light_c  = colors.HexColor(_LIGHT)
    grey_c   = colors.HexColor(_GREY)

    h1   = sty("H1",   fontSize=22, leading=28, textColor=navy_c,  fontName="Helvetica-Bold")
    h2   = sty("H2",   fontSize=13, leading=17, textColor=navy_c,  fontName="Helvetica-Bold", spaceBefore=14, spaceAfter=4)
    sub  = sty("Sub",  fontSize=10, leading=13, textColor=grey_c,  fontName="Helvetica")
    body = sty("Body", fontSize=9,  leading=13, textColor=colors.HexColor(_BLACK), fontName="Helvetica")
    bull = sty("Bull", fontSize=9,  leading=14, textColor=colors.HexColor(_BLACK), fontName="Helvetica", leftIndent=10, spaceAfter=3)
    big  = sty("Big",  fontSize=28, leading=34, textColor=navy_c,  fontName="Helvetica-Bold")
    med  = sty("Med",  fontSize=11, leading=15, textColor=colors.HexColor(_BLACK), fontName="Helvetica")

    label = _month_label(data["month"], data["year"])
    story = []

    # ── Header ───────────────────────────────────────────────────────────────
    story.append(Paragraph(f"Monthly Spending Report — {label}", h1))
    story.append(Paragraph("Ava Kahn — LMU Law School Budget Tracker", sub))
    story.append(Paragraph(f"Generated {date.today().strftime('%B %d, %Y')}", sub))
    story.append(HRFlowable(width="100%", thickness=2, color=navy_c, spaceAfter=10))

    # ── Section 1: Executive Summary ────────────────────────────────────────
    story.append(Paragraph("Executive Summary", h2))

    net   = data["total_spent"] - data["total_budget"]
    color = green_c if net <= 0 else red_c
    sign  = "Under" if net <= 0 else "Over"
    verdict = f"{sign} budget by ${abs(net):.0f}"

    summary_data = [
        [
            Paragraph(f"${data['total_spent']:.0f}", big),
            Paragraph(f"${data['total_budget']:.0f}", big),
            Paragraph(f"${abs(net):.0f}", big),
        ],
        [
            Paragraph("Total Spent", sub),
            Paragraph("Total Budget", sub),
            Paragraph(sign, sty("VerdSub", fontSize=10, leading=13,
                                textColor=color, fontName="Helvetica-Bold")),
        ],
    ]
    summary_table = Table(summary_data, colWidths=[2.1 * inch, 2.1 * inch, 2.1 * inch])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), light_c),
        ("BOX",        (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
        ("INNERGRID",  (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
        ("ALIGN",      (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR",  (2, 0), (2, 0), color),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Budget Health Score: <b>{data['health_score']}/100</b>   "
        f"({sum(1 for c in config.BUDGET_TARGETS if data['totals'].get(c, 0) <= config.BUDGET_TARGETS[c])} of "
        f"{len(config.BUDGET_TARGETS)} categories on track)",
        med,
    ))
    story.append(Spacer(1, 4))

    # ── Section 2: Spending by Category ─────────────────────────────────────
    story.append(Paragraph("Spending by Category", h2))

    cat_header = ["Category", "Budget", "Actual", "Variance", "Status"]
    cat_rows   = [cat_header]
    chart_cats, chart_budgets, chart_actuals = [], [], []

    for cat, budget in config.BUDGET_TARGETS.items():
        actual  = data["totals"].get(cat, 0.0)
        var     = actual - budget
        pct     = actual / budget * 100 if budget else 0
        if pct >= 100:
            status, row_bg = "⚠ Over", colors.HexColor("#FDECEA")
        elif pct >= 90:
            status, row_bg = "~ Near", colors.HexColor("#FEF3CD")
        else:
            status, row_bg = "✓ OK",  colors.HexColor("#EBF9F1")
        cat_rows.append([
            cat.replace("_", " ").title(),
            f"${budget:,.0f}",
            f"${actual:,.0f}",
            f"{'−' if var < 0 else '+'}${abs(var):,.0f}",
            status,
        ])
        chart_cats.append(cat.replace("_", " ").title()[:12])
        chart_budgets.append(budget)
        chart_actuals.append(actual)

    col_w = [1.8*inch, 0.9*inch, 0.9*inch, 1.0*inch, 0.85*inch]
    ct = Table(cat_rows, colWidths=col_w)
    ts = TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  navy_c),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("ALIGN",         (1, 1), (-1, -1), "RIGHT"),
        ("ALIGN",         (4, 1), (4, -1),  "CENTER"),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light_c]),
    ])
    # Per-row status colouring
    for i, row in enumerate(cat_rows[1:], start=1):
        status_val = row[4]
        bg = (colors.HexColor("#FDECEA") if "Over" in status_val
              else colors.HexColor("#FEF3CD") if "Near" in status_val
              else colors.HexColor("#EBF9F1"))
        ts.add("BACKGROUND", (0, i), (-1, i), bg)
    ct.setStyle(ts)
    story.append(ct)
    story.append(Spacer(1, 10))

    # Bar chart (horizontal, actual vs budget)
    story.append(_build_bar_chart(chart_cats, chart_budgets, chart_actuals))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "<font color='#27AE60'>■</font> Budget   "
        "<font color='#1A2744'>■</font> Actual",
        sty("Legend", fontSize=8, leading=10, textColor=grey_c, fontName="Helvetica"),
    ))

    # ── Section 3: Top 10 Transactions ──────────────────────────────────────
    story.append(Paragraph("Top 10 Transactions", h2))
    txn_header = ["Date", "Merchant", "Amount", "Category"]
    txn_rows   = [txn_header]
    for r in data["top10"]:
        txn_rows.append([
            str(r.get("date", ""))[:10],
            (r.get("description") or "")[:32],
            f"${float(r['amount']):,.2f}",
            r.get("category", "").replace("_", " ").title(),
        ])
    tt = Table(txn_rows, colWidths=[0.9*inch, 2.8*inch, 0.9*inch, 1.1*inch])
    tt.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  navy_c),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 8),
        ("ALIGN",         (2, 1), (2, -1),  "RIGHT"),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light_c]),
    ]))
    story.append(tt)

    # ── Section 4: Notable Observations ─────────────────────────────────────
    story.append(Paragraph("Notable Observations", h2))
    for obs in observations:
        story.append(Paragraph(obs, bull))

    # ── Section 5: Month-over-Month ──────────────────────────────────────────
    if data["has_prev"]:
        story.append(Paragraph("Month-over-Month Comparison", h2))
        prev_label = _prev_month_label(data["month"], data["year"])
        mom_header = ["Category", f"{prev_label} Actual", f"{label} Actual", "Change"]
        mom_rows   = [mom_header]
        for cat, budget in config.BUDGET_TARGETS.items():
            prev_a = data["prev_totals"].get(cat, 0.0)
            curr_a = data["totals"].get(cat, 0.0)
            if prev_a == 0 and curr_a == 0:
                continue
            delta = curr_a - prev_a
            mom_rows.append([
                cat.replace("_", " ").title(),
                f"${prev_a:,.0f}",
                f"${curr_a:,.0f}",
                f"{'▲' if delta > 0 else '▼'} ${abs(delta):,.0f}",
            ])
        mt = Table(mom_rows, colWidths=[1.8*inch, 1.2*inch, 1.2*inch, 1.2*inch])
        mt.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0),  navy_c),
            ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("ALIGN",         (1, 1), (-1, -1), "RIGHT"),
            ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light_c]),
        ]))
        story.append(mt)

    # ── Footer ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=1, color=grey_c))
    months_runway = (config.MONTHLY_NET / data["total_spent"] if data["total_spent"] else 0)
    story.append(Paragraph(
        f"Generated by your Life Coach Agent  |  "
        f"At this spend rate, your loan disbursement covers {months_runway:.1f} months.",
        sty("Footer", fontSize=7.5, leading=11, textColor=grey_c,
            fontName="Helvetica", spaceBefore=4),
    ))

    doc.build(story)
    return buf.getvalue()


def _build_bar_chart(categories: list, budgets: list, actuals: list) -> object:
    """Return a reportlab Drawing with a horizontal bar chart (actual vs budget)."""
    from reportlab.graphics.shapes import Drawing, Rect, String, Line
    from reportlab.lib import colors
    from reportlab.lib.units import inch

    row_h    = 14
    label_w  = 80
    chart_w  = 310
    pad      = 4
    height   = len(categories) * (row_h + pad) + 30
    width    = label_w + chart_w + 20

    d = Drawing(width, height)
    max_val = max(max(budgets), max(actuals), 1)
    scale   = chart_w / max_val

    navy_c  = colors.HexColor(_NAVY)
    green_c = colors.HexColor(_GREEN)
    red_c   = colors.HexColor(_RED)

    for i, (cat, bud, act) in enumerate(zip(categories, budgets, actuals)):
        y = height - 20 - i * (row_h + pad)

        # Label
        d.add(String(label_w - 4, y + 3, cat, fontSize=7,
                     fontName="Helvetica", fillColor=colors.HexColor(_BLACK),
                     textAnchor="end"))

        # Budget bar (light navy, thin)
        bw = bud * scale
        d.add(Rect(label_w, y + row_h // 2, bw, 3,
                   fillColor=navy_c, strokeColor=None))

        # Actual bar
        aw   = act * scale
        fill = red_c if act > bud else green_c
        d.add(Rect(label_w, y, aw, row_h - 2,
                   fillColor=fill, strokeWidth=0, strokeColor=None))

    return d


# ── Supabase upload ─────────────────────────────────────────────────────────
# Requires a public bucket named "reports" in Supabase Storage.
# Create it at: Supabase dashboard → Storage → New bucket → name "reports" → Public

def _upload_supabase(pdf_bytes: bytes, filename: str) -> str | None:
    try:
        db = database.get_client()
        db.storage.from_("reports").upload(
            path=filename,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
        url = db.storage.from_("reports").get_public_url(filename)
        return url
    except Exception as e:
        print(f"Supabase storage upload failed: {e}")
        return None


# ── Google Drive upload ─────────────────────────────────────────────────────

def _upload_drive(pdf_bytes: bytes, filename: str):
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload
        from google.oauth2.service_account import Credentials

        creds_raw = config.GOOGLE_SHEETS_CREDENTIALS
        if os.path.isfile(creds_raw):
            creds = Credentials.from_service_account_file(creds_raw, scopes=DRIVE_SCOPES)
        else:
            creds = Credentials.from_service_account_info(
                json.loads(creds_raw), scopes=DRIVE_SCOPES
            )

        service   = build("drive", "v3", credentials=creds, cache_discovery=False)
        folder_id = _get_or_create_drive_folder(service, "Life Coach Reports")

        service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf"),
            fields="id",
        ).execute()
    except Exception as e:
        print(f"Google Drive upload failed: {e}")


def _get_or_create_drive_folder(service, name: str) -> str:
    q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
         f"and trashed=false")
    res = service.files().list(q=q, fields="files(id)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    folder = service.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    return folder["id"]


# ── Sheet write-back ────────────────────────────────────────────────────────

def _write_sheet_actuals(year: int, month: int, totals: dict[str, float]):
    try:
        sheets.write_month_totals(totals)
    except Exception as e:
        print(f"Sheet write-back failed: {e}")


# ── WhatsApp notification ───────────────────────────────────────────────────

def _notify_ava(message: str):
    try:
        from twilio.rest import Client as TwilioClient
        number = database.get_context("user_whatsapp_number")
        if not number:
            return
        to = f"whatsapp:{number}" if not number.startswith("whatsapp:") else number
        TwilioClient(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN).messages.create(
            from_=f"whatsapp:{config.TWILIO_WHATSAPP_NUMBER}",
            to=to,
            body=message,
        )
    except Exception as e:
        print(f"WhatsApp notify error: {e}")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _month_label(month: int, year: int) -> str:
    return datetime(year, month, 1).strftime("%B %Y")


def _prev_month_label(month: int, year: int) -> str:
    if month == 1:
        return datetime(year - 1, 12, 1).strftime("%B %Y")
    return datetime(year, month - 1, 1).strftime("%B %Y")
