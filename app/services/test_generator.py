"""Deterministic demo test-case generators.

Used by ``scripts/seed_demo.py`` to build reproducible public + hidden test
cases for the demo problems. Every generator is a pure function of
``(count, seed)``: the same seed always produces the same cases, so demo
results are stable across machines and re-runs.

These are DEMO helpers — real problems can be created through the API like
any other problem.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class GeneratedCase:
    input_data: str
    expected_output: str
    is_public: bool
    label: str


def _sum_two(rng: random.Random, index: int) -> tuple[str, str]:
    """A + B. The first two cases are fixed (the public examples the demo
    fixtures expect), later cases are deterministic-random."""
    if index == 0:
        return "2 3\n", "5"
    if index == 1:
        return "10 10\n", "20"
    a = rng.randint(-10**6, 10**6)
    b = rng.randint(-10**6, 10**6)
    return f"{a} {b}\n", str(a + b)


def _sum_of_numbers(rng: random.Random, index: int) -> tuple[str, str]:
    n = rng.randint(1, 40)
    values = [rng.randint(-1000, 1000) for _ in range(n)]
    input_data = f"{n}\n" + " ".join(str(v) for v in values) + "\n"
    return input_data, str(sum(values))


def _max_value(rng: random.Random, index: int) -> tuple[str, str]:
    n = rng.randint(1, 50)
    values = [rng.randint(-10**6, 10**6) for _ in range(n)]
    input_data = f"{n}\n" + " ".join(str(v) for v in values) + "\n"
    return input_data, str(max(values))


def _count_odds(rng: random.Random, index: int) -> tuple[str, str]:
    n = rng.randint(1, 60)
    values = [rng.randint(-500, 500) for _ in range(n)]
    input_data = f"{n}\n" + " ".join(str(v) for v in values) + "\n"
    return input_data, str(sum(1 for v in values if v % 2 != 0))


#: slug -> generator function
GENERATORS: dict[str, Callable[[random.Random, int], tuple[str, str]]] = {
    "a-plus-b": _sum_two,
    "sum-of-n-numbers": _sum_of_numbers,
    "max-of-n-numbers": _max_value,
    "count-odd-numbers": _count_odds,
}


def generate_cases(
    slug: str, *, count: int, public: int, seed: int = 42
) -> list[GeneratedCase]:
    """Build ``count`` cases for a demo problem; the first ``public`` are public.

    Raises ``KeyError`` for unknown slugs so a typo fails loudly instead of
    silently seeding an empty problem.
    """
    generator = GENERATORS[slug]
    rng = random.Random(f"{slug}:{seed}")
    cases: list[GeneratedCase] = []
    for i in range(count):
        input_data, expected = generator(rng, i)
        cases.append(
            GeneratedCase(
                input_data=input_data,
                expected_output=expected,
                is_public=i < public,
                label=f"demo-{i + 1:02d}",
            )
        )
    return cases
