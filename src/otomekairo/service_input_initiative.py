from __future__ import annotations

from otomekairo.service_input_initiative_context import ServiceInputInitiativeContextMixin
from otomekairo.service_input_initiative_families import ServiceInputInitiativeFamiliesMixin
from otomekairo.service_input_initiative_scoring import ServiceInputInitiativeScoringMixin


class ServiceInputInitiativeMixin(
    ServiceInputInitiativeContextMixin,
    ServiceInputInitiativeScoringMixin,
    ServiceInputInitiativeFamiliesMixin,
):
    pass
