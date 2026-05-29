class_objects  = []
shared_andor = None
shared_stage = None   # dict {"x": KST101, "y": KST101, "z": KST101} set by StageControls

# Set to True when the app is launched with the -dev flag.
# Drivers that support a mock mode (e.g. KST101) read this at connect time
# to decide whether to use real hardware or a simulated replacement.
dev_mode: bool = False

# Populated by AppLayout before other windows initialize.
# Maps logical names to DPG item tags for embedded panel containers.
layout_containers: dict = {}

# True while any custom input widget has keyboard focus, or while a file
# dialog is open.  Keyboard shortcut handlers check this flag and bail out
# early so that typing in an input never accidentally triggers toolbar
# actions.  Bool assignment is atomic in CPython, so no lock is needed.
currently_editing: bool = False