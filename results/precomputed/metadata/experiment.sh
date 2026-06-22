#! /bin/bash
time ./scripts/eval.sh --main --no-color &> maineval.log
time ./scripts/eval.sh --sweep --no-color &> sweepeval.log
time ./scripts/eval.sh --koala --no-color &> koalaeval.log
