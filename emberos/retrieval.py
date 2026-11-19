"""Small lexical ranker shared by SQLite notes and bounded conversation recall."""

from dataclasses import asdict, dataclass
import re


_VISUAL_ARTIFACT = re.compile(r"\b(?:mural|poster|illustration|mockup|infographic|logo|diagram)s?\b", re.I)
_DESIGN_DETAILS = frozenset("paint ink font fonts lettering color colors colour colours palette layout design typography".split())
_STYLE_INSTRUCTION = re.compile(
    r"(?:use|choose|approve|change|increase|decrease|adjust|keep)\s+"
    r"(?:[a-z-]+\s+){0,5}[a-z-]+[.!]?", re.I,
)
_ALIASES = {
    "bicycle": frozenset({"bicycle", "bicycles", "bike", "bikes"}),
    "ceramic": frozenset({"ceramic", "ceramics", "potter", "pottery", "clay"}),
    "kiln": frozenset({"kiln", "oven"}),
    "watering": frozenset({"irrigate", "irrigation", "water", "watering"}),
    "schedule": frozenset({"calendar", "routine", "schedule"}),
    "funding": frozenset({"award", "bursary", "fellowship", "grant", "scholarship", "scholarships"}),
    "deadline": frozenset({"date", "deadline", "due", "expiration", "expiry"}),
}
_ALIAS_OWNER = {word: concept for concept, values in _ALIASES.items() for word in values}
_WEEKLY_ROUTINE = re.compile(
    r"\bevery\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I
)


def words(text):
    return set(re.findall(r"[^\W_]+", text.casefold()))


def normalized_words(text):
    tokens = words(text)
    result = {_ALIAS_OWNER.get(token, token) for token in tokens}
    if _WEEKLY_ROUTINE.search(text):
        result.add("schedule")
    return result


def candidate_words(terms):
    return set().union(*(_ALIASES.get(term, {term}) for term in terms))


def _snippet(text, query, limit=240):
    compact = re.sub(r"\s+", " ", str(text)).strip()
    if len(compact) <= limit:
        return compact
    folded = compact.casefold()
    positions = [folded.find(term) for term in normalized_words(query) if folded.find(term) >= 0]
    start = max(0, min(positions) - limit // 4) if positions else 0
    end = min(len(compact), start + limit)
    if start and start < end:
        boundary = compact.find(" ", max(0, start - 1), min(end, start + 24))
        if boundary >= 0:
            start = boundary + 1
    if end < len(compact):
        boundary = compact.rfind(" ", max(start, end - 24), end)
        if boundary > start + limit // 2:
            end = boundary
    return ("…" if start else "") + compact[start:end].strip() + ("…" if end < len(compact) else "")


@dataclass(frozen=True)
class RetrievalHit:
    """One provenance-bearing result shared by note and history retrieval."""

    source: str
    record_id: int
    title: str
    timestamp: str
    snippet: str
    score: float
    rank: int
    status: str = "matched"

    def to_dict(self):
        return asdict(self)


def retrieval_hit(source, record_id, title, timestamp, text, score, rank, query="", *, record_status=None):
    hit = RetrievalHit(
        source=str(source), record_id=int(record_id), title=str(title)[:200],
        timestamp=str(timestamp or ""), snippet=_snippet(text, query),
        score=float(score), rank=int(rank),
    ).to_dict()
    if record_status is not None:
        hit["record_status"] = str(record_status)
    return hit


def retrieval_metadata(source, query, scope, results):
    results = list(results)
    status = "matches" if results else "no_matches"
    return {"source": source, "query": query, "scope": scope,
            "status": status, "retrieval": status, "results": results}


def incidental_visual_reference(terms, request, arguments, answer):
    """Detect a subject used only as a modifier of a visual artifact in a saved turn."""
    artifact = _VISUAL_ARTIFACT.search(request)
    if not artifact or terms & _DESIGN_DETAILS or not _STYLE_INSTRUCTION.fullmatch(answer.strip()):
        return False
    # In "review the X poster", X describes the artwork. Only suppress X
    # when the reply concerns styling and neither reply nor arguments support X.
    modifiers = re.findall(r"[^\W_]+", request[:artifact.start()].casefold())[-len(terms):]
    if terms != set(modifiers) or terms & words(request[artifact.start():]):
        return False
    response_words = words(answer)
    return bool(response_words & _DESIGN_DETAILS) and not terms & (response_words | words(arguments))


def relevance(terms, primary, secondary, body):
    """Favor term coverage, then field importance and density within the primary field."""
    title = normalized_words(primary)
    tags = normalized_words(secondary)
    content = normalized_words(body)
    covered = len(terms & (title | tags | content))
    # Require all terms for short queries and at least two thirds otherwise.
    if not covered or covered * 3 < len(terms) * 2:
        return 0
    primary_hits = len(terms & title)
    weighted = 4 * primary_hits + 2 * len(terms & tags) + len(terms & content)
    return covered * (7 * len(terms) + 1) + weighted + primary_hits / (len(title) + 1)
