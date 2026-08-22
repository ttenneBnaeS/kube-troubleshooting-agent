import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_CORPUS_DIR = Path(__file__).parent / "corpus"
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


@dataclass
class CorpusDoc:
    file: str
    title: str
    source_url: str
    content: str


def load_corpus() -> list[CorpusDoc]:
    """Load every corpus/*.md file, parsing its YAML frontmatter (title, source_url)."""
    docs = []
    for path in sorted(_CORPUS_DIR.glob("*.md")):
        match = _FRONTMATTER_RE.match(path.read_text())
        if not match:
            raise ValueError(f"{path} is missing YAML frontmatter (title/source_url)")
        meta = yaml.safe_load(match.group(1))
        docs.append(
            CorpusDoc(
                file=path.name,
                title=meta["title"],
                source_url=meta["source_url"],
                content=match.group(2).strip(),
            )
        )
    return docs
