"""Transcript-backed verification helpers for yt-scribe."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .transcripts import timestamp_anchor

VERIFY_STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "but",
    "for",
    "from",
    "has",
    "have",
    "into",
    "that",
    "the",
    "this",
    "was",
    "were",
    "with",
}


def normalize_verification_text(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def strip_markdown_marker(line: str) -> str:
    return re.sub(r"^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+|#{1,6}\s+)", "", line).strip()


def split_polished_claim_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        claim_line = strip_markdown_marker(line)
        if not claim_line:
            continue
        base_column = line.find(claim_line) + 1 if claim_line in line else 1
        for match in re.finditer(r"\S.*?(?:[.!?](?=\s+|$)|$)", claim_line):
            claim = match.group(0).strip()
            if not claim:
                continue
            leading_spaces = len(match.group(0)) - len(match.group(0).lstrip())
            records.append(
                {
                    "claim": claim,
                    "polished_location": {
                        "line": line_number,
                        "column": base_column + match.start() + leading_spaces,
                    },
                }
            )
    return records


def split_polished_claims(text: str) -> list[str]:
    return [record["claim"] for record in split_polished_claim_records(text)]


def verification_terms(text: str) -> list[str]:
    terms = re.findall(r"[A-Za-z][A-Za-z'-]*|\d+(?:[.,:/-]\d+)*", text)
    return [
        term
        for term in terms
        if normalize_verification_text(term) not in VERIFY_STOPWORDS
    ]


def risky_verification_terms(text: str) -> list[str]:
    names = re.findall(r"\b[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)*\b", text)
    numbers = re.findall(r"\b\d+(?:[.,:/-]\d+)*\b", text)
    terms = []
    for term in [*names, *numbers]:
        normalized = normalize_verification_text(term)
        if normalized and normalized not in VERIFY_STOPWORDS and term not in terms:
            terms.append(term)
    return terms


def transcript_entries_from_text(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^\[(\d{2}:\d{2}(?::\d{2})?)\]\s*(.+)$", stripped)
        if match:
            anchor, entry_text = match.groups()
        else:
            anchor, entry_text = "", stripped
        entries.append(
            {
                "anchor": anchor,
                "text": entry_text,
                "normalized": normalize_verification_text(entry_text),
            }
        )
    return entries


def transcript_entries_from_segments(segments: list[Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        anchor = timestamp_anchor(float(segment.get("start") or 0))
        entries.append(
            {
                "anchor": anchor,
                "text": text,
                "normalized": normalize_verification_text(text),
            }
        )
    return entries


def load_verification_transcript(path: str | Path) -> dict[str, Any]:
    text = Path(path).expanduser().read_text(encoding="utf-8")
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            segments = parsed.get("segments")
            if isinstance(segments, list):
                entries = transcript_entries_from_segments(segments)
                transcript_text = "\n".join(entry["text"] for entry in entries)
                return {"text": transcript_text, "entries": entries}
            if isinstance(parsed.get("text"), str):
                transcript_text = parsed["text"]
                return {
                    "text": transcript_text,
                    "entries": transcript_entries_from_text(transcript_text),
                }
        if isinstance(parsed, list):
            entries = transcript_entries_from_segments(parsed)
            transcript_text = "\n".join(entry["text"] for entry in entries)
            return {"text": transcript_text, "entries": entries}
    return {"text": text, "entries": transcript_entries_from_text(text)}


def find_transcript_anchor(
    claim_terms: list[str],
    transcript_entries: list[dict[str, str]],
) -> str | None:
    normalized_terms = [normalize_verification_text(term) for term in claim_terms]
    normalized_terms = [term for term in normalized_terms if term]
    best_entry: dict[str, str] | None = None
    best_score = 0
    for entry in transcript_entries:
        entry_text = entry["normalized"]
        score = sum(1 for term in normalized_terms if term in entry_text)
        if score > best_score:
            best_entry = entry
            best_score = score
    if best_entry and best_entry["anchor"]:
        return best_entry["anchor"]
    return None


def verify_claim(
    claim: str,
    transcript_text: str,
    transcript_entries: list[dict[str, str]],
    index: int,
    polished_location: dict[str, int] | None = None,
) -> dict[str, Any]:
    transcript_normalized = normalize_verification_text(transcript_text)
    claim_normalized = normalize_verification_text(claim)
    terms = verification_terms(claim)
    unsupported_terms = [
        term
        for term in risky_verification_terms(claim)
        if normalize_verification_text(term) not in transcript_normalized
    ]
    anchor = find_transcript_anchor(terms, transcript_entries)

    if claim_normalized and claim_normalized in transcript_normalized:
        status = "supported"
        severity = "info"
        message = "The claim text appears in the transcript."
    elif unsupported_terms:
        status = "unsupported"
        severity = "high"
        message = "The claim contains names or numbers not found in the transcript."
    else:
        normalized_terms = [normalize_verification_text(term) for term in terms]
        normalized_terms = [term for term in normalized_terms if term]
        supported_terms = [term for term in normalized_terms if term in transcript_normalized]
        if normalized_terms and len(supported_terms) == len(normalized_terms):
            status = "supported"
            severity = "info"
            message = "The claim's key terms appear in the transcript."
        elif normalized_terms and not supported_terms and len(normalized_terms) >= 3:
            status = "unsupported"
            severity = "medium"
            message = "No key terms from the claim were found in the transcript."
        else:
            status = "uncertain"
            severity = "medium"
            message = "The claim could not be confirmed or rejected deterministically."

    return {
        "index": index,
        "status": status,
        "severity": severity,
        "claim": claim,
        "polished_location": polished_location or {"line": None, "column": None},
        "message": message,
        "unsupported_terms": unsupported_terms,
        "transcript_anchor": anchor,
    }


def verify_polished_output(polished_text: str, transcript_text: str) -> dict[str, Any]:
    transcript_entries = transcript_entries_from_text(transcript_text)
    findings = [
        verify_claim(
            record["claim"],
            transcript_text,
            transcript_entries,
            index,
            record["polished_location"],
        )
        for index, record in enumerate(split_polished_claim_records(polished_text), start=1)
    ]
    summary = {
        "claims": len(findings),
        "supported": sum(1 for finding in findings if finding["status"] == "supported"),
        "unsupported": sum(1 for finding in findings if finding["status"] == "unsupported"),
        "uncertain": sum(1 for finding in findings if finding["status"] == "uncertain"),
    }
    return {"summary": summary, "findings": findings}


def verify_polished_file(polished_path: str | Path, transcript_path: str | Path) -> dict[str, Any]:
    polished_text = Path(polished_path).expanduser().read_text(encoding="utf-8")
    transcript = load_verification_transcript(transcript_path)
    findings = [
        verify_claim(
            record["claim"],
            transcript["text"],
            transcript["entries"],
            index,
            record["polished_location"],
        )
        for index, record in enumerate(split_polished_claim_records(polished_text), start=1)
    ]
    summary = {
        "claims": len(findings),
        "supported": sum(1 for finding in findings if finding["status"] == "supported"),
        "unsupported": sum(1 for finding in findings if finding["status"] == "unsupported"),
        "uncertain": sum(1 for finding in findings if finding["status"] == "uncertain"),
    }
    return {"summary": summary, "findings": findings}


def render_verification(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        (
            f"claims: {summary['claims']}, supported: {summary['supported']}, "
            f"unsupported: {summary['unsupported']}, uncertain: {summary['uncertain']}"
        )
    ]
    for status in ("unsupported", "uncertain", "supported"):
        grouped = [finding for finding in result["findings"] if finding["status"] == status]
        if not grouped:
            continue
        lines.append(f"{status}:")
        for finding in grouped:
            anchor = f" [{finding['transcript_anchor']}]" if finding["transcript_anchor"] else ""
            location = finding.get("polished_location") or {}
            line = location.get("line")
            location_text = f"line {line}: " if line else ""
            lines.append(f"- {location_text}{finding['claim']}{anchor}\n  {finding['message']}")
    return "\n".join(lines) + "\n"
