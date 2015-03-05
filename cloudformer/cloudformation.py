from __future__ import unicode_literals

import time

import boto.cloudformation

from cloudformer.errors import StackCreationError


class CloudFormation(object):
    """Manages operations on CloudFormation.

    This is a wrapper around boto's CloudFormation API that simplifies
    some operations, such as waiting for the creation of a stack to finish.
    """

    DEFAULT_TIMEOUT_MINS = 30

    def __init__(self, region):
        self.cnx = boto.cloudformation.connect_to_region(region)

    def validate_template(self, template_body):
        """Validate the given template string."""
        return self.cnx.validate_template(template_body)

    def create_stack_and_wait(self, stack_name, template_body, params,
                              tags, timeout_mins=DEFAULT_TIMEOUT_MINS):
        """Create a stack and wait for it to complete.

        Once the stack has been successfully created, a
        boto.cloudformation.stack.Stack will be returned.

        If there's any problem in creating the stack, a StackCreationError
        will be raised.
        """
        stack_id = self.cnx.create_stack(
            stack_name,
            template_body=template_body,
            parameters=params,
            timeout_in_minutes=timeout_mins,
            tags=tags)

        stack = None

        while True:
            stacks = self.cnx.describe_stacks(stack_id)

            if not stacks:
                raise StackCreationError(
                    'No stacks found for the newly-created stack ID "%s"'
                    % stack_id)

            stack = stacks[0]

            if stack.stack_status != 'CREATE_IN_PROGRESS':
                break

            time.sleep(30)

        if stack.stack_status != 'CREATE_COMPLETE':
            raise StackCreationError(
                'Stack creation failed. Got status: "%s"'
                % stack.stack_status)

        return stack

    def delete_stack(self, stack_id):
        """Delete an existing stack."""
        self.cnx.delete_stack(stack_id)
