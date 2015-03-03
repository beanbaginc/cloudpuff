from __future__ import unicode_literals

import argparse
import textwrap

from cloudformer.utils.log import init_logging


class BaseCommand(object):
    """Base class for a cloudformer command.

    This takes care of the standard setup and argument parsing for a command.
    """

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


def run_command(cmd_class):
    """Run a command.

    This instantiates the given BaseCommand subclass and runs it.
    """
    cmd = cmd_class()
    cmd.run()
