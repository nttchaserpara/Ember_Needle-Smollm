"""Sample simultaneous RSS of Ember and its descendants during a request."""

import threading

import psutil


class RequestMemorySampler:
    def __init__(self, process=None, interval=0.05):
        self.process = process if process is not None else psutil.Process()
        self.interval = interval
        self.peak_total = 0
        self.parent_at_peak = 0
        self.children_at_peak = 0
        self._stop = threading.Event()
        self._thread = None

    def _sample(self):
        try:
            parent = self.process.memory_info().rss
            children = self.process.children(recursive=True)
        except psutil.Error:
            return
        child_rss = 0
        for child in children:
            try:
                child_rss += child.memory_info().rss
            except psutil.Error:
                # A worker can exit between discovery and reading its RSS.
                continue
        total = parent + child_rss
        if total > self.peak_total:
            self.peak_total = total
            self.parent_at_peak = parent
            self.children_at_peak = child_rss

    def _monitor(self):
        while not self._stop.wait(self.interval):
            self._sample()

    def __enter__(self):
        self._sample()
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        self._thread.join()
        self._sample()
