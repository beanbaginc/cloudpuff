"""Base command support."""

from __future__ import annotations

import argparse
import sys
import textwrap
from typing import Iterable, TYPE_CHECKING

from colorama import Fore, Style, init as init_colorama

from cloudpuff.utils.log import init_logging


class BaseCommand:
    """Base class for a cloudpuff command.

    This takes care of the standard setup and argument parsing for a command.
    """

    ICON_ERROR = '\u2717'
    ICON_SUCCESS = '\u2713'
    ICON_PROGRESS = '\u25ba'

    STYLED_ICON_ERROR = Fore.RED + ICON_ERROR + Style.RESET_ALL
    STYLED_ICON_SUCCESS = Fore.GREEN + ICON_SUCCESS + Style.RESET_ALL
    STYLED_ICON_PROGRESS = Fore.YELLOW + ICON_PROGRESS + Style.RESET_ALL

    EVENT_ACTION_LABELS = {
        'CREATE_COMPLETE': 'Created',
        'CREATE_FAILED': 'Failed to create',
        'CREATE_IN_PROGRESS': 'Creating',
        'DELETE_COMPLETE': 'Deleted',
        'DELETE_FAILED': 'Failed to delete',
        'DELETE_IN_PROGRESS': 'Deleting',
        'ROLLBACK_COMPLETE': 'Rolled back',
        'ROLLBACK_FAILED': 'Failed to roll back',
        'ROLLBACK_IN_PROGRESS': 'Rolling back',
        'UPDATE_COMPLETE': 'Updated',
        'UPDATE_COMPLETE_CLEANUP_IN_PROGRESS': 'Updated and cleaning up',
        'UPDATE_IN_PROGRESS': 'Updating',
        'UPDATE_ROLLBACK_COMPLETE': 'Rolled back update for',
        'UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS':
            'Rolled back and cleaning up',
        'UPDATE_ROLLBACK_FAILED': 'Failed to roll back update for',
        'UPDATE_ROLLBACK_IN_PROGRESS': 'Rolling back update for',
    }

    def main(self) -> None:
        raise NotImplementedError

    def run(self) -> None:
        """Run the command.

        This will parse any options, initialize logging, and call
        the subclass's main().
        """
        parser = self.setup_options()

        self.options = parser.parse_args()
        init_logging(debug=self.options.debug)

        init_colorama(strip=not sys.stdout.isatty())

        self.main()

    def setup_options(self) -> argparse.ArgumentParser:
        """Set up options for the command.

        This instantiates an ArgumentParser with the standard --debug and
        --dry-run options. It then calls the subclass's add_options(),
        which can provide additional options for the parser.

        Returns:
            argparse.ArgumentParser:
            The populated argument parser.
        """
        parser = argparse.ArgumentParser(
            description=textwrap.dedent('    %s' % self.__doc__),
            formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument('-d', '--debug',
                            action='store_true',
                            default=False,
                            help='Displays debug output.')
        parser.add_argument('--dry-run',
                            action='store_true',
                            default=False,
                            help='Simulates all operations.')

        self.add_options(parser)

        return parser

    def add_options(
        self,
        parser: argparse.ArgumentParser,
    ) -> None:
        """Add custom options to the parser.

        Subclasses can override this to add additional options to the
        argument parser.

        Args:
            parser (argparse.ArgumentParser):
                The argument parser to add options to.
        """
        pass

    def print_error(
        self,
        s: str,
    ) -> None:
        """Print an error to the console.

        Args:
            s (str):
                The error string to print.
        """
        sys.stderr.write(textwrap.fill(
            s,
            initial_indent='%s ' % self.STYLED_ICON_ERROR,
            subsequent_indent='  '))

    def print_success(
        self,
        s: str,
    ) -> None:
        """Print a success message to the console.

        Args:
            s (str):
                The string to print.
        """
        print(textwrap.fill(
            s,
            initial_indent='%s ' % self.STYLED_ICON_SUCCESS,
            subsequent_indent='  '))

    def print_stack_events(self, events):
        """Print stack events to the console as they come in.

        This will take a generator of stack events and print them out as
        they come in. Each event will continue an icon representing the event
        ("X" for error, checkmark for success, ">" for progress), along
        with information on the event.

        Args:
            events (generator):
                A generator of events.
        """
        for event in events:
            event_status = event.resource_status

            if event_status.endswith('FAILED'):
                icon = self.ICON_ERROR
                status_color = Fore.RED
            elif event_status.endswith('COMPLETE'):
                icon = self.ICON_SUCCESS
                status_color = Fore.GREEN
            elif event_status.endswith('IN_PROGRESS'):
                icon = self.ICON_PROGRESS
                status_color = Fore.YELLOW
            else:
                # This shouldn't happen, as it's not a valid state.
                icon = '?'
                status_color = ''

            # Override the color for rollbacks.
            if 'ROLLBACK_IN_PROGRESS' in event_status:
                status_color = Fore.RED

            action = self.EVENT_ACTION_LABELS.get(event_status, event_status)

            print('%s%s%s %s %s (%s)'
                   % (status_color, icon, Style.RESET_ALL, action,
                      event.logical_resource_id, event.resource_type))

            if event.resource_status_reason:
                print('%s%s%s'
                      % (status_color,
                         textwrap.fill(event.resource_status_reason,
                                       initial_indent='  ',
                                       subsequent_indent='  '),
                         Style.RESET_ALL))


def run_command(cmd_class):
    """Run a command.

    This instantiates the given BaseCommand subclass and runs it.
    """
    cmd = cmd_class()
    cmd.run()
