"""文档解析、清洗与按格式切分。

支持 markdown / text / html / csv / json / jsonl / yaml。
每种格式有专属的解析与切分策略，切分结果均保留 heading 用于引用溯源。
"""
from __future__ import annotations

import csv
import html
import io
import json
import re
from dataclasses import dataclass, field

SUPPORTED_TYPES = ("markdown", "text", "html", "csv", "json", "jsonl", "yaml")


@dataclass
class ParsedSection:
    heading: str
    text: str


@dataclass
class DocumentParseResult:
    title: str
    sections: list[ParsedSection] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    detected_type: str = "text"


_HTML_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_BLOCK_RE = re.compile(
    r"<(h[1-6]|p|li|tr|blockquote|pre|section|article)\b[^>]*>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_CODE_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")


def _clean_text(text: str) -> str:
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    return "\n".join(out).strip()


def detect_content_type(filename: str, raw: str, hint: str = "") -> str:
    """根据文件扩展名与内容特征推断格式。"""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    ext_map = {
        "md": "markdown", "markdown": "markdown", "txt": "text", "log": "text",
        "htm": "html", "html": "html", "csv": "csv", "tsv": "csv",
        "json": "json", "jsonl": "jsonl", "ndjson": "jsonl",
        "yaml": "yaml", "yml": "yaml",
    }
    if hint in SUPPORTED_TYPES:
        return hint
    if ext in ext_map:
        return ext_map[ext]
    stripped = raw.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    if stripped.startswith("<!DOCTYPE html") or stripped.startswith("<html"):
        return "html"
    if re.search(r"^#{1,6}\s+", raw, re.MULTILINE):
        return "markdown"
    if "\t" in raw.split("\n")[0] or "," in raw.split("\n")[0]:
        return "csv"
    return "text"


def parse_document(raw: str, content_type: str = "markdown") -> DocumentParseResult:
    parser = {
        "markdown": _parse_markdown,
        "text": _parse_text,
        "html": _parse_html,
        "csv": _parse_csv,
        "json": _parse_json,
        "jsonl": _parse_jsonl,
        "yaml": _parse_yaml,
    }.get(content_type, _parse_text)
    result = parser(raw)
    result.detected_type = content_type if content_type in SUPPORTED_TYPES else "text"
    return result


def _parse_markdown(raw: str) -> DocumentParseResult:
    result = DocumentParseResult(title="")
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    current_heading = "正文"
    buffer: list[str] = []
    in_code = False

    def flush():
        text = _clean_text("\n".join(buffer))
        if text:
            result.sections.append(ParsedSection(heading=current_heading, text=text))
        buffer.clear()

    for line in lines:
        if _MD_CODE_FENCE_RE.match(line):
            in_code = not in_code
            buffer.append(line)
            continue
        match = _MD_HEADING_RE.match(line) if not in_code else None
        if match:
            flush()
            current_heading = match.group(2).strip()
            if not result.title:
                result.title = current_heading
            continue
        buffer.append(line)
    flush()
    if not result.title:
        result.title = result.sections[0].text.split("\n")[0][:80] if result.sections else "未命名文档"
    return result


def _parse_text(raw: str) -> DocumentParseResult:
    text = _clean_text(raw)
    first = next((line for line in text.split("\n") if line), "未命名文档")
    return DocumentParseResult(title=first[:80], sections=[ParsedSection(heading="正文", text=text)] if text else [])


def _parse_html(raw: str) -> DocumentParseResult:
    result = DocumentParseResult(title="")
    cleaned = _HTML_SCRIPT_RE.sub(" ", raw)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", cleaned, re.IGNORECASE | re.DOTALL)
    if title_match:
        result.title = _clean_text(html.unescape(title_match.group(1)))[:80]
    for match in _HTML_BLOCK_RE.finditer(cleaned):
        tag = match.group(1).lower()
        inner = _HTML_TAG_RE.sub(" ", match.group(2))
        text = _clean_text(html.unescape(inner))
        if not text:
            continue
        heading = tag.upper() if tag.startswith("h") else "正文"
        result.sections.append(ParsedSection(heading=heading, text=text))
        if not result.title:
            result.title = text[:80]
    if not result.sections:
        text = _clean_text(html.unescape(_HTML_TAG_RE.sub(" ", cleaned)))
        if text:
            result.sections.append(ParsedSection(heading="正文", text=text))
            result.title = result.title or text.split("\n")[0][:80]
    return result


def _parse_csv(raw: str) -> DocumentParseResult:
    delimiter = "\t" if "\t" in raw.split("\n")[0] else ","
    reader = csv.reader(io.StringIO(raw), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return DocumentParseResult(title="空表格", sections=[])
    header, data_rows = rows[0], rows[1:]
    result = DocumentParseResult(title="表格数据")
    # 每个 section 包装一批行；切分策略会按 header 分组、批内保留表头。
    for row in data_rows:
        pairs = []
        for index, cell in enumerate(row):
            column = header[index] if index < len(header) else f"列{index + 1}"
            if cell.strip():
                pairs.append(f"{column}: {cell.strip()}")
        if pairs:
            result.sections.append(ParsedSection(heading="表格行", text="\n".join(pairs)))
    return result


def _parse_json(raw: str) -> DocumentParseResult:
    data = json.loads(raw)
    result = DocumentParseResult(title="JSON 数据")
    if isinstance(data, dict):
        for key, value in data.items():
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
            result.sections.append(ParsedSection(heading=str(key), text=text))
    elif isinstance(data, list):
        for index, item in enumerate(data, start=1):
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, indent=2)
            result.sections.append(ParsedSection(heading=f"条目 {index}", text=text))
    else:
        result.sections.append(ParsedSection(heading="值", text=str(data)))
    return result


def _parse_jsonl(raw: str) -> DocumentParseResult:
    result = DocumentParseResult(title="JSONL 数据")
    for index, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        except json.JSONDecodeError:
            text = line
        result.sections.append(ParsedSection(heading=f"记录 {index}", text=text))
    return result


def _parse_yaml(raw: str) -> DocumentParseResult:
    result = DocumentParseResult(title="YAML 数据")
    current_key = "根"
    buffer: list[str] = []
    for line in raw.splitlines():
        top_level = re.match(r"^([A-Za-z_\u4e00-\u9fff][\w\u4e00-\u9fff\-]*)\s*:", line)
        if top_level:
            if buffer:
                result.sections.append(ParsedSection(heading=current_key, text=_clean_text("\n".join(buffer))))
                buffer.clear()
            current_key = top_level.group(1)
        buffer.append(line)
    if buffer:
        result.sections.append(ParsedSection(heading=current_key, text=_clean_text("\n".join(buffer))))
    return result


@dataclass
class Chunk:
    index: int
    heading: str
    content: str


def split_into_chunks(
    parsed: DocumentParseResult,
    chunk_size: int = 400,
    chunk_overlap: int = 60,
) -> list[Chunk]:
    """按格式采用不同切分策略：保留表格行/JSON 条目/YAML 配置块的语义边界。"""
    if chunk_size < 50:
        chunk_size = 50
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        chunk_overlap = chunk_size // 6

    fmt = parsed.detected_type
    chunks: list[Chunk] = []

    def add(heading: str, content: str):
        content = content.strip()
        if content:
            chunks.append(Chunk(index=len(chunks), heading=heading, content=content))

    if fmt == "csv":
        # 表格：按行分组，每组不超过 chunk_size，每行保留"列: 值"结构。
        group: list[str] = []
        for section in parsed.sections:
            candidate = "\n".join(group + [section.text])
            if group and len(candidate) > chunk_size:
                add("表格行", "\n".join(group))
                group = []
            group.append(section.text)
        if group:
            add("表格行", "\n".join(group))
        return chunks

    if fmt in ("json", "jsonl"):
        # JSON/JSONL：每个条目独立成块，超长再滑切但不破坏外层结构。
        for section in parsed.sections:
            if len(section.text) <= chunk_size:
                add(section.heading, section.text)
            else:
                _sliding_add(add, section.heading, section.text, chunk_size, chunk_overlap)
        return chunks

    if fmt == "yaml":
        # YAML：每个顶级配置块独立，超长按行滑切。
        for section in parsed.sections:
            if len(section.text) <= chunk_size:
                add(section.heading, section.text)
            else:
                _sliding_add(add, section.heading, section.text, chunk_size, chunk_overlap, line_aware=True)
        return chunks

    if fmt == "html":
        # HTML：语义块（标题/段落/列表项/表格行）即切分单元，过短的相邻块合并。
        pending: list[ParsedSection] = []
        for section in parsed.sections:
            combined = "\n".join(item.text for item in pending + [section])
            if pending and len(combined) > chunk_size:
                add(pending[0].heading, "\n".join(item.text for item in pending))
                pending = []
            pending.append(section)
        if pending:
            add(pending[0].heading, "\n".join(item.text for item in pending))
        return chunks

    # markdown / text：按段落优先，超长段落滑切。
    for section in parsed.sections:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section.text) if p.strip()]
        for paragraph in paragraphs:
            if len(paragraph) <= chunk_size:
                add(section.heading, paragraph)
            else:
                _sliding_add(add, section.heading, paragraph, chunk_size, chunk_overlap)
    return chunks


def _sliding_add(add, heading: str, text: str, chunk_size: int, chunk_overlap: int, line_aware: bool = False):
    """超长文本滑切，优先在句子/行边界断开。"""
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        window = text[start:end]
        if end < len(text):
            separators = ("\n", "。", "；", "！", "？", "，") if line_aware else ("。", "；", "！", "？", "\n", "，")
            for sep in separators:
                pos = window.rfind(sep)
                if pos > chunk_size // 2:
                    end = start + pos + 1
                    break
        piece = text[start:end].strip()
        if piece:
            add(heading, piece)
        if end >= len(text):
            break
        start = max(start + 1, end - chunk_overlap)
