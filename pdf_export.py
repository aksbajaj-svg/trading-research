"""Convert the daily markdown report to PDF via WeasyPrint.

Pipeline: markdown -> HTML (with tables/code/fenced extensions) -> PDF.
Embedded CSS handles page size, fonts, table styling, and page breaks.
"""

import os
import markdown
from weasyprint import HTML, CSS

CSS_STYLE = """
@page {
    size: Letter;
    margin: 0.6in 0.6in 0.7in 0.6in;
    @bottom-center {
        content: "Daily Tech Stock Research — Page " counter(page) " of " counter(pages);
        font-family: 'Helvetica', sans-serif;
        font-size: 8pt;
        color: #888;
    }
}
body {
    font-family: 'Helvetica', 'Arial', sans-serif;
    font-size: 9pt;
    line-height: 1.4;
    color: #222;
}
h1 { font-size: 18pt; color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 6px; margin-top: 0; }
h2 { font-size: 13pt; color: #1a1a1a; border-bottom: 1px solid #ccc; padding-bottom: 3px;
     margin-top: 18pt; page-break-after: avoid; }
h3 { font-size: 11pt; color: #333; margin-top: 12pt; page-break-after: avoid; }
h4 { font-size: 10pt; color: #444; margin-top: 10pt; page-break-after: avoid; }
p, li { font-size: 9pt; }
strong { color: #111; }
a { color: #0366d6; text-decoration: none; }
code {
    font-family: 'Menlo', 'Consolas', monospace;
    background: #f4f4f4;
    padding: 1px 4px;
    border-radius: 3px;
    font-size: 8.5pt;
}
pre {
    background: #f4f4f4;
    padding: 8px;
    border-radius: 4px;
    font-size: 8pt;
    overflow-x: auto;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 8pt 0;
    font-size: 8pt;
    page-break-inside: avoid;
}
th {
    background: #2d3748;
    color: white;
    text-align: left;
    padding: 5px 7px;
    font-weight: 600;
    font-size: 8pt;
}
td {
    border: 1px solid #e0e0e0;
    padding: 4px 7px;
    vertical-align: top;
}
tr:nth-child(even) td { background: #fafafa; }
hr { border: none; border-top: 1px solid #ddd; margin: 16pt 0; }
blockquote {
    border-left: 3px solid #999;
    margin: 8pt 0;
    padding: 4pt 10pt;
    color: #555;
    font-style: italic;
}
ul, ol { padding-left: 20pt; }
"""


def markdown_to_pdf(md_path: str, pdf_path: str) -> str:
    with open(md_path) as f:
        md_text = f.read()

    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
    )
    html_doc = f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{html_body}</body></html>"

    HTML(string=html_doc).write_pdf(pdf_path, stylesheets=[CSS(string=CSS_STYLE)])
    return pdf_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 pdf_export.py <input.md> [output.pdf]")
        sys.exit(1)
    md_path = sys.argv[1]
    pdf_path = sys.argv[2] if len(sys.argv) > 2 else md_path.replace(".md", ".pdf")
    out = markdown_to_pdf(md_path, pdf_path)
    print(f"PDF: {out} ({os.path.getsize(out):,} bytes)")
