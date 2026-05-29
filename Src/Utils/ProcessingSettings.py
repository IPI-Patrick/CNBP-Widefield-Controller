from collections import deque


class ProcessingSettings:
    """Holds all science-pipeline parameters used by Andor.process_frame.

    GUI callbacks update attributes on this object (e.g.
    ``andor.settings.drift_correction_enabled = True``).  Any registered
    change-callback is notified so that downstream consumers (e.g. ROI plot
    buffers) can react immediately.
    """

    _fields = {
        "lp_filter_enabled": False,
        "lp_filter_cutoff_hz": 10.0,
        "drift_correction_enabled": False,
        "bg_removal_enabled": False,
        "bg_removal_sigma": 20.0,
        "crop_percent": 100.0,
        "display_mode": "Normal",   # "Normal" | "Difference" | "Contrast"
        "zero_frame": None,         # numpy array or None
        "frame_rate_hz": 10.0,
        "max_value": 65535.0,
        # Colormap / display settings — updated by CameraFeed GUI callbacks.
        "colormap_lut_gpu": None,         # GPU LUT array shape (N, 3) float32, or None
        "colormap_double_sided": False,   # True for Difference / Contrast modes
        "autoscale_enabled": True,
        "autoscale_grace_percent": 5.0,
        "scale_min": 0.0,
        "scale_max": 65535.0,
        "mirrored_difference_scale": False,
    }

    def __init__(self):
        object.__setattr__(self, "_change_callbacks", [])
        for key, default in type(self)._fields.items():
            object.__setattr__(self, key, default)

    # ------------------------------------------------------------------

    def add_change_callback(self, callback):
        """Register *callback(field_name, new_value)* for any setting change."""
        self._change_callbacks.append(callback)

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name in type(self)._fields:
            for cb in list(getattr(self, "_change_callbacks", [])):
                try:
                    cb(name, value)
                except Exception:
                    pass
