"""CloudFormation operations."""

from __future__ import annotations

import os
import time
from typing import Iterable, Iterator, Optional, Sequence, TYPE_CHECKING

import boto.cloudformation
from boto.exception import BotoServerError

from cloudpuff.errors import (StackCreationError,
                              StackLookupError,
                              StackUpdateError,
                              StackUpdateNotRequired)


class CloudFormation:
    """Manages operations on CloudFormation.

    This is a wrapper around boto's CloudFormation API that simplifies
    some operations, such as waiting for the creation of a stack to finish.
    """

    DEFAULT_TIMEOUT_MINS = 30

    def __init__(
        self,
        *,
        region: str,
    ) -> None:
        """Initialize the CloudFormation interface.

        Args:
            region (str):
                The AWS region to connect to.
        """
        self.cnx = boto.cloudformation.connect_to_region(region)

    def lookup_stacks(
        self,
        *,
        statuses: Sequence[str] = [],
        tags: dict[str, str] = {},
    ) -> Sequence[boto.cloudformation.stack.Stack]:
        """Return stacks known to CloudFormation.

        This can be filtered down by providing one or more valid status strings
        and/or tags that must match those in the stack.

        Args:
            statuses (list, optional):
                A list of valid statuses for the stack.

            tags (dict, optional):
                Tags and their values that must be present on the stack.

        Returns:
            list of boto.cloudformation.stack.Stack:
            The list of stacks.
        """
        stacks = self.cnx.describe_stacks()

        if statuses:
            stacks = (
                stack
                for stack in stacks
                if stack.stack_status in statuses
            )

        if tags:
            stacks = (
                stack
                for stack in stacks
                if self._get_stack_has_tags(stack, tags)
            )

        return list(stacks)

    def lookup_stack(
        self,
        stack_name: str,
    ) -> boto.cloudformation.stack.Stack:
        """Return the stack with the given name.

        Args:
            stack_name (str):
                The name of the stack to look up.

        Returns:
            boto.cloudformation.stack.Stack:
            The resulting stack.

        Raises:
            cloudpuff.errors.StackLookupError:
                The stack could not be found.
        """
        stacks = self.cnx.describe_stacks(stack_name)

        if not stacks:
            raise StackLookupError('The stack "%s" was not found' % stack_name)

        return stacks[0]

    def lookup_stack_events(
        self,
        stack_name: str,
    ) -> Sequence[boto.cloudformation.stack.StackEvent]:
        """Look up all events for a stack.

        Args:
            stack_name (str):
                The name of the stack to look up events for.

        Returns:
            list of boto.cloudformation.stack.StackEvent:
            The list of events on the stack, in order of newest to oldest.
        """
        return self.cnx.describe_stack_events(stack_name)

    def validate_template(
        self,
        template_body: str,
    ) -> object:
        """Validate the given template string.

        Args:
            template_body (str):
                The template body to validate.

        Returns:
            object:
            The validation result.
        """
        return self.cnx.validate_template(template_body)

    def create_stack_and_wait(
        self,
        stack_name: str,
        template_body: str,
        params: dict[str, str],
        rollback_on_error: bool = True,
        tags: dict[str, str] = {},
        timeout_mins: int = DEFAULT_TIMEOUT_MINS,
    ) -> Iterator[boto.cloudformation.stack.StackEvent]:
        """Create a stack and wait for it to complete.

        As changes are made to the stack, events will be yielded to the caller,
        until the update either finishes or fails.

        Args:
            stack_name (str):
                The name of the new stack.

            template_body (str):
                The template to use for the stack.

            params (dict):
                The parameters to pass to the stack.

            rollback_on_error (bool, optional):
                Whether to roll back the stack changes if there's an error.
                If ``False``, the stack will be deleted on error.

            tags (dict, optional):
                Tags to apply to the stack.

            timeout_mins (int, optional):
                The amount of time to wait without any activity before
                giving up.

        Yields:
            boto.cloudformation.stack.StackEvent:
            Events for changes being performed.

        Raises:
            cloudpuff.errors.StackCreateError:
                An error creating the stack.
        """
        stack_id = self.cnx.create_stack(
            stack_name,
            template_body=template_body,
            parameters=list(params.items()),
            timeout_in_minutes=timeout_mins,
            disable_rollback=not rollback_on_error,
            tags=tags,
            capabilities=['CAPABILITY_IAM', 'CAPABILITY_NAMED_IAM'])

        stack = None
        stack_status = None

        try:
            for event, stack_status in self._wait_for_stack(stack_id):
                yield event
        except StackLookupError:
            raise StackCreationError(
                'No stacks found for the newly-created stack ID "%s"'
                % stack_id)

        if stack_status != 'CREATE_COMPLETE':
            raise StackCreationError(
                'Stack creation failed. Got status: "%s"'
                % stack_status)

    def update_stack_and_wait(
        self,
        *,
        stack_name: str,
        template_body: str,
        params: dict[str, str],
        rollback_on_error: bool = True,
        tags: dict[str, str] = {},
        timeout_mins: int = DEFAULT_TIMEOUT_MINS,
    ) -> Iterator[boto.cloudformation.stack.StackEvent]:
        """Update a stack and wait for it to complete.

        As changes are made to the stack, events will be yielded to the caller,
        until the update either finishes or fails.

        Args:
            stack_name (str):
                The name of the stack to update.

            template_body (str):
                The template to use for the stack.

            params (dict):
                The parameters to pass to the stack.

            rollback_on_error (bool, optional):
                Whether to roll back the stack changes if there's an error.

            tags (dict, optional):
                Tags to apply to the stack.

            timeout_mins (int, optional):
                The amount of time to wait without any activity before
                giving up.

        Yields:
            boto.cloudformation.stack.StackEvent:
            Events for changes being performed.

        Raises:
            cloudpuff.errors.StackUpdateError:
                An error updating the stack.
        """
        last_event_id = self.lookup_stack_events(stack_name)[0].event_id

        try:
            stack_id = self.cnx.update_stack(
                stack_name,
                template_body=template_body,
                parameters=list(params.items()),
                timeout_in_minutes=timeout_mins,
                disable_rollback=not rollback_on_error,
                tags=tags,
                capabilities=['CAPABILITY_IAM'])
        except BotoServerError as e:
            if e.message == 'No updates are to be performed.':
                raise StackUpdateNotRequired(e.message)
            else:
                raise StackUpdateError(e.message)

        stack = None
        stack_status = None

        try:
            for event, stack_status in self._wait_for_stack(stack_id,
                                                            last_event_id):
                yield event
        except StackUpdateError:
            raise StackUpdateError(
                'No stacks found for the newly-updated stack ID "%s"'
                % stack_id)

        if stack_status != 'UPDATE_COMPLETE':
            raise StackUpdateError(
                'Stack update failed. Got status: "%s"'
                % stack_status)

    def delete_stack(
        self,
        stack_id: str,
    ) -> None:
        """Delete an existing stack.

        Args:
            stack_id (str):
                The ID of the stack to delete.
        """
        self.cnx.delete_stack(stack_id)

    def _get_stack_has_tags(
        self,
        stack: boto.cloudformation.stack.Stack,
        tags: dict[str, str],
    ) -> bool:
        """Return whether a stack has all specified tags.

        Args:
            stack (boto.cloudformation.stack.Stack):
                The stack to check.

            tags (dict):
                A dictionary of required tags.

        Returns:
            bool:
            ``True`` if the stack has all the required tags, or ``False``
            otherwise.
        """
        for tag_name, tag_value in tags.items():
            if not stack.tags.get(tag_name) == tag_value:
                return False

        return True

    def _wait_for_stack(
        self,
        stack_name: str,
        last_event_id: Optional[str] = None,
    ) -> Iterator[tuple[boto.cloudformation.stack.StackEvent, str]]:
        """Wait for a create/update stack operation to complete.

        As changes are made to the stack, events will be yielded to the caller,
        until the update either finishes or fails.

        Args:
            stack_name (str):
                The name of the stack.

            last_event_id (str, optional):
                The last known event ID. If specified, only events made after
                this ID will be yielded.

        Yields:
            tuple:
            A tuple of (:py:class:`boto.cloudformation.stack.StackEvent`,
            unicode).

            The first item in the tuple is the next event returned. The
            second is the stack status shown at last fetch (immediately
            prior to the current batch of events being processed).
        """
        while True:
            stack = self.lookup_stack(stack_name)
            events = self.lookup_stack_events(stack_name)
            stack_status = stack.stack_status

            found_current_event = False
            new_events = []

            for event in events:
                if event.event_id == last_event_id:
                    break

                new_events.append(event)

            for event in reversed(new_events):
                yield event, stack_status

            last_event_id = events[0].event_id

            if not stack_status.endswith('IN_PROGRESS'):
                break

            time.sleep(2)
