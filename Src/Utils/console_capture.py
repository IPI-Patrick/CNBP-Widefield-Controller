import queue
import sys
import threading
from collections import deque


_CONSOLE_CAPTURE = None
_CONSOLE_CAPTURE_LOCK = threading.Lock()


class _StreamTee:
    def __init__(self, capture, stream_name, original_stream):
        self._capture = capture
        self._stream_name = stream_name
        self._original_stream = original_stream
        self._pending = ""
        self.encoding = getattr(original_stream, "encoding", "utf-8")
        self.errors = getattr(original_stream, "errors", "strict")

    def write(self, text):
        if not isinstance(text, str):
            text = str(text)

        self._original_stream.write(text)
        for line in self._append_text(text):
            self._capture.enqueue_line(self._stream_name, line)
        return len(text)

    def flush(self):
        self._original_stream.flush()
        pending_line = self._consume_pending()
        if pending_line is not None:
            self._capture.enqueue_line(self._stream_name, pending_line)

    def isatty(self):
        return bool(getattr(self._original_stream, "isatty", lambda: False)())

    def fileno(self):
        fileno = getattr(self._original_stream, "fileno", None)
        if fileno is None:
            raise OSError("Underlying stream does not expose fileno().")
        return fileno()

    def writable(self):
        return True

    def _append_text(self, text):
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        self._pending += normalized
        completed_lines = []

        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            completed_lines.append(line)

        return completed_lines

    def _consume_pending(self):
        if not self._pending:
            return None

        pending_line = self._pending
        self._pending = ""
        return pending_line

    def __getattr__(self, name):
        return getattr(self._original_stream, name)


class ConsoleCapture:
    def __init__(self, max_lines=100):
        self.max_lines = max_lines
        self._queue = queue.Queue()
        self._lines = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._version = 0
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._run, name="ConsoleCaptureThread", daemon=True)
        self._worker.start()

    def _run(self):
        while not self._stop_event.is_set():
            try:
                line = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            with self._lock:
                self._lines.append(line)
                self._version += 1

    def enqueue_line(self, stream_name, line):
        self._queue.put(self._format_line(stream_name, line))

    def _format_line(self, stream_name, line):
        if stream_name == "stderr":
            return f"[stderr] {line}"
        return line

    def get_snapshot(self):
        with self._lock:
            return self._version, list(self._lines)

    def clear(self):
        with self._lock:
            self._lines.clear()
            self._version += 1


def install_console_capture(max_lines=100):
    global _CONSOLE_CAPTURE

    with _CONSOLE_CAPTURE_LOCK:
        if _CONSOLE_CAPTURE is not None:
            return _CONSOLE_CAPTURE

        capture = ConsoleCapture(max_lines=max_lines)
        sys.stdout = _StreamTee(capture, "stdout", sys.stdout)
        sys.stderr = _StreamTee(capture, "stderr", sys.stderr)
        _CONSOLE_CAPTURE = capture
        return capture


def get_console_capture():
    return install_console_capture()