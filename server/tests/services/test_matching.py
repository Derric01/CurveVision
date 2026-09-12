"""The assignment solver, checked against brute force.

An assignment algorithm that is subtly wrong does not crash; it quietly returns a slightly
worse pairing, and every caller believes it. The only honest test is to compute the true
optimum independently — by trying every permutation — and demand the solver match it. That
is what `TestAgainstBruteForce` does, on hundreds of random matrices.
"""

from __future__ import annotations

import itertools
import math
import random

import pytest

from curvevision.services.matching import UNMATCHED, solve, total_cost


def brute_force(cost: list[list[float]]) -> float:
    """The true minimum, by trying every assignment. Only viable for tiny matrices."""
    rows, columns = len(cost), len(cost[0])
    best = math.inf
    short, long = min(rows, columns), max(rows, columns)
    for chosen in itertools.permutations(range(long), short):
        if rows <= columns:
            total = sum(cost[row][column] for row, column in enumerate(chosen))
        else:
            total = sum(cost[row][column] for column, row in enumerate(chosen))
        best = min(best, total)
    return best


def is_one_to_one(assignment: list[int], columns: int) -> bool:
    used = [column for column in assignment if column != UNMATCHED]
    return len(used) == len(set(used)) and all(0 <= column < columns for column in used)


class TestShape:
    def test_an_empty_matrix_assigns_nothing(self):
        assert solve([]) == []

    def test_rows_with_no_columns_are_all_unmatched(self):
        assert solve([[], []]) == [UNMATCHED, UNMATCHED]

    def test_a_single_cell(self):
        assert solve([[5.0]]) == [0]

    def test_more_rows_than_columns_leaves_the_expensive_rows_out(self):
        cost = [[1.0], [9.0], [4.0]]
        assignment = solve(cost)
        assert assignment.count(UNMATCHED) == 2
        assert assignment[0] == 0, "the cheapest row should take the only column"

    def test_more_columns_than_rows_uses_every_row(self):
        cost = [[9.0, 1.0, 5.0]]
        assert solve(cost) == [1]

    def test_a_ragged_matrix_is_refused(self):
        with pytest.raises(ValueError, match="same length"):
            solve([[1.0, 2.0], [3.0]])

    def test_an_infinite_cost_is_refused(self):
        """`inf` makes "no solution" representable, and then callers get a nonsense answer.

        A large finite number says the same thing and always leaves a solution to find.
        """
        with pytest.raises(ValueError, match="finite"):
            solve([[1.0, math.inf]])


class TestOptimality:
    def test_the_case_greedy_gets_wrong(self):
        """The matrix from the module docstring. This is why the solver exists."""
        cost = [
            [0.10, 0.20],
            [0.15, 1.00],
        ]
        assignment = solve(cost)

        assert total_cost(cost, assignment) == pytest.approx(0.35)
        assert assignment == [1, 0], "greedy would take 0.10 first and pay 1.10 in total"

    def test_an_obvious_diagonal(self):
        cost = [
            [1.0, 9.0, 9.0],
            [9.0, 1.0, 9.0],
            [9.0, 9.0, 1.0],
        ]
        assert solve(cost) == [0, 1, 2]

    def test_negative_costs_are_handled(self):
        """Callers pass `1 - similarity`; a similarity above 1 from a rounding wobble
        would make a cost negative, and the solver must not misbehave on it."""
        cost = [
            [-0.5, 0.0],
            [0.0, -0.2],
        ]
        assignment = solve(cost)
        assert total_cost(cost, assignment) == pytest.approx(-0.7)

    def test_ties_still_produce_a_valid_assignment(self):
        cost = [[1.0] * 4 for _ in range(4)]
        assignment = solve(cost)
        assert sorted(assignment) == [0, 1, 2, 3]


class TestAgainstBruteForce:
    @pytest.mark.parametrize(
        "rows,columns",
        [(1, 1), (2, 2), (3, 3), (4, 4), (2, 4), (4, 2), (1, 5), (5, 1), (3, 5), (5, 3)],
    )
    def test_random_matrices_match_the_true_optimum(self, rows: int, columns: int):
        generator = random.Random(f"{rows}x{columns}")
        for _ in range(40):
            cost = [
                [round(generator.uniform(-1, 5), 3) for _ in range(columns)] for _ in range(rows)
            ]
            assignment = solve(cost)

            assert is_one_to_one(assignment, columns), assignment
            assert len([c for c in assignment if c != UNMATCHED]) == min(rows, columns), (
                "the solver must match as many pairs as it possibly can"
            )
            assert total_cost(cost, assignment) == pytest.approx(brute_force(cost)), cost

    def test_integer_costs_and_duplicates(self):
        """Integer-valued matrices with many equal entries are where ties bite."""
        generator = random.Random("ties")
        for _ in range(60):
            size = generator.randint(1, 5)
            cost = [[float(generator.randint(0, 3)) for _ in range(size)] for _ in range(size)]
            assignment = solve(cost)
            assert total_cost(cost, assignment) == pytest.approx(brute_force(cost)), cost


class TestSize:
    def test_a_sixty_by_sixty_matrix_is_solved(self):
        """Not a benchmark — a check that the dual bookkeeping does not fall over at size.

        Frames with dozens of objects exist, and the failure mode of a bad implementation
        here is an infinite loop, not a wrong number.
        """
        generator = random.Random("big")
        size = 60
        cost = [[generator.uniform(0, 1) for _ in range(size)] for _ in range(size)]

        assignment = solve(cost)

        assert sorted(assignment) == list(range(size))
