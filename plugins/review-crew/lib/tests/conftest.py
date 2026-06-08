import os
import sys

# Put the review-code module dir (parent of tests/) on sys.path so tests can
# `import circuit_breaker` / `import resolve_diff_lines` directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
