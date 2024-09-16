"""CloudFormation operations."""

from __future__ import annotations

import os
import time
from typing import Iterable, Iterator, Optional, Sequence, TYPE_CHECKING

import boto3
from botocore.exceptions import ClientError

from cloudpuff.errors import (StackCreationError,
                              StackLookupError,
                              StackUpdateError,
                              StackUpdateNotRequired)

if TYPE_CHECKING:
    from mypy_boto3_cloudformation.client import CloudFormationClient
    from mypy_boto3_cloudformation.literals import StackStatusType
    from mypy_boto3_cloudformation.type_defs import (
        ParameterTypeDef,
        StackEventTypeDef,
        StackTypeDef,
        TagTypeDef,
        ValidateTemplateOutputTypeDef,
    )


class CloudFormation:
    """Manages operations on CloudFormation.

    This is a wrapper around boto's CloudFormation API that simplifies
    some operations, such as waiting for the creation of a stack to finish.
    """

    DEFAULT_TIMEOUT_MINS = 30

    ######################
    # Instance variables #
    ######################

    #: The client connection to CloudFormation.
    cnx: CloudFormationClient

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
        session = boto3.Session(
            profile_name=os.environ.get('CLOUDPUFF_AWS_PROFILE'))
        self.cnx = session.client('cloudformation', region_name=region)

    def lookup_stacks(
        self,
        *,
        statuses: Sequence[StackStatusType] = [],
        tags: dict[str, str] = {},
    ) -> Sequence[StackTypeDef]:
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
        stacks: Iterable[StackTypeDef] = self.cnx.describe_stacks()['Stacks']

        if statuses:
            stacks = (
                stack
                for stack in stacks
                if stack['StackStatus'] in statuses
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
    ) -> StackTypeDef:
        """Return the stack with the given name.

        Args:
            stack_name (str):
                The name of the stack to look up.

        Returns:
            mypy_boto3_cloudformation.type_defs.StackTypeDef:
            The resulting stack.

        Raises:
            cloudpuff.errors.StackLookupError:
                The stack could not be found.
        """
        try:
            stacks = self.cnx.describe_stacks(StackName=stack_name)['Stacks']
        except ClientError:
            stacks = None

        if not stacks:
            raise StackLookupError('The stack "%s" was not found' % stack_name)

        return stacks[0]

    def lookup_stack_events(
        self,
        stack_name: str,
    ) -> Sequence[StackEventTypeDef]:
        """Look up all events for a stack.

        Args:
            stack_name (str):
                The name of the stack to look up events for.

        Returns:
            list of mypy_boto3_cloudformation.type_defs.StackEventTypeDef:
            The list of events on the stack, in order of newest to oldest.
        """
        return (
            self.cnx.describe_stack_events(StackName=stack_name)
            ['StackEvents']
        )

    def validate_template(
        self,
        template_body: str,
    ) -> ValidateTemplateOutputTypeDef:
        """Validate the given template string.

        Args:
            template_body (str):
                The template body to validate.

        Returns:
            mypy_boto3_cloudformation.type_defs.ValidateTemplateOutputTypeDef:
            The validation result.
        """
        return self.cnx.validate_template(TemplateBody=template_body)

    def create_stack_and_wait(
        self,
        *,
        stack_name: str,
        template_body: str,
        params: dict[str, str],
        rollback_on_error: bool = True,
        tags: dict[str, str] = {},
        timeout_mins: int = DEFAULT_TIMEOUT_MINS,
    ) -> Iterator[StackEventTypeDef]:
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
            mypy_boto3_cloudformation.type_defs.StackEventTypeDef:
            Events for changes being performed.

        Raises:
            cloudpuff.errors.StackCreateError:
                An error creating the stack.
        """
        stack_id = (
            self.cnx.create_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Parameters=self._normalize_params(params),
                TimeoutInMinutes=timeout_mins,
                DisableRollback=not rollback_on_error,
                Tags=self._normalize_tags(tags),
                Capabilities=['CAPABILITY_IAM', 'CAPABILITY_NAMED_IAM'])
            ['StackId']
        )

        stack_status: Optional[StackStatusType] = None

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
    ) -> Iterator[StackEventTypeDef]:
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
        last_event_id = self.lookup_stack_events(stack_name)[0]['EventId']

        try:
            stack_id = (
                self.cnx.update_stack(
                    StackName=stack_name,
                    TemplateBody=template_body,
                    Parameters=self._normalize_params(params),
                    DisableRollback=not rollback_on_error,
                    Tags=self._normalize_tags(tags),
                    Capabilities=['CAPABILITY_IAM', 'CAPABILITY_NAMED_IAM'])
                ['StackId']
            )
        except ClientError as e:
            msg = str(e)

            if msg == 'No updates are to be performed.':
                raise StackUpdateNotRequired(msg)
            else:
                raise StackUpdateError(msg)

        stack_status: Optional[StackStatusType] = None

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
        stack_name: str,
    ) -> None:
        """Delete an existing stack.

        Args:
            stack_name (str):
                The name of the stack to delete.
        """
        self.cnx.delete_stack(StackName=stack_name)

    def _normalize_params(
        self,
        params: dict[str, str],
    ) -> Sequence[ParameterTypeDef]:
        """Normalize parameters for a stack.

        This converts a dictionary of parameter names/values to sequences
        of dictionaries for CloudFormation's API.

        Args:
            params (dict):
                The parameters to normalize.

        Returns:
            list of dict:
            The list of normalized parameters.
        """
        return [
            {
                'ParameterKey': key,
                'ParameterValue': value,
            }
            for key, value in params.items()
        ]

    def _normalize_tags(
        self,
        tags: dict[str, str],
    ) -> Sequence[TagTypeDef]:
        """Normalize tags for a stack.

        This converts a dictionary of tags names/values to sequences of
        dictionaries for CloudFormation's API.

        Args:
            tags (dict):
                The tags to normalize.

        Returns:
            list of dict:
            The list of tags parameters.
        """
        return [
            {
                'Key': key,
                'Value': str(value),
            }
            for key, value in tags.items()
        ]

    def _get_stack_has_tags(
        self,
        stack: StackTypeDef,
        tags: dict[str, str],
    ) -> bool:
        """Return whether a stack has all specified tags.

        Args:
            stack (mypy_boto3_cloudformation.type_defs.StackTypeDef):
                The stack to check.

            tags (dict):
                A dictionary of required tags.

        Returns:
            bool:
            ``True`` if the stack has all the required tags, or ``False``
            otherwise.
        """
        stack_tags = {
            tag['Key']: tag['Value']
            for tag in stack.get('Tags', [])
        }

        for tag_name, tag_value in tags.items():
            if stack_tags.get(tag_name) != tag_value:
                return False

        return True

    def _wait_for_stack(
        self,
        stack_name: str,
        last_event_id: Optional[str] = None,
    ) -> Iterator[tuple[StackEventTypeDef, StackStatusType]]:
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
            A 2-tuple in the form of:

            Tuple:
                0 (mypy_boto3_cloudformation.type_defs.StackEventTypeDef):
                    The next event returned.

                1 (str):
                    The status shown at the last fetch (immediately prior to
                    the current batch of events being processed).
        """
        while True:
            stack = self.lookup_stack(stack_name)
            events = self.lookup_stack_events(stack_name)
            stack_status = stack['StackStatus']

            new_events: list[StackEventTypeDef] = []

            for event in events:
                if event['EventId'] == last_event_id:
                    break

                if event.get('PhysicalResourceId'):
                    new_events.append(event)

            if new_events:
                for event in reversed(new_events):
                    yield event, stack_status

                last_event_id = events[0]['EventId']

            if not stack_status.endswith('IN_PROGRESS'):
                break

            time.sleep(2)
