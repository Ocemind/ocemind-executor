"""文档解析：PDF / Word / Excel / 纯文本 → Markdown。

不依赖 pandas / openpyxl：xlsx 和 docx 本质都是 zip + XML，标准库直接读，
这样打包体积和出错面都小得多。PDF 需要 pypdf（纯 Python，无编译依赖）。
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .base import Engine, fail, ok

SUPPORTED = {".xlsx", ".docx", ".pdf", ".csv", ".txt", ".md", ".jsonl", ".json"}

NS_XLSX = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# ---------------------------------------------------------------- Excel

def _col_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref or "")
    if not letters:
        return 0
    idx = 0
    for ch in letters.group(1):
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def read_xlsx(path: Path) -> list[tuple[str, list[list[str]]]]:
    sheets: list[tuple[str, list[list[str]]]] = []
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{NS_XLSX}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{NS_XLSX}t")))

        rels: dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in names:
            root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            for rel in root:
                rels[rel.get("Id", "")] = rel.get("Target", "")

        sheet_files: list[tuple[str, str]] = []
        if "xl/workbook.xml" in names:
            root = ET.fromstring(zf.read("xl/workbook.xml"))
            for sheet in root.iter(f"{NS_XLSX}sheet"):
                rid = sheet.get(f"{NS_REL}id", "")
                target = rels.get(rid, "")
                target = target.lstrip("/")
                if not target.startswith("xl/"):
                    target = "xl/" + target
                sheet_files.append((sheet.get("name", "Sheet"), target))

        if not sheet_files:
            sheet_files = [(n.rsplit("/", 1)[-1], n) for n in sorted(names)
                           if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]

        for title, target in sheet_files:
            if target not in names:
                continue
            root = ET.fromstring(zf.read(target))
            rows: list[list[str]] = []
            for row in root.iter(f"{NS_XLSX}row"):
                cells: dict[int, str] = {}
                for cell in row.iter(f"{NS_XLSX}c"):
                    ref = cell.get("r", "")
                    kind = cell.get("t", "")
                    value = ""
                    if kind == "inlineStr":
                        value = "".join(t.text or "" for t in cell.iter(f"{NS_XLSX}t"))
                    else:
                        v = cell.find(f"{NS_XLSX}v")
                        if v is not None and v.text is not None:
                            value = v.text
                            if kind == "s":
                                try:
                                    value = shared[int(value)]
                                except (ValueError, IndexError):
                                    pass
                    cells[_col_index(ref)] = value
                if cells:
                    width = max(cells) + 1
                    rows.append([cells.get(i, "") for i in range(width)])
            sheets.append((title, rows))
    return sheets


def sheets_to_md(sheets, source: str) -> str:
    parts = [f"# {source}\n"]
    for title, rows in sheets:
        parts.append(f"\n## {title}\n")
        if not rows:
            parts.append("（空表）\n")
            continue
        width = max(len(r) for r in rows)
        header = [c or f"列{i + 1}" for i, c in enumerate(rows[0] + [""] * (width - len(rows[0])))]
        parts.append("| " + " | ".join(header) + " |")
        parts.append("|" + "|".join(["---"] * width) + "|")
        for row in rows[1:]:
            padded = row + [""] * (width - len(row))
            parts.append("| " + " | ".join(str(c).replace("|", "\\|") for c in padded) + " |")
        parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------- Word

def read_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    lines: list[str] = []
    for para in root.iter(f"{NS_W}p"):
        text = "".join(node.text or "" for node in para.iter(f"{NS_W}t"))
        if text.strip():
            style = para.find(f"{NS_W}pPr/{NS_W}pStyle")
            level = 0
            if style is not None:
                match = re.search(r"(\d)", style.get(f"{NS_W}val", ""))
                if match:
                    level = int(match.group(1))
            lines.append(("#" * min(level + 1, 6) + " " + text) if level else text)
    return "\n\n".join(lines)


# ---------------------------------------------------------------- PDF

def read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError("PDF 需要 pypdf：pip install pypdf") from exc
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        if text.strip():
            pages.append(f"## 第 {i} 页\n\n{text.strip()}")
    return "\n\n".join(pages)


# ---------------------------------------------------------------- 引擎

class DocParse(Engine):
    id = "doc.parse"
    kinds = ("document",)

    def run(self, ctx) -> dict:
        src = Path(ctx.inputs.get("path") or "")
        if not src.exists():
            return fail("文件不存在")
        suffix = src.suffix.lower()
        if suffix not in SUPPORTED:
            return fail(f"不支持的文档类型: {suffix}")

        ctx.progress(None, "解析中")
        try:
            if suffix == ".xlsx":
                content = sheets_to_md(read_xlsx(src), src.stem)
            elif suffix == ".docx":
                content = f"# {src.stem}\n\n" + read_docx(src)
            elif suffix == ".pdf":
                content = f"# {src.stem}\n\n" + read_pdf(src)
            elif suffix in (".csv",):
                text = src.read_text(encoding="utf-8", errors="replace")
                rows = list(csv.reader(io.StringIO(text)))
                content = sheets_to_md([(src.stem, rows)], src.stem)
            elif suffix in (".json", ".jsonl"):
                content = _json_to_md(src)
            else:
                content = src.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return fail(f"{type(exc).__name__}: {exc}")

        if not content.strip():
            return fail("没有解析出内容（可能是扫描件，需要走 OCR）")

        out_dir = Path(ctx.params.get("dir") or Path(ctx.config.home) / "documents")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / (ctx.params.get("name") or f"{src.stem}.md")
        if out.resolve() == src.resolve():
            # 输入的本来就是 .md，默认名会把源文件覆盖掉——加个后缀避开
            out = out_dir / f"{src.stem}.parsed{src.suffix}"
        out.write_text(content, encoding="utf-8")
        return ok(out)


def _json_to_md(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if path.suffix.lower() == ".jsonl":
        items = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    else:
        items = json.loads(text)
    if isinstance(items, list) and items and isinstance(items[0], dict):
        keys = list(items[0].keys())
        rows = [keys] + [[str(item.get(k, "")) for k in keys] for item in items]
        return sheets_to_md([("data", rows)], path.stem)
    return "```json\n" + json.dumps(items, ensure_ascii=False, indent=2) + "\n```\n"
