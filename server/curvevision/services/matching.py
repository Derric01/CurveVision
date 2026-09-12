"""Optimal one-to-one assignment: the Hungarian algorithm (Kuhn--Munkres).

Given a cost matrix, pick at most one column per row and at most one row per column so that
the total cost is as small as possible. Two places in CurveVision need exactly that, and
both of them get a measurably worse answer from the obvious greedy alternative.

**Why not greedy.** Take the cheapest pair, remove its row and column, repeat. It is four
lines and it is wrong often enough to matter::

        old X   old Y
    A    0.10    0.20
    B    0.15    1.00

Greedy takes A-X at 0.10, which leaves B only Y at 1.00 -- above any sane threshold, so B
goes unmatched and one real correspondence is lost. The optimal pairing is A-Y plus B-X, a
total of 0.35 against greedy's 1.10, and it matches *both*. Two adjacent cars annotated by
two people is that matrix, and losing the match there means shipping a duplicate object.

**Why not SciPy.** `scipy.optimize.linear_sum_assignment` is the usual answer and is what
the upstream project this follows uses. CurveVision ships as a single PyInstaller executable
on the desktop, where SciPy costs tens of megabytes -- for one function, on matrices that
are almost always smaller than 10x10. This is ~90 lines with no dependency.

This is the O(n^3) shortest-augmenting-path formulation: repeatedly find a minimum-cost
augmenting path in the equality subgraph using dual variables (`u`, `v`) to keep every
reduced cost non-negative. Rectangular matrices are handled by transposing so that rows are
never the longer side, which is what keeps the bound at O(n^2 m) with n <= m.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

#: Returned for a row that the solver left unmatched.
UNMATCHED = -1


def solve(cost: Sequence[Sequence[float]]) -> list[int]:
    """Assign rows to columns at minimum total cost.

    Returns one entry per row: the column it was assigned to, or `UNMATCHED` when there are
    more rows than columns and this row lost. Costs must be finite; use a large finite value
    rather than `inf` to express "never pair these", so a solution always exists.
    """
    rows = len(cost)
    if rows == 0:
        return []
    columns = len(cost[0])
    if columns == 0:
        return [UNMATCHED] * rows
    if any(len(values) != columns for values in cost):
        raise ValueError("every row of the cost matrix must have the same length")
    for values in cost:
        for value in values:
            if not math.isfinite(value):
                raise ValueError("cost values must be finite")

    if rows > columns:
        # Solve the transpose so the loop below always runs over the shorter side, then
        # invert the answer. Without this a tall matrix costs O(n^3) in the larger n.
        transposed = [[cost[i][j] for i in range(rows)] for j in range(columns)]
        column_to_row = solve(transposed)
        flipped = [UNMATCHED] * rows
        for column, matched_row in enumerate(column_to_row):
            if matched_row != UNMATCHED:
                flipped[matched_row] = column
        return flipped

    # Dual variables. `u` is indexed by row and `v` by column, both 1-based with index 0
    # used as the scratch slot for the row currently being augmented.
    u = [0.0] * (rows + 1)
    v = [0.0] * (columns + 1)
    #: Which row each column is assigned to; 0 means free.
    column_row = [0] * (columns + 1)

    for current in range(1, rows + 1):
        column_row[0] = current
        free_column = 0
        #: Cheapest reduced cost reaching each column, and which column we came from.
        minimum = [math.inf] * (columns + 1)
        previous = [0] * (columns + 1)
        visited = [False] * (columns + 1)

        while True:
            visited[free_column] = True
            row = column_row[free_column]
            delta = math.inf
            next_column = 0

            for column in range(1, columns + 1):
                if visited[column]:
                    continue
                reduced = cost[row - 1][column - 1] - u[row] - v[column]
                if reduced < minimum[column]:
                    minimum[column] = reduced
                    previous[column] = free_column
                if minimum[column] < delta:
                    delta = minimum[column]
                    next_column = column

            # Shift the duals so the cheapest edge becomes tight, keeping every reduced
            # cost non-negative -- the invariant that makes the result optimal.
            for column in range(columns + 1):
                if visited[column]:
                    u[column_row[column]] += delta
                    v[column] -= delta
                else:
                    minimum[column] -= delta

            free_column = next_column
            if column_row[free_column] == 0:
                break

        # Walk the augmenting path back, flipping each edge.
        while free_column:
            moved = previous[free_column]
            column_row[free_column] = column_row[moved]
            free_column = moved

    assignment = [UNMATCHED] * rows
    for column in range(1, columns + 1):
        row = column_row[column]
        if row != 0:
            assignment[row - 1] = column - 1
    return assignment


def total_cost(cost: Sequence[Sequence[float]], assignment: Sequence[int]) -> float:
    """The cost of an assignment, for tests and for comparing against a baseline."""
    return sum(cost[row][column] for row, column in enumerate(assignment) if column != UNMATCHED)
