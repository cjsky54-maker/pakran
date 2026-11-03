"""Engineering-friendly result text for the result panel and Copy button."""
from __future__ import annotations

from typing import Optional


def format_result_text(result: dict) -> str:
    """Format a test result dict (Standard or Quick) as QC record text."""
    lines: list[str] = []
    status = result.get("status", "")
    overall = result.get("overall_result")
    test_type = (result.get("test_type") or "").capitalize() or "Test"
    header_status = overall or status

    if overall == "PASS":
        lines.append("QUALITY CONTROL: PASS")
        lines.append("All Regions are within their allowed temperature limits.")
    elif overall == "FAIL":
        lines.append("QUALITY CONTROL: FAIL")
        for region in result.get("regions", []):
            if region.get("status") != "FAIL":
                continue
            lines.append("")
            lines.append(f"Region {region['label']}: FAIL")
            lines.append("")
            lines.append(f"Measured Max: {region['max']:.1f} C")
            lines.append(f"Ambient: {region['ambient']:.1f} C")
            lines.append(f"Measured DeltaT: {region['delta_t']:.1f} C")
            lines.append(f"Maximum Allowed DeltaT: {region['max_allowed']:.1f} C")
            lines.append(f"Exceedance: {region['exceedance']:.1f} C")
    else:
        lines.append(f"QUALITY CONTROL: {header_status or 'NOT EVALUATED'}")
        reason: Optional[str] = result.get("reason")
        if reason:
            lines.append(f"Reason: {reason}")
        if status == "TIMEOUT":
            lines.append("Temperature rise threshold was not reached within the waiting timeout.")
        elif status == "INCOMPLETE":
            lines.append("The test did not produce a deciding sample; collected data was saved.")

    lines.append("")
    lines.append(
        f"{test_type} Test - Test ID {result.get('test_id', '?')}"
    )
    deciding = result.get("deciding_sample_t")
    if deciding is not None:
        lines.append(f"Deciding sample: T{deciding:g} s")
    return "\n".join(lines)
