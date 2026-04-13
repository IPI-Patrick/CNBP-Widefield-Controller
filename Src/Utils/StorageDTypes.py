import numpy as np


SUPPORTED_FLOAT_STORAGE_DTYPES = ("float8", "float12", "float16")
_FLOAT_STORAGE_DTYPE = np.dtype(np.float16)
_MANTISSA_BITS = {
    "float8": 3,
    "float12": 7,
    "float16": 10,
}
_STORAGE_BITS = {
    "float8": 8,
    "float12": 12,
    "float16": 16,
}
_LEGACY_FLOAT_DTYPE_ALIASES = {
    "float32": "float16",
    "float64": "float16",
    "int8": "float16",
    "int16": "float16",
    "int32": "float16",
    "int64": "float16",
    "uint8": "float16",
    "uint16": "float16",
    "uint32": "float16",
    "uint64": "float16",
}


def canonicalize_float_storage_dtype_name(dtype_name, fallback=None):
    text = str(dtype_name or "").strip().lower()
    if text in SUPPORTED_FLOAT_STORAGE_DTYPES:
        return text

    resolved = None
    try:
        resolved = np.dtype(text).name
    except Exception:
        resolved = None

    if resolved in SUPPORTED_FLOAT_STORAGE_DTYPES:
        return resolved

    alias = _LEGACY_FLOAT_DTYPE_ALIASES.get(text) or _LEGACY_FLOAT_DTYPE_ALIASES.get(resolved or "")
    if alias is not None:
        return alias

    if fallback is not None:
        return canonicalize_float_storage_dtype_name(fallback)

    supported = ", ".join(SUPPORTED_FLOAT_STORAGE_DTYPES)
    raise ValueError(f"Unsupported data type '{dtype_name}'. Supported values: {supported}")


def get_float_storage_dtype(dtype_name):
    canonicalize_float_storage_dtype_name(dtype_name)
    return _FLOAT_STORAGE_DTYPE


def get_float_storage_bits(dtype_name):
    normalized_name = canonicalize_float_storage_dtype_name(dtype_name)
    return _STORAGE_BITS[normalized_name]


def get_float_storage_bytes(dtype_name):
    return get_float_storage_bits(dtype_name) / 8.0


def quantize_to_float_storage_dtype(values, dtype_name):
    normalized_name = canonicalize_float_storage_dtype_name(dtype_name)
    array = np.asarray(values, dtype=np.float32)

    if normalized_name == "float16":
        return array.astype(_FLOAT_STORAGE_DTYPE, copy=False)

    quantized = np.array(array, dtype=np.float32, copy=True)
    finite_mask = np.isfinite(quantized)
    if np.any(finite_mask):
        mantissa_bits = _MANTISSA_BITS[normalized_name]
        finite_values = quantized[finite_mask]
        mantissa, exponent = np.frexp(finite_values)
        scale = float(2 ** mantissa_bits)
        mantissa = np.round(mantissa * scale) / scale
        quantized[finite_mask] = np.ldexp(mantissa, exponent)

    return quantized.astype(_FLOAT_STORAGE_DTYPE, copy=False)