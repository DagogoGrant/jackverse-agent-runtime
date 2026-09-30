# Memory Subsystem Retrieval Benchmark: Empirical Evaluation & Decision Hardening Report

> **Research Question**: Does dense semantic and hybrid (FTS5 BM25 + Dense + RRF) retrieval statistically outperform deterministic lexical token overlap on real developer queries, while strictly preserving memory lifecycle safety and context budget constraints?

## 1. Frozen Experimental Hyperparameters (Dev Set Calibration)

Hyperparameters were calibrated strictly on the physically isolated development corpus (`DEV_CASES`), frozen to `frozen_hyperparameters.json`, and evaluated without subsequent retuning.

- **Primary Dense Model**: `Snowflake/snowflake-arctic-embed-s` (dim=384, revision=default)
- **Arctic Query Config**: `qpname=query` (fingerprint: `Snowflake/snowflake-arctic-embed-s@default:dim=384:qpname=query:l2=1`)
- **Dense Similarity Threshold (tau)**: `0.55`
- **Sparse Evidence Gate**: `sparse_min_coverage=0.35`, `sparse_bm25_cutoff=-2.5`
- **Sparse Stopword Policy**: `v1-standard-english-34`
- **Hybrid Relevance Gate**: `True` (active post-fusion gate)
- **Hybrid RRF Constant (k)**: `10`
- **Sparse / Dense Weights**: `1.0 : 1.0`
- **Candidate Pool Size**: `10`
- **Index Readiness Policy**: `incremental`
- **Cold Load Times**: Arctic = `0.126s`, BGE = `7.038s`

---

## 2. Historical Step 4 Baseline & Gated Hybrid Ablation (Held-Out Corpus)

The historical Step 4 snapshot revealed that while Raw Hybrid achieved strong recall, blind fusion of uncalibrated BM25 resulted in **0% No-Match Accuracy** (100% false retrieval) and degraded Hit@1 (0.833 vs 1.000 for Dense). Introducing the post-fusion Hybrid Relevance Gate in Step 4.5 eliminated unevidenced false retrievals:

| System | Hit@1 | Hit@3 | Recall@3 | MRR | No-Match Acc | False Ret. | Violations | Median Latency |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| **System A (Lexical)** | 0.611 | 0.778 | 0.778 | 0.676 | 0.000 | 1.000 | 0 | 0.6 ms |
| **System B (BM25)** | 0.722 | 0.889 | 0.889 | 0.796 | 0.000 | 1.000 | 0 | 0.6 ms |
| **System C1 (Dense BGE)** | 1.000 | 1.000 | 1.000 | 1.000 | 0.667 | 0.333 | 0 | 17.1 ms |
| **System C2 (Dense Arctic)** | 1.000 | 1.000 | 1.000 | 1.000 | 0.833 | 0.167 | 0 | 17.4 ms |
| **System D (Raw Hybrid RRF)** | 0.833 | 1.000 | 1.000 | 0.917 | 0.000 | 1.000 | 0 | 20.7 ms |
| **System D (Gated Hybrid RRF)** | 1.000 | 1.000 | 1.000 | 1.000 | 0.667 | 0.333 | 0 | 17.4 ms |

> **Ablation Finding**: Gated Hybrid restored Hit@1 from **0.833 to 1.000**, maintained **Hit@3 = 1.000**, and elevated No-Match Accuracy from **0.000 to 0.667** by discarding unevidenced sparse distractors.

---

## 3. Untouched Final Confirmation Evaluation (20 Fresh Queries)

To maintain methodological rigor, final validation was executed once on the completely fresh, untouched `CONFIRMATION_CASES` dataset spanning cloud infrastructure, security protocols, developer preferences, semantic distractors, and out-of-domain queries:

| System | Hit@1 | Hit@3 | Recall@3 | MRR | No-Match Acc | False Ret. | Violations | Median Latency |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| **System A (Lexical)** | 0.846 | 0.846 | 0.846 | 0.846 | 0.000 | 1.000 | 0 | 0.5 ms |
| **System B (BM25)** | 0.923 | 1.000 | 1.000 | 0.962 | 0.000 | 1.000 | 0 | 0.7 ms |
| **System C1 (Dense BGE)** | 1.000 | 1.000 | 1.000 | 1.000 | 0.714 | 0.286 | 0 | 16.3 ms |
| **System C2 (Dense Arctic)** | 1.000 | 1.000 | 1.000 | 1.000 | 0.857 | 0.143 | 0 | 17.4 ms |
| **System D (Gated Hybrid BGE)** | 1.000 | 1.000 | 1.000 | 1.000 | 0.714 | 0.286 | 0 | 16.8 ms |
| **System D2 (Gated Hybrid Arctic)** | 1.000 | 1.000 | 1.000 | 1.000 | 0.857 | 0.143 | 0 | 18.8 ms |

---

## 4. Long-Memory Positional Analysis (>512 Tokens)

Evaluated retrieval of a secret fact (`ALPHA-BRAVO-999`) placed at different token depths in ~4,500-character entries:

| Retrieval System | Target at Beginning (<100 tokens) | Target in Middle (~300 tokens) | Target Past Boundary (~700 tokens) |
|:---|:---:|:---:|:---:|
| **BM25 (FTS5)** | ✓ Found (Rank 1) | ✓ Found (Rank 3) | ✓ Found (Rank 2) |
| **BGE-small (512 max tokens)** | ✓ Found (Rank 1) | ✓ Found (Rank 3) | ✓ Found (Rank 2) |
| **Arctic-small (512 max tokens)** | ✓ Found (Rank 1) | ✓ Found (Rank 3) | ✓ Found (Rank 2) |

> **Important Scope Limitation**: SQLite FTS5 BM25 indexes the full document without length truncation, allowing BM25 to rescue information beyond the dense encoder's 512-token truncation window **WHEN useful lexical terms are present**. This does **not** imply that Hybrid generally solves semantic retrieval beyond 512 tokens: if an entry beyond token 512 is queried using abstract paraphrases with zero lexical overlap, both the truncated dense representation and BM25 can fail.

---

## 5. Large-Corpus Scaling Test (~1,000 Records) & Architectural Caveats
- **Corpus Size**: 1 target needle (`scale_target_needle`) + 999 distractor records.
- **Pre-indexing Time (1,000 records)**: `5.26s` (~4.9 ms / record).
- **Legacy `candidate_limit=500` Failure**: In legacy mode, only the top 500 recent records are loaded, completely blinding retrieval to needle #1000.
- **Full-Corpus `candidate_limit=None` Retrieval**:

| System | Needle Retrieved | Needle Rank | Warm Latency |
|:---|:---:|:---:|---:|
| **System A (Lexical)** | ✓ Yes | 1 | 18.8 ms |
| **System B (BM25)** | ✓ Yes | 1 | 12.4 ms |
| **System C1 (Dense BGE)** | ✓ Yes | 1 | 109.7 ms |
| **System D (Gated Hybrid BGE)** | ✓ Yes | 1 | 151.6 ms |

> **Scaling Caveat**: In `candidate_limit=None` mode, dense semantic retrieval computes an **O(N) dot-product scan** over stored memory vectors. While query latency on 1,000 records (~115 ms) is well within the 200 ms interactive budget on CPU, this linear scan will scale with corpus size. Therefore, `candidate_limit=None` is appropriate for primary lab correctness, but `candidate_limit` must remain explicitly configurable.

---

## 6. Model Comparison & Production Selection

| Metric / Property | BAAI/bge-small-en-v1.5 | Snowflake/snowflake-arctic-embed-s | Winner |
|:---|:---|:---|:---|
| **Cold Load Time (CPU)** | `7.038s` | `0.126s` | **Arctic (~37x faster)** |
| **Confirmation Hit@1** | 1.000 | 1.000 | Tie |
| **Confirmation Hit@3** | 1.000 | 1.000 | Tie |
| **Confirmation MRR** | 1.000 | 1.000 | Tie |
| **Confirmation No-Match Acc** | 0.714 | **0.857** | **Arctic (+14.3%)** |
| **Gated Hybrid Integration** | Full support | Full support | Tie |

Both models perform exceptionally under the Gated Hybrid policy. Arctic provides significantly faster cold loading and superior out-of-domain abstention.

---

## 7. Production Index-Readiness & Wiring Recommendations
1. **Default Production Strategy**: **Gated Hybrid RRF** (`HybridRRFStrategy(enable_relevance_gate=True)`).
2. **Encoder**: `Snowflake/snowflake-arctic-embed-s` (evidence-backed lab default) with `BAAI/bge-small-en-v1.5` supported.
3. **Configurable Index-Readiness Policy**: Startup embedding backfill is configured via `index_readiness: 'eager' | 'incremental' | 'skip'`:
   - `eager`: Startup loads model and backfills all missing/stale embeddings before serving queries.
   - `incremental` (Default): Startup does not backfill; newly admitted/updated eligible memories are embedded at admission time; pre-existing missing vectors follow missing_embedding policy.
   - `skip`: No automatic document indexing; dense branch skips missing/stale vectors; BM25 remains available.
4. **Corpus Scope**: Set `candidate_limit: None` for complete corpus coverage, with configurable numeric limits for resource-constrained deployments.
