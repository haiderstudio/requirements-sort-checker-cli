#!/bin/sh
# Run the checker on the bundled examples and show the report.
# The checker exits 1 because the examples contain files with
# entries out of order; that is the point of the examples, so
# this script exits 0.
cd "$(dirname "$0")/.." || exit 2
python3 src/requirements_sort_checker.py examples || true
