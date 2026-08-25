import asyncio
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from uuid import NAMESPACE_URL, UUID, uuid5

from app.assets.policy import can_read_resource
from app.assets.schemas import DataScope, SecurityLevel
from app.identity.schemas import Principal
from app.knowledge.schemas import KnowledgeDocumentStatus, KnowledgeSearchFilters
from app.knowledge.service_types import UploadedFile

CHUNK_SIZE = 600
CHUNK_OVERLAP = 80
_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]", re.IGNORECASE)
_SUPPORTED_TEXT_TYPES = frozenset(
    {"text/plain", "text/markdown", "text/x-markdown", "text/html", "application/xhtml+xml"}
)
_SUPPORTED_EXTENSIONS = frozenset({"txt", "md", "markdown", "html", "htm"})


class DocumentExtractionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    tenant_id: UUID
    knowledge_base_id: UUID
    document_id: UUID
    asset_id: UUID
    creator_id: UUID
    owner_department_id: str
    project_id: str | None
    data_scope: DataScope
    security_level: SecurityLevel
    chunk_id: UUID
    title: str
    ordinal: int
    text: str


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: IndexedChunk
    score: float


def extract_document(uploaded: UploadedFile) -> ExtractedDocument:
    mime_type = uploaded.content_type.lower().split(";", 1)[0].strip()
    extension = uploaded.filename.rsplit(".", 1)[-1].lower() if "." in uploaded.filename else ""
    if uploaded.content.startswith(b"%PDF-"):
        raise DocumentExtractionError(
            "UNSUPPORTED_DOCUMENT_TYPE",
            "PDF text extraction is not available in the offline phase-one index",
        )
    if mime_type not in _SUPPORTED_TEXT_TYPES and extension not in _SUPPORTED_EXTENSIONS:
        raise DocumentExtractionError(
            "UNSUPPORTED_DOCUMENT_TYPE",
            f"No offline text extractor is available for {mime_type or 'unknown content type'}",
        )
    try:
        decoded = uploaded.content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise DocumentExtractionError(
            "DOCUMENT_ENCODING_INVALID",
            "Phase-one text documents must use UTF-8 encoding",
        ) from exc
    if _looks_binary(decoded):
        raise DocumentExtractionError(
            "DOCUMENT_BINARY_CONTENT",
            "Document contains binary control bytes and cannot be indexed as text",
        )

    is_html = mime_type in {"text/html", "application/xhtml+xml"} or extension in {"html", "htm"}
    text = _html_to_text(decoded) if is_html else decoded
    normalized = normalize_text(text)
    if not normalized:
        raise DocumentExtractionError("DOCUMENT_TEXT_EMPTY", "Document contains no indexable text")
    title = uploaded.filename.rsplit(".", 1)[0].strip() or uploaded.filename
    return ExtractedDocument(title=title, text=normalized)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in normalized.split("\n")]
    output: list[str] = []
    blank = False
    for line in lines:
        if line:
            output.append(line)
            blank = False
        elif output and not blank:
            output.append("")
            blank = True
    return "\n".join(output).strip()


def chunk_text(text: str, *, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if size < 1 or overlap < 0 or overlap >= size:
        raise ValueError("chunk size must be positive and overlap must be in [0, size)")
    normalized = normalize_text(text)
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        hard_end = min(start + size, len(normalized))
        end = hard_end
        if hard_end < len(normalized):
            boundary = max(
                normalized.rfind("\n", start + size // 2, hard_end),
                normalized.rfind(" ", start + size // 2, hard_end),
            )
            if boundary > start:
                end = boundary
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


class InMemoryKnowledgeIndex:
    """Tenant and knowledge-base scoped deterministic lexical index."""

    def __init__(self) -> None:
        self._chunks: dict[tuple[UUID, UUID], list[IndexedChunk]] = {}
        self._lock = asyncio.Lock()

    async def replace_document(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        document_id: UUID,
        asset_id: UUID,
        creator_id: UUID,
        owner_department_id: str,
        project_id: str | None,
        data_scope: DataScope,
        security_level: SecurityLevel,
        title: str,
        chunks: list[str],
    ) -> list[IndexedChunk]:
        indexed = [
            IndexedChunk(
                tenant_id=tenant_id,
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                asset_id=asset_id,
                creator_id=creator_id,
                owner_department_id=owner_department_id,
                project_id=project_id,
                data_scope=data_scope,
                security_level=security_level,
                chunk_id=uuid5(
                    NAMESPACE_URL,
                    f"knowledge:{tenant_id}:{knowledge_base_id}:{document_id}:{ordinal}:{text}",
                ),
                title=title,
                ordinal=ordinal,
                text=text,
            )
            for ordinal, text in enumerate(chunks)
        ]
        key = (tenant_id, knowledge_base_id)
        async with self._lock:
            existing = [item for item in self._chunks.get(key, []) if item.document_id != document_id]
            self._chunks[key] = existing + indexed
        return indexed

    async def search(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        query: str,
        top_k: int,
        subject_id: UUID,
        department_ids: frozenset[str],
        project_ids: frozenset[str],
        security_clearance: str,
        filters: KnowledgeSearchFilters | None = None,
    ) -> list[ScoredChunk]:
        query_tokens = Counter(_tokens(query))
        if not query_tokens:
            return []
        candidates = [
            chunk
            for chunk in self._chunks.get((tenant_id, knowledge_base_id), [])
            if can_read_resource(
                Principal(
                    user_id=subject_id,
                    tenant_id=tenant_id,
                    display_name="Knowledge search subject",
                    department_ids=department_ids,
                    project_ids=project_ids,
                    security_clearance=security_clearance,
                ),
                creator_id=chunk.creator_id,
                owner_department_id=chunk.owner_department_id,
                project_id=chunk.project_id,
                data_scope=chunk.data_scope,
                security_level=chunk.security_level,
            )
            and _matches_filters(chunk, filters)
        ]
        scored = [
            ScoredChunk(chunk=chunk, score=score)
            for chunk in candidates
            if (score := _lexical_score(query_tokens, chunk.text)) > 0
        ]
        scored.sort(key=lambda item: (-item.score, item.chunk.ordinal, str(item.chunk.chunk_id)))
        return scored[:top_k]


def _tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN_PATTERN.finditer(normalize_text(text))]


def _lexical_score(query_tokens: Counter[str], text: str) -> float:
    document_tokens = Counter(_tokens(text))
    matched = sum(min(count, document_tokens[token]) for token, count in query_tokens.items())
    if matched == 0:
        return 0.0
    coverage = matched / sum(query_tokens.values())
    density = matched / max(sum(document_tokens.values()), 1)
    return round(min(1.0, coverage * 0.85 + density * 0.15), 6)


def _matches_filters(chunk: IndexedChunk, filters: KnowledgeSearchFilters | None) -> bool:
    if filters is None:
        return True
    if filters.document_status not in {None, KnowledgeDocumentStatus.INDEXED}:
        return False
    if filters.document_ids and chunk.document_id not in filters.document_ids:
        return False
    if filters.asset_ids and chunk.asset_id not in filters.asset_ids:
        return False
    if filters.data_scopes and chunk.data_scope not in filters.data_scopes:
        return False
    if filters.security_levels and chunk.security_level not in filters.security_levels:
        return False
    if filters.title_contains and normalize_text(filters.title_contains).casefold() not in chunk.title.casefold():
        return False
    return True


def _looks_binary(text: str) -> bool:
    controls = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
    return "\x00" in text or controls > max(1, len(text) // 100)


class _VisibleHTMLParser(HTMLParser):
    _BLOCKS = frozenset(
        {"article", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "section", "tr"}
    )
    _HIDDEN = frozenset({"script", "style", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.casefold()
        if lowered in self._HIDDEN:
            self._hidden_depth += 1
        elif lowered in self._BLOCKS and self.parts:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in self._HIDDEN:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        elif lowered in self._BLOCKS and self.parts:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._hidden_depth == 0:
            self.parts.append(data)


def _html_to_text(source: str) -> str:
    parser = _VisibleHTMLParser()
    parser.feed(source)
    parser.close()
    return "".join(parser.parts)
