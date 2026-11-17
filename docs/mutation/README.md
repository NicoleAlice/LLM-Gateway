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

## Results on the current code (2026-11-17)

| Script | Area | Mutants | Killed | Survived |
|---|---|---:|---:|---:|
| `task1.py` | settings, app factory, tooling config | 14 | 14 | 0 |
| `task2.py` | request/response models, fake provider | 28 | 28 | 0 |
| `task3.py` | API keys, hashing, key and tenant repositories | 33 | 33 | 0 |
| `task4.py` | error contract, auth, roles, admin endpoints, body limit | 43 | 43 | 0 |
| `task5.py` | routing rules and endpoints | 25 | 25 | 0 |
| `task6.py` | date-effective pricing, cost | 17 | 17 | 0 |
| `task7.py` | `/v1/chat`, audit writes, cancellation, failure paths | 39 | 38 | 1 |
| `task8.py` | `/v1/audit`, `/v1/usage`, time windows, UTC | 18 | 18 | 0 |
| `task_a1.py` | keyset pagination, cursor codec (Phase 1b-A) | 22 | 22 | 0 |
| `task_a2.py` | usage aggregated by the repository (Phase 1b-A) | 14 | 14 | 0 |
| **Total** | | **253** | **252** | **1** |

`task8.py` had 26 mutants; its 8 aggregation mutants (S01-S08) targeted code that moved
from the `/v1/usage` route into the repository in task A2, so they now live, retargeted,
in `task_a2.py`.

The survivor, `N02` in `task7.py`, is an **equivalent mutant**: it changes the default
`error_code` of a chat outcome, but every exception path sets a code and the success path
clears it, so no behaviour can observe the default. It is kept as defence in depth.

During development the first runs had many more survivors (for example 15 of 33 on the
admin endpoints, including "the secret is never checked"); each one got a test before
the task was committed.
