---
name: docx-writer
description: Author a Microsoft Word (.docx) documentation file directly via python-docx — no markdown intermediary. Trigger when the user requests Word output, a .docx artifact, "make me a docx", or when the pipeline passes `--format docx` / `--format both`. The model supplies the document structure as a JSON outline; the skill renders it.
---

# docx-writer

Write a `.docx` file **directly** from a structured outline. Do **not** read a
markdown file and convert it — author the docx natively.

The model owns the content; this skill owns how that content becomes a Word
document.

## Step 1 — ensure python-docx is available

```bash
python3 -c "import docx" 2>/dev/null || pip install --quiet python-docx
```

## Step 2 — write the outline to a JSON file

Build a JSON document with this shape and write it to `outline.json` next to
the target docx. Every block is one of the supported types below.

```json
{
  "title": "Smarty Notifications API",
  "subtitle": "API documentation — v1",
  "blocks": [
    {"type": "heading", "level": 1, "text": "Overview"},
    {"type": "paragraph", "text": "Plain-language description of what the service does."},
    {"type": "heading", "level": 2, "text": "Tech stack"},
    {"type": "bullets", "items": ["Go 1.22", "Fiber v3", "PostgreSQL", "Redis"]},
    {"type": "heading", "level": 1, "text": "API reference"},
    {"type": "heading", "level": 2, "text": "POST /api/v1/notifications"},
    {"type": "paragraph", "text": "Bulk-create notifications for a user."},
    {"type": "table", "headers": ["Field", "Type", "Required", "Description"], "rows": [
      ["user_id", "string", "yes", "Target user ID"],
      ["items",   "array",  "yes", "Notification items (min 1)"]
    ]},
    {"type": "code", "language": "json", "text": "{\n  \"user_id\": \"user-1\",\n  \"items\": []\n}"},
    {"type": "note", "text": "Idempotent on (user_id, notification_id)."}
  ]
}
```

Supported block types:

| `type`      | Required fields                          | Notes                                    |
|-------------|------------------------------------------|------------------------------------------|
| `heading`   | `level` (1–4), `text`                    | Maps to Word Heading 1–4 styles          |
| `paragraph` | `text`                                   | Body paragraph; preserves newlines       |
| `bullets`   | `items` (list of strings)                | Bullet list                              |
| `numbered`  | `items` (list of strings)                | Numbered list                            |
| `table`     | `headers` (list), `rows` (list of lists) | First row is bold header                 |
| `code`      | `text`, optional `language`              | Monospace; intended for JSON / commands  |
| `note`      | `text`                                   | Italicized callout                       |
| `page_break`| —                                        | Forces a new page                        |

Rules for content:
- Use **realistic, consistent example values** across tables and code blocks.
- For mermaid diagrams, emit a `code` block with `language: "mermaid"` — Word
  can't render them, but the reader can paste them into `mermaid.live`.
- Cover the same sections the pipeline asks for (Overview, System context,
  Workflows, Auth, API reference, Error model, Data models, Non-functional).
- One `heading` block per section / endpoint — do not stuff multiple sections
  into one paragraph.

## Step 3 — render the docx

Run this script. Substitute `OUTLINE` and `DST` for the real paths.

```bash
python3 - <<'PY'
import json
from pathlib import Path
from docx import Document
from docx.shared import Pt, RGBColor

OUTLINE = "outline.json"             # ← replace
DST     = "API_DOCUMENTATION.docx"   # ← replace

data = json.loads(Path(OUTLINE).read_text(encoding="utf-8"))
doc = Document()

if data.get("title"):
    doc.add_heading(data["title"], level=0)
if data.get("subtitle"):
    p = doc.add_paragraph()
    r = p.add_run(data["subtitle"])
    r.italic = True

for block in data.get("blocks", []):
    t = block.get("type")

    if t == "heading":
        level = max(1, min(int(block.get("level", 1)), 4))
        doc.add_heading(block.get("text", ""), level=level)

    elif t == "paragraph":
        doc.add_paragraph(block.get("text", ""))

    elif t == "bullets":
        for item in block.get("items", []):
            doc.add_paragraph(str(item), style="List Bullet")

    elif t == "numbered":
        for item in block.get("items", []):
            doc.add_paragraph(str(item), style="List Number")

    elif t == "table":
        headers = block.get("headers", []) or []
        rows = block.get("rows", []) or []
        if not headers and not rows:
            continue
        tbl = doc.add_table(rows=1 + len(rows), cols=max(len(headers), max((len(r) for r in rows), default=0)))
        tbl.style = "Light Grid Accent 1"
        hdr = tbl.rows[0].cells
        for i, h in enumerate(headers):
            hdr[i].text = str(h)
            for run in hdr[i].paragraphs[0].runs:
                run.bold = True
        for i, row in enumerate(rows, start=1):
            cells = tbl.rows[i].cells
            for j, val in enumerate(row):
                cells[j].text = str(val)

    elif t == "code":
        p = doc.add_paragraph()
        run = p.add_run(block.get("text", ""))
        run.font.name = "Courier New"
        run.font.size = Pt(9)

    elif t == "note":
        p = doc.add_paragraph()
        run = p.add_run("Note: " + block.get("text", ""))
        run.italic = True
        run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    elif t == "page_break":
        doc.add_page_break()

    else:
        # Unknown block type — render as a plain paragraph so nothing is silently dropped.
        doc.add_paragraph(json.dumps(block))

doc.save(DST)
print(f"wrote {DST}")
PY
```

## Step 4 — verify

```bash
ls -lh "$DST" && file "$DST"
```

`file` should mention `Microsoft Word` or `Zip archive` (Office Open XML is a
zip container). If the file is missing or zero bytes, surface the error from
the python invocation; do not report success.

## Constraints

- Do not read or convert from a `.md` file — author the outline yourself.
- Do not delete the outline JSON unless instructed; keep it next to the docx
  so the run is reproducible.
- If `python-docx` install fails (offline / restricted), report the error
  clearly and stop.
