# ZOE tests

Run from the `addon/` directory:

```
cd addon
pip install -r requirements-dev.txt
pytest -q
```

All tests are offline: no HTTP, no Anthropic calls. The `tmp_stores` fixture in
`tests/conftest.py` repoints every `settings.*_path` at a per-test temp dir so
tests never touch the real `/data/*.json`.
