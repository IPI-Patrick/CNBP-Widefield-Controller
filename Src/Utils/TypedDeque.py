from __future__ import annotations

import numpy as np


class TypedDeque:
    def __init__(self, iterable=None, *, maxlen, dtype, shape=()):
        if maxlen is None:
            raise ValueError("TypedDeque requires a maxlen")

        self.maxlen = max(1, int(maxlen))
        self.dtype = np.dtype(dtype)
        normalized_shape = tuple(int(dimension) for dimension in np.atleast_1d(shape)) if shape not in (None, ()) else ()
        self.shape = normalized_shape
        storage_shape = (self.maxlen,) + self.shape
        self._buffer = np.zeros(storage_shape, dtype=self.dtype)
        self._start = 0
        self._length = 0

        if iterable is not None:
            self.extend(iterable)

    def __len__(self):
        return self._length

    def __iter__(self):
        for index in range(self._length):
            yield self[index]

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(self._length)
            return [self[position] for position in range(start, stop, step)]

        normalized_index = int(index)
        if normalized_index < 0:
            normalized_index += self._length
        if normalized_index < 0 or normalized_index >= self._length:
            raise IndexError("TypedDeque index out of range")

        buffer_index = (self._start + normalized_index) % self.maxlen
        value = self._buffer[buffer_index]
        if self.shape == ():
            return value.item()
        return np.array(value, copy=True)

    def __array__(self, dtype=None):
        array = self.to_array(copy=True)
        if dtype is not None:
            return array.astype(dtype, copy=False)
        return array

    def __repr__(self):
        return f"TypedDeque(maxlen={self.maxlen}, dtype={self.dtype.name}, shape={self.shape}, items={list(self)!r})"

    def _coerce_value(self, value):
        array = np.asarray(value, dtype=self.dtype)
        if self.shape == ():
            if array.shape not in ((), (1,)):
                raise ValueError(f"Expected scalar value for TypedDeque, got shape {array.shape}")
            return array.reshape(())

        if array.shape != self.shape:
            raise ValueError(f"Expected value with shape {self.shape}, got {array.shape}")
        return array

    def append(self, value):
        coerced = self._coerce_value(value)
        if self._length < self.maxlen:
            insert_index = (self._start + self._length) % self.maxlen
            self._length += 1
        else:
            insert_index = self._start
            self._start = (self._start + 1) % self.maxlen

        self._buffer[insert_index] = coerced

    def extend(self, iterable):
        for value in iterable:
            self.append(value)

    def clear(self):
        self._start = 0
        self._length = 0
        self._buffer.fill(0)

    def to_array(self, copy=True):
        if self._length == 0:
            return np.zeros((0,) + self.shape, dtype=self.dtype)

        if self._start + self._length <= self.maxlen:
            view = self._buffer[self._start:self._start + self._length]
        else:
            first = self._buffer[self._start:]
            second = self._buffer[:(self._start + self._length) % self.maxlen]
            view = np.concatenate((first, second), axis=0)

        if copy:
            return np.array(view, copy=True)
        return view

    def copy(self):
        return TypedDeque(self, maxlen=self.maxlen, dtype=self.dtype, shape=self.shape)
