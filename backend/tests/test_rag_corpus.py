"""The curated docs corpus and the manifest eval checks citations against."""

import json

from eval.scorers import CORPUS_MANIFEST
from rag.loader import load_corpus


def test_every_page_loads_with_frontmatter():
    docs = load_corpus()
    assert docs
    for d in docs:
        assert d.title and d.content
        assert d.source_url.startswith("https://"), d.file


def test_manifest_matches_corpus():
    # `cited_urls` validity is judged against this manifest; a page added to
    # the corpus but not the manifest would make every citation of it look
    # invented.
    manifest = {e["file"]: e for e in json.loads(CORPUS_MANIFEST.read_text())}
    corpus = {d.file: d for d in load_corpus()}
    assert set(manifest) == set(corpus)
    for name, doc in corpus.items():
        assert manifest[name]["source_url"] == doc.source_url, name
