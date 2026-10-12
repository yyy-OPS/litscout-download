"""Synthetic publisher documents for tests; no real literature or user data."""
from io import BytesIO

from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw


def pdf_bytes(title="Cross disciplinary battery discovery", doi="10.1234/example", extra="", image_title=False, blank=False):
    stream = BytesIO()
    canvas = Canvas(stream)
    if image_title:
        image = Image.new("RGB", (900, 65), "white")
        ImageDraw.Draw(image).text((10, 20), title, fill="black")
        canvas.drawImage(ImageReader(image), 30, 735, width=540, height=39)
    elif not blank:
        canvas.drawString(30, 780, title)
        canvas.drawString(30, 760, doi)
    if not blank:
        canvas.drawString(30, 715, "Methods: battery cells and cross-disciplinary measurements. " + extra)
    canvas.showPage()
    if not blank:
        canvas.drawString(30, 780, "Results and discussion: verified full-text evidence. Detailed measurements.")
    canvas.showPage()
    canvas.save()
    return stream.getvalue()
