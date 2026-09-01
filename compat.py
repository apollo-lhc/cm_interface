"""Compatibility helpers for the Python 3.6 runtime used on target boards."""

from collections import namedtuple


_MISSING = object()


class _CompatField:
    def __init__(self, default=_MISSING, default_factory=_MISSING):
        if default is not _MISSING and default_factory is not _MISSING:
            raise ValueError("cannot specify both default and default_factory")
        self.default = default
        self.default_factory = default_factory


def _fallback_field(*, default=_MISSING, default_factory=_MISSING):
    """Provide the ``field`` options used by the local Python 3.6 scripts."""
    return _CompatField(default=default, default_factory=default_factory)


def _mutable_fallback_dataclass(cls):
    """Add basic mutable-dataclass behavior to an annotated class."""
    annotations = getattr(cls, "__annotations__", {})
    field_names = list(annotations)
    defaults = {}
    found_default = False

    for name in field_names:
        value = getattr(cls, name, _MISSING)
        if isinstance(value, _CompatField):
            defaults[name] = value
            found_default = True
            if hasattr(cls, name):
                delattr(cls, name)
        elif value is not _MISSING:
            defaults[name] = _CompatField(default=value)
            found_default = True
        elif found_default:
            raise TypeError("fields without defaults cannot follow defaults")

    def __init__(self, *args, **kwargs):
        if len(args) > len(field_names):
            raise TypeError("too many positional arguments")
        for name, value in zip(field_names, args):
            if name in kwargs:
                raise TypeError("multiple values for field {!r}".format(name))
            setattr(self, name, value)
        for name in field_names[len(args):]:
            if name in kwargs:
                value = kwargs.pop(name)
            elif name in defaults:
                configured = defaults[name]
                if configured.default_factory is not _MISSING:
                    value = configured.default_factory()
                else:
                    value = configured.default
            else:
                raise TypeError("missing required field {!r}".format(name))
            setattr(self, name, value)
        if kwargs:
            name = next(iter(kwargs))
            raise TypeError("unexpected field {!r}".format(name))

    def __repr__(self):
        values = ", ".join(
            "{}={!r}".format(name, getattr(self, name)) for name in field_names
        )
        return "{}({})".format(type(self).__name__, values)

    def __eq__(self, other):
        if type(self) is not type(other):
            return NotImplemented
        return all(getattr(self, name) == getattr(other, name)
                   for name in field_names)

    cls.__init__ = __init__
    cls.__repr__ = __repr__
    cls.__eq__ = __eq__
    cls.__hash__ = None
    return cls


def _fallback_dataclass(_cls=None, *, frozen=False):
    """Implement the small dataclass subset used by local code.

    Python 3.6 does not include :mod:`dataclasses`. Frozen package records use
    a namedtuple-backed class; mutable demo records additionally support
    annotated fields, trailing defaults, and ``default_factory``.
    """
    def wrap(cls):
        if not frozen:
            return _mutable_fallback_dataclass(cls)

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
    from dataclasses import dataclass, field
except ImportError:  # Python 3.6 target runtime
    dataclass = _fallback_dataclass
    field = _fallback_field


def spaced_hex(data):
    """Return space-separated hexadecimal bytes on Python 3.6 and newer."""
    return " ".join("{:02x}".format(value) for value in data)
