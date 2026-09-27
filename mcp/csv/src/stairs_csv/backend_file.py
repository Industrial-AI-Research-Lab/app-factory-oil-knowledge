"""CSV core input helpers from ``stairs-backend``."""

import csv

# Public module API
__all__ = ("decode_csv_bytes", "detect_csv_delimiter")

# Supported csv delimiters
CSV_DELIMITERS = (";", ",")
# Supported csv encodings
CSV_ENCODINGS = ("utf-8-sig", "cp1251")


def detect_csv_delimiter(text: str) -> str:
    """
    Detect the backend CSV delimiter, preferring a stable header split.
    Returns an element of CSV_DELIMITERS
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ";"  # unreachable via parser (empty is rejected earlier); default keeps totality
    sample_lines = lines[:5]

    def field_count(line: str, delimiter: str) -> int:
        """Count fields of one line under a delimiter, 0 when unparseable."""
        try:
            return len(next(csv.reader([line], delimiter=delimiter)))
        except (csv.Error, StopIteration):
            return 0

    header_scores = {
        delimiter: field_count(sample_lines[0], delimiter)
        for delimiter in CSV_DELIMITERS
    }
    best_header_score = max(header_scores.values())
    best_header_delimiters = [
        delimiter
        for delimiter, score in header_scores.items()
        if score == best_header_score
    ]
    if best_header_score > 1 and len(best_header_delimiters) == 1:
        return best_header_delimiters[0]

    consistency_scores: dict[str, tuple[int, int]] = {}
    for delimiter in CSV_DELIMITERS:
        counts = [field_count(line, delimiter) for line in sample_lines]
        expected = counts[0]
        consistency_scores[delimiter] = (
            sum(count == expected for count in counts),
            expected,
        )
    best_consistency = max(consistency_scores.values())
    best_consistency_delimiters = [
        delimiter
        for delimiter, score in consistency_scores.items()
        if score == best_consistency
    ]
    if best_consistency[1] > 1 and len(best_consistency_delimiters) == 1:
        return best_consistency_delimiters[0]

    sample = "\n".join(sample_lines)
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(CSV_DELIMITERS)).delimiter
    except csv.Error:
        semicolons = sample.count(";")
        commas = sample.count(",")
        return ";" if semicolons >= commas else ","


def decode_csv_bytes(content: bytes) -> tuple[str, str]:
    """
    Decode bytes using the source's ordered encoding fallback.
    Returns the content and one of CSV_ENCODINGS
    """
    for encoding in CSV_ENCODINGS:
        try:
            return content.decode(encoding), encoding
        except (UnicodeDecodeError, ValueError, LookupError):
            continue
    raise UnicodeError("unsupported CSV encoding")
