"""Device-side operations: inventory, safety, wipe planning and execution.

Headless and Qt-free: the root worker imports only this package (plus the
stdlib), never the GUI. The chaff core's guarantees (no block devices, no
escalation; major-plan.md §9.5/§9.6) are preserved by keeping that boundary:
device operations live here, under these rules, and nowhere else.
"""
