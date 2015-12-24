from __future__ import unicode_literals


class StackCreationError(Exception):
    """Error creating a new stack."""


class StackLookupError(Exception):
    """Error looking up a stack on CloudFormation."""


class StackUpdateError(Exception):
    """Error updating an existing stack."""
