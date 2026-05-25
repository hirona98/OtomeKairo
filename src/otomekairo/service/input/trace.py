from __future__ import annotations

from otomekairo.service.input.trace_build import ServiceInputTraceBuildMixin
from otomekairo.service.input.trace_compact import ServiceInputTraceCompactMixin
from otomekairo.service.input.trace_persist import ServiceInputTracePersistMixin


class ServiceInputTraceMixin(
    ServiceInputTraceBuildMixin,
    ServiceInputTraceCompactMixin,
    ServiceInputTracePersistMixin,
):
    pass
