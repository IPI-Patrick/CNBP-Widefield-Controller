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