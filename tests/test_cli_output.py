import io
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fandom_dict.cli.output import CommandOutput, OutputLogHandler


class Stream(io.StringIO):
    def __init__(self, *, tty: bool = False) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class CliOutputTests(unittest.TestCase):
    def test_outputs_mode_prints_paths_and_errors_only(self) -> None:
        stdout = Stream()
        stderr = Stream()
        output = CommandOutput("outputs", stdout=stdout, stderr=stderr)

        output.path("build/file.txt")
        output.info("normal progress")
        output.detail("word-by-word detail")
        output.warning("heads up")
        output.error("major failure")

        self.assertEqual(stdout.getvalue(), "build/file.txt\n")
        self.assertEqual(stderr.getvalue(), "error: major failure\n")

    def test_small_and_full_levels_filter_detail(self) -> None:
        small_stdout = Stream()
        small = CommandOutput("small", stdout=small_stdout, stderr=Stream())
        small.info("summary")
        small.detail("detail")
        small.warning("warning")
        self.assertIn("summary", small_stdout.getvalue())
        self.assertIn("warning: warning", small_stdout.getvalue())
        self.assertNotIn("detail", small_stdout.getvalue())

        full_stdout = Stream()
        full = CommandOutput("full", stdout=full_stdout, stderr=Stream())
        full.info("summary")
        full.detail("detail")
        self.assertIn("summary", full_stdout.getvalue())
        self.assertIn("detail", full_stdout.getvalue())

    def test_log_file_receives_full_uncolored_output(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "command.log"
            output = CommandOutput(
                "outputs",
                stdout=Stream(tty=True),
                stderr=Stream(tty=True),
                log_file=log_path,
            )
            output.path("build/file.txt")
            output.info("summary")
            output.detail("detail")
            output.warning("warning")
            output.error("failure")
            output.close()

            log_text = log_path.read_text(encoding="utf-8")

        self.assertIn("build/file.txt", log_text)
        self.assertIn("summary", log_text)
        self.assertIn("detail", log_text)
        self.assertIn("warning: warning", log_text)
        self.assertIn("error: failure", log_text)
        self.assertNotIn("\x1b[", log_text)

    def test_color_is_only_used_for_interactive_streams(self) -> None:
        tty_stdout = Stream(tty=True)
        tty_stderr = Stream(tty=True)
        tty_output = CommandOutput("small", stdout=tty_stdout, stderr=tty_stderr)
        tty_output.warning("warning")
        tty_output.error("failure")
        self.assertIn("\x1b[33m", tty_stdout.getvalue())
        self.assertIn("\x1b[31m", tty_stderr.getvalue())

        pipe_stdout = Stream(tty=False)
        pipe_stderr = Stream(tty=True)
        pipe_output = CommandOutput("small", stdout=pipe_stdout, stderr=pipe_stderr)
        pipe_output.warning("warning")
        pipe_output.error("failure")
        self.assertNotIn("\x1b[", pipe_stdout.getvalue())
        self.assertNotIn("\x1b[", pipe_stderr.getvalue())

    def test_logging_handler_respects_verbosity(self) -> None:
        stdout = Stream()
        stderr = Stream()
        output = CommandOutput("full", stdout=stdout, stderr=stderr)
        logger = logging.getLogger("tests.cli-output")
        logger.handlers.clear()
        logger.propagate = False
        handler = OutputLogHandler(output)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        logger.debug("detail")
        logger.info("summary")
        logger.warning("careful")
        logger.error("boom")

        self.assertIn("detail", stdout.getvalue())
        self.assertIn("summary", stdout.getvalue())
        self.assertIn("warning: careful", stdout.getvalue())
        self.assertIn("error: boom", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
