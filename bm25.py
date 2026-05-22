"""bm25.py — BM25 sparse retrieval index for LocallyAI"""
import json
import math
import re
from pathlib import Path

_INDEX_FILE = "bm25_index.json"
_K1 = 1.5
_B  = 0.75


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


class BM25Index:
    def __init__(self, storage_dir: str) -> None:
        self._path = Path(storage_dir) / _INDEX_FILE
        self._docs: list[dict] = []
        self._idf: dict[str, float] = {}
        self._avg_dl: float = 0.0
        if self._path.exists():
            self._load()

    def build(self, documents: list[dict]) -> None:
        """Build from [{chunk_id, text, source, firm_id}, ...]"""
        self._docs = []
        for doc in documents:
            tokens = _tokenize(doc.get("text", ""))
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            self._docs.append({
                "chunk_id": str(doc["chunk_id"]),
                "text":    doc.get("text", ""),
                "source":  doc.get("source", ""),
                "firm_id": doc.get("firm_id", ""),
                "tf":      tf,
                "dl":      len(tokens),
            })

        n = len(self._docs)
        if n == 0:
            self._avg_dl = 0.0
            self._idf = {}
            return

        self._avg_dl = sum(d["dl"] for d in self._docs) / n

        df: dict[str, int] = {}
        for doc in self._docs:
            for term in doc["tf"]:
                df[term] = df.get(term, 0) + 1
        self._idf = {
            term: math.log((n - freq + 0.5) / (freq + 0.5) + 1)
            for term, freq in df.items()
        }

    def search(
        self, query: str, top_k: int = 10, firm_id: str | None = None
    ) -> list[dict]:
        if not self._docs or self._avg_dl == 0:
            return []
        terms = _tokenize(query)
        scores: dict[str, tuple[float, dict]] = {}
        for doc in self._docs:
            if firm_id and doc.get("firm_id") != firm_id:
                continue
            dl   = doc["dl"]
            norm = _K1 * (1 - _B + _B * dl / self._avg_dl)
            score = 0.0
            for term in terms:
                if term not in self._idf:
                    continue
                tf = doc["tf"].get(term, 0)
                score += self._idf[term] * (tf * (_K1 + 1)) / (tf + norm)
            if score > 0:
                scores[doc["chunk_id"]] = (score, doc)

        ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)[:top_k]
        return [
            {"chunk_id": cid, "text": d["text"], "score": sc, "source": d["source"]}
            for cid, (sc, d) in ranked
        ]

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"avg_dl": self._avg_dl, "idf": self._idf, "docs": self._docs}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self._path)

    def _load(self) -> None:
        try:
            payload      = json.loads(self._path.read_text(encoding="utf-8"))
            self._docs   = payload.get("docs", [])
            self._idf    = payload.get("idf", {})
            self._avg_dl = payload.get("avg_dl", 0.0)
        except Exception:
            pass
