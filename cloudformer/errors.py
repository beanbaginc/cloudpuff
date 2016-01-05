from __future__ import unicode_literals


class InvalidTagError(Exception):
    """A tag name or value was invalid."""


class StackCreationError(Exception):
    """Error creating a new stack."""


class StackLookupError(Exception):
    """Error looking up a stack on CloudFormation."""


class StackUpdateError(Exception):
    """Error updating an existing stack."""


class StackUpdateNotRequired(StackUpdateError):
    """An attempted stack update was not required."""
