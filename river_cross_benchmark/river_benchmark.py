"""
River Crossing Benchmark - Reproducing "Illusion of Thinking" on Claude 4.6
Based on: https://arxiv.org/abs/2506.06941 (Apple Research)

N actors (a_1..a_N) and N agents (A_1..A_N) must cross a river.
Boat capacity: k=2 for N<=3, k=3 for N>3.
Constraint: no actor can be with another agent unless their own agent is present.

Usage:
  python river_benchmark.py                          # Sonnet 4.6 without thinking
  python river_benchmark.py --thinking               # Sonnet 4.6 with adaptive thinking
  python river_benchmark.py --model opus             # Opus 4.6 without thinking
  python river_benchmark.py --compare                # Compare without vs with thinking

Pricing (Feb 2026):
  Sonnet 4.6 : $3/$15 per million input/output tokens
  Opus 4.6   : $5/$25 per million input/output tokens
"""

import anthropic
import json
import re
import argparse
import time
import matplotlib.pyplot as plt
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

# ─── Simulator ────────────────────────────────────────────────────────────────

class RiverCrossingSimulator:
    """
    Validates a River Crossing solution.

    State: left_bank (set), right_bank (set), boat_side ('left' or 'right')
    People: actors a_1..a_N, agents A_1..A_N
    Constraint: on any bank or in the boat, if actor a_i is present with agent A_j (i!=j),
                then A_i must also be present.
    Boat: capacity k, cannot travel empty.
    """

    def __init__(self, n: int):
        self.n = n
        self.k = 2 if n <= 3 else 3
        self.actors = {f"a_{i}" for i in range(1, n + 1)}
        self.agents = {f"A_{i}" for i in range(1, n + 1)}
        self.all_people = self.actors | self.agents
        # Initial state: everyone on left
        self.left = set(self.all_people)
        self.right = set()
        self.boat_side = "left"
        self.move_count = 0

    def _agent_of(self, actor: str) -> str:
        return "A_" + actor[2:]

    def _actor_of(self, agent: str) -> str:
        return "a_" + agent[2:]

    def _is_safe(self, group: set) -> tuple[bool, str]:
        """Check safety constraint for a group of people (bank or boat)."""
        actors_present = {p for p in group if p.startswith("a_")}
        agents_present = {p for p in group if p.startswith("A_")}
        for actor in actors_present:
            own_agent = self._agent_of(actor)
            # Check if any foreign agent is present
            foreign_agents = agents_present - {own_agent}
            if foreign_agents and own_agent not in agents_present:
                return False, f"{actor} is with {foreign_agents} but {own_agent} is absent"
        return True, "ok"

    def validate_solution(self, moves: list) -> tuple[bool, int, str]:
        """Returns (success, n_valid_moves, error_msg)"""
        sim = RiverCrossingSimulator(self.n)

        for i, move in enumerate(moves):
            # Parse move: list of person identifiers
            try:
                people = [p.strip() for p in move]
            except Exception as e:
                return False, i, f"Invalid move format at step {i}: {move}"

            if not people:
                return False, i, f"Move {i} is empty — boat cannot travel empty"

            if len(people) > sim.k:
                return False, i, f"Move {i}: {len(people)} people exceeds boat capacity {sim.k}"

            # Validate all people exist
            for p in people:
                if p not in sim.all_people:
                    return False, i, f"Move {i}: unknown person '{p}'"

            # All passengers must be on the current boat side
            source = sim.left if sim.boat_side == "left" else sim.right
            dest = sim.right if sim.boat_side == "left" else sim.left

            for p in people:
                if p not in source:
                    return False, i, f"Move {i}: {p} is not on {sim.boat_side} bank"

            # Check safety on the boat
            ok, msg = sim._is_safe(set(people))
            if not ok:
                return False, i, f"Move {i}: unsafe on boat — {msg}"

            # Move people
            for p in people:
                source.remove(p)
                dest.add(p)

            # Check safety on both banks after move
            ok, msg = sim._is_safe(sim.left)
            if not ok:
                return False, i, f"Move {i}: unsafe on left bank after move — {msg}"

            ok, msg = sim._is_safe(sim.right)
            if not ok:
                return False, i, f"Move {i}: unsafe on right bank after move — {msg}"

            sim.boat_side = "right" if sim.boat_side == "left" else "left"
            sim.move_count += 1

        # Check goal: everyone on right bank
        if sim.right == sim.all_people and len(sim.left) == 0:
            return True, sim.move_count, "solved"
        else:
            return False, sim.move_count, f"incorrect final state — left: {sim.left}, right: {sim.right}"


# ─── Move Extraction ──────────────────────────────────────────────────────────

def extract_moves(text: str) -> Optional[list]:
    """
    Extract river crossing moves from model response.
    Expected format: moves = [["A_1", "a_1"], ["A_1"], ...]
    """
    patterns = [
        r'moves\s*=\s*(\[\s*\[.*?\]\s*\])',
        r'<answer>.*?moves\s*=\s*(\[\s*\[.*?\]\s*\])',
        r'(\[\s*\[\s*["\']?[aA]_\d["\']?.*?\]\s*\])',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                raw = match.group(1)
                # Normalize quotes
                raw = raw.replace("'", '"')
                moves = json.loads(raw)
                if isinstance(moves, list) and all(isinstance(m, list) for m in moves):
                    # Clean whitespace from each person identifier
                    moves = [[p.strip() for p in m] for m in moves]
                    return moves
            except json.JSONDecodeError:
                continue
    return None


# ─── Prompts ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a helpful assistant. Solve this puzzle for me.
You can represent actors with a_1, a_2, ... and agents with A_1, A_2, ...
Your solution must be a list of boat moves where each move indicates the people on the boat.

For example, if there were two actors and two agents, you should return:
moves = [["A_2", "a_2"], ["A_2"], ["A_1", "A_2"], ["A_1"], ["A_1", "a_1"]]

which indicates that in the first move, A_2 and a_2 row from left to right, and in the second move, A_2 rows from right to left and so on.

Requirements:
- When exploring potential solutions in your thinking process, always include the corresponding complete list of boat moves.
- The list should not have comments.
- Ensure your final answer also includes the complete list of moves for the final solution."""


def make_user_prompt(n: int) -> str:
    k = 2 if n <= 3 else 3
    actors = ", ".join(f"a_{i}" for i in range(1, n + 1))
    agents = ", ".join(f"A_{i}" for i in range(1, n + 1))
    return f"""{n} actors ({actors}) and their {n} agents ({agents}) want to cross a river in a boat that is capable of holding only {k} people at a time, with the constraint that no actor can be in the presence of another agent, including while riding the boat, unless their own agent is also present, because each agent is worried their rivals will poach their client.

Initially, all actors and agents are on the left side of the river with the boat.

How should they cross the river? (Note: the boat cannot travel empty)

Your final answer must be in the format: moves = [["person1", "person2"], ["person1"], ...]"""


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
    Use streaming for long requests. Retries on overloaded/incomplete errors.
    Returns (full_text, total_tokens).
    """
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
    n_range: range = range(2, 7),
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
        k = 2 if n <= 3 else 3
        print(f"\n{'='*55}")
        print(f"N={n} pairs | Boat capacity: {k}")
        print(f"{'='*55}")

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
                text, total_tokens = call_with_streaming(client, kwargs)
                moves = extract_moves(text)

                if moves is None:
                    result = Result(n=n, success=False, n_valid_moves=0,
                                    total_moves=0, error="No moves extracted",
                                    tokens_used=total_tokens, raw_response=text[:500])
                    print(f"FAIL (no moves extracted) | {total_tokens} tokens")
                else:
                    sim = RiverCrossingSimulator(n)
                    success, valid_count, msg = sim.validate_solution(moves)
                    result = Result(n=n, success=success, n_valid_moves=valid_count,
                                    total_moves=len(moves), error=msg,
                                    tokens_used=total_tokens, raw_response=text[:500])
                    status = "OK" if success else f"FAIL ({msg[:50]})"
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

def plot_results(all_series: list[tuple[list[Result], str]], filename: str = "river_benchmark.png"):
    colors_palette = ['#3498db', '#e74c3c', '#2ecc71', '#f39c12']
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))

    title = "River Crossing Benchmark — " + " vs ".join(label for _, label in all_series)
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

    ax1.set_xlabel("Complexity (number of actor-agent pairs)", fontsize=11)
    ax1.set_ylabel("Accuracy (%)", fontsize=11)
    ax1.set_title("Accuracy vs Complexity", fontsize=12)
    ax1.set_ylim(-5, 110)
    ax1.axhline(y=50, color='gray', linestyle='--', alpha=0.4)
    ax1.legend(loc='upper right', fontsize=9)
    ax1.grid(alpha=0.2)

    ax2.set_xlabel("Complexity (number of actor-agent pairs)", fontsize=11)
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
    print("RESULTS SUMMARY — River Crossing")
    print("="*60)
    print(f"{'N':>4} | {'k':>4} | {'Accuracy':>10} | {'Avg tokens':>12}")
    print("-"*45)
    for n in n_values:
        k = 2 if n <= 3 else 3
        n_results = [r for r in results if r.n == n]
        acc = sum(r.success for r in n_results) / len(n_results) * 100
        avg_tokens = np.mean([r.tokens_used for r in n_results])
        collapse = " <- COLLAPSE" if acc == 0 else ""
        print(f"{n:>4} | {k:>4} | {acc:>9.0f}% | {avg_tokens:>12,.0f}{collapse}")


def save_results(results: list[Result], filename: str):
    raw = [{"n": r.n, "success": r.success, "valid_moves": r.n_valid_moves,
            "total_moves": r.total_moves, "tokens": r.tokens_used, "error": r.error}
           for r in results]
    with open(filename, "w") as f:
        json.dump(raw, f, indent=2)
    print(f"Results saved: {filename}")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="River Crossing Benchmark - Illusion of Thinking reproduction")
    parser.add_argument("--model", choices=["sonnet", "opus"], default="sonnet",
                        help="Model to test (default: sonnet)")
    parser.add_argument("--thinking", action="store_true",
                        help="Enable adaptive thinking")
    parser.add_argument("--compare", action="store_true",
                        help="Compare without vs with thinking (2x more expensive)")
    parser.add_argument("--n-min", type=int, default=2,
                        help="Minimum N pairs (default: 2)")
    parser.add_argument("--n-max", type=int, default=6,
                        help="Maximum N pairs (default: 6)")
    parser.add_argument("--samples", type=int, default=3,
                        help="Samples per N value (default: 3, paper used: 25)")
    parser.add_argument("--max-tokens", type=int, default=8000,
                        help="Max output tokens per request (default: 8000)")
    args = parser.parse_args()

    model_id = "claude-opus-4-6" if args.model == "opus" else "claude-sonnet-4-6"
    model_label = "Opus 4.6" if args.model == "opus" else "Sonnet 4.6"
    n_range = range(args.n_min, args.n_max + 1)

    print("River Crossing Benchmark — Illusion of Thinking Reproduction")
    print(f"Based on: arxiv.org/abs/2506.06941 (Apple Research)")
    print(f"Boat capacity: k=2 for N<=3, k=3 for N>3")

    all_series = []

    if args.compare:
        print(f"\nComparison mode: {model_label} without vs with thinking")
        r1 = run_benchmark(n_range, args.samples, model_id, thinking=False, max_tokens=args.max_tokens)
        print_summary(r1)
        all_series.append((r1, f"{model_label} (no thinking)"))

        r2 = run_benchmark(n_range, args.samples, model_id, thinking=True, max_tokens=args.max_tokens)
        print_summary(r2)
        all_series.append((r2, f"{model_label} (with thinking)"))

        save_results(r1 + r2, f"river_results_{args.model}_compare.json")
        plot_results(all_series, f"river_benchmark_{args.model}_compare.png")

    else:
        label = f"{model_label} ({'with' if args.thinking else 'without'} thinking)"
        results = run_benchmark(n_range, args.samples, model_id, thinking=args.thinking, max_tokens=args.max_tokens)
        print_summary(results)
        all_series.append((results, label))

        slug = f"{args.model}{'_thinking' if args.thinking else ''}"
        save_results(results, f"river_results_{slug}.json")
        plot_results(all_series, f"river_benchmark_{slug}.png")