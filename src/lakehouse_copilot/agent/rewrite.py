"""Query rewriting: turn the user's words into a better search query before retrieval.

Users write "AOV in blr?" or a follow-up like "and for Chennai?". Search works better
with the full, standalone question in the catalog's own vocabulary:
    "What is the average order value (average order value) in Bengaluru?"

- SynonymRewriter (offline): adds the official metric/glossary names for any synonym
  found, and glues a short follow-up onto the previous question.
- LLMRewriter: asks the chat model to rewrite using the conversation history.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lakehouse_copilot.agent.semantic import normalise
from lakehouse_copilot.catalog.model import Catalog

YEAR = re.compile(r"\b(?:(?:in|for|of|during)\s+)?20\d\d\b")
FOLLOW_UP = re.compile(r"^(and|what about|how about|same for|now|also|only|just)\b", re.I)


@dataclass(frozen=True)
class RewrittenQuery:
    original: str
    text: str  # search query for retrieval (may include expanded names)
    method: str
    standalone: str = ""  # the full question after resolving follow-ups, no expansions

    @property
    def question(self) -> str:
        return self.standalone or self.text


def _previous_user_question(history: list[dict]) -> str | None:
    return next((m["content"] for m in reversed(history) if m["role"] == "user"), None)


class SynonymRewriter:
    method = "synonyms"

    def __init__(self, catalog: Catalog) -> None:
        self.terms: list[tuple[str, str]] = []
        for m in catalog.metrics:
            canonical = re.sub(r"\s*\(.*?\)", "", m.label).lower()
            for s in (*m.synonyms, *re.findall(r"\((.*?)\)", m.label)):
                self.terms.append((normalise(s), canonical))
        for g in catalog.glossary:
            for s in g.synonyms:
                self.terms.append((normalise(s), g.term.lower()))
        self.terms.sort(key=lambda t: -len(t[0]))

    def rewrite(self, question: str, history: list[dict] | None = None) -> RewrittenQuery:
        text = question.strip()
        previous = _previous_user_question(history or [])
        if previous and (FOLLOW_UP.match(text) or len(text.split()) <= 3):
            # A follow-up that names a new year replaces the old year ("and in 2025?").
            if YEAR.search(text):
                previous = YEAR.sub("", previous)
            text = f"{previous} {text}"
        standalone = text
        norm = normalise(text)
        expansions = []
        for synonym, canonical in self.terms:
            found = re.search(rf"\b{re.escape(synonym)}\b", norm)
            if found and canonical not in norm and canonical not in expansions:
                expansions.append(canonical)
        if expansions:
            text = f"{text} ({', '.join(expansions)})"
        return RewrittenQuery(question, text, self.method, standalone)


REWRITE_PROMPT = """You rewrite questions for a search engine over a data catalog of an
Indian e-commerce lakehouse (tables, columns, business metric definitions, glossary).
Rewrite the user's LAST question into one standalone search query:
- resolve follow-ups using the conversation (for example "and in Chennai?")
- expand abbreviations (AOV -> average order value, blr -> Bengaluru, COD -> cash on delivery)
- keep every filter (dates, cities, categories)
Return only the rewritten query, nothing else."""


class LLMRewriter:
    method = "llm"

    def __init__(self, llm) -> None:
        self.llm = llm

    def rewrite(self, question: str, history: list[dict] | None = None) -> RewrittenQuery:
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in (history or [])[-6:])
        messages = [
            {"role": "system", "content": REWRITE_PROMPT},
            {"role": "user", "content": f"Conversation:\n{convo}\n\nLast question: {question}"},
        ]
        text = (self.llm.chat(messages).content or question).strip()
        return RewrittenQuery(question, text, self.method, standalone=text)
