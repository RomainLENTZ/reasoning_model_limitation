"""
Tower of Hanoi Benchmark - Reproducing "Illusion of Thinking" on Claude 4.6
Based on: https://arxiv.org/abs/2506.06941 (Apple Research)

Usage:
  python hanoi_benchmark.py                          # Sonnet 4.6 without thinking
  python hanoi_benchmark.py --thinking               # Sonnet 4.6 with adaptive thinking
  python hanoi_benchmark.py --model opus             # Opus 4.6 without thinking
  python hanoi_benchmark.py --model opus --thinking  # Opus 4.6 with thinking
  python hanoi_benchmark.py --compare                # Compare without vs with thinking

Pricing (Feb 2026):
  Sonnet 4.6 : $3/$15 per million input/output tokens
  Opus 4.6   : $5/$25 per million input/output tokens
"""

import anthropic
import json
import re
import argparse
import matplotlib.pyplot as plt
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

# ─── Simulator ────────────────────────────────────────────────────────────────

class HanoiSimulator:
    def __init__(self, n_disks: int):
        self.n = n_disks
        self.pegs = [list(range(n_disks, 0, -1)), [], []]

    def is_valid_move(self, disk: int, from_peg: int, to_peg: int) -> tuple[bool, str]:
        if from_peg not in range(3) or to_peg not in range(3):
            return False, f"Peg out of bounds: {from_peg} -> {to_peg}"
        if not self.pegs[from_peg]:
            return False, f"Peg {from_peg} is empty"
        if self.pegs[from_peg][-1] != disk:
            return False, f"Disk {disk} is not on top of peg {from_peg} (top={self.pegs[from_peg][-1]})"
        if self.pegs[to_peg] and self.pegs[to_peg][-1] < disk:
            return False, f"Cannot place disk {disk} on top of disk {self.pegs[to_peg][-1]}"
        return True, "ok"

    def execute_move(self, disk: int, from_peg: int, to_peg: int) -> tuple[bool, str]:
        valid, msg = self.is_valid_move(disk, from_peg, to_peg)
        if not valid:
            return False, msg
        self.pegs[to_peg].append(self.pegs[from_peg].pop())
        return True, "ok"

    def is_solved(self) -> bool:
        return self.pegs[2] == list(range(self.n, 0, -1))

    def validate_solution(self, moves: list) -> tuple[bool, int, str]:
        sim = HanoiSimulator(self.n)
        for i, move in enumerate(moves):
            try:
                disk, from_peg, to_peg = int(move[0]), int(move[1]), int(move[2])
            except (IndexError, ValueError):
                return False, i, f"Invalid format at move {i}: {move}"
            ok, msg = sim.execute_move(disk, from_peg, to_peg)
            if not ok:
                return False, i, f"Invalid move #{i}: {msg}"
        return sim.is_solved(), len(moves), "solved" if sim.is_solved() else "incorrect final state"


# ─── Move Extraction ──────────────────────────────────────────────────────────

def extract_moves(text: str) -> Optional[list]:
    patterns = [
        r'moves\s*=\s*(\[\s*\[.*?\]\s*\])',
        r'<answer>.*?moves\s*=\s*(\[\s*\[.*?\]\s*\])',
        r'(\[\s*\[\d+,\s*\d+,\s*\d+\](?:,\s*\[\d+,\s*\d+,\s*\d+\])*\s*\])',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                moves = json.loads(match.group(1))
                if isinstance(moves, list) and all(len(m) == 3 for m in moves):
                    return moves
            except json.JSONDecodeError:
                continue
    return None


# ─── Prompt ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a helpful assistant. Solve this puzzle for me.

There are three pegs and n disks of different sizes stacked on the first peg. The disks are numbered from 1 (smallest) to n (largest). Disk moves in this puzzle should follow:
1. Only one disk can be moved at a time.
2. Each move consists of taking the upper disk from one stack and placing it on top of another stack.
3. A larger disk may not be placed on top of a smaller disk.

The goal is to move the entire stack to the third peg.

Example: With 3 disks numbered 1 (smallest), 2, and 3 (largest), the initial state is [[3, 2, 1], [], []], and a solution might be:
moves = [[1, 0, 2], [2, 0, 1], [1, 2, 1], [3, 0, 2], [1, 1, 0], [2, 1, 2], [1, 0, 2]]

Requirements:
- The positions are 0-indexed (the leftmost peg is 0).
- Ensure your final answer includes the complete list of moves in the format:
moves = [[disk_id, from_peg, to_peg], ...]"""

def make_user_prompt(n: int) -> str:
    disks = list(range(n, 0, -1))
    return f"""I have a puzzle with {n} disks of different sizes.

Initial configuration:
- Peg 0: {disks} (bottom to top, largest first)
- Peg 1: (empty)
- Peg 2: (empty)

Goal configuration:
- Peg 0: (empty)
- Peg 1: (empty)
- Peg 2: {disks} (bottom to top, largest first)

Rules:
- Only one disk can be moved at a time.
- Only the top disk from any stack can be moved.
- A larger disk may not be placed on top of a smaller disk.

Find the sequence of moves to transform the initial configuration into the goal configuration.
Your final answer must be in the format: moves = [[disk_id, from_peg, to_peg], ...]"""


# ─── Benchmark ────────────────────────────────────────────────────────────────

@dataclass
class Result:
    n: int
    success: bool
    n_valid_moves: int
    total_moves: int
    error: str
    tokens_used: int
    raw_response: str = field(repr=False, default="")


def estimate_cost(results: list, model: str) -> float:
    input_price = 5.0 if "opus" in model else 3.0
    output_price = 25.0 if "opus" in model else 15.0
    total_tokens = sum(r.tokens_used for r in results)
    return (total_tokens * 0.6 / 1_000_000 * input_price +
            total_tokens * 0.4 / 1_000_000 * output_price)


def call_with_streaming(client, kwargs: dict, max_retries: int = 4) -> tuple[str, int]:
    """
    Use streaming to handle long requests (required for max_tokens > ~4096 with thinking).
    Retries automatically on overloaded errors with exponential backoff.
    Returns (full_text, total_tokens).
    """
    import time

    for attempt in range(max_retries):
        try:
            with client.messages.stream(**kwargs) as stream:
                final = stream.get_final_message()
                text = ""
                for block in final.content:
                    if hasattr(block, "text"):
                        text += block.text
                return text, final.usage.input_tokens + final.usage.output_tokens

        except Exception as e:
            err = str(e).lower()
            is_overloaded = "overloaded" in err
            is_incomplete = "incomplete" in err or "chunked" in err
            is_retryable = is_overloaded or is_incomplete

            if is_retryable and attempt < max_retries - 1:
                wait = 30 * (2 ** attempt)  # 30s, 60s, 120s
                reason = "overloaded" if is_overloaded else "incomplete response"
                print(f" [{reason}, retry {attempt+1}/{max_retries-1} in {wait}s...]", end=" ", flush=True)
                time.sleep(wait)
            else:
                raise


def run_benchmark(
    n_range: range = range(1, 11),
    samples_per_n: int = 3,
    model: str = "claude-sonnet-4-6",
    thinking: bool = False,
    max_tokens: int = 8000
) -> list[Result]:
    client = anthropic.Anthropic()
    results = []
    thinking_label = "with adaptive thinking" if thinking else "without thinking"
    print(f"\nModel: {model} | {thinking_label}")
    print(f"   N={n_range.start}->{n_range.stop-1} | {samples_per_n} samples per N | max_tokens={max_tokens}")

    for n in n_range:
        min_moves = 2**n - 1
        print(f"\n{'='*50}")
        print(f"N={n} disks | Minimum moves required: {min_moves}")
        print(f"{'='*50}")

        n_success = 0
        for sample in range(samples_per_n):
            print(f"  Sample {sample+1}/{samples_per_n}...", end=" ", flush=True)

            kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": make_user_prompt(n)}],
            }

            if thinking:
                kwargs["thinking"] = {"type": "adaptive"}

            try:
                # Always use streaming — required for large max_tokens or thinking mode
                text, total_tokens = call_with_streaming(client, kwargs)

                moves = extract_moves(text)

                if moves is None:
                    result = Result(n=n, success=False, n_valid_moves=0,
                                    total_moves=0, error="No moves extracted",
                                    tokens_used=total_tokens, raw_response=text[:500])
                    print(f"FAIL (no moves extracted) | {total_tokens} tokens")
                else:
                    sim = HanoiSimulator(n)
                    success, valid_count, msg = sim.validate_solution(moves)
                    result = Result(n=n, success=success, n_valid_moves=valid_count,
                                    total_moves=len(moves), error=msg,
                                    tokens_used=total_tokens, raw_response=text[:500])
                    status = "OK" if success else f"FAIL ({msg[:40]})"
                    print(f"{status} | {valid_count}/{len(moves)} valid moves | {total_tokens} tokens")
                    if success:
                        n_success += 1

            except Exception as e:
                result = Result(n=n, success=False, n_valid_moves=0,
                                total_moves=0, error=str(e), tokens_used=0)
                print(f"ERROR: {e}")

            results.append(result)

        accuracy = n_success / samples_per_n * 100
        print(f"  -> Accuracy N={n}: {accuracy:.0f}% ({n_success}/{samples_per_n})")

    cost = estimate_cost(results, model)
    print(f"\nEstimated cost: ~${cost:.2f}")
    return results


# ─── Plotting ─────────────────────────────────────────────────────────────────

def plot_results(all_series: list[tuple[list[Result], str]], filename: str = "hanoi_benchmark.png"):
    colors_palette = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12']
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

    title = "Tower of Hanoi Benchmark — " + " vs ".join(label for _, label in all_series)
    fig.suptitle(f"{title}\n(reproduction of 'The Illusion of Thinking', Apple Research 2025)",
                 fontsize=12, fontweight='bold')

    for idx, (results, label) in enumerate(all_series):
        color = colors_palette[idx % len(colors_palette)]
        n_values = sorted(set(r.n for r in results))

        accuracy_by_n = {}
        tokens_by_n = {}
        for n in n_values:
            n_results = [r for r in results if r.n == n]
            accuracy_by_n[n] = sum(r.success for r in n_results) / len(n_results) * 100
            tokens_by_n[n] = np.mean([r.tokens_used for r in n_results])

        ax1.plot(n_values, list(accuracy_by_n.values()), 'o-',
                 color=color, linewidth=2.5, markersize=8,
                 markerfacecolor='white', markeredgewidth=2.5, label=label)

        ax2.plot(n_values, list(tokens_by_n.values()), 'o-',
                 color=color, linewidth=2.5, markersize=8,
                 markerfacecolor='white', markeredgewidth=2.5, label=label)

        collapse_n = next((n for n in n_values if accuracy_by_n[n] == 0), None)
        if collapse_n:
            ax1.axvline(x=collapse_n, color=color, linestyle=':', alpha=0.5)

    ax1.set_xlabel("Complexity (number of disks)", fontsize=11)
    ax1.set_ylabel("Accuracy (%)", fontsize=11)
    ax1.set_title("Accuracy vs Complexity", fontsize=12)
    ax1.set_ylim(-5, 110)
    ax1.axhline(y=50, color='gray', linestyle='--', alpha=0.4)
    ax1.legend(loc='upper right', fontsize=9)
    ax1.grid(alpha=0.2)

    ax2.set_xlabel("Complexity (number of disks)", fontsize=11)
    ax2.set_ylabel("Average tokens used", fontsize=11)
    ax2.set_title("Reasoning effort (tokens) vs Complexity", fontsize=12)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:,.0f}'))
    ax2.legend(loc='upper left', fontsize=9)
    ax2.grid(alpha=0.2)

    plt.tight_layout()
    plt.savefig(filename, dpi=150, bbox_inches='tight')
    print(f"\nChart saved: {filename}")
    return filename


# ─── Summary & Save ───────────────────────────────────────────────────────────

def print_summary(results: list[Result]):
    n_values = sorted(set(r.n for r in results))
    print("\n" + "="*60)
    print("RESULTS SUMMARY")
    print("="*60)
    print(f"{'N':>4} | {'Accuracy':>10} | {'Min moves':>10} | {'Avg tokens':>12}")
    print("-"*55)
    for n in n_values:
        n_results = [r for r in results if r.n == n]
        acc = sum(r.success for r in n_results) / len(n_results) * 100
        min_moves = 2**n - 1
        avg_tokens = np.mean([r.tokens_used for r in n_results])
        collapse = " <- COLLAPSE" if acc == 0 else ""
        print(f"{n:>4} | {acc:>9.0f}% | {min_moves:>10} | {avg_tokens:>12,.0f}{collapse}")


def save_results(results: list[Result], filename: str):
    raw = [{"n": r.n, "success": r.success, "valid_moves": r.n_valid_moves,
            "total_moves": r.total_moves, "tokens": r.tokens_used, "error": r.error}
           for r in results]
    with open(filename, "w") as f:
        json.dump(raw, f, indent=2)
    print(f"Results saved: {filename}")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tower of Hanoi Benchmark - Illusion of Thinking reproduction")
    parser.add_argument("--model", choices=["sonnet", "opus"], default="sonnet",
                        help="Model to test (default: sonnet)")
    parser.add_argument("--thinking", action="store_true",
                        help="Enable adaptive thinking")
    parser.add_argument("--compare", action="store_true",
                        help="Compare without vs with thinking (2x more expensive)")
    parser.add_argument("--n-max", type=int, default=10,
                        help="Maximum number of disks (default: 10)")
    parser.add_argument("--samples", type=int, default=3,
                        help="Samples per N value (default: 3, paper used: 25)")
    parser.add_argument("--max-tokens", type=int, default=8000,
                        help="Max output tokens per request (default: 8000, paper: 64000)")
    args = parser.parse_args()

    model_id = "claude-opus-4-6" if args.model == "opus" else "claude-sonnet-4-6"
    model_label = "Opus 4.6" if args.model == "opus" else "Sonnet 4.6"
    n_range = range(1, args.n_max + 1)

    print("Tower of Hanoi Benchmark — Illusion of Thinking Reproduction")
    print(f"Based on: arxiv.org/abs/2506.06941 (Apple Research)")

    all_series = []

    if args.compare:
        print(f"\nComparison mode: {model_label} without vs with thinking")
        r1 = run_benchmark(n_range, args.samples, model_id, thinking=False, max_tokens=args.max_tokens)
        print_summary(r1)
        all_series.append((r1, f"{model_label} (no thinking)"))

        r2 = run_benchmark(n_range, args.samples, model_id, thinking=True, max_tokens=args.max_tokens)
        print_summary(r2)
        all_series.append((r2, f"{model_label} (with thinking)"))

        save_results(r1 + r2, f"hanoi_results_{args.model}_compare.json")
        plot_results(all_series, f"hanoi_benchmark_{args.model}_compare.png")

    else:
        label = f"{model_label} ({'with' if args.thinking else 'without'} thinking)"
        results = run_benchmark(n_range, args.samples, model_id, thinking=args.thinking, max_tokens=args.max_tokens)
        print_summary(results)
        all_series.append((results, label))

        slug = f"{args.model}{'_thinking' if args.thinking else ''}"
        save_results(results, f"hanoi_results_{slug}.json")
        plot_results(all_series, f"hanoi_benchmark_{slug}.png")