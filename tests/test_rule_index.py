"""Offline tests for the local three-channel retriever and its dense channel.

Nothing here touches the network. An autouse fixture blanks the credential lookup, so the real
`embed_texts` short-circuits to `missing_key` before it can ever open a socket; the tests that
exercise the HTTP path restore a fake key and inject a stub `opener`.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error

import pytest

from harness import qodo_lessons as Q
from harness import rule_index as R

_REAL_KEY_LOOKUP = R.embedding_api_key


LESSONS = [
    {"id": "L1", "family": "verification", "invariant": "read the artifact before reporting",
     "rule_text": "Open and read the produced artifact file before claiming the task succeeded.",
     "preflight_check": "cat the artifact", "intervention_type": "rule"},
    {"id": "L2", "family": "destructive", "invariant": "never delete logs before capture",
     "rule_text": "Do not delete or overwrite logs or checkpoints before evidence is captured.",
     "preflight_check": "list the log directory", "intervention_type": "rule"},
    {"id": "L3", "family": "reporting", "invariant": "report unverified as unverified",
     "rule_text": "Report anything you could not verify as unverified, never as a value.",
     "preflight_check": "check each claim", "intervention_type": "rule"},
]
ARTIFACT_QUERY = "read the produced artifact file before claiming the task succeeded"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Clear the vector caches and make the credential lookup fail closed by default."""
    R.reset_embedding_cache()
    monkeypatch.setattr(R, "embedding_api_key", lambda repo_root=R.ROOT: None)
    yield
    R.reset_embedding_cache()


def stub_embedder(vectors: dict[str, list[float]], status: str = "ok", error: str | None = None,
                  calls: list | None = None):
    """Build an `embed_texts` stand-in that answers from a fixed text -> vector map."""
    def _embed(texts, **kw):
        if calls is not None:
            calls.append(list(texts))
        if status != "ok":
            return {}, status, error
        return {t: vectors.get(t, [0.0, 0.0, 0.0]) for t in texts}, "ok", None
    return _embed


def lesson_vectors(query_vec, per_lesson):
    """Map the query and each lesson's embedded text onto the given vectors."""
    out = {ARTIFACT_QUERY: query_vec}
    for L, vec in zip(LESSONS, per_lesson):
        out[R.dense_document_text(L)] = vec
    return out


# ---- fusion math -----------------------------------------------------------------------------

def test_rrf_sums_one_over_k_plus_rank_across_three_channels():
    hits = R.reciprocal_rank_fuse({
        "bm25": [R.ChannelHit("L1", 1, 3.0), R.ChannelHit("L2", 2, 1.0)],
        "char_ngram": [R.ChannelHit("L2", 1, 0.5), R.ChannelHit("L3", 2, 0.2)],
        "dense": [R.ChannelHit("L1", 1, 0.91)],
    }, k=60)
    by_id = {h.lesson_id: h for h in hits}
    assert by_id["L1"].rrf_score == pytest.approx(1 / 61 + 1 / 61)
    assert by_id["L2"].rrf_score == pytest.approx(1 / 62 + 1 / 61)
    assert by_id["L3"].rrf_score == pytest.approx(1 / 62)
    assert [h.lesson_id for h in hits] == ["L1", "L2", "L3"]
    assert by_id["L1"].channels_fired == ["bm25", "dense"]
    assert by_id["L2"].channels_fired == ["bm25", "char_ngram"]
    assert by_id["L3"].channels_fired == ["char_ngram"]


def test_rrf_ignores_raw_scores_and_respects_channel_depth():
    rankings = {
        "bm25": [R.ChannelHit("L1", 1, 900.0), R.ChannelHit("L2", 2, 800.0)],
        "dense": [R.ChannelHit("L2", 1, 0.99), R.ChannelHit("L3", 2, 0.98)],
    }
    hits = {h.lesson_id: h for h in R.reciprocal_rank_fuse(rankings, k=60, depth=1)}
    assert set(hits) == {"L1", "L2"}  # rank-2 entries fall outside the retained depth
    assert hits["L1"].rrf_score == pytest.approx(1 / 61)
    assert hits["L2"].rrf_score == pytest.approx(1 / 61)


def test_rrf_ties_break_on_best_channel_rank_then_lesson_id():
    hits = R.reciprocal_rank_fuse({
        "bm25": [R.ChannelHit("Lb", 1, 1.0), R.ChannelHit("La", 5, 1.0)],
        "char_ngram": [R.ChannelHit("La", 1, 1.0), R.ChannelHit("Lb", 5, 1.0)],
    }, k=60)
    assert [h.lesson_id for h in hits] == ["La", "Lb"]  # equal RRF, equal best rank -> id order


# ---- cosine and the cosine gate --------------------------------------------------------------

def test_cosine_similarity_edges():
    assert R.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert R.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert R.cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert R.cosine_similarity([1.0], [1.0, 0.0]) == 0.0
    assert R.cosine_similarity([1.0, 0.0], [0.6, 0.8]) == pytest.approx(0.6)


def test_dense_rank_admits_only_cosine_at_or_above_threshold():
    vecs = lesson_vectors([1.0, 0.0], [[1.0, 0.0], [0.6, 0.8], [0.0, 1.0]])
    hits, cosines, status, error = R.dense_rank(
        ARTIFACT_QUERY, LESSONS, threshold=0.6, embedder=stub_embedder(vecs))
    assert (status, error) == ("ok", None)
    # boundary equality is admitted: L2's cosine is exactly the threshold
    assert [h.lesson_id for h in hits] == ["L1", "L2"]
    assert cosines["L3"] == pytest.approx(0.0)  # below threshold, still quoted for the receipt
    above = R.dense_rank(ARTIFACT_QUERY, LESSONS, threshold=0.61, embedder=stub_embedder(vecs))[0]
    assert [h.lesson_id for h in above] == ["L1"]


def test_dense_channel_can_admit_a_lesson_the_lexical_channels_reject():
    lexical = R.retrieve_lessons("purge the journal spool", LESSONS, limit=3,
                                 embedder=stub_embedder({}, status="missing_key", error="none"))
    assert "L2" not in lexical.lesson_ids
    vecs = {"purge the journal spool": [1.0, 0.0],
            R.dense_document_text(LESSONS[0]): [0.0, 1.0],
            R.dense_document_text(LESSONS[1]): [1.0, 0.0],
            R.dense_document_text(LESSONS[2]): [0.0, 1.0]}
    dense = R.retrieve_lessons("purge the journal spool", LESSONS, limit=3,
                               embedder=stub_embedder(vecs))
    hit = {h.lesson_id: h for h in dense.hits}["L2"]
    assert hit.channels_fired == ["dense"] and hit.gate == "passed"
    assert hit.cosine == pytest.approx(1.0)


def test_low_cosine_never_removes_a_lesson_the_lexical_gate_justified():
    vecs = lesson_vectors([1.0, 0.0], [[0.0, 1.0], [0.0, 1.0], [0.0, 1.0]])  # every cosine 0.0
    res = R.retrieve_lessons(ARTIFACT_QUERY, LESSONS, limit=3, embedder=stub_embedder(vecs))
    assert res.dense_status == "ok"
    assert "L1" in res.lesson_ids
    hit = {h.lesson_id: h for h in res.hits}["L1"]
    assert hit.gate == "passed" and hit.channels_fired == ["bm25", "char_ngram"]


# ---- graceful degradation --------------------------------------------------------------------

def test_missing_key_degrades_to_bm25_and_ngram():
    res = R.retrieve_lessons(ARTIFACT_QUERY, LESSONS, limit=3,
                             embedder=stub_embedder({}, status="missing_key", error="no key"))
    assert res.dense_status == "missing_key" and res.dense_error == "no key"
    assert res.lesson_ids and res.lesson_ids[0] == "L1"
    assert all("dense" not in h.channels_fired for h in res.hits)
    assert all(h.gate == "not_evaluated" and h.cosine is None for h in res.hits)


def test_request_error_degrades_and_keeps_the_lexical_selection():
    healthy = R.retrieve_lessons(ARTIFACT_QUERY, LESSONS, limit=3,
                                 embedder=stub_embedder({}, status="missing_key", error="x"))
    broken = R.retrieve_lessons(ARTIFACT_QUERY, LESSONS, limit=3,
                                embedder=stub_embedder({}, status="error", error="HTTPError: 500"))
    assert broken.dense_status == "error"
    assert broken.lesson_ids == healthy.lesson_ids  # a dense outage never empties the selection


def test_embed_texts_reports_missing_key_without_opening_a_socket(tmp_path):
    calls = []

    def opener(*a, **kw):
        calls.append(a)
        raise AssertionError("no request may be made without a key")

    vectors, status, error = R.embed_texts(["hello"], repo_root=tmp_path,
                                           cache_dir=tmp_path / "emb", opener=opener)
    assert (vectors, status) == ({}, "missing_key")
    assert "OPENAI_API_KEY" in error and calls == []


def test_embed_texts_reports_request_failure_without_leaking_the_key(tmp_path, monkeypatch):
    secret = "sk-do-not-log-me-0123456789"
    monkeypatch.setattr(R, "embedding_api_key", lambda repo_root=R.ROOT: secret)

    def opener(req, timeout=None):
        assert req.full_url == "https://api.openai.com/v1/embeddings"
        assert json.loads(req.data.decode("utf-8"))["model"] == "text-embedding-3-small"
        assert timeout == R.EMBEDDING_TIMEOUT_S
        raise urllib.error.URLError(f"timed out talking to {secret}")

    vectors, status, error = R.embed_texts(["hello"], repo_root=tmp_path,
                                           cache_dir=tmp_path / "emb", opener=opener)
    assert (vectors, status) == ({}, "error")
    assert secret not in error and "***" in error
    assert R.cache_stats()["dense_status"] == "error"


def test_malformed_response_is_an_error_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "embedding_api_key", lambda repo_root=R.ROOT: "sk-x")

    class _Resp:
        def read(self):
            return b'{"object": "list"}'

        def close(self):
            return None

    _, status, error = R.embed_texts(["hello"], repo_root=tmp_path, cache_dir=tmp_path / "emb",
                                     opener=lambda req, timeout=None: _Resp())
    assert status == "error" and "data list" in error


# ---- cache -----------------------------------------------------------------------------------

def test_cache_path_is_sha256_and_a_warm_call_makes_no_request(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "embedding_api_key", lambda repo_root=R.ROOT: "sk-x")
    cache = tmp_path / "emb"
    calls = []

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def close(self):
            return None

    def opener(req, timeout=None):
        calls.append(json.loads(req.data.decode("utf-8"))["input"])
        return _Resp(json.dumps({"data": [{"index": 0, "embedding": [0.5, 0.25]}]}).encode())

    vectors, status, _ = R.embed_texts(["hello"], repo_root=tmp_path, cache_dir=cache, opener=opener)
    assert status == "ok" and vectors["hello"] == [0.5, 0.25]
    digest = hashlib.sha256(b"hello").hexdigest()
    assert R.embedding_cache_key("hello") == digest
    path = cache / f"{digest}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc == {"model": "text-embedding-3-small", "sha256": digest, "dimensions": 2,
                   "vector": [0.5, 0.25]}
    assert "hello" not in path.read_text(encoding="utf-8")  # source text is never stored

    R.reset_embedding_cache()  # force the disk cache, not the in-process one
    vectors, status, _ = R.embed_texts(["hello"], repo_root=tmp_path, cache_dir=cache, opener=opener)
    assert (status, vectors["hello"], calls) == ("ok", [0.5, 0.25], [["hello"]])
    assert R.cache_stats()["cache_hits"] == 1 and R.cache_stats()["requests"] == 0


def test_cache_entry_from_another_model_is_ignored(tmp_path):
    cache = tmp_path / "emb"
    cache.mkdir(parents=True)
    digest = R.embedding_cache_key("hello")
    (cache / f"{digest}.json").write_text(json.dumps(
        {"model": "text-embedding-3-large", "sha256": digest, "dimensions": 2, "vector": [1, 2]}),
        encoding="utf-8")
    _, status, _ = R.embed_texts(["hello"], repo_root=tmp_path, cache_dir=cache)
    assert status == "missing_key"  # the stale entry was rejected, so a fresh call was needed
    assert R.cache_stats()["cache_misses"] == 1


def test_warm_retrieval_stays_under_50ms():
    vecs = lesson_vectors([1.0, 0.0], [[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    index = R.LessonIndex(LESSONS * 30)
    embedder = stub_embedder(vecs)
    index.retrieve(ARTIFACT_QUERY, limit=3, embedder=embedder)
    t0 = time.perf_counter()
    index.retrieve(ARTIFACT_QUERY, limit=3, embedder=embedder)
    assert (time.perf_counter() - t0) * 1000 < 50


# ---- key lookup ------------------------------------------------------------------------------

def test_environment_key_wins_over_the_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text('OPENAI_API_KEY="sk-from-file"\n', encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert _REAL_KEY_LOOKUP(tmp_path) == "sk-from-env"
    monkeypatch.delenv("OPENAI_API_KEY")
    assert _REAL_KEY_LOOKUP(tmp_path) == "sk-from-file"
    (tmp_path / ".env").write_text("DAYTONA_API_KEY=x\n", encoding="utf-8")
    assert _REAL_KEY_LOOKUP(tmp_path) is None


# ---- receipts written by harness.qodo_lessons.select ------------------------------------------

def test_select_records_a_local_receipt_next_to_the_qodo_receipt(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(
        json.dumps(r) for L in LESSONS for r in (
            {"kind": "lesson", **L}, {"kind": "status", "lesson_id": L["id"], "status": "active"})),
        encoding="utf-8")
    index_path = tmp_path / "lesson_index.json"
    monkeypatch.setattr(Q, "LEDGER", ledger)
    monkeypatch.setattr(Q, "INDEX", index_path)
    monkeypatch.setattr(Q.C, "CASES", [{"id": "CASE_A", "task": f"Then: {ARTIFACT_QUERY}"}])
    monkeypatch.setattr(Q, "_qodo", lambda *a, **kw: {"rules": [
        {"ruleId": 7, "name": f"{Q.RULE_PREFIX}L1 (verification): read the artifact", "score": 0.9}]})

    vecs = lesson_vectors([1.0, 0.0], [[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
    Q.select(top_k=3, embedder=stub_embedder(vecs))

    doc = json.loads(index_path.read_text(encoding="utf-8"))
    assert doc["index"]["CASE_A"] == ["L1"]  # Qodo remains the selector
    assert doc["retrieval"]["embedding_model"] == "text-embedding-3-small"
    assert doc["retrieval"]["cosine_threshold"] == R.DEFAULT_COSINE_THRESHOLD
    assert doc["retrieval"]["rrf_k"] == 60
    assert doc["qodo_agreement"]["compared"] == 1

    local = doc["receipts"]["CASE_A"]["local"]
    assert local["dense_status"] == "ok" and local["dense_error"] is None
    assert local["selected"][0] == "L1"
    hit = local["hits"][0]
    assert hit["lesson_id"] == "L1" and hit["gate"] == "passed"
    assert "dense" in hit["channels_fired"] and "qodo" in hit["channels_fired"]
    assert hit["channels"]["bm25"]["rank"] == 1 and hit["cosine"] == pytest.approx(1.0)
    assert hit["qodo_rank"] == 1
    assert local["agreement"]["both"] == ["L1"]
    assert "vector" not in index_path.read_text(encoding="utf-8")


def test_select_records_a_local_receipt_when_qodo_is_unavailable(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(
        json.dumps(r) for L in LESSONS for r in (
            {"kind": "lesson", **L}, {"kind": "status", "lesson_id": L["id"], "status": "active"})),
        encoding="utf-8")
    index_path = tmp_path / "lesson_index.json"
    monkeypatch.setattr(Q, "LEDGER", ledger)
    monkeypatch.setattr(Q, "INDEX", index_path)
    monkeypatch.setattr(Q.C, "CASES", [{"id": "CASE_B", "task": f"Then: {ARTIFACT_QUERY}"}])
    monkeypatch.setattr(Q, "_qodo", lambda *a, **kw: None)

    Q.select(top_k=3, embedder=stub_embedder({}, status="error", error="URLError: boom"))

    doc = json.loads(index_path.read_text(encoding="utf-8"))
    assert doc["index"]["CASE_B"] is None  # unknown -> eval-runner falls back to all active lessons
    local = doc["receipts"]["CASE_B"]["local"]
    assert local["dense_status"] == "error" and local["dense_error"] == "URLError: boom"
    assert local["selected"] == ["L1"]  # BM25 + n-gram still selected a lesson
    assert local["hits"][0]["gate"] == "not_evaluated"
    assert doc["retrieval"]["dense_status_counts"] == {"error": 1}
