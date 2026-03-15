#!/usr/bin/env python3
"""
agent_loop.py — built-in OpenAI gpt-4o agent for autoresearch pods.

CLI:
    python agent_loop.py \
        --workspace      /workspace/{run_id} \
        --output-dir     /artifacts/output \
        --run-id         {run_id} \
        --max-iterations {m} \
        --time-budget    {t}   # seconds per train.py execution

Writes OUTPUT_DIR/iter-NNN-{run_id}.log per iteration.
Exit 0 = at least one successful iteration.
Exit 1 = all iterations failed.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel


class TrainPyProposal(BaseModel):
    reasoning: str
    train_py: str


def log(msg: str):
    print(f"[agent] {msg}", flush=True)


def parse_val_bpb(text: str) -> float | None:
    m = re.search(r"val_bpb:\s+([0-9.]+)", text)
    return float(m.group(1)) if m else None


def run_train(workspace: Path, timeout: int) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "train.py"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )
    return result.returncode, result.stdout + result.stderr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-iterations", type=int, required=True)
    parser.add_argument("--time-budget", type=int, required=True)
    args = parser.parse_args()

    workspace = Path(args.workspace)
    output_dir = Path(args.output_dir)
    run_id = args.run_id
    max_iters = args.max_iterations
    time_budget = args.time_budget

    openai_client = OpenAI()
    model = os.environ.get("OPENAI_MODEL", "gpt-5.4")

    parent_val_bpb_str = os.environ.get("AUTORESEARCH_PARENT_METRIC_VALUE", "")
    parent_val_bpb: float | None = float(parent_val_bpb_str) if parent_val_bpb_str else None
    if parent_val_bpb is not None:
        log(f"Parent val_bpb to beat: {parent_val_bpb}")

    system_prompt = (workspace / "program.md").read_text()

    history: list[dict] = []
    ran_at_least_one = False
    results_tsv = workspace / "results.tsv"

    for i in range(1, max_iters + 1):
        iter_tag = f"{i:03d}"
        iter_log = output_dir / f"iter-{iter_tag}-{run_id}.log"
        log(f"Iteration {i}/{max_iters}")

        current_code = (workspace / "train.py").read_text()

        history_lines = []
        for h in history:
            if h.get("error"):
                history_lines.append(f"iter {h['iteration']}: CRASH — {h['error'][:300]}")
            else:
                history_lines.append(f"iter {h['iteration']}: val_bpb={h.get('val_bpb', 'N/A')}")

        user_msg = f"Iteration {i}/{max_iters}.\n\n"
        if parent_val_bpb is not None:
            user_msg += f"Target to beat: the parent generation achieved val_bpb={parent_val_bpb}. You must go lower.\n\n"
        user_msg += f"Current train.py:\n```python\n{current_code}\n```\n"
        if history_lines:
            user_msg += "\nPrevious results this run:\n" + "\n".join(history_lines) + "\n"
        user_msg += "\nPropose a new train.py that achieves lower val_bpb. Return the COMPLETE file with no omissions, ellipses, or '...' placeholders — every line must be present and valid Python."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        try:
            completion = openai_client.beta.chat.completions.parse(
                model=model,
                messages=messages,
                response_format=TrainPyProposal,
            )
            proposal = completion.choices[0].message.parsed
            new_code = proposal.train_py
            log(f"Reasoning: {proposal.reasoning[:200]}")
            # Validate syntax — reject truncated/placeholder responses
            try:
                compile(new_code, "train.py", "exec")
            except SyntaxError as syn_err:
                log(f"WARNING: LLM returned invalid Python ({syn_err}). Keeping current train.py.")
                new_code = current_code
        except Exception as exc:
            log(f"WARNING: OpenAI call failed: {exc}. Keeping current train.py.")
            new_code = current_code

        bak = workspace / "train.py.bak"
        shutil.copy(workspace / "train.py", bak)
        (workspace / "train.py").write_text(new_code)

        try:
            exit_code, output = run_train(workspace, timeout=time_budget + 240)
        except subprocess.TimeoutExpired:
            exit_code = -1
            output = f"TimeoutExpired after {time_budget + 240}s"

        iter_log.write_text(output)
        log(f"train.py exited {exit_code}, log -> {iter_log}")

        if exit_code != 0:
            log("train.py crashed. Restoring backup.")
            shutil.copy(bak, workspace / "train.py")
            history.append({"iteration": i, "error": output[-500:]})
            with open(results_tsv, "a") as f:
                f.write(f"{i}\tFAILED\t\n")
            continue

        val_bpb = parse_val_bpb(output)
        log(f"val_bpb={val_bpb}")
        history.append({"iteration": i, "val_bpb": val_bpb})
        with open(results_tsv, "a") as f:
            f.write(f"{i}\t{val_bpb}\t\n")
        ran_at_least_one = True

    if not ran_at_least_one:
        log("All iterations failed.")
        sys.exit(1)

    log("Agent loop complete.")


if __name__ == "__main__":
    main()
