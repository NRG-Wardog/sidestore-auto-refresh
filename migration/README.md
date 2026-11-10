# Baseline compatibility fixtures

Only the baseline registry and product-evidence regression fixtures remain here.
They support the retained collector/verifier tests, not the current build entry.
All p0/p1/p2 and ADI experimental source graphs, adapters and private tests have
been retired. Their input values now fail closed before acquisition or writes.

The compatibility CLI requires explicit `--pins`; it has no default source graph.
Do not use the test fixture as a production build default. Current builds use
`ci/build/candidate-graph.json` through `ci/build/contracts/`.

Run the retained checks with:

```sh
python3 -B -m unittest discover -s migration/tests -p 'test_*.py' -v
```

`contracts/` and its parity evidence remain hash-bound. Removing them requires
retiring or replacing the remaining baseline collector/verifier product tests.
