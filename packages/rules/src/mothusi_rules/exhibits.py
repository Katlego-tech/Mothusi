"""Deterministic exhibit provenance and page-quote checks; no approval decision."""

from collections.abc import Callable

from mothusi_contracts import CheckResult, Exhibit

EXHIBIT = "exhibit"
MAX_MODEL_BYTES = 6 * 1024 * 1024
LICENCES = ("CC-BY-4.0", "CC0-1.0")


def check_exhibit(exhibit: Exhibit, page_text: Callable[[str, int], str]) -> CheckResult:
    """Name every refused model property and citation, preserving stop order."""
    failures: list[str] = []
    if exhibit.model.licence not in LICENCES:
        failures.append(f"licence {exhibit.model.licence} not in {LICENCES}")
    if exhibit.model.bytes > MAX_MODEL_BYTES:
        failures.append(f"model bytes {exhibit.model.bytes} exceed {MAX_MODEL_BYTES}")
    for stop in exhibit.stops:
        if not stop.citations:
            failures.append(f"{stop.stop_id}: no citation")
        for citation in stop.citations:
            page = f"{citation.document} p {citation.pdf_page}"
            try:
                text = page_text(citation.document, citation.pdf_page)
            except KeyError, ValueError:
                failures.append(f"{stop.stop_id}: {page} not held")
                continue
            quote = " ".join(citation.quote.split()).casefold()
            if quote not in " ".join(text.split()).casefold():
                failures.append(f"{stop.stop_id}: quote not on {page}")
    return CheckResult(
        check=EXHIBIT,
        about=exhibit.exhibit_id,
        passed=not failures,
        detail="; ".join(failures) or "exhibit is grounded",
    )
