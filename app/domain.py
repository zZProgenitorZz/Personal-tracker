"""What all domains (reading, watching, ...) share."""


class DomainError(Exception):
    """A request that breaks a domain's rules. The message is for the user."""


def title_key(title: str) -> str:
    """The same title as Reading sees it (find_by_title): case and surrounding spaces don't count."""
    return title.strip().lower()
