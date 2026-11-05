"""Operator-safe diagnostics explicitly constructed by installer phases."""


class InstallerError(ValueError):
    """Contains reviewed diagnostics, never arbitrary remote stderr or secrets."""
