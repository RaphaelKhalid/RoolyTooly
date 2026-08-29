"""Local lesson retriever (BM25 + family graph) and a latency/agreement benchmark against Qodo.

    python -m harness.rule_index bench [--queries 10]

Qodo's rule search is the authoritative, org-wide memory; this index is the fast local path the
harness can consult per task (microseconds) and reconcile with Qodo receipts. The benchmark reports
median latency of both and how often they select the same lessons for the same task.
"""
from __future__ import annotations

import json
import math
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench import cases as C  # noqa: E402
from harness import qodo_lessons as Q  # noqa: E402

_TOK = re.compile(r"[a-z0-9_]+")
STOP = Q._STOPWORDS


def _tokens(text: str) -> list[str]:
    return [t for t in _TOK.findall((text or "").lower()) if t not in STOP and len(t) > 1]


class LessonIndex:
    """BM25 over lesson text plus a family graph: lessons of the same family reinforce each other."""

    def __init__(self, lessons: list[dict], k1: float = 1.4, b: float = 0.75):
        self.lessons = lessons
        self.docs = [_tokens(f"{L.get('family','')} {L.get('invariant','')} {L.get('rule_text','')} {L.get('preflight_check','')}") for L in lessons]
        self.k1, self.b = k1, b
        self.avgdl = (sum(len(d) for d in self.docs) / len(self.docs)) if self.docs else 1.0
        df: Counter = Counter()
        for d in self.docs:
            df.update(set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tf = [Counter(d) for d in self.docs]
        self.family_graph: dict[str, list[int]] = defaultdict(list)
        for i, L in enumerate(lessons):
            self.family_graph[L.get("family", "")].append(i)

    @staticmethod
    def _ngrams(text: str, n: int = 3) -> Counter:
        t = re.sub(r"\s+", " ", (text or "").lower())
        return Counter(t[i:i + n] for i in range(max(0, len(t) - n + 1)))

    def _ngram_scores(self, query: str) -> list[float]:
        """Character 3-gram cosine: robust to wording/morphology, the classic complement to BM25."""
        qv = self._ngrams(query)
        qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0
        out = []
        for L in self.lessons:
            dv = self._ngrams(f"{L.get('invariant','')} {L.get('rule_text','')}")
            dn = math.sqrt(sum(v * v for v in dv.values())) or 1.0
            out.append(sum(qv[g] * dv.get(g, 0) for g in qv) / (qn * dn))
        return out

    def score(self, query: str) -> list[tuple[float, dict]]:
        """Hybrid retrieval: BM25 and char-n-gram channels fused with reciprocal-rank fusion (RRF),
        then a one-hop family-graph expansion. A dense-embedding channel plugs into the same fusion
        when an embedding endpoint is configured (not used offline)."""
        bm = self._bm25(query)
        ng = self._ngram_scores(query)
        rank_bm = {i: r for r, (i, _) in enumerate(sorted(enumerate(bm), key=lambda x: -x[1]))}
        rank_ng = {i: r for r, (i, _) in enumerate(sorted(enumerate(ng), key=lambda x: -x[1]))}
        k = 60.0
        fused = [(1 / (k + rank_bm[i]) + 1 / (k + rank_ng[i])) * (1 if (bm[i] > 0 or ng[i] > 0.05) else 0)
                 for i in range(len(self.lessons))]
        by_fam: dict[str, float] = defaultdict(float)
        for i, L in enumerate(self.lessons):
            by_fam[L.get("family", "")] = max(by_fam[L.get("family", "")], fused[i])
        out = [(fused[i] + 0.25 * by_fam[L.get("family", "")], L) for i, L in enumerate(self.lessons)]
        return sorted(out, key=lambda x: -x[0])

    def _bm25(self, query: str) -> list[float]:
        q = _tokens(query)
        out = []
        for i, L in enumerate(self.lessons):
            dl = len(self.docs[i]) or 1
            s = 0.0
            for t in q:
                if t in self.tf[i]:
                    f = self.tf[i][t]
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out.append(s)
        return out

    def select(self, query: str, threshold: float = 0.02, top_k: int = 3) -> list[str]:
        return [L["id"] for s, L in self.score(query)[:top_k] if s >= threshold]


def bench(n_queries: int = 10) -> dict:
    idx_doc = json.loads(Q.INDEX.read_text(encoding="utf-8")) if Q.INDEX.exists() else {"index": {}, "receipts": {}}
    lesson_ids = sorted({lid for v in idx_doc["index"].values() if v for lid in v}) or [L["id"] for L in Q.active_lessons()]
    lessons = [L for L in Q.all_lessons() if L["id"] in lesson_ids]
    index = LessonIndex(lessons)
    cases = [c for c in C.CASES if c["split"] != "train"][:n_queries]
    local_ms, qodo_ms, agree = [], [], 0
    rows = []
    for c in cases:
        ask = c["task"].split("Then: ")[-1][:300]
        t0 = time.perf_counter(); local = index.select(ask); local_ms.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        res = Q._qodo("rules", "search", "--query", f"Name: Pre-claim verification for this coding task\nCategory: Correctness\nContent: {ask}", "--top-k", "5", "--scopes", Q.SCOPE)
        qodo_ms.append((time.perf_counter() - t0) * 1000)
        qodo = []
        for r in (res or {}).get("rules", []) if isinstance(res, dict) else []:
            name = r.get("name") or ""
            if name.startswith(Q.RULE_PREFIX):
                qodo.append(name[len(Q.RULE_PREFIX):].split(" ")[0])
        same = set(local) == set(qodo)
        agree += same
        rows.append({"case_id": c["id"], "local": local, "qodo": qodo, "agree": same})
        print(f"{c['id']:<30} local={local} qodo={qodo} {'=' if same else '!='}", flush=True)
    out = {"n": len(cases), "lessons_indexed": len(lessons),
           "local_ms_median": round(statistics.median(local_ms), 3) if local_ms else None,
           "qodo_ms_median": round(statistics.median(qodo_ms), 1) if qodo_ms else None,
           "agreement_rate": round(agree / len(cases), 3) if cases else None, "rows": rows,
           "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    p = ROOT / "results" / f"retrieval_bench_{int(time.time())}.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("n", "lessons_indexed", "local_ms_median", "qodo_ms_median", "agreement_rate")}))
    print("wrote", p.relative_to(ROOT))
    return out


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--queries") + 1]) if "--queries" in sys.argv else 10
    bench(n)
