"""Versioned project document: the on-disk (.ngsui) model and its serializer.

Pure data — no Qt, no libngspice — so it is fully unit-testable. MainWindow
builds a :class:`ProjectDocument` from its widgets, serializes it, and on load
applies the validated fields back to the widgets. All structural validation
lives here; the UI layer only translates a raised :class:`ProjectError` into a
dialog.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

#: Current on-disk schema version. Bump when the shape of the dict changes and
#: add a branch to :func:`migrate`.
CURRENT_VERSION = 2


class ProjectError(Exception):
    """Raised when a project file is malformed or cannot be parsed."""


@dataclass
class ProjectDocument:
    """In-memory representation of a saved project.

    Field names and types mirror what the GUI widgets produce/consume:
    ``analysis``/``script``/``cosim`` are dicts, ``measurements`` is a list,
    ``notes``/``netlist`` are plain strings.
    """

    netlist: str = ""
    analysis: dict = field(default_factory=dict)
    measurements: list = field(default_factory=list)
    notes: str = ""
    script: dict = field(default_factory=dict)
    cosim: dict = field(default_factory=dict)
    version: int = CURRENT_VERSION

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "netlist": self.netlist,
            "analysis": self.analysis,
            "measurements": self.measurements,
            "notes": self.notes,
            "script": self.script,
            "cosim": self.cosim,
        }

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    # -- deserialization -----------------------------------------------------

    @classmethod
    def from_dict(cls, raw: object) -> "ProjectDocument":
        """Build a document from a decoded JSON value.

        Unknown/missing fields fall back to defaults; wrongly-typed fields are
        ignored rather than fatal, matching the previous lenient load behavior.
        Only a non-object root is fatal.
        """
        if not isinstance(raw, dict):
            raise ProjectError("Invalid project file: root must be a JSON object.")
        data = migrate(raw)

        def _typed(key: str, types: type | tuple[type, ...], default):
            val = data.get(key, default)
            return val if isinstance(val, types) else default

        version = data.get("version", CURRENT_VERSION)
        if not isinstance(version, int):
            version = CURRENT_VERSION

        return cls(
            netlist=_typed("netlist", str, ""),
            analysis=_typed("analysis", dict, {}),
            measurements=_typed("measurements", list, []),
            notes=_typed("notes", str, ""),
            script=_typed("script", dict, {}),
            cosim=_typed("cosim", dict, {}),
            version=version,
        )

    @classmethod
    def loads(cls, text: str) -> "ProjectDocument":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProjectError(f"Invalid project JSON: {exc}") from exc
        return cls.from_dict(raw)


def migrate(raw: dict) -> dict:
    """Upgrade an older project dict in place to :data:`CURRENT_VERSION`.

    Older versions are migrated forward (no transforms needed yet — v2 is the
    only released schema). A version *newer* than this build understands is
    rejected outright: silently accepting it would apply unknown-shaped data
    through the lenient field loader and quietly drop or misread fields the
    newer writer added. Better to tell the user their file needs a newer build.
    """
    version = raw.get("version", 1)
    if isinstance(version, int) and version > CURRENT_VERSION:
        raise ProjectError(
            f"Project schema version {version} is newer than this build "
            f"supports (max {CURRENT_VERSION}). Update ngspice-ui to open it."
        )
    return raw
