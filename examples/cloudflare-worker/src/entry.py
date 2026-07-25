"""Extract the first page of an uploaded PDF in a Cloudflare Python Worker."""

from workers import Request, Response, WorkerEntrypoint

import pylopdf

_MIB = 1024 * 1024
_MAX_INPUT_BYTES = 4 * _MIB
_LIMITS = pylopdf.DocumentLimits(
    max_file_size=_MAX_INPUT_BYTES,
    max_pages=100,
    max_objects=50_000,
    max_decompressed_size=16 * _MIB,
    max_page_content_size=4 * _MIB,
    max_total_decompressed_size=32 * _MIB,
    max_object_depth=64,
    max_text_size=512 * 1024,
)


class Default(WorkerEntrypoint):
    """Handle one bounded PDF upload."""

    async def fetch(self, request: Request) -> Response:
        """Return page count and first-page text as JSON."""
        declared_size = request.headers.get("content-length")
        if declared_size is not None:
            try:
                declared_bytes = int(declared_size)
            except ValueError:
                return Response.from_json(
                    {"error": "invalid_content_length"},
                    status=400,
                )
            if declared_bytes > _MAX_INPUT_BYTES:
                return Response.from_json(
                    {"error": "file_size", "message": "PDF exceeds the 4 MiB Worker limit"},
                    status=413,
                )

        data = await request.bytes()
        try:
            with pylopdf.Document(stream=data, limits=_LIMITS) as document:
                first_page = document.get_page_text(0) if document.page_count else ""
                return Response.from_json(
                    {
                        "pages": document.page_count,
                        "first_page_text": first_page,
                    }
                )
        except pylopdf.LimitError as error:
            return Response.from_json(
                {"error": error.code, "message": str(error)},
                status=413,
            )
        except pylopdf.PdfError as error:
            return Response.from_json(
                {"error": "invalid_pdf", "message": str(error)},
                status=422,
            )
