from __future__ import annotations

from otomekairo.service.spontaneous.capability_context import ServiceSpontaneousCapabilityContextMixin
from otomekairo.service.spontaneous.capability_cycle import ServiceSpontaneousCapabilityCycleMixin
from otomekairo.service.spontaneous.capability_payload import ServiceSpontaneousCapabilityPayloadMixin


class ServiceSpontaneousCapabilityResultMixin(
    ServiceSpontaneousCapabilityPayloadMixin,
    ServiceSpontaneousCapabilityContextMixin,
    ServiceSpontaneousCapabilityCycleMixin,
):
    pass
