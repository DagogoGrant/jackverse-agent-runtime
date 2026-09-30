"""Evaluation metrics for Memory Retrieval Benchmark Suite."""

from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
from typing import Any


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    category: str
    query: str
    retrieved_ids: list[str]
    expected_ids: set[str]
    acceptable_ids: set[str]
    is_no_match: bool
    is_lifecycle: bool
    ineligible_ids: set[str]
    hit_1: float
    hit_3: float
    recall_3: float
    mrr: float
    is_abstention: bool
    is_false_retrieval: bool
    lifecycle_violation: bool
    latency_ms: float


@dataclass(frozen=True)
class BenchmarkSummary:
    system_name: str
    total_cases: int
    relevant_cases: int
    no_match_cases: int
    lifecycle_cases: int
    hit_1: float
    hit_3: float
    recall_3: float
    mrr: float
    abstention_accuracy: float
    false_retrieval_rate: float
    lifecycle_violations: int
    latency_median_ms: float
    latency_mean_ms: float
    latency_std_ms: float
    latency_p95_ms: float
    cold_load_seconds: float = 0.0
    indexing_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_name": self.system_name,
            "total_cases": self.total_cases,
            "relevant_cases": self.relevant_cases,
            "no_match_cases": self.no_match_cases,
            "lifecycle_cases": self.lifecycle_cases,
            "hit_1": round(self.hit_1, 4),
            "hit_3": round(self.hit_3, 4),
            "recall_3": round(self.recall_3, 4),
            "mrr": round(self.mrr, 4),
            "abstention_accuracy": round(self.abstention_accuracy, 4),
            "false_retrieval_rate": round(self.false_retrieval_rate, 4),
            "lifecycle_violations": self.lifecycle_violations,
            "latency_median_ms": round(self.latency_median_ms, 2),
            "latency_mean_ms": round(self.latency_mean_ms, 2),
            "latency_std_ms": round(self.latency_std_ms, 2),
            "latency_p95_ms": round(self.latency_p95_ms, 2),
            "cold_load_seconds": round(self.cold_load_seconds, 3),
            "indexing_seconds": round(self.indexing_seconds, 3),
        }


def evaluate_case(
    case_id: str,
    category: str,
    query: str,
    retrieved_ids: list[str],
    expected_ids: set[str],
    acceptable_ids: set[str] | None = None,
    is_no_match: bool = False,
    is_lifecycle: bool = False,
    ineligible_ids: set[str] | None = None,
    latency_ms: float = 0.0,
) -> CaseResult:
    """Compute individual metrics for a single query benchmark execution."""
    acc_ids = (expected_ids | acceptable_ids) if acceptable_ids else expected_ids
    inelig_ids = ineligible_ids or set()

    if is_no_match:
        is_abstention = len(retrieved_ids) == 0
        is_false_retrieval = not is_abstention
        return CaseResult(
            case_id=case_id,
            category=category,
            query=query,
            retrieved_ids=retrieved_ids,
            expected_ids=expected_ids,
            acceptable_ids=acceptable_ids or set(),
            is_no_match=True,
            is_lifecycle=is_lifecycle,
            ineligible_ids=inelig_ids,
            hit_1=0.0,
            hit_3=0.0,
            recall_3=0.0,
            mrr=0.0,
            is_abstention=is_abstention,
            is_false_retrieval=is_false_retrieval,
            lifecycle_violation=False,
            latency_ms=latency_ms,
        )

    # Relevant query evaluation
    hit_1 = 1.0 if retrieved_ids and retrieved_ids[0] in acc_ids else 0.0
    top_3 = retrieved_ids[:3]
    hit_3 = 1.0 if any(rid in acc_ids for rid in top_3) else 0.0

    relevant_found = sum(1 for rid in top_3 if rid in expected_ids)
    recall_3 = (relevant_found / len(expected_ids)) if expected_ids else 0.0

    mrr = 0.0
    for idx, rid in enumerate(retrieved_ids):
        if rid in acc_ids:
            mrr = 1.0 / (idx + 1)
            break

    lifecycle_violation = any(rid in inelig_ids for rid in retrieved_ids)

    return CaseResult(
        case_id=case_id,
        category=category,
        query=query,
        retrieved_ids=retrieved_ids,
        expected_ids=expected_ids,
        acceptable_ids=acceptable_ids or set(),
        is_no_match=False,
        is_lifecycle=is_lifecycle,
        ineligible_ids=inelig_ids,
        hit_1=hit_1,
        hit_3=hit_3,
        recall_3=recall_3,
        mrr=mrr,
        is_abstention=False,
        is_false_retrieval=False,
        lifecycle_violation=lifecycle_violation,
        latency_ms=latency_ms,
    )


def aggregate_results(
    system_name: str,
    case_results: list[CaseResult],
    cold_load_seconds: float = 0.0,
    indexing_seconds: float = 0.0,
) -> BenchmarkSummary:
    """Aggregate per-case results into summary benchmark metrics."""
    relevant = [c for c in case_results if not c.is_no_match]
    no_matches = [c for c in case_results if c.is_no_match]
    lifecycle = [c for c in case_results if c.is_lifecycle]

    hit_1 = statistics.mean([c.hit_1 for c in relevant]) if relevant else 0.0
    hit_3 = statistics.mean([c.hit_3 for c in relevant]) if relevant else 0.0
    recall_3 = statistics.mean([c.recall_3 for c in relevant]) if relevant else 0.0
    mrr = statistics.mean([c.mrr for c in relevant]) if relevant else 0.0

    abstention_acc = (
        statistics.mean([1.0 if c.is_abstention else 0.0 for c in no_matches])
        if no_matches
        else 1.0
    )
    false_retrieval = (
        statistics.mean([1.0 if c.is_false_retrieval else 0.0 for c in no_matches])
        if no_matches
        else 0.0
    )

    total_violations = sum(1 for c in lifecycle if c.lifecycle_violation)

    latencies = [c.latency_ms for c in case_results if c.latency_ms > 0]
    if latencies:
        median_lat = statistics.median(latencies)
        mean_lat = statistics.mean(latencies)
        std_lat = statistics.stdev(latencies) if len(latencies) > 1 else 0.0
        sorted_lats = sorted(latencies)
        p95_idx = int(math.ceil(0.95 * len(sorted_lats))) - 1
        p95_lat = sorted_lats[max(0, p95_idx)]
    else:
        median_lat = mean_lat = std_lat = p95_lat = 0.0

    return BenchmarkSummary(
        system_name=system_name,
        total_cases=len(case_results),
        relevant_cases=len(relevant),
        no_match_cases=len(no_matches),
        lifecycle_cases=len(lifecycle),
        hit_1=hit_1,
        hit_3=hit_3,
        recall_3=recall_3,
        mrr=mrr,
        abstention_accuracy=abstention_acc,
        false_retrieval_rate=false_retrieval,
        lifecycle_violations=total_violations,
        latency_median_ms=median_lat,
        latency_mean_ms=mean_lat,
        latency_std_ms=std_lat,
        latency_p95_ms=p95_lat,
        cold_load_seconds=cold_load_seconds,
        indexing_seconds=indexing_seconds,
    )
