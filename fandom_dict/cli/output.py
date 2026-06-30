"""Shared command-line output helpers."""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import TextIO


VERBOSITY_OUTPUTS = "outputs"
VERBOSITY_SMALL = "small"
VERBOSITY_FULL = "full"
VERBOSITY_CHOICES = (VERBOSITY_OUTPUTS, VERBOSITY_SMALL, VERBOSITY_FULL)
DIAGNOSTIC_LINE_COLORS = {
    "alias:": "32",
    "omitted alias:": "33",
    "multi-target lookup:": "36",
}

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_IMPORTANT_ATTRIBUTE_RE = re.compile(r'(?:alias|lookup)="[^"]*"')


class CommandOutput:
    """Route CLI messages according to verbosity, color, and log-file settings."""

    def __init__(
        self,
        verbosity: str = VERBOSITY_SMALL,
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        log_file: Path | None = None,
    ) -> None:
        if verbosity not in VERBOSITY_CHOICES:
            raise ValueError(f"unsupported verbosity: {verbosity}")
        self.verbosity = verbosity
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self._log_handle = None
        self._stdout_broken = False
        self._stderr_broken = False
        if log_file is not None:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = log_file.open("a", encoding="utf-8")
        self._use_color = bool(self.stdout.isatty() and self.stderr.isatty())

    def close(self) -> None:
        """Close the optional transcript log."""

        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler, OutputLogHandler) and handler.output is self:
                root.removeHandler(handler)
        if not root.handlers:
            root.setLevel(logging.WARNING)
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def path(self, value: Path | str) -> None:
        """Print a created output path, visible at every verbosity level."""

        self._write_stdout(str(value))

    def info(self, message: str) -> None:
        """Print a normal high-level progress message."""

        self._write_log(message)
        if self.verbosity in {VERBOSITY_SMALL, VERBOSITY_FULL}:
            self._write_stdout(self._color_diagnostic_line(message), log=False)

    def detail(self, message: str) -> None:
        """Print a detailed progress message only at full verbosity."""

        self._write_log(message)
        if self.verbosity == VERBOSITY_FULL:
            self._write_stdout(self._color_diagnostic_line(message), log=False)

    def warning(self, message: str) -> None:
        """Print a warning unless stdout is reserved for output paths."""

        self._write_log(f"warning: {message}")
        if self.verbosity in {VERBOSITY_SMALL, VERBOSITY_FULL}:
            self._write_stdout(self._color(f"warning: {message}", "33"), log=False)

    def error(self, message: str) -> None:
        """Print a major error to stderr regardless of stdout verbosity."""

        self._write_log(f"error: {message}")
        self._write_stderr(self._color(f"error: {message}", "31"), log=False)

    def _write_stdout(self, message: str, *, log: bool = True) -> None:
        if self._stdout_broken:
            if log:
                self._write_log(message)
            return
        try:
            print(message, file=self.stdout)
        except BrokenPipeError:
            self._stdout_broken = True
            self._redirect_standard_stream_to_devnull(self.stdout)
            if log:
                self._write_log(message)
            return
        if log:
            self._write_log(message)

    def _write_stderr(self, message: str, *, log: bool = True) -> None:
        if self._stderr_broken:
            if log:
                self._write_log(message)
            return
        try:
            print(message, file=self.stderr)
        except BrokenPipeError:
            self._stderr_broken = True
            self._redirect_standard_stream_to_devnull(self.stderr)
            if log:
                self._write_log(message)
            return
        if log:
            self._write_log(message)

    def _write_log(self, message: str) -> None:
        if self._log_handle is not None:
            self._log_handle.write(_ANSI_RE.sub("", message) + "\n")
            self._log_handle.flush()

    def _color(self, message: str, code: str) -> str:
        if not self._use_color:
            return message
        return f"\x1b[{code}m{message}\x1b[0m"

    def _color_diagnostic_line(self, message: str) -> str:
        for prefix, code in DIAGNOSTIC_LINE_COLORS.items():
            if message.startswith(prefix):
                message = self._bold_important_attributes(message)
                return self._color(message, code)
        return message

    def _bold_important_attributes(self, message: str) -> str:
        if not self._use_color:
            return message
        return _IMPORTANT_ATTRIBUTE_RE.sub(lambda match: f"\x1b[1m{match.group(0)}\x1b[22m", message)

    @staticmethod
    def _redirect_standard_stream_to_devnull(stream: TextIO) -> None:
        """Prevent interpreter shutdown flush errors after a closed pipe."""

        if stream is not sys.stdout and stream is not sys.stderr:
            return
        try:
            fd = stream.fileno()
        except (AttributeError, OSError):
            return
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(devnull, fd)
            finally:
                os.close(devnull)
        except OSError:
            return


class OutputLogHandler(logging.Handler):
    """Bridge Python logging records to ``CommandOutput``."""

    def __init__(self, output: CommandOutput) -> None:
        super().__init__(level=logging.DEBUG)
        self.output = output

    def emit(self, record: logging.LogRecord) -> None:
        """Write one logging record through the shared output policy."""

        try:
            message = self.format(record)
            if record.levelno >= logging.ERROR:
                self.output.error(message)
            elif record.levelno >= logging.WARNING:
                self.output.warning(message)
            elif record.levelno >= logging.INFO:
                self.output.info(message)
            else:
                self.output.detail(message)
        except Exception:
            self.handleError(record)


def add_output_arguments(parser) -> None:
    """Add common verbosity and transcript flags to a command parser."""

    parser.add_argument(
        "--verbosity",
        choices=VERBOSITY_CHOICES,
        default=VERBOSITY_SMALL,
        help="Control stdout detail: outputs, small, or full.",
    )
    parser.add_argument("--log-file", type=Path, help="Write a full uncolored command transcript.")


def output_from_args(args) -> CommandOutput:
    """Create a ``CommandOutput`` from parsed argparse values."""

    return CommandOutput(args.verbosity, log_file=args.log_file)


def configure_logging(output: CommandOutput) -> None:
    """Send root logger records through the shared command output helper."""

    root = logging.getLogger()
    root.handlers.clear()
    handler = OutputLogHandler(output)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
