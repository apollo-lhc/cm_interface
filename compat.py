"""Compatibility helpers for the Python 3.6 runtime used on target boards."""

from collections import namedtuple


def _fallback_dataclass(_cls=None, *, frozen=False):
    """Implement the small frozen-dataclass subset used by this package.

    Python 3.6 does not include :mod:`dataclasses`.  All records in this
    package are frozen and use only annotated fields, trailing defaults,
    properties, and ordinary methods, which a namedtuple-backed class covers.
    """
    if not frozen:
        raise TypeError("the compatibility dataclass only supports frozen=True")

    def wrap(cls):
        annotations = getattr(cls, "__annotations__", {})
        field_names = list(annotations)
        base = namedtuple(cls.__name__, field_names)

        defaults = []
        found_default = False
        for name in field_names:
            if hasattr(cls, name):
                found_default = True
                defaults.append(getattr(cls, name))
            elif found_default:
                raise TypeError("fields without defaults cannot follow defaults")
        if defaults:
            base.__new__.__defaults__ = tuple(defaults)

        excluded = {
            "__annotations__", "__dict__", "__doc__", "__module__",
            "__weakref__",
        }
        excluded.update(field_names)
        namespace = {
            name: value
            for name, value in cls.__dict__.items()
            if name not in excluded
        }
        namespace.update({
            "__annotations__": annotations,
            "__doc__": cls.__doc__,
            "__module__": cls.__module__,
            "__slots__": (),
        })
        return type(cls.__name__, (base,), namespace)

    if _cls is None:
        return wrap
    return wrap(_cls)


try:
    from dataclasses import dataclass
except ImportError:  # Python 3.6 target runtime
    dataclass = _fallback_dataclass


def spaced_hex(data):
    """Return space-separated hexadecimal bytes on Python 3.6 and newer."""
    return " ".join("{:02x}".format(value) for value in data)
