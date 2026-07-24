# pylopdf fuzzing

`fuzz_api.py` uses Atheris to mutate PDF bytes while exercising the public
pylopdf workflow:

1. open with a decompression limit;
2. extract positioned text and search;
3. render at low resolution;
4. edit metadata and save with object streams;
5. reopen and extract the saved output.

Invalid PDFs may raise `PdfError`. A Rust panic, process crash, hang, excessive
memory use, or an exception outside the documented error hierarchy is a
finding.

Run a bounded local session with a writable output corpus followed by the
redistributable real-world seeds:

```powershell
uv sync --group fuzz --python 3.13
New-Item -ItemType Directory -Force fuzz/corpus
uv run python fuzz/fuzz_api.py `
  -max_total_time=300 -timeout=15 -rss_limit_mb=2048 -max_len=1048576 `
  fuzz/corpus tests/assets/real_world
```

Minimize a reproducer before adding it to the regression corpus. Record its
source, license, and known limitations in `tests/assets/real_world/README.md`.
Never upload a confidential or non-redistributable PDF as a fuzz seed.
