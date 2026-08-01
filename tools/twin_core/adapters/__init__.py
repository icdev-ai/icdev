# CUI // SP-CTI — Twin Core adapter package
"""Per-canvas twin adapters.

Each module here registers a thin :class:`~tools.twin_core.registry.TwinAdapter`
via the :func:`~tools.twin_core.registry.register_twin` decorator. Dropping a new
``<canvas>.py`` file in this package is all that's needed to add a canvas to the
cross-canvas twin layer — :meth:`TwinRegistry.discover` imports them by
filesystem scan, never a hardcoded list.

Reference implementations (twx-core-01): ``ndc`` (network) and ``pdc`` (pipeline).
Remaining canvas adapters (BDC/SDC/DDC/ODC/IDC/Mission) land in twx-core-02.
"""
