import numpy as np


SUPPORTED_STORAGE_BIT_DEPTHS = ("8", "12", "16")
SUPPORTED_STORAGE_DTYPES = SUPPORTED_STORAGE_BIT_DEPTHS
_SIGNED_STORAGE_DTYPE = np.dtype(np.float16)
_RAW_STORAGE_DTYPES = {
    "8": np.dtype(np.uint8),
    "12": np.dtype(np.uint16),
    "16": np.dtype(np.uint16),
}
_SIGNED_MANTISSA_BITS = {
    "8": 3,
    "12": 7,
    "16": 10,
}
_LEGACY_STORAGE_ALIASES = {
    "8": "8",
    "8-bit": "8",
    "float8": "8",
    "uint8": "8",
    "12": "12",
    "12-bit": "12",
    "float12": "12",
    "16": "16",
    "16-bit": "16",
    "float16": "16",
    "float32": "16",
    "float64": "16",
    "int8": "16",
    "int16": "16",
    "int32": "16",
    "int64": "16",
    "uint16": "16",
    "uint32": "16",
    "uint64": "16",
}


def canonicalize_storage_bit_depth_name(dtype_name, fallback=None):
    text = str(dtype_name or "").strip().lower()
    alias = _LEGACY_STORAGE_ALIASES.get(text)
    if alias is not None:
        return alias

    resolved = None
    try:
        resolved = np.dtype(text).name
    except Exception:
        resolved = None

    alias = _LEGACY_STORAGE_ALIASES.get(resolved or "")
    if alias is not None:
        return alias

    if fallback is not None:
        return canonicalize_storage_bit_depth_name(fallback)

    supported = ", ".join(SUPPORTED_STORAGE_BIT_DEPTHS)
    raise ValueError(f"Unsupported data type '{dtype_name}'. Supported values: {supported}")


def get_storage_bit_depth(dtype_name):
    return int(canonicalize_storage_bit_depth_name(dtype_name))


def get_raw_storage_dtype(dtype_name):
    normalized_name = canonicalize_storage_bit_depth_name(dtype_name)
    return _RAW_STORAGE_DTYPES[normalized_name]


def get_signed_storage_dtype(dtype_name):
    canonicalize_storage_bit_depth_name(dtype_name)
    return _SIGNED_STORAGE_DTYPE


def get_raw_storage_bytes(dtype_name):
    return float(get_raw_storage_dtype(dtype_name).itemsize)


def get_signed_storage_bytes(dtype_name):
    return float(get_signed_storage_dtype(dtype_name).itemsize)


def get_raw_storage_max_value(dtype_name):
    return float((1 << get_storage_bit_depth(dtype_name)) - 1)


def quantize_to_raw_storage_dtype(values, dtype_name, source_max_value=None):
    normalized_name = canonicalize_storage_bit_depth_name(dtype_name)
    raw_dtype = get_raw_storage_dtype(normalized_name)
    quantized = np.asarray(values, dtype=np.float32)
    target_max = float((1 << get_storage_bit_depth(normalized_name)) - 1)

    if source_max_value is None:
        finite_mask = np.isfinite(quantized)
        source_max_value = float(np.max(quantized[finite_mask])) if np.any(finite_mask) else target_max

    source_max_value = max(float(source_max_value), 1.0)
    quantized = np.clip(quantized, 0.0, source_max_value)
    if source_max_value > target_max:
        quantized = np.round((quantized / source_max_value) * target_max)
    else:
        quantized = np.round(quantized)

    return quantized.astype(raw_dtype, copy=False)


def quantize_to_signed_storage_dtype(values, dtype_name):
    normalized_name = canonicalize_storage_bit_depth_name(dtype_name)
    array = np.asarray(values, dtype=np.float32)

    if normalized_name == "16":
        return array.astype(_SIGNED_STORAGE_DTYPE, copy=False)

    quantized = np.array(array, dtype=np.float32, copy=True)
    finite_mask = np.isfinite(quantized)
    if np.any(finite_mask):
        mantissa_bits = _SIGNED_MANTISSA_BITS[normalized_name]
        finite_values = quantized[finite_mask]
        mantissa, exponent = np.frexp(finite_values)
        scale = float(2 ** mantissa_bits)
        mantissa = np.round(mantissa * scale) / scale
        quantized[finite_mask] = np.ldexp(mantissa, exponent)

    return quantized.astype(_SIGNED_STORAGE_DTYPE, copy=False)