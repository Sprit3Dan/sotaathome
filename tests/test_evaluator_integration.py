import json
import shutil
import tempfile
import unittest
from pathlib import Path

from evaluator.cli import run_evaluation
from evaluator.models import EvaluatorConfig


class EvaluatorIntegrationTests(unittest.TestCase):
    def test_run_evaluation_end_to_end_writes_outputs(self):
        repo_root = Path(__file__).resolve().parents[1]
        sample_src = repo_root / "evaluator" / "examples" / "sample_artifacts"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            input_dir = temp_root / "input"
            output_dir = temp_root / "output"
            shutil.copytree(sample_src, input_dir)

            summary = run_evaluation(input_dir, output_dir, EvaluatorConfig())

            self.assertGreater(summary["run_count"], 0)
            self.assertGreater(summary["valid_run_count"], 0)
            self.assertEqual(summary["run_count"], summary["valid_run_count"] + summary["invalid_run_count"])

            expected_outputs = {
                "runs.json",
                "aggregates.json",
                "promotions.json",
                "frontier.json",
                "next_jobs.json",
                "allocation_summary.json",
            }
            written_files = {path.name for path in output_dir.glob("*.json")}
            self.assertTrue(expected_outputs.issubset(written_files))

            promotions = json.loads((output_dir / "promotions.json").read_text(encoding="utf-8"))
            levels = {item["promotion_level"] for item in promotions}
            self.assertIn("gold", levels)
            self.assertIn("none", levels)


if __name__ == "__main__":
    unittest.main()
