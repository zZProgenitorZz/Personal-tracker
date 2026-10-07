"""Wat alle domeinen (reading, watching, ...) delen."""


class DomainError(Exception):
    """Een verzoek dat de regels van een domein overtreedt. De melding is voor de gebruiker."""
