"""Small lexical ranker shared by SQLite notes and bounded conversation recall."""

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
