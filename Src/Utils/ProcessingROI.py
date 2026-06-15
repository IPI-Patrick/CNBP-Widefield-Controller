from collections import deque

import numpy as np


class ProcessingROI:
    """Data-layer ROI: pixel bounds, a boolean mask, and deque-based plot buffers.

    The mask is (re)computed the first time a frame of the right shape is
    processed, and whenever *x*, *y*, *w*, or *h* change.  Changing any of
    those properties also clears the plot buffers so stale trace data is not
    displayed alongside new data.

    Plot buffers are filled by ``Andor.process_frame`` on every processed frame.
    """

    def __init__(self, x, y, w, h, max_points=200, tag=None):
        self.tag = tag
        self.max_points = int(max_points)
        self._x = int(x)
        self._y = int(y)
        self._w = int(w)
        self._h = int(h)
        self._frame_shape = None
        self.mask = None
        self.slice_bounds = (0, 0, 0, 0)  # (y1, y2, x1, x2) — kept in sync with mask
        self.plot_x = deque(maxlen=self.max_points)
        self.plot_y = deque(maxlen=self.max_points)

    # ------------------------------------------------------------------
    # Coordinate properties — each setter recomputes the mask and clears
    # the plot buffers so downstream consumers see a clean restart.
    # ------------------------------------------------------------------

    @property
    def x(self):
        return self._x

    @x.setter
    def x(self, value):
        self._x = int(value)
        self._recompute_mask_and_clear()

    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, value):
        self._y = int(value)
        self._recompute_mask_and_clear()

    @property
    def w(self):
        return self._w

    @w.setter
    def w(self, value):
        self._w = int(value)
        self._recompute_mask_and_clear()

    @property
    def h(self):
        return self._h

    @h.setter
    def h(self, value):
        self._h = int(value)
        self._recompute_mask_and_clear()

    # ------------------------------------------------------------------

    def update_bounds(self, x, y, w, h):
        """Set all four bounds at once (avoids four separate mask recomputes)."""
        self._x = int(x)
        self._y = int(y)
        self._w = int(w)
        self._h = int(h)
        self._recompute_mask_and_clear()

    def update_mask(self, frame_shape):
        """(Re)compute the boolean pixel mask for *frame_shape* (h, w)."""
        h_img, w_img = int(frame_shape[0]), int(frame_shape[1])
        x1 = max(0, self._x)
        y1 = max(0, self._y)
        x2 = min(w_img, self._x + self._w)
        y2 = min(h_img, self._y + self._h)
        self.slice_bounds = (y1, y2, x1, x2)
        mask = np.zeros((h_img, w_img), dtype=bool)
        if x2 > x1 and y2 > y1:
            mask[y1:y2, x1:x2] = True
        self.mask = mask
        self._frame_shape = (h_img, w_img)

    def clear_plot_buffers(self):
        """Empty the accumulated plot data (new maxlen-bounded deques)."""
        self.plot_x = deque(maxlen=self.max_points)
        self.plot_y = deque(maxlen=self.max_points)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _recompute_mask_and_clear(self):
        if self._frame_shape is not None:
            self.update_mask(self._frame_shape)
        self.clear_plot_buffers()
