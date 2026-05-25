"""Plan dataclass — the in-memory representation of a plan file on disk."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .frontmatter import Frontmatter, parse, strip_frontmatter


@dataclass(frozen=True, slots=True)
class Plan:
    path: Path
    name: str         # basename including the .md suffix
    slug: str         # name with the trailing .md stripped
    body: str         # plan text with frontmatter removed
    fm: Frontmatter

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        default_review_rounds: int = 2,
        max_review_rounds: int = 3,
    ) -> Plan:
        text = path.read_text()
        fm = parse(
            text,
            default_review_rounds=default_review_rounds,
            max_review_rounds=max_review_rounds,
        )
        body = strip_frontmatter(text)
        name = path.name
        slug = name[:-3] if name.endswith(".md") else name
        return cls(path=path, name=name, slug=slug, body=body, fm=fm)
