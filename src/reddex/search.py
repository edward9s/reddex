from __future__ import annotations

import bisect
import re
import sqlite3
from dataclasses import dataclass

_WORD_RE = re.compile(r"[\w]+", re.UNICODE)


@dataclass(frozen=True)
class TextMatch:
    rank: int
    fuzzy_score: int
    position: int
    length_delta: int


def match_rank(name: str, query: str) -> int:
    haystack = name.lower()
    needle = query.lower()
    if haystack == needle:
        return 0
    if haystack.startswith(needle):
        return 1
    if needle in haystack:
        return 2
    return 3 if fuzzy_score(name, query) >= 0 else -1


def fuzzy_score(name: str, query: str) -> int:
    haystack = name.lower()
    needle = query.lower()
    if len(needle) < 3 or len(needle) > len(haystack):
        return -1

    allowed_gaps = max(4, len(needle))
    best: int | None = None

    for start, char in enumerate(haystack):
        if char != needle[0]:
            continue

        previous = start
        search_at = start + 1
        gaps = 0
        boundaries = 1 if _is_word_boundary(name, start) else 0
        matched = True

        for wanted in needle[1:]:
            found = haystack.find(wanted, search_at)
            if found < 0:
                matched = False
                break

            gaps += found - previous - 1
            previous = found
            search_at = found + 1
            if _is_word_boundary(name, found):
                boundaries += 1

        if not matched:
            continue

        span = previous - start + 1
        if gaps > allowed_gaps or span > len(needle) * 2:
            continue

        score = (
            gaps * 100
            + start * 10
            + (len(needle) - boundaries) * 2
            + max(0, len(name) - len(needle))
        )
        if best is None or score < best:
            best = score

    return -1 if best is None else best


def _is_word_boundary(name: str, index: int) -> bool:
    if index <= 0:
        return True

    current = name[index]
    previous = name[index - 1]
    if previous in ".$_-" or previous.isspace():
        return True
    if current.isupper() and previous.islower():
        return True
    return current.isdigit() != previous.isdigit()


def _words(text: str) -> list[tuple[str, int]]:
    return [(match.group(0), match.start()) for match in _WORD_RE.finditer(text)]


def _best_term_match(text: str, term: str) -> TextMatch | None:
    best: TextMatch | None = None
    for word, position in _words(text):
        rank = match_rank(word, term)
        if rank < 0:
            continue
        score = fuzzy_score(word, term) if rank == 3 else 0
        candidate = TextMatch(
            rank=rank,
            fuzzy_score=score,
            position=position,
            length_delta=abs(len(word) - len(term)),
        )
        if best is None or _match_key(candidate) < _match_key(best):
            best = candidate
    return best


def _match_key(match: TextMatch) -> tuple[int, int, int, int]:
    return (
        match.rank,
        match.fuzzy_score,
        match.position,
        match.length_delta,
    )


def score_text(text: str | None, query: str) -> tuple[int, ...] | None:
    if not text:
        return None

    raw_query = query.strip()
    if not raw_query:
        return None

    folded_text = text.casefold()
    folded_query = raw_query.casefold()

    # A literal phrase/substring beats token-level fuzzy matches.
    direct_at = folded_text.find(folded_query)
    if direct_at >= 0:
        return (-1, direct_at, len(text) - len(raw_query))

    terms = [match.group(0) for match in _WORD_RE.finditer(raw_query)]
    if not terms:
        return None

    matches: list[TextMatch] = []
    for term in terms:
        match = _best_term_match(text, term)
        if match is None:
            return None
        matches.append(match)

    worst_rank = max(match.rank for match in matches)
    fuzzy_total = sum(
        match.fuzzy_score for match in matches if match.rank == 3
    )
    rank_total = sum(match.rank for match in matches)
    position_total = sum(match.position for match in matches)
    length_total = sum(match.length_delta for match in matches)

    return (
        worst_rank,
        fuzzy_total,
        rank_total,
        position_total,
        length_total,
    )


def score_message(row: sqlite3.Row, query: str) -> tuple[int, ...] | None:
    fields = (
        (0, row["body"]),
        (1, row["sender"]),
        (2, row["room_name"]),
    )
    best: tuple[int, ...] | None = None
    for field_priority, value in fields:
        score = score_text(value, query)
        if score is None:
            continue
        candidate = (field_priority, *score)
        if best is None or candidate < best:
            best = candidate
    return best


def _snippet(body: str, query: str, width: int = 120) -> str:
    folded = body.casefold()
    needle = query.strip().casefold()
    at = folded.find(needle) if needle else -1

    if at < 0:
        terms = [match.group(0) for match in _WORD_RE.finditer(query)]
        positions: list[int] = []
        for term in terms:
            match = _best_term_match(body, term)
            if match is not None:
                positions.append(match.position)
        at = min(positions) if positions else 0

    start = max(0, at - width // 3)
    end = min(len(body), start + width)
    prefix = "… " if start else ""
    suffix = " …" if end < len(body) else ""
    return prefix + body[start:end].replace("\n", " ") + suffix


def smart_search_messages(
    connection: sqlite3.Connection,
    query: str,
    limit: int = 20,
) -> list[dict[str, object]]:
    query = query.strip()
    if not query or limit <= 0:
        return []

    ranked: list[tuple[tuple[object, ...], dict[str, object]]] = []

    rows = connection.execute(
        """
        SELECT
            id,
            event_id,
            room_id,
            room_name,
            sender,
            created_at_ms,
            body,
            web_url,
            raw_json,
            archived_at
        FROM messages
        """
    )

    for row in rows:
        score = score_message(row, query)
        if score is None:
            continue

        item = dict(row)
        item["snippet"] = _snippet(str(row["body"]), query)
        item["match_score"] = score

        # Search quality first. For equally good matches, newest messages win.
        key: tuple[object, ...] = (
            *score,
            -int(row["created_at_ms"]),
            str(row["event_id"]).casefold(),
        )
        bisect.insort(ranked, (key, item))
        if len(ranked) > limit:
            ranked.pop()

    return [item for _, item in ranked]
