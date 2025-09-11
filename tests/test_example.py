# Rules for creating tests:
# - Files must be prefixed or suffixed with "test" (i.e. "test_*.py" or "*_test.py")
# - Functions implementing tests must be prefixed with "test" (i.e. "test_*")
# - Classes implementing tests must be prefixed with "Test" (i.e. "Test*")
# - See https://docs.pytest.org/en/7.1.x/explanation/goodpractices.html#conventions-for-python-test-discovery
#   for a more detailed explanation

import z3

# This is how project modules are imported
from sash.example import example


def test_example():
    assert example() == z3.sat
