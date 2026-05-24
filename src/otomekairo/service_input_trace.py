from __future__ import annotations

from otomekairo.service_input_trace_build import ServiceInputTraceBuildMixin
from otomekairo.service_input_trace_compact import ServiceInputTraceCompactMixin
from otomekairo.service_input_trace_persist import ServiceInputTracePersistMixin


class ServiceInputTraceMixin(
    ServiceInputTraceBuildMixin,
    ServiceInputTraceCompactMixin,
    ServiceInputTracePersistMixin,
):
    pass
