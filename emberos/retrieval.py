"""Small lexical ranker shared by SQLite notes and bounded conversation recall."""

import re


def words(text):
    return set(re.findall(r"[^\W_]+", text.casefold()))


def relevance(terms, primary, secondary, body):
    """Favor term coverage, then field importance and density within the primary field."""
    title = words(primary)
    tags = words(secondary)
    content = words(body)
    covered = len(terms & (title | tags | content))
    # Require all terms for short queries and at least two thirds otherwise.
    if not covered or covered * 3 < len(terms) * 2:
        return 0
    primary_hits = len(terms & title)
    weighted = 4 * primary_hits + 2 * len(terms & tags) + len(terms & content)
    return covered * (7 * len(terms) + 1) + weighted + primary_hits / (len(title) + 1)
