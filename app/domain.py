"""What all domains (reading, watching, ...) share."""


class DomainError(Exception):
    """A request that breaks a domain's rules. The message is for the user."""
