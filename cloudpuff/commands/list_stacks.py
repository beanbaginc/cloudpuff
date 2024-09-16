"""Command for listing stacks."""

from __future__ import annotations

import json
from typing import Optional, Sequence, TYPE_CHECKING

from colorama import Fore, Style

from cloudpuff.cloudformation import CloudFormation
from cloudpuff.commands import BaseCommand, run_command

if TYPE_CHECKING:
    import argparse

    from mypy_boto3_cloudformation.type_defs import StackTypeDef


class ListStacks(BaseCommand):
    """Lists all stacks and their outputs in CloudFormation."""

    def add_options(
        self,
        parser: argparse.ArgumentParser,
    ) -> None:
        """Add options for the command.

        Args:
            parser (argparse.ArgumentParser):
                The argument parser to add options to.
        """
        parser.add_argument(
            '--region',
            default='us-east-1',
            help='The region to connect to.')
        parser.add_argument(
            '--json',
            action='store_true',
            default=False,
            help='Output as JSON.')
        parser.add_argument(
            'stack_names',
            metavar='NAME',
            nargs='*',
            help='Limit results to the given stack name(s).')

    def main(self) -> None:
        """Main entry point for the command."""
        cf = CloudFormation(region=self.options.region)

        stacks = cf.lookup_stacks()

        if self.options.stack_names:
            stack_names = set(self.options.stack_names)

            stacks = [
                stack
                for stack in stacks
                if stack['StackName'] in stack_names
            ]

        if self.options.json:
            self._print_stacks_json(stacks)
        else:
            self._print_stacks(stacks)

    def _print_stacks_json(
        self,
        stacks: Sequence[StackTypeDef],
    ) -> None:
        """Print the list of stacks as pretty-printed JSON.

        Args:
            stacks (list of mypy_boto3_cloudformation.type_defs.StackTypeDef):
                List of stacks to print.
        """
        print(json.dumps(
            [
                {
                    'name': stack['StackName'],
                    'status': stack['StackStatus'],
                    'description': stack.get('Description', ''),
                    'arn': stack.get('StackId', ''),
                    'created': stack['CreationTime'].isoformat(),
                    'tags': {
                        tag['Key']: tag['Value']
                        for tag in stack.get('Tags', [])
                    },
                    'outputs': {
                        output['OutputKey']: output.get('OutputValue', '')
                        for output in stack.get('Outputs', [])
                        if 'OutputKey' in output
                    },
                }
                for stack in stacks
            ],
            indent=2))

    def _print_stacks(
        self,
        stacks: Sequence[StackTypeDef],
    ) -> None:
        """Print the list of stacks as formatted console output.

        Args:
            stacks (list of mypy_boto3_cloudformation.type_defs.StackTypeDef):
                List of stacks to print.
        """
        first: bool = True

        for stack in stacks:
            if first:
                first = False
            else:
                print()
                print()

            stack_status = stack['StackStatus']

            if stack_status.endswith('FAILED'):
                status_color = Fore.RED
            elif stack_status.endswith('COMPLETE'):
                status_color = Fore.GREEN
            elif stack_status.endswith('PROGRESS'):
                status_color = Fore.YELLOW
            else:
                status_color = None

            self._print_field(stack['StackName'],
                              key_color=Fore.CYAN)
            self._print_field('Status',
                              stack_status,
                              indent_level=1,
                              value_color=status_color)
            self._print_field('Description',
                              stack.get('Description', ''),
                              indent_level=1)
            self._print_field('ARN',
                              stack.get('StackId', ''),
                              indent_level=1)
            self._print_field('Created',
                              stack.get('CreationTime', None),
                              indent_level=1)

            outputs = stack.get('Outputs')

            if outputs:
                self._print_field('Outputs', indent_level=1)

                for output in outputs:
                    if 'OutputKey' in output:
                        self._print_field(output['OutputKey'],
                                          output.get('OutputValue', ''),
                                          indent_level=2)

            tags = stack.get('Tags', [])

            if tags:
                self._print_field('Tags', indent_level=1)

                for tag in tags:
                    self._print_field(tag['Key'],
                                      tag['Value'],
                                      indent_level=2)

    def _print_field(
        self,
        key: str,
        value: object = '',
        *,
        indent_level: int = 0,
        key_color: Optional[str] = None,
        value_color: Optional[str] = None,
    ) -> None:
        """Print a key/value field to the console.

        The keys will be printed as bold. This has several additional options
        for controlling presentation.

        Args:
            key (str):
                The key to print.

            value (str, optional):
                The value to print.

            indent_level (int, optional):
                The indentation level. Each level adds 4 spaces before the key.

            key_color (str, optional):
                The color code to use for the key.

            value_color (str, optional):
                The color code to use for the value.
        """
        if key_color:
            key = '%s%s%s' % (key_color, key, Style.RESET_ALL)

        if value and value_color:
            value = '%s%s%s' % (value_color, value, Style.RESET_ALL)

        s = '%s%s%s:%s' % (indent_level * '    ', Style.BRIGHT, key,
                           Style.RESET_ALL)

        if value:
            s += ' %s' % value

        print(s)


def main():
    run_command(ListStacks)
