"""Human-readable progress display for CLI recommender runs.

Uses print() for user-facing output. All methods are no-ops when disabled.
"""

from __future__ import annotations

import sys
from typing import Any


class ProgressDisplay:
    """Print-friendly progress reporter for the recommender pipeline."""

    def __init__(self, *, enabled: bool = True, file: Any = None) -> None:
        self._enabled = enabled
        self._file = file or sys.stdout
        self._indent = 0
        self._phase_num = 0

    # -- control ----------------------------------------------------------------

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def blank(self) -> None:
        if self._enabled:
            self._file.write("\n")

    # -- top-level structure ----------------------------------------------------

    def separator(self) -> None:
        if self._enabled:
            self._file.write("───\n")

    def header(self, company_name: str) -> None:
        if self._enabled:
            self._file.write(f"\n═══ 开始分析：{company_name} ═══\n\n")

    def phase(self, step: int, total: int, name: str) -> None:
        self._phase_num = step
        if self._enabled:
            self._file.write(f"📊 阶段 {step}/{total}: {name}\n")

    def done(self, report_path: str, status: str = "success") -> None:
        if self._enabled:
            icon = "✓" if status in ("success", "partial") else "✗"
            self._file.write(f"\n  {icon} 报告: {report_path}\n")

    # -- single-line status -----------------------------------------------------

    def ok(self, detail: str) -> None:
        if self._enabled:
            self._file.write(f"  ✓ {detail}\n")

    def skip(self, detail: str) -> None:
        if self._enabled:
            self._file.write(f"  ⏭ {detail}\n")

    def fail(self, detail: str) -> None:
        if self._enabled:
            self._file.write(f"  ✗ {detail}\n")

    def info(self, detail: str) -> None:
        if self._enabled:
            self._file.write(f"  {detail}\n")

    def raw(self, text: str) -> None:
        if self._enabled:
            self._file.write(text)
            if not text.endswith("\n"):
                self._file.write("\n")

    def tree(self, detail: str) -> None:
        if self._enabled:
            self._file.write(f"  └─ {detail}\n")

    def branch(self, detail: str) -> None:
        if self._enabled:
            self._file.write(f"  ├─ {detail}\n")

    # -- tables -----------------------------------------------------------------

    def table(self, rows: list[tuple[str, str]], *, indent: int = 2) -> None:
        """Print aligned two-column rows like a table.

        Args:
            rows: list of (label, value) pairs.
            indent: leading spaces.
        """
        if not self._enabled or not rows:
            return
        prefix = " " * indent
        max_len = max(len(label) for label, _ in rows)
        for label, value in rows:
            self._file.write(f"{prefix}{label:<{max_len + 1}} {value}\n")

    def dim_table(self, label_width: int, rows: list[tuple[str, str, str]]) -> None:
        """Print dimension-aligned rows: dim_id | stat | detail."""
        if not self._enabled or not rows:
            return
        w = label_width
        for dim_id, stat, detail in rows:
            self._file.write(f"  ├─ {dim_id:<{w}} {stat:<12} {detail}\n")

    # -- flush ------------------------------------------------------------------

    def flush(self) -> None:
        if self._enabled:
            self._file.flush()


# Module-level singleton — set enabled/disabled before pipeline runs.
display = ProgressDisplay()
