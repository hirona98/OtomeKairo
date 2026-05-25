from __future__ import annotations

from otomekairo.service.input.initiative_context import ServiceInputInitiativeContextMixin
from otomekairo.service.input.initiative_families import ServiceInputInitiativeFamiliesMixin
from otomekairo.service.input.initiative_scoring import ServiceInputInitiativeScoringMixin


class ServiceInputInitiativeMixin(
    ServiceInputInitiativeContextMixin,
    ServiceInputInitiativeScoringMixin,
    ServiceInputInitiativeFamiliesMixin,
):
    pass
