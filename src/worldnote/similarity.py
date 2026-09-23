from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    return "".join(
        character
        for character in value
        if character.isalnum() and not unicodedata.combining(character)
    )


def _text_units(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\u3400-\u9fff]+", "", normalized)
    if not normalized:
        return set()
    if len(normalized) < 3:
        return {normalized}
    return {normalized[index : index + 3] for index in range(len(normalized) - 2)}


def text_similarity(left: str, right: str) -> float:
    return set_similarity(_text_units(left), _text_units(right))


def set_similarity(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = {unicodedata.normalize("NFKC", value).casefold() for value in left}
    right_set = {unicodedata.normalize("NFKC", value).casefold() for value in right}
    if not left_set and not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)
