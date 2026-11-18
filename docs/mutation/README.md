# Mutation testing

Each script deliberately breaks the code in one specific way (a *mutant*), runs the test
suite, restores the file, and reports whether the tests caught it (`killed`) or not
(`SURVIVED`). A surviving mutant means a behaviour no test pins down.

Run one from the repository root:

```bash
uv run python -I docs/mutation/task4.py .
```

Every script asserts that each mutation pattern exists and is not a no-op, so a stale
pattern fails loudly instead of reporting a false "killed".

## Results on the current code (2026-11-18)

| Script | Area | Mutants | Killed | Survived |
|---|---|---:|---:|---:|
| `task1.py` | settings, app factory, tooling config | 14 | 14 | 0 |
| `task2.py` | request/response models, fake provider | 28 | 28 | 0 |
| `task3.py` | API keys, hashing, key and tenant repositories | 33 | 33 | 0 |
| `task4.py` | error contract, auth, roles, admin endpoints, body limit | 39 | 39 | 0 |
| `task5.py` | routing rules and endpoints | 25 | 25 | 0 |
| `task6.py` | date-effective pricing, cost | 17 | 17 | 0 |
| `task7.py` | `/v1/chat`, audit writes, cancellation, failure paths | 39 | 38 | 1 |
| `task8.py` | `/v1/audit`, `/v1/usage`, time windows, UTC | 18 | 18 | 0 |
| `task_a1.py` | keyset pagination, cursor codec (Phase 1b-A) | 22 | 22 | 0 |
| `task_a2.py` | usage aggregated by the repository (Phase 1b-A) | 14 | 14 | 0 |
| `task_a3.py` | atomic revoke, last-admin rule (Phase 1b-A) | 13 | 13 | 0 |
| `task_b1.py` | Anthropic provider, error mapping, 400 for rejected requests (Phase 1b-B) | 43 | 43 | 0 |
| `task_b2.py` | provider timeout, its setting, 504 vs 502, late answers (Phase 1b-B) | 20 | 20 | 0 |
| **Total** | | **325** | **324** | **1** |

Some mutants moved when their target moved: `task8.py` had 26 (its aggregation mutants
S01-S08 now live, retargeted, in `task_a2.py`), and `task4.py` had 43 (its last-admin
mutants A08-A10 and A13 now live in `task_a3.py`).

`task_b1.py` and `task_b2.py` run pytest with their own interpreter instead of `uv run`, so a failed
environment sync can't be counted as a killed mutant.

The survivor, `N02` in `task7.py`, is an **equivalent mutant**: it changes the default
`error_code` of a chat outcome, but every exception path sets a code and the success path
clears it, so no behaviour can observe the default. It is kept as defence in depth.

During development the first runs had many more survivors (for example 15 of 33 on the
admin endpoints, including "the secret is never checked"); each one got a test before
the task was committed.
