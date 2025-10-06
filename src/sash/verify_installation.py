import tempfile

import libdash
import z3


def main():
    # Verify libdash works
    with tempfile.NamedTemporaryFile(suffix=".sh") as temp_file:
        temp_file.write(b'#!/bin/sh\necho "Hello, World!"\n')
        temp_file.flush()
        _ = libdash.parse(temp_file.name)

    # Verify z3 works
    x = z3.Real("x")
    y = z3.Real("y")
    s = z3.Solver()
    s.add(x + y > 5, x > 1, y > 1)
    _ = s.check()

    print("All good!")
