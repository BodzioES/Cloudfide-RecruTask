"""``search_policies`` tool: find the parts of the shop rules that matter."""

from __future__ import annotations

from ..policies import PolicyIndex

NO_MATCH_HINT = (
    "This is not covered by the shop documents. Tell the customer you could "
    "not find it and suggest contacting the staff at help@citywheels.example "
    "or +48 000 000 000."
)


def search_policies(
    query: str,
    *,
    index: PolicyIndex,
    top_k: int = 3,
) -> dict[str, object]:
    """Return the best matching policy chunks, or a clear no-match result."""

    text = (query or "").strip()
    if not text:
        return {
            "found": False,
            "query": query,
            "results": [],
            "message": "No question was provided.",
            "hint": "Ask the customer what they would like to know.",
        }

    matches = index.search(text, top_k=_clamp_top_k(top_k))
    if not matches:
        return {
            "found": False,
            "query": text,
            "results": [],
            "message": "No matching policy text was found.",
            "hint": NO_MATCH_HINT,
        }

    return {
        "found": True,
        "query": text,
        "results": [match.to_dict() for match in matches],
    }


def _clamp_top_k(value: object) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 3
    return max(1, min(number, 10))
