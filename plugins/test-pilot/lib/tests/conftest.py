import os
import sys

# Put the lib dir (parent of tests/) on sys.path so tests can
# `import store` / `import engine` directly, like review-crew's conftest.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
