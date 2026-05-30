from __future__ import annotations

from otomekairo.service.input.world_state_foreground import ServiceInputWorldStateForegroundMixin
from otomekairo.service.input.world_state_normalize import ServiceInputWorldStateNormalizeMixin
from otomekairo.service.input.world_state_source_pack import ServiceInputWorldStateSourcePackMixin


class ServiceInputWorldStateMixin(
    ServiceInputWorldStateSourcePackMixin,
    ServiceInputWorldStateNormalizeMixin,
    ServiceInputWorldStateForegroundMixin,
):
    pass
