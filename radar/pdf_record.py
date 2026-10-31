"""Verification record (DRAFT) PDF. Pure: dict in, bytes out."""
import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _s(x):
    return escape(str(x if x is not None else "").encode("latin-1", "replace").decode("latin-1"))


def _table(rows, widths):
    t = Table([[Paragraph(_s(c), getSampleStyleSheet()["BodyText"]) for c in r] for r in rows], colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def build_record(d):
    buf = io.BytesIO()
    ss = getSampleStyleSheet()

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 7)
        canvas.drawString(2 * cm, 1.2 * cm, f"Page {doc.page} | content hash {d['content_hash'][:32]}")
        canvas.drawString(2 * cm, 0.8 * cm, "Draft record. A typed name or timestamp is not a legally binding signature.")

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                            bottomMargin=2 * cm, pageCompression=0)
    f = d["finding"]
    el = [Paragraph("Verification record (DRAFT)", ss["Title"]),
          Paragraph(_s(f"Project: {d['project']}"), ss["Normal"]),
          Paragraph(_s(f"Generated: {d['generated_at']} by {d['generated_by']}"), ss["Normal"]),
          Paragraph(_s(f"Site location (entered by user): {d['site_location']}"), ss["Normal"]), Spacer(1, 10),
          Paragraph("Finding", ss["Heading2"]),
          Paragraph(_s(f"[{f['severity'].upper()}] {f['title']}  (status: {f['status']}, id: {f['fingerprint']})"), ss["Normal"]),
          Spacer(1, 6), Paragraph("Evidence", ss["Heading2"]),
          _table([["Field", "Value"]] + [[k, v] for k, v in f["evidence"].items()], [5 * cm, 11 * cm]),
          Spacer(1, 6), Paragraph("Downstream activities affected", ss["Heading2"])]
    el.append(_table([["ID", "Activity", "Slip (days)"]] + [[a["id"], a["name"], a["slip_days"]] for a in d["affected"]],
                     [2 * cm, 11 * cm, 3 * cm]) if d["affected"] else Paragraph("None identified.", ss["Normal"]))
    el += [Spacer(1, 6), Paragraph("Review history", ss["Heading2"])]
    el.append(_table([["User", "Decision", "Comment", "Time"]] + [[r["user"], r["decision"], r["comment"], r["ts"]] for r in d["reviews"]],
                     [4 * cm, 3 * cm, 6 * cm, 3 * cm]) if d["reviews"] else Paragraph("No reviews yet.", ss["Normal"]))
    el += [Spacer(1, 24), Paragraph("Sign-off", ss["Heading2"]),
           _table([["Role", "Name", "Date", "Signature"], ["Field Engineer", "", "", ""], ["Project Director", "", "", ""]],
                  [4 * cm, 5 * cm, 3 * cm, 4 * cm])]
    doc.build(el, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
