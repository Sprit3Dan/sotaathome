import unittest

from evaluator.allocate import _round_split, build_next_jobs
from evaluator.frontier import build_frontier
from evaluator.models import CandidateAggregate, EvaluatorConfig, FrontierEntry, PromotionDecision


class EvaluatorBehaviorTests(unittest.TestCase):
    def _aggregate(
        self,
        candidate_id: str,
        parent_candidate_id: str,
        resource_class: str,
        best_norm: float,
        valid_run_count: int = 1,
    ) -> CandidateAggregate:
        return CandidateAggregate(
            candidate_id=candidate_id,
            parent_candidate_id=parent_candidate_id,
            resource_class=resource_class,
            primary_metric_name="val_bpb",
            primary_metric_direction="min",
            run_count=valid_run_count,
            valid_run_count=valid_run_count,
            success_count=valid_run_count,
            unique_worker_count=1,
            unique_seed_count=1,
            mean_delta_primary_metric=-0.001,
            median_delta_primary_metric=-0.001,
            stddev_delta_primary_metric=None,
            best_delta_primary_metric=-0.001,
            worst_delta_primary_metric=-0.001,
            mean_normalized_delta=best_norm,
            best_normalized_delta=best_norm,
            improved_run_count=1,
            run_ids=[f"run-{candidate_id}"],
        )

    def test_round_split_distributes_remainder_beyond_exploit(self):
        exploit, explore, verify = _round_split(3, 0.7, 0.2, 0.1)
        self.assertEqual((exploit, explore, verify), (2, 1, 0))

    def test_frontier_deduplicates_candidate_between_near_miss_and_diversity(self):
        config = EvaluatorConfig(near_miss_delta=0.0005, diversity_slots=2)
        aggregates = [
            self._aggregate("cand-a", "parent-a", "2060-12gb", 0.0008),
            self._aggregate("cand-b", "parent-b", "2060-12gb", -0.0002),
        ]
        decisions = [
            PromotionDecision(
                candidate_id="cand-a",
                parent_candidate_id="parent-a",
                resource_class="2060-12gb",
                promotion_level="none",
                reasons=["near miss"],
                stats={"best_normalized_delta": 0.0008},
            ),
            PromotionDecision(
                candidate_id="cand-b",
                parent_candidate_id="parent-b",
                resource_class="2060-12gb",
                promotion_level="none",
                reasons=["no improvement"],
                stats={"best_normalized_delta": -0.0002},
            ),
        ]

        frontier = build_frontier(aggregates, decisions, config)
        roles_by_candidate = {}
        for entry in frontier:
            roles_by_candidate.setdefault(entry.candidate_id, set()).add(entry.role)

        self.assertIn("near_miss", roles_by_candidate["cand-a"])
        self.assertNotIn("diversity", roles_by_candidate["cand-a"])

    def test_verify_pool_uses_silver_and_near_miss_roles(self):
        config = EvaluatorConfig(
            jobs_per_resource_class=10,
            exploit_ratio=0.7,
            explore_ratio=0.2,
            verify_ratio=0.1,
        )
        frontier = [
            FrontierEntry(
                candidate_id="cand-silver",
                parent_candidate_id="parent-s",
                resource_class="2060-12gb",
                role="silver",
                promotion_level="silver",
                score_hint=0.0012,
                rationale="",
            ),
            FrontierEntry(
                candidate_id="cand-near",
                parent_candidate_id="parent-n",
                resource_class="2060-12gb",
                role="near_miss",
                promotion_level="none",
                score_hint=0.0006,
                rationale="",
            ),
            FrontierEntry(
                candidate_id="cand-div",
                parent_candidate_id="parent-d",
                resource_class="2060-12gb",
                role="diversity",
                promotion_level="n/a",
                score_hint=0.0,
                rationale="",
            ),
        ]

        jobs, _summary = build_next_jobs(frontier, config)
        verify_jobs = [job for job in jobs if job.job_type == "verify"]
        self.assertTrue(verify_jobs)
        verify_parents = {job.parent_candidate_id for job in verify_jobs}
        self.assertTrue(verify_parents.issubset({"cand-silver", "cand-near"}))


if __name__ == "__main__":
    unittest.main()
