# pylopdf Cloudflare Python Worker

This example accepts a PDF request body and returns the page count plus
first-page text. Its limits are tighter than `DocumentLimits.web()` because a
Cloudflare isolate's 128 MB budget also includes Python, JavaScript, and
WebAssembly runtime memory.

The PyEmscripten wheel first ships with pylopdf 0.11. From this directory:

```bash
uv sync
uv run pywrangler dev
```

Then send a small PDF:

```bash
curl --request POST \
  --header "content-type: application/pdf" \
  --data-binary @document.pdf \
  http://localhost:8787
```

Use `uv run pywrangler deploy` after selecting a current compatibility date and
reviewing Cloudflare account limits. A declared `Content-Length` enables an
early rejection, but the `DocumentLimits` file budget remains authoritative.
The body must be buffered because pylopdf currently accepts paths or complete
byte strings, so reduce `_MAX_INPUT_BYTES` for workloads with less headroom.
