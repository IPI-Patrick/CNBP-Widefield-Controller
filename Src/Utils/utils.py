import os
import importlib.util

def scale(value, *args):
    """
    Scales a value from one range to another.

    Usage:
        scale(value, [in_min, in_max, out_min, out_max])
        scale(value, in_min, in_max, out_min, out_max)

    Example:
        scale(50, 0, 100, 0, 1000) -> 500
        scale(50, [0, 100, 0, 1000]) -> 500
    """
    if len(args) == 1 and isinstance(args[0], (list, tuple)) and len(args[0]) == 4:
        in_min, in_max, out_min, out_max = args[0]
    elif len(args) == 4:
        in_min, in_max, out_min, out_max = args
    else:
        raise ValueError("Provide either a 4-element array or four numbers for the range.")

    if in_max == in_min:
        raise ValueError("Input range cannot be zero.")

    scaled = (value - in_min) / (in_max - in_min) * (out_max - out_min) + out_min
    return scaled

def clamp(value, min_value, max_value):
    """
    Clamps a value between a minimum and maximum value.
    
    :param value: The value to clamp.
    :param min_value: The minimum value.
    :param max_value: The maximum value.
    :return: The clamped value.
    """
    return max(min(value, max_value), min_value)


def load_window_classes(folder):
    window_classes = []
    
    if "Unused" in folder:
        return []
    
    for filename in sorted(os.listdir(folder)):

        if filename.endswith(".py") and not filename.startswith("__"):
            module_name     = filename[:-3]
            module_path     = os.path.join(folder, filename)
            spec            = importlib.util.spec_from_file_location(module_name, module_path)
            module          = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Find class in module (assume class name matches filename, case-insensitive)
            for attr in dir(module):
                obj = getattr(module, attr)
                if isinstance(obj, type) and obj.__module__ == module.__name__:
                    window_classes.append(obj)    

    # print(f"Loaded window classes: {[cls.__name__ for cls in window_classes]}")
    return window_classes