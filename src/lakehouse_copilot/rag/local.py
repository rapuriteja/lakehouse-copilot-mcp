"""Local retriever: TF-IDF keyword search with scikit-learn. Free, offline, fast.

TF-IDF scores a chunk higher when it shares rare words with the query. Word bigrams
help with phrases like "net revenue" or "festive season". It cannot match meaning
without shared words; the Azure retriever adds vector search for that.
"""

from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from lakehouse_copilot.catalog.docs import Chunk
from lakehouse_copilot.rag.base import SearchHit


class TfidfRetriever:
    name = "local-tfidf"

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 2), sublinear_tf=True, stop_words="english", lowercase=True
        )
        # The title is repeated so that words in it count a little more.
        corpus = [f"{c.title}. {c.title}. {c.text}" for c in chunks]
        self.matrix = self.vectorizer.fit_transform(corpus)

    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        scores = linear_kernel(self.vectorizer.transform([query]), self.matrix).ravel()
        best = scores.argsort()[::-1][:k]
        return [SearchHit(self.chunks[i], float(scores[i])) for i in best if scores[i] > 0]
