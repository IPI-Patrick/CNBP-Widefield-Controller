import dearpygui.dearpygui as dpg
from Utils.custom_widgets import add_input_float, add_input_int
import Utils.shared_state as shared_state
from Utils.state_persistence import apply_window_state, capture_window_state, load_state_file, save_state_file


class MockCameraControls:
    """Control window for the simulated camera — only visible when running in mock (disconnected) mode."""

    def __init__(self):
        self._camera = None
        self._initialized = False
        self._pending_camera_state = None

        _mock_tab = shared_state.layout_containers.get("mock_camera_tab")
        if _mock_tab:
            self.window_id = _mock_tab
        else:
            self.window_id = dpg.add_window(
                label="Mock Camera Settings",
                tag="#MockCameraControls",
                width=300,
                height=700,
                pos=(650, 620),
                show=False,
            )
        dpg.push_container_stack(self.window_id)
        if True:

            dpg.add_text("Simulation parameters (disconnected mode)")
            dpg.add_separator()

            with dpg.tree_node(label="Focus", default_open=True, span_full_width=True):
                self._focus_slider_id = dpg.add_slider_float(
                    label="Defocus (sigma px)",
                    width=-130,
                    default_value=0.8,
                    min_value=0.0,
                    max_value=12.0,
                    format="%.2f",
                    callback=self._on_focus_changed,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Particles", default_open=True, span_full_width=True):
                self._num_particles_slider_id = dpg.add_slider_int(
                    label="Count",
                    width=-130,
                    default_value=20,
                    min_value=1,
                    max_value=60,
                    callback=self._on_num_particles_changed,
                )
                self._min_radius_slider_id = dpg.add_slider_float(
                    label="Min radius (px)",
                    width=-130,
                    default_value=2.0,
                    min_value=1.0,
                    max_value=20.0,
                    format="%.1f",
                    callback=self._on_min_radius_changed,
                )
                self._max_radius_slider_id = dpg.add_slider_float(
                    label="Max radius (px)",
                    width=-130,
                    default_value=4.0,
                    min_value=1.0,
                    max_value=30.0,
                    format="%.1f",
                    callback=self._on_max_radius_changed,
                )
                dpg.add_button(
                    label="Regenerate Particles",
                    width=-1,
                    callback=self._on_regenerate_particles,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Intensity", default_open=True, span_full_width=True):
                self._mean_slider_id = dpg.add_slider_float(
                    label="Mean",
                    width=-130,
                    default_value=0.15,
                    min_value=0.0,
                    max_value=1.0,
                    format="%.2f",
                    callback=self._on_mean_changed,
                )
                self._std_slider_id = dpg.add_slider_float(
                    label="Std",
                    width=-130,
                    default_value=0.10,
                    min_value=0.0,
                    max_value=0.5,
                    format="%.2f",
                    callback=self._on_std_changed,
                )
                self._amplitude_slider_id = dpg.add_slider_float(
                    label="Amplitude",
                    width=-130,
                    default_value=0.35,
                    min_value=0.0,
                    max_value=1.0,
                    format="%.2f",
                    callback=self._on_amplitude_changed,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Illumination", default_open=True, span_full_width=True):
                self._illum_enabled_id = dpg.add_checkbox(
                    label="Enabled",
                    default_value=True,
                    callback=self._on_illum_enabled_changed,
                )
                self._illum_cx_slider_id = dpg.add_slider_float(
                    label="Centre X",
                    width=-130,
                    default_value=0.5,
                    min_value=0.0,
                    max_value=1.0,
                    format="%.2f",
                    callback=self._on_illum_cx_changed,
                )
                self._illum_cy_slider_id = dpg.add_slider_float(
                    label="Centre Y",
                    width=-130,
                    default_value=0.5,
                    min_value=0.0,
                    max_value=1.0,
                    format="%.2f",
                    callback=self._on_illum_cy_changed,
                )
                self._illum_sigma_slider_id = dpg.add_slider_float(
                    label="Sigma (px)",
                    width=-130,
                    default_value=300.0,
                    min_value=10.0,
                    max_value=1000.0,
                    format="%.0f",
                    callback=self._on_illum_sigma_changed,
                )
                self._illum_peak_slider_id = dpg.add_slider_float(
                    label="Peak intensity",
                    width=-130,
                    default_value=0.3,
                    min_value=0.0,
                    max_value=1.0,
                    format="%.2f",
                    callback=self._on_illum_peak_changed,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Translation", default_open=True, span_full_width=True):
                self._trans_x_slider_id = dpg.add_slider_int(
                    label="X offset (px)",
                    width=-130,
                    default_value=0,
                    min_value=-512,
                    max_value=512,
                    callback=self._on_trans_x_changed,
                )
                self._trans_y_slider_id = dpg.add_slider_int(
                    label="Y offset (px)",
                    width=-130,
                    default_value=0,
                    min_value=-512,
                    max_value=512,
                    callback=self._on_trans_y_changed,
                )
                dpg.add_button(
                    label="Reset",
                    width=-1,
                    callback=self._on_trans_reset,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Drift", default_open=True, span_full_width=True):
                self._drift_enabled_id = dpg.add_checkbox(
                    label="Enabled",
                    default_value=True,
                    callback=self._on_drift_enabled_changed,
                )
                self._drift_speed_slider_id = dpg.add_slider_float(
                    label="Speed",
                    width=-130,
                    default_value=0.5,
                    min_value=0.1,
                    max_value=5.0,
                    format="%.2f",
                    callback=self._on_drift_speed_changed,
                )
                self._drift_amplitude_slider_id = dpg.add_slider_float(
                    label="Amplitude (px)",
                    width=-130,
                    default_value=20.0,
                    min_value=0.0,
                    max_value=100.0,
                    format="%.1f",
                    callback=self._on_drift_amplitude_changed,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Global Pulse", default_open=True, span_full_width=True):
                self._pulse_period_id = add_input_float(
                    label="Period (s)",
                    width=-130,
                    default_value=3.0,
                    min_value=0.1,
                    step=0.5,
                    format="%.2f",
                    callback=self._on_pulse_period_changed,
                )
                self._pulse_amplitude_id = dpg.add_slider_float(
                    label="Amplitude",
                    width=-130,
                    default_value=0.5,
                    min_value=0.0,
                    max_value=1.0,
                    format="%.2f",
                    callback=self._on_pulse_amplitude_changed,
                )

            dpg.add_separator()

            with dpg.tree_node(label="Fiducials", default_open=True, span_full_width=True):
                self._fiducial_size_id = add_input_int(
                    label="Size (px)",
                    width=-130,
                    default_value=6,
                    min_value=1,
                    max_value=50,
                    step=1,
                    callback=self._on_fiducial_size_changed,
                )
                self._fiducial_offset_id = dpg.add_slider_float(
                    label="Separation",
                    width=-130,
                    default_value=0.20,
                    min_value=0.05,
                    max_value=0.49,
                    format="%.2f",
                    callback=self._on_fiducial_offset_changed,
                )

        dpg.pop_container_stack()

    # ── Camera access ───────────────────────────────────────────────────────────

    def _get_mock_camera(self):
        andor = getattr(shared_state, "shared_andor", None)
        if andor is None:
            return None
        if not getattr(andor, "isMock", False):
            return None
        return getattr(andor, "camera", None)

    def _sync_controls_to_camera(self, camera):
        dpg.set_value(self._focus_slider_id, float(camera.focus_sigma))
        dpg.set_value(self._num_particles_slider_id, int(camera.num_particles))
        dpg.set_value(self._min_radius_slider_id, float(camera.particle_min_radius))
        dpg.set_value(self._max_radius_slider_id, float(camera.particle_max_radius))
        dpg.set_value(self._mean_slider_id, float(camera.particle_mean))
        dpg.set_value(self._std_slider_id, float(camera.particle_std))
        dpg.set_value(self._amplitude_slider_id, float(camera.particle_amplitude))
        dpg.set_value(self._illum_enabled_id, bool(camera.illum_enabled))
        dpg.set_value(self._illum_cx_slider_id, float(camera.illum_center_x_frac))
        dpg.set_value(self._illum_cy_slider_id, float(camera.illum_center_y_frac))
        dpg.set_value(self._illum_sigma_slider_id, float(camera.illum_sigma))
        dpg.set_value(self._illum_peak_slider_id, float(camera.illum_peak))
        dpg.set_value(self._trans_x_slider_id, int(camera.translation_x))
        dpg.set_value(self._trans_y_slider_id, int(camera.translation_y))
        dpg.set_value(self._drift_enabled_id, bool(camera.drift_enabled))
        dpg.set_value(self._drift_speed_slider_id, float(camera.drift_speed))
        dpg.set_value(self._drift_amplitude_slider_id, float(camera.drift_amplitude))
        dpg.set_value(self._pulse_period_id, float(camera.global_pulse_period))
        dpg.set_value(self._pulse_amplitude_id, float(camera.global_pulse_amplitude))
        dpg.set_value(self._fiducial_size_id, int(camera.fiducial_size))
        dpg.set_value(self._fiducial_offset_id, float(camera.fiducial_offset_frac))

    # ── Callbacks ───────────────────────────────────────────────────────────────

    def _on_focus_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.focus_sigma = float(app_data)

    def _on_num_particles_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.set_num_particles(int(app_data))

    def _on_min_radius_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            new_min = float(app_data)
            camera.set_particle_min_radius(new_min)
            if camera.particle_max_radius < new_min:
                camera.set_particle_max_radius(new_min)
                dpg.set_value(self._max_radius_slider_id, new_min)

    def _on_max_radius_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            new_max = float(app_data)
            camera.set_particle_max_radius(new_max)
            if camera.particle_min_radius > new_max:
                camera.set_particle_min_radius(new_max)
                dpg.set_value(self._min_radius_slider_id, new_max)

    def _on_regenerate_particles(self, *_):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.regenerate_particles()

    def _on_mean_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.particle_mean = float(app_data)
            camera.rerandomize_particle_properties()

    def _on_std_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.particle_std = float(app_data)
            camera.rerandomize_particle_properties()

    def _on_amplitude_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.particle_amplitude = float(app_data)

    def _on_illum_enabled_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.illum_enabled = bool(app_data)

    def _on_illum_cx_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.illum_center_x_frac = float(app_data)
            camera._illum_cache_key = None

    def _on_illum_cy_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.illum_center_y_frac = float(app_data)
            camera._illum_cache_key = None

    def _on_illum_sigma_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.illum_sigma = max(1.0, float(app_data))
            camera._illum_cache_key = None

    def _on_illum_peak_changed(self, sender, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.illum_peak = float(app_data)
            camera._illum_cache_key = None

    def _on_trans_x_changed(self, _, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.translation_x = int(app_data)

    def _on_trans_y_changed(self, _, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.translation_y = int(app_data)

    def _on_trans_reset(self, *_):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.translation_x = 0
            camera.translation_y = 0
        dpg.set_value(self._trans_x_slider_id, 0)
        dpg.set_value(self._trans_y_slider_id, 0)

    def _on_drift_enabled_changed(self, _, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.drift_enabled = bool(app_data)

    def _on_drift_speed_changed(self, _, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.drift_speed = float(app_data)

    def _on_drift_amplitude_changed(self, _, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.drift_amplitude = float(app_data)

    def _on_pulse_period_changed(self, _, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.global_pulse_period = max(0.1, float(app_data))

    def _on_pulse_amplitude_changed(self, _, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.global_pulse_amplitude = float(app_data)

    def _on_fiducial_size_changed(self, _, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.fiducial_size = max(1, int(app_data))

    def _on_fiducial_offset_changed(self, _, app_data):
        camera = self._get_mock_camera()
        if camera is not None:
            camera.fiducial_offset_frac = float(app_data)

    # ── Render / state ──────────────────────────────────────────────────────────

    def render(self):
        camera = self._get_mock_camera()
        is_mock = camera is not None

        if is_mock:
            if not dpg.is_item_shown(self.window_id):
                dpg.show_item(self.window_id)
            if not self._initialized:
                pending = getattr(self, "_pending_camera_state", None)
                if pending:
                    self._apply_state_to_camera(camera, pending)
                    self._pending_camera_state = None
                self._sync_controls_to_camera(camera)
                self._initialized = True
            self._camera = camera
        else:
            if dpg.is_item_shown(self.window_id):
                dpg.hide_item(self.window_id)
            self._initialized = False
            self._camera = None

    def _apply_state_to_camera(self, camera, state):
        if "focus_sigma" in state:
            camera.focus_sigma = float(state["focus_sigma"])
        if "num_particles" in state:
            camera.set_num_particles(int(state["num_particles"]))
        if "particle_min_radius" in state:
            camera.set_particle_min_radius(float(state["particle_min_radius"]))
        if "particle_max_radius" in state:
            camera.set_particle_max_radius(float(state["particle_max_radius"]))
        if "particle_mean" in state:
            camera.particle_mean = float(state["particle_mean"])
        if "particle_std" in state:
            camera.particle_std = float(state["particle_std"])
        if "particle_amplitude" in state:
            camera.particle_amplitude = float(state["particle_amplitude"])
        if "illum_enabled" in state:
            camera.illum_enabled = bool(state["illum_enabled"])
        if "illum_center_x_frac" in state:
            camera.illum_center_x_frac = float(state["illum_center_x_frac"])
            camera._illum_cache_key = None
        if "illum_center_y_frac" in state:
            camera.illum_center_y_frac = float(state["illum_center_y_frac"])
            camera._illum_cache_key = None
        if "illum_sigma" in state:
            camera.illum_sigma = max(1.0, float(state["illum_sigma"]))
            camera._illum_cache_key = None
        if "illum_peak" in state:
            camera.illum_peak = float(state["illum_peak"])
            camera._illum_cache_key = None
        if "translation_x" in state:
            camera.translation_x = int(state["translation_x"])
        if "translation_y" in state:
            camera.translation_y = int(state["translation_y"])
        if "drift_enabled" in state:
            camera.drift_enabled = bool(state["drift_enabled"])
        if "drift_speed" in state:
            camera.drift_speed = float(state["drift_speed"])
        if "drift_amplitude" in state:
            camera.drift_amplitude = float(state["drift_amplitude"])
        if "global_pulse_period" in state:
            camera.global_pulse_period = max(0.1, float(state["global_pulse_period"]))
        if "global_pulse_amplitude" in state:
            camera.global_pulse_amplitude = float(state["global_pulse_amplitude"])
        if "fiducial_size" in state:
            camera.fiducial_size = max(1, int(state["fiducial_size"]))
        if "fiducial_offset_frac" in state:
            camera.fiducial_offset_frac = float(state["fiducial_offset_frac"])

    def SaveState(self):
        save_state_file(
            type(self).__name__,
            {
                "focus_sigma": float(dpg.get_value(self._focus_slider_id)),
                "num_particles": int(dpg.get_value(self._num_particles_slider_id)),
                "particle_min_radius": float(dpg.get_value(self._min_radius_slider_id)),
                "particle_max_radius": float(dpg.get_value(self._max_radius_slider_id)),
                "particle_mean": float(dpg.get_value(self._mean_slider_id)),
                "particle_std": float(dpg.get_value(self._std_slider_id)),
                "particle_amplitude": float(dpg.get_value(self._amplitude_slider_id)),
                "illum_enabled": bool(dpg.get_value(self._illum_enabled_id)),
                "illum_center_x_frac": float(dpg.get_value(self._illum_cx_slider_id)),
                "illum_center_y_frac": float(dpg.get_value(self._illum_cy_slider_id)),
                "illum_sigma": float(dpg.get_value(self._illum_sigma_slider_id)),
                "illum_peak": float(dpg.get_value(self._illum_peak_slider_id)),
                "translation_x": int(dpg.get_value(self._trans_x_slider_id)),
                "translation_y": int(dpg.get_value(self._trans_y_slider_id)),
                "drift_enabled": bool(dpg.get_value(self._drift_enabled_id)),
                "drift_speed": float(dpg.get_value(self._drift_speed_slider_id)),
                "drift_amplitude": float(dpg.get_value(self._drift_amplitude_slider_id)),
                "global_pulse_period": float(dpg.get_value(self._pulse_period_id)),
                "global_pulse_amplitude": float(dpg.get_value(self._pulse_amplitude_id)),
                "fiducial_size": int(dpg.get_value(self._fiducial_size_id)),
                "fiducial_offset_frac": float(dpg.get_value(self._fiducial_offset_id)),
            },
        )

    def LoadState(self):
        state = load_state_file(type(self).__name__)
        if not state:
            return
        keys = (
            "focus_sigma", "num_particles", "particle_min_radius", "particle_max_radius",
            "particle_mean", "particle_std", "particle_amplitude",
            "illum_enabled", "illum_center_x_frac", "illum_center_y_frac", "illum_sigma", "illum_peak",
            "translation_x", "translation_y", "drift_enabled", "drift_speed", "drift_amplitude",
            "global_pulse_period", "global_pulse_amplitude",
            "fiducial_size", "fiducial_offset_frac",
        )
        pending = {k: state[k] for k in keys if k in state}
        if pending:
            self._pending_camera_state = pending

        slider_map = {
            "focus_sigma": (self._focus_slider_id, float),
            "num_particles": (self._num_particles_slider_id, int),
            "particle_min_radius": (self._min_radius_slider_id, float),
            "particle_max_radius": (self._max_radius_slider_id, float),
            "particle_mean": (self._mean_slider_id, float),
            "particle_std": (self._std_slider_id, float),
            "particle_amplitude": (self._amplitude_slider_id, float),
            "illum_enabled": (self._illum_enabled_id, bool),
            "illum_center_x_frac": (self._illum_cx_slider_id, float),
            "illum_center_y_frac": (self._illum_cy_slider_id, float),
            "illum_sigma": (self._illum_sigma_slider_id, float),
            "illum_peak": (self._illum_peak_slider_id, float),
            "translation_x": (self._trans_x_slider_id, int),
            "translation_y": (self._trans_y_slider_id, int),
            "drift_enabled": (self._drift_enabled_id, bool),
            "drift_speed": (self._drift_speed_slider_id, float),
            "drift_amplitude": (self._drift_amplitude_slider_id, float),
            "global_pulse_period": (self._pulse_period_id, float),
            "global_pulse_amplitude": (self._pulse_amplitude_id, float),
            "fiducial_size": (self._fiducial_size_id, int),
            "fiducial_offset_frac": (self._fiducial_offset_id, float),
        }
        for key, (item_id, cast) in slider_map.items():
            if key in state:
                dpg.set_value(item_id, cast(state[key]))
