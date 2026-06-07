import os
import sys

# Put the eval module dir (parent of tests/) on sys.path so tests can
# `import score` directly, mirroring the review-code conftest.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
