# Working on TrafficSense

Follow [README.md](README.md). Start with fixture/reference mode to develop
without provider credentials or model weights.

Create a branch, keep changes focused, and run backend tests/lint plus frontend
tests/build for affected components. Integration tests need PostGIS/Redis and
migrations as described in README.

Document new settings in `.env.example` with blank credentials and conservative
activation defaults. Keep `.env` private. Exclude weights, checkpoints, notebook
outputs, local databases, logs and personal documents. Preserve source metadata
headers and keep Python modules focused.

Enable the included local commit guard with `git config core.hooksPath .githooks`.
Using your virtual environment, run `python scripts/check_publication.py` before
staging and `python scripts/check_publication.py --staged` afterward. Review the
staged diff. In the PR, explain the change and validation. Label synthetic
benchmarks and distinguish trained artifacts from verified runtime integration
and real-world evaluation.
