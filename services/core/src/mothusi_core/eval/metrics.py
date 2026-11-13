"""Pure eval metrics: exact agreement, nearest-rank latency and wall-time cost."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from math import ceil, isfinite

from mothusi_core.eval.results import CallTiming, CostBasis, LatencySummary, ReaderAccuracy


def _rounded(value: Decimal, places: str) -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def agreement(questions_compared: int, disagreements: int) -> Decimal | None:
    """Exact awarded-mark agreement, half-up to four places; no sample is unknown."""
    if questions_compared < 0 or not 0 <= disagreements <= questions_compared:
        raise ValueError("counts must satisfy 0 <= disagreements <= questions_compared")
    if questions_compared == 0:
        return None
    return _rounded(Decimal(questions_compared - disagreements) / questions_compared, "0.0001")


def percentile(values: Sequence[float], q: float) -> Decimal:
    """Nearest rank for q in [0, 1], with q=0 selecting the minimum; two places."""
    if not isfinite(q) or not 0 <= q <= 1:
        raise ValueError("q must be finite and between 0 and 1")
    if not values or any(not isfinite(value) or value < 0 for value in values):
        raise ValueError("latency samples must be nonempty, finite and non-negative")
    ordered = sorted(values)
    return _rounded(Decimal(str(ordered[max(1, ceil(q * len(ordered))) - 1])), "0.01")


def summarise_latency(
    calls: Sequence[CallTiming], limits: Mapping[str, float]
) -> tuple[LatencySummary, ...]:
    """Summarise each role/model pair; limits compare unrounded call times."""
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for call in calls:
        grouped[call.role, call.model].append(call.seconds)
    summaries: list[LatencySummary] = []
    for (role, model), seconds in sorted(grouped.items()):
        limit = limits.get(role)
        if limit is not None and (not isfinite(limit) or limit < 0):
            raise ValueError("latency limits must be finite and non-negative")
        summaries.append(
            LatencySummary(
                role=role,
                model=model,
                calls=len(seconds),
                p50_s=percentile(seconds, 0.5),
                p95_s=percentile(seconds, 0.95),
                max_s=percentile(seconds, 1),
                limit_s=Decimal(str(limit)) if limit is not None else None,
                over_limit=sum(value > limit for value in seconds) if limit is not None else 0,
            )
        )
    return tuple(summaries)


def run_cost(basis: CostBasis, minutes: Decimal, token_cost_zar: Decimal) -> Decimal:
    """Price wall time plus token charges, rounding only the final rand total."""
    if not minutes.is_finite() or minutes < 0:
        raise ValueError("minutes must be finite and non-negative")
    if not token_cost_zar.is_finite() or token_cost_zar < 0:
        raise ValueError("token cost must be finite and non-negative")
    return _rounded(
        minutes / 60 * (basis.instance_usd_per_hour + basis.stack_usd_per_hour) * basis.zar_per_usd
        + token_cost_zar,
        "0.01",
    )


def _normalise(text: str) -> str:
    return " ".join(text.split())


def cer(reference: str, read: str) -> tuple[int, int]:
    """Whitespace-normalised Levenshtein distance and reference character count."""
    reference, read = _normalise(reference), _normalise(read)
    reference_length = len(reference)
    # The row follows the shorter string to bound memory by the shorter line.
    if len(reference) < len(read):
        reference, read = read, reference
    previous = list(range(len(read) + 1))
    for i, expected in enumerate(reference, 1):
        current = [i]
        for j, actual in enumerate(read, 1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (expected != actual),
                )
            )
        previous = current
    return previous[-1], reference_length


def reader_accuracy(pages: Sequence[Sequence[tuple[str, str, bool]]]) -> ReaderAccuracy:
    """Aggregate line matches and character-weighted CER across photographed pages."""
    lines = exact = distance = characters = uncertain_lines = uncertain_wrong = unflagged = 0
    for page in pages:
        for reference, read, uncertain in page:
            edits, length = cer(reference, read)
            wrong = edits != 0
            lines += 1
            exact += not wrong
            distance += edits
            characters += length
            uncertain_lines += uncertain
            uncertain_wrong += uncertain and wrong
            unflagged += wrong and not uncertain
    if not pages or not lines or not characters:
        raise ValueError("reader accuracy needs pages, lines and reference characters")
    return ReaderAccuracy(
        pages=len(pages),
        lines=lines,
        line_exact_rate=_rounded(Decimal(exact) / lines, "0.0001"),
        cer=_rounded(Decimal(distance) / characters, "0.0001"),
        uncertain_lines=uncertain_lines,
        uncertain_and_wrong=uncertain_wrong,
        wrong_not_flagged=unflagged,
    )
