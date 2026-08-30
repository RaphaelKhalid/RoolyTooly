"""Local lesson retriever: three channels, RRF fusion, dense embeddings.

It also ships a latency/agreement benchmark against Qodo rule search:

    python -m harness.rule_index bench [--queries 10]

Qodo's rule search is the authoritative, org-wide memory; this index is the fast local path the
harness can consult per task and reconcile with Qodo receipts. Three local channels are ranked
independently and fused with reciprocal-rank fusion (RRF, k=60):

  * ``bm25``       - BM25 over lesson family/invariant/rule/preflight text.
  * ``char_ngram`` - character 3-gram cosine, robust to wording and morphology.
  * ``dense``      - OpenAI ``text-embedding-3-small`` cosine, gated at 0.42.

    RRF(d) = sum over channels c of 1[d in c] / (60 + rank_c(d))

Ranks are one-based and each channel retains at most 50 results. Raw BM25 / n-gram / cosine
values are never added into the RRF sum; they are kept for receipts only.

The dense channel is best-effort. OPENAI_API_KEY is read from the environment or from the
gitignored .env at the repo root, vectors are cached under results/embeddings/<sha256>.json,
and the HTTP call has a 5 s timeout. If the key is missing or the request fails, dense_status
becomes "missing_key" or "error", the dense channel is dropped from the fusion, and retrieval
continues on BM25 + char-n-gram - an embedding failure must never silently disable lessons.

Eligibility gate: a lesson may be selected only if it shows real evidence in at least one channel
(BM25 >= 2.0, char-3gram cosine >= 0.18, or - when dense is available - cosine >= 0.42). The gate
is a disjunction so that a dense outage, or a merely mediocre cosine, can never remove a lesson
the lexical channels already justified; `gate` records whether dense was evaluated at all.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench import cases as C  # noqa: E402
from harness import qodo_lessons as Q  # noqa: E402

_TOK = re.compile(r"[a-z0-9_]+")
BM25_GATE = 2.0    # calibrate from results/retrieval_bench_*.json: raise if unrelated tasks still select lessons
NGRAM_GATE = 0.18
TOP_K = 3          # both retrievers are compared on their top-3 accepted lessons
FAMILY_HOP = 0.25  # one-hop family reinforcement, applied only to lessons that matched on their own
STOP = Q._STOPWORDS

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_URL = "https://api.openai.com/v1/embeddings"
EMBEDDING_TIMEOUT_S = 5.0
DEFAULT_COSINE_THRESHOLD = 0.42
DEFAULT_RRF_K = 60
DEFAULT_CHANNEL_DEPTH = 50
CHANNELS = ("bm25", "char_ngram", "dense")

# In-process vector cache: a warm retrieval touches neither disk nor network.
_VECTORS: dict[str, list[float]] = {}
_STATE: dict[str, Any] = {"cache_hits": 0, "cache_misses": 0, "requests": 0,
                          "dense_status": "not_run", "dense_error": None}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOK.findall((text or "").lower()) if t not in STOP and len(t) > 1]


# ---- embeddings ------------------------------------------------------------------------------

def embedding_cache_dir(repo_root: Path | str = ROOT) -> Path:
    """Return the directory holding cached embedding vectors for this repo."""
    return Path(repo_root) / "results" / "embeddings"


def embedding_api_key(repo_root: Path | str = ROOT) -> str | None:
    """Read OPENAI_API_KEY from the environment, else from the gitignored .env file."""
    key = os.environ.get("OPENAI_API_KEY")
    env = Path(repo_root) / ".env"
    if not key and env.exists():
        try:
            lines = env.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            if line.startswith("OPENAI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key or None


def embedding_cache_key(text: str) -> str:
    """Return the sha256 hex digest naming this text's embedding cache file."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_vector(vec: object) -> bool:
    """A vector is usable only as a non-empty list of finite real numbers."""
    if not isinstance(vec, list) or not vec:
        return False
    for x in vec:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            return False
    return True


def _dimension_spread(vectors: dict[str, list[float]]) -> list[int]:
    """List the distinct vector lengths in a set; more than one means corruption."""
    return sorted({len(v) for v in vectors.values()})


def _sanitize(message: str, key: str | None) -> str:
    """Strip any credential out of an error message and clip it to receipt size."""
    out = str(message)
    if key:
        out = out.replace(key, "***")
    return out[:200]


def _cache_read(cache_dir: Path, digest: str) -> list[float] | None:
    """Load one validated cached vector, or None when absent, stale or corrupt."""
    try:
        doc = json.loads((cache_dir / f"{digest}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("model") != EMBEDDING_MODEL or doc.get("sha256") != digest:
        return None
    vec = doc.get("vector")
    # a NaN/inf or ragged entry is treated as absent: a silent 0.0 cosine would look healthy
    if not _valid_vector(vec) or doc.get("dimensions") != len(vec):
        return None
    return [float(x) for x in vec]


def _cache_write(cache_dir: Path, digest: str, vector: list[float]) -> None:
    """Write one vector to the cache atomically; the source text is never stored."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_dir / f".{digest}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({"model": EMBEDDING_MODEL, "sha256": digest,
                                   "dimensions": len(vector), "vector": vector}), encoding="utf-8")
        os.replace(tmp, cache_dir / f"{digest}.json")
    except OSError:
        pass  # a cache that cannot be written is a slowdown, never a retrieval failure


def reset_embedding_cache() -> None:
    """Drop the in-process vector cache and counters (used by tests and benchmarks)."""
    _VECTORS.clear()
    _STATE.update({"cache_hits": 0, "cache_misses": 0, "requests": 0,
                   "dense_status": "not_run", "dense_error": None})


def cache_stats() -> dict:
    """Return embedding cache counters and the last dense degradation status."""
    return dict(_STATE)


def embed_texts(texts: Sequence[str], *, repo_root: Path | str = ROOT, cache_dir: Path | None = None,
                opener: Callable[..., Any] | None = None,
                timeout_s: float = EMBEDDING_TIMEOUT_S) -> tuple[dict[str, list[float]], str, str | None]:
    """Embed texts from cache, then one OpenAI call for whatever is still missing.

    Returns (vectors_by_text, status, error) where status is "ok", "missing_key" or "error". On
    failure the already-cached vectors are still returned, so callers can decide whether a partial
    corpus is usable; they must never treat a failure as an empty result set."""
    opener = urllib.request.urlopen if opener is None else opener
    cdir = Path(cache_dir) if cache_dir is not None else embedding_cache_dir(repo_root)
    wanted: list[str] = []
    for t in texts:
        if t not in wanted:
            wanted.append(t)
    vectors: dict[str, list[float]] = {}
    missing: list[str] = []
    for t in wanted:
        digest = embedding_cache_key(t)
        vec = _VECTORS.get(digest)
        if vec is None:
            vec = _cache_read(cdir, digest)
            if vec is not None:
                _VECTORS[digest] = vec
        if vec is None:
            _STATE["cache_misses"] += 1
            missing.append(t)
        else:
            _STATE["cache_hits"] += 1
            vectors[t] = vec
    if not missing:
        dims = _dimension_spread(vectors)
        if len(dims) > 1:
            err = f"cached embedding dimensions are inconsistent: {dims}"
            _STATE.update({"dense_status": "error", "dense_error": err})
            print(f"[rule_index] dense channel unavailable (error): {err}", file=sys.stderr)
            return vectors, "error", err
        _STATE.update({"dense_status": "ok", "dense_error": None})
        return vectors, "ok", None

    key = embedding_api_key(repo_root)
    if not key:
        _STATE.update({"dense_status": "missing_key", "dense_error": "OPENAI_API_KEY is not set"})
        return vectors, "missing_key", "OPENAI_API_KEY is not set"

    payload = json.dumps({"model": EMBEDDING_MODEL, "input": missing}).encode("utf-8")
    req = urllib.request.Request(EMBEDDING_URL, data=payload, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        _STATE["requests"] += 1
        resp = opener(req, timeout=timeout_s)
        try:
            doc = json.loads(resp.read().decode("utf-8"))
        finally:
            closer = getattr(resp, "close", None)
            if callable(closer):
                closer()
        rows = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(rows, list):
            raise ValueError(f"embeddings response has no data list: {str(doc)[:120]}")
        # nothing is cached until the whole batch validates: a bad vector must not be persisted
        fetched: dict[str, list[float]] = {}
        for row in rows:
            i = row.get("index")
            vec = row.get("embedding")
            if not isinstance(i, int) or not (0 <= i < len(missing)):
                raise ValueError("embeddings response row has no usable index")
            if not _valid_vector(vec):
                raise ValueError("embeddings response row is not a finite numeric vector")
            fetched[missing[i]] = [float(x) for x in vec]
        absent = [t for t in missing if t not in fetched]
        if absent:
            raise ValueError(f"embeddings response covered {len(missing) - len(absent)}/{len(missing)} inputs")
        dims = _dimension_spread({**vectors, **fetched})
        if len(dims) > 1:
            raise ValueError(f"embedding dimensions are inconsistent: {dims}")
        for text, vector in fetched.items():
            digest = embedding_cache_key(text)
            _VECTORS[digest] = vector
            _cache_write(cdir, digest, vector)
            vectors[text] = vector
    except Exception as exc:  # noqa: BLE001 - any embedding failure degrades, never raises
        err = _sanitize(f"{type(exc).__name__}: {exc}", key)
        _STATE.update({"dense_status": "error", "dense_error": err})
        print(f"[rule_index] dense channel unavailable (error): {err}", file=sys.stderr)
        return vectors, "error", err
    _STATE.update({"dense_status": "ok", "dense_error": None})
    return vectors, "ok", None


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return the cosine of two vectors, or 0.0 if either is empty or all zero."""
    if len(left) != len(right) or not left:
        return 0.0
    ln = math.sqrt(sum(a * a for a in left))
    rn = math.sqrt(sum(b * b for b in right))
    if ln == 0.0 or rn == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (ln * rn)


def dense_document_text(lesson: dict) -> str:
    """Build the text embedded for one lesson: family, invariant and rule text."""
    parts = [lesson.get("family", ""), lesson.get("invariant", ""), lesson.get("rule_text", "")]
    return "\n".join(str(p).strip() for p in parts if p and str(p).strip())


# ---- channels and fusion ---------------------------------------------------------------------

@dataclass(frozen=True)
class ChannelHit:
    """This records one channel's one-based rank and raw score for one lesson."""

    lesson_id: str
    rank: int
    score: float


@dataclass
class RetrievalHit:
    """This is one fused lesson plus the per-channel receipts that produced it."""

    lesson_id: str
    rrf_score: float
    channels: dict[str, ChannelHit] = field(default_factory=dict)
    cosine: float | None = None
    gate: str = "not_evaluated"
    score: float = 0.0  # rrf_score after the one-hop family-graph reinforcement

    @property
    def channels_fired(self) -> list[str]:
        """Names of the channels that ranked this lesson, in canonical order."""
        return [c for c in CHANNELS if c in self.channels]

    def receipt(self) -> dict:
        """Serialize this hit as the receipt written to results/lesson_index.json."""
        return {"lesson_id": self.lesson_id, "channels_fired": self.channels_fired,
                "channels": {n: {"rank": h.rank, "score": round(h.score, 6)}
                             for n, h in sorted(self.channels.items())},
                "rrf_score": round(self.rrf_score, 6), "score": round(self.score, 6),
                "cosine": self.cosine, "gate": self.gate}


@dataclass
class RetrievalResult:
    """This holds the fused selection for one query and the dense channel health."""

    hits: list[RetrievalHit] = field(default_factory=list)
    dense_status: str = "not_run"
    dense_error: str | None = None
    cosine_threshold: float = DEFAULT_COSINE_THRESHOLD
    rrf_k: int = DEFAULT_RRF_K
    channel_depth: int = DEFAULT_CHANNEL_DEPTH

    @property
    def lesson_ids(self) -> list[str]:
        """The selected lesson ids, in fused order."""
        return [h.lesson_id for h in self.hits]

    def receipt(self) -> dict:
        """Serialize the result: config, dense health and one receipt per hit."""
        return {"selected": self.lesson_ids, "dense_status": self.dense_status,
                "dense_error": self.dense_error, "cosine_threshold": self.cosine_threshold,
                "rrf_k": self.rrf_k, "channel_depth": self.channel_depth,
                "hits": [h.receipt() for h in self.hits]}


def _rank_channel(ids: Sequence[str], scores: Sequence[float], depth: int) -> list[ChannelHit]:
    """Turn raw per-lesson scores into a truncated, deterministically ordered rank."""
    pairs = [(ids[i], float(scores[i])) for i in range(len(ids)) if scores[i] > 0.0]
    pairs.sort(key=lambda p: (-p[1], p[0]))
    return [ChannelHit(lid, r, s) for r, (lid, s) in enumerate(pairs[:depth], start=1)]


def dense_rank(query: str, documents: Sequence[dict], *, repo_root: Path | str = ROOT,
               cache_dir: Path | None = None, threshold: float = DEFAULT_COSINE_THRESHOLD,
               depth: int = DEFAULT_CHANNEL_DEPTH,
               embedder: Callable[..., Any] = embed_texts) -> tuple[list[ChannelHit], dict[str, float], str, str | None]:
    """Rank lessons by embedding cosine, admitting only cosine >= threshold.

    Returns (hits, cosines_by_lesson_id, status, error). The cosine map covers every lesson that
    could be embedded, including ones below the threshold, so receipts can still quote them."""
    docs = list(documents)
    if not docs or not (query or "").strip():
        return [], {}, "not_run", None
    texts = [dense_document_text(L) for L in docs]
    vectors, status, error = embedder([query, *texts], repo_root=repo_root, cache_dir=cache_dir)
    if status != "ok":
        return [], {}, status, error
    qv = vectors.get(query)
    if not qv:
        return [], {}, "error", "query embedding missing from response"
    cosines: dict[str, float] = {}
    for L, text in zip(docs, texts):
        dv = vectors.get(text)
        if dv:
            cosines[L.get("id", "")] = cosine_similarity(qv, dv)
    if len(cosines) < len(docs):
        return [], cosines, "error", f"embedded {len(cosines)}/{len(docs)} lessons"
    ids = [L.get("id", "") for L in docs]
    admitted = [cosines.get(i, 0.0) if cosines.get(i, 0.0) >= threshold else 0.0 for i in ids]
    return _rank_channel(ids, admitted, depth), cosines, "ok", None


def reciprocal_rank_fuse(rankings: Mapping[str, Sequence[ChannelHit]], *, k: int = DEFAULT_RRF_K,
                         depth: int = DEFAULT_CHANNEL_DEPTH) -> list[RetrievalHit]:
    """Fuse per-channel rankings with reciprocal-rank fusion at constant k.

    A channel contributes 1/(k + rank) for a lesson only when that lesson survives inside the
    channel's retained depth; raw channel scores never enter the sum."""
    scores: dict[str, float] = defaultdict(float)
    channels: dict[str, dict[str, ChannelHit]] = defaultdict(dict)
    for name in sorted(rankings):
        for hit in list(rankings[name])[:depth]:
            scores[hit.lesson_id] += 1.0 / (k + hit.rank)
            channels[hit.lesson_id][name] = hit
    hits = [RetrievalHit(lid, scores[lid], dict(channels[lid]), score=scores[lid]) for lid in scores]
    hits.sort(key=lambda h: (-h.rrf_score, min(c.rank for c in h.channels.values()), h.lesson_id))
    return hits


def retrieve_lessons(query: str, documents: Sequence[dict], *, limit: int | None = TOP_K,
                     repo_root: Path | str = ROOT, cache_dir: Path | None = None,
                     cosine_threshold: float = DEFAULT_COSINE_THRESHOLD, rrf_k: int = DEFAULT_RRF_K,
                     channel_depth: int = DEFAULT_CHANNEL_DEPTH, dense: bool = True,
                     embedder: Callable[..., Any] = embed_texts,
                     index: "LessonIndex | None" = None) -> RetrievalResult:
    """Fuse BM25, char-n-gram and dense rankings, gate them, and return top lessons.

    This is the explicit three-channel entry point, so `dense` defaults to True and callers opt
    into the network by calling it. The dense channel joins the fusion only when it is healthy;
    otherwise the result carries its dense_status and retrieval proceeds on the lexical channels
    alone. Passing dense=False skips embedding entirely and reports dense_status "not_run"."""
    idx = index if index is not None else LessonIndex(list(documents))
    lessons = idx.lessons
    ids = [L.get("id", "") for L in lessons]
    bm = idx.bm25_scores(query)
    ng = idx.ngram_scores(query)
    if dense:
        dense_hits, cosines, dense_status, dense_error = dense_rank(
            query, lessons, repo_root=repo_root, cache_dir=cache_dir, threshold=cosine_threshold,
            depth=channel_depth, embedder=embedder)
    else:  # local-only: no key is read, no request is made, no embedder is called
        dense_hits, cosines, dense_status, dense_error = [], {}, "not_run", None
    rankings: dict[str, Sequence[ChannelHit]] = {
        "bm25": _rank_channel(ids, bm, channel_depth),
        "char_ngram": _rank_channel(ids, ng, channel_depth),
    }
    dense_ok = dense_status == "ok"
    if dense_ok:
        rankings["dense"] = dense_hits
    fused = reciprocal_rank_fuse(rankings, k=rrf_k, depth=channel_depth)

    bm_by_id = dict(zip(ids, bm))
    ng_by_id = dict(zip(ids, ng))
    fam_by_id = {L.get("id", ""): L.get("family", "") for L in lessons}
    for hit in fused:
        cos = cosines.get(hit.lesson_id) if dense_ok else None
        # unrounded on purpose: the receipt must quote the value the gate compared, so a
        # threshold can be recalibrated from receipts without a rounding disagreement
        hit.cosine = cos
        eligible = (bm_by_id.get(hit.lesson_id, 0.0) >= BM25_GATE
                    or ng_by_id.get(hit.lesson_id, 0.0) >= NGRAM_GATE
                    or (dense_ok and cos is not None and cos >= cosine_threshold))
        hit.gate = ("passed" if dense_ok else "not_evaluated") if eligible else "blocked"
    by_fam: dict[str, float] = defaultdict(float)
    for hit in fused:
        if hit.gate != "blocked":
            fam = fam_by_id.get(hit.lesson_id, "")
            by_fam[fam] = max(by_fam[fam], hit.rrf_score)
    kept = []
    for hit in fused:
        if hit.gate == "blocked":
            continue
        # the family hop only reinforces lessons that matched on their own; it never resurrects a zero
        hit.score = hit.rrf_score + FAMILY_HOP * by_fam[fam_by_id.get(hit.lesson_id, "")]
        kept.append(hit)
    kept.sort(key=lambda h: (-h.score, min(c.rank for c in h.channels.values()), h.lesson_id))
    if limit is not None:
        kept = kept[:limit]
    return RetrievalResult(kept, dense_status, dense_error, cosine_threshold, rrf_k, channel_depth)


class LessonIndex:
    """The index scores lessons with BM25 and char-n-grams and groups them by family."""

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
        # precomputed so a warm query does no per-lesson n-gram work
        self._ngram_vecs = [self._ngrams(f"{L.get('invariant','')} {L.get('rule_text','')}") for L in lessons]
        self._ngram_norms = [math.sqrt(sum(v * v for v in dv.values())) or 1.0 for dv in self._ngram_vecs]

    @staticmethod
    def _ngrams(text: str, n: int = 3) -> Counter:
        t = re.sub(r"\s+", " ", (text or "").lower())
        return Counter(t[i:i + n] for i in range(max(0, len(t) - n + 1)))

    def ngram_scores(self, query: str) -> list[float]:
        """Character 3-gram cosine: robust to wording, the complement to BM25."""
        qv = self._ngrams(query)
        qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0
        return [sum(qv[g] * dv.get(g, 0) for g in qv) / (qn * dn)
                for dv, dn in zip(self._ngram_vecs, self._ngram_norms)]

    _ngram_scores = ngram_scores  # kept for callers written against the pre-fusion name

    def bm25_scores(self, query: str) -> list[float]:
        """Okapi BM25 score of every lesson in the index against the query."""
        q = _tokens(query)
        out = []
        for i, _L in enumerate(self.lessons):
            dl = len(self.docs[i]) or 1
            s = 0.0
            for t in q:
                if t in self.tf[i]:
                    f = self.tf[i][t]
                    s += self.idf.get(t, 0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            out.append(s)
        return out

    _bm25 = bm25_scores  # kept for callers written against the pre-fusion name

    def retrieve(self, query: str, limit: int | None = TOP_K, dense: bool = True,
                 **kw: Any) -> RetrievalResult:
        """Run the three-channel fusion over this index and return the full result.

        This is the explicit API: `dense=True` is the default here, so calling it is the act of
        opting into the embedding request."""
        return retrieve_lessons(query, self.lessons, limit=limit, index=self, dense=dense, **kw)

    def score(self, query: str, dense: bool = False, **kw: Any) -> list[tuple[float, dict]]:
        """Return fused (score, lesson) pairs in descending order, lexically by default.

        This wrapper predates the dense channel and stays local-only unless dense=True: callers
        that never asked for a network call must not get one."""
        by_id = {L.get("id", ""): L for L in self.lessons}
        res = self.retrieve(query, limit=None, dense=dense, **kw)
        return [(h.score, by_id[h.lesson_id]) for h in res.hits if h.lesson_id in by_id]

    def select(self, query: str, threshold: float = 0.02, top_k: int = 3, dense: bool = False,
               **kw: Any) -> list[str]:
        """Return ids of the top_k lessons at or above threshold, lexically by default.

        Local-only unless dense=True, for the same reason as score()."""
        hits = self.retrieve(query, limit=top_k, dense=dense, **kw).hits
        return [h.lesson_id for h in hits if h.score >= threshold]


def bench(n_queries: int = 10) -> dict:
    """Compare the local retriever with Qodo rule search: latency and agreement."""
    idx_doc = json.loads(Q.INDEX.read_text(encoding="utf-8")) if Q.INDEX.exists() else {"index": {}, "receipts": {}}
    lesson_ids = sorted({lid for v in idx_doc["index"].values() if v for lid in v}) or [L["id"] for L in Q.active_lessons()]
    lessons = [L for L in Q.all_lessons() if L["id"] in lesson_ids]
    index = LessonIndex(lessons)
    cases = [c for c in C.CASES if c["split"] != "train"][:n_queries]
    local_ms, qodo_ms = [], []
    agree = compared = unavailable = 0
    dense_counts: Counter = Counter()
    rows = []
    for c in cases:
        ask = c["task"].split("Then: ")[-1][:300]
        t0 = time.perf_counter()
        res_local = index.retrieve(ask, limit=TOP_K)
        local_ms.append((time.perf_counter() - t0) * 1000)
        local = [h.lesson_id for h in res_local.hits if h.score >= 0.02]
        dense_counts[res_local.dense_status] += 1
        t0 = time.perf_counter()
        try:
            res = Q._qodo("rules", "search", "--query", f"Name: Pre-claim verification for this coding task\nCategory: Correctness\nContent: {ask}", "--top-k", str(TOP_K), "--scopes", Q.SCOPE)
        except Exception as exc:  # noqa: BLE001 - a Qodo outage is recorded, never fatal
            res = {"_error": str(exc)}
        elapsed = (time.perf_counter() - t0) * 1000
        if not isinstance(res, dict) or "rules" not in res:
            unavailable += 1
            rows.append({"case_id": c["id"], "local": local, "qodo": None, "status": "qodo_unavailable",
                         "receipt": res_local.receipt()})
            print(f"{c['id']:<30} local={local} qodo=UNAVAILABLE dense={res_local.dense_status}", flush=True)
            continue
        qodo_ms.append(elapsed)
        qodo = []
        for r in res["rules"]:
            name = r.get("name") or ""
            if name.startswith(Q.RULE_PREFIX):
                qodo.append(name[len(Q.RULE_PREFIX):].split(" ")[0])
        qodo = qodo[:TOP_K]
        same = set(local) == set(qodo)
        compared += 1
        agree += same
        rows.append({"case_id": c["id"], "local": local, "qodo": qodo, "agree": same, "status": "ok",
                     "receipt": res_local.receipt()})
        print(f"{c['id']:<30} local={local} qodo={qodo} {'=' if same else '!='} dense={res_local.dense_status}", flush=True)
    out = {"n": len(cases), "compared": compared, "qodo_unavailable": unavailable, "lessons_indexed": len(lessons),
           "local_ms_median": round(statistics.median(local_ms), 3) if local_ms else None,
           "qodo_ms_median": round(statistics.median(qodo_ms), 1) if qodo_ms else None,
           "agreement_rate": round(agree / compared, 3) if compared else None,
           "embedding_model": EMBEDDING_MODEL, "cosine_threshold": DEFAULT_COSINE_THRESHOLD,
           "rrf_k": DEFAULT_RRF_K, "channel_depth": DEFAULT_CHANNEL_DEPTH,
           "dense_status_counts": dict(dense_counts), "embedding_cache": cache_stats(),
           "rows": rows, "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    p = out_dir / f"retrieval_bench_{int(time.time())}.json"
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("n", "compared", "qodo_unavailable", "lessons_indexed", "local_ms_median",
                                          "qodo_ms_median", "agreement_rate", "dense_status_counts",
                                          "embedding_model", "cosine_threshold", "rrf_k")}))
    print("wrote", p.relative_to(ROOT))
    return out


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--queries") + 1]) if "--queries" in sys.argv else 10
    bench(n)
