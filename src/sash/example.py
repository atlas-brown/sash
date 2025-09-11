import z3

import libdash


def example():
    x = z3.Real("x")
    y = z3.Real("y")
    s = z3.Solver()
    s.add(x + y > 5, x > 1, y > 1)
    return s.check()
