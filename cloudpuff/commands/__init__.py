from __future__ import print_function, unicode_literals

import argparse
import sys
import textwrap

from colorama import Fore, Style, init as init_colorama

from cloudpuff.utils.log import init_logging


class BaseCommand(object):
    """Base class for a cloudpuff command.

    This takes care of the standard setup and argument parsing for a command.
    """

    ICON_ERROR = '\u2717'
    ICON_SUCCESS = '\u2713'
    ICON_PROGRESS = '\u25ba'

    STYLED_ICON_ERROR = Fore.RED + ICON_ERROR + Style.RESET_ALL
    STYLED_ICON_SUCCESS = Fore.GREEN + ICON_SUCCESS + Style.RESET_ALL
    STYLED_ICON_PROGRESS = Fore.YELLOW + ICON_PROGRESS + Style.RESET_ALL

    def main(self):
        raise NotImplementedError

    def run(self):
        """Run the command.

        This will parse any options, initialize logging, and call
        the subclass's main().
        """
        parser = self.setup_options()

        self.options = parser.parse_args()
        init_logging(debug=self.options.debug)

        init_colorama(strip=not sys.stdout.isatty())

        self.main()

    def setup_options(self):
        """Set up options for the command.

        This instantiates an ArgumentParser with the standard --debug and
        --dry-run options. It then calls the subclass's add_options(),
        which can provide additional options for the parser.
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

    def add_options(self, parser):
        """Add custom options to the parser.

        Subclasses can override this to add additional options to the
        argument parser.
        """
        pass

    def print_error(self, s):
        """Print an error to the console.

        Args:
            s (unicode):
                The error string to print.
        """
        sys.stderr.write(textwrap.fill(
            s,
            initial_indent='%s ' % self.STYLED_ICON_ERROR,
            subsequent_indent='  '))

    def print_success(self, s):
        """Print a success message to the console.

        Args:
            s (unicode):
                The string to print.
        """
        print(textwrap.fill(
            s,
            initial_indent='%s ' % self.STYLED_ICON_SUCCESS,
            subsequent_indent='  '))


def run_command(cmd_class):
    """Run a command.

    This instantiates the given BaseCommand subclass and runs it.
    """
    cmd = cmd_class()
    cmd.run()
