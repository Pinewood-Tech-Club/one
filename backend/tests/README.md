# Backend test suite

Fast, dependency-light tests for the security-sensitive backend modules. They
run without the heavy production `requirements.txt` (no torch/docling): the only
runtime deps the tested code needs are `cryptography`, `PyJWT`, and
`python-dotenv`, all pinned in `requirements-test.txt`.

## Setup

Create a throwaway virtualenv and install just the test deps:

```bash
python3 -m venv .venv-test
./.venv-test/bin/pip install -r requirements-test.txt
```

## Running the tests

Run everything from the `backend/` directory (pytest config lives in
`pyproject.toml`):

```bash
python -m pytest -q
```

## Running mutation tests

Mutation testing is the objective measure of test quality: it makes small edits
("mutants") to the source and checks whether the suite fails. A **surviving**
mutant is a change the tests did *not* catch — i.e. a real gap.

We use [`mutmut`](https://mutmut.readthedocs.io/) (>= 3.6). It is configured in
`pyproject.toml` under `[tool.mutmut]` and mutates only the modules that are
actually covered by this suite:

- `db/encryption.py`
- `auth/jwt_utils.py`
- `db/mobile.py`
- `db/job_leases.py`
- `services/scraper/store.py`

mutmut 3.x is pytest-native: instead of a free-form runner string it invokes
pytest itself, so the "test command" is expressed as `pytest_add_cli_args`
(`-q` here — equivalent to `python -m pytest -q`). It runs the suite from an
isolated `./mutants/` copy, so the first-party modules the tested code imports
but that we do not mutate (`config.py`, `db/pool.py`, `db/init.py`, and the
package `__init__.py` files) are listed under `also_copy`.

Run from `backend/`:

```bash
# Mutate every configured module and run the whole suite against each mutant.
mutmut run

# Show the summary (killed vs. survived) after a run.
mutmut results

# See the actual code change for one surviving mutant.
mutmut show <mutant-name>          # e.g. mutmut show db.encryption.x_decrypt_token__mutmut_3
```

To iterate quickly on a single module, pass a mutant-name glob so only that
module's mutants are executed (mutmut still generates all of them, but only runs
the matches):

```bash
mutmut run "db.encryption.*"
```

The `mutants/` working copy and `.mutmut-cache` are build artifacts and are
git-ignored.
