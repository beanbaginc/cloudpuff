from __future__ import print_function, unicode_literals

import sys

from cloudpuff.commands import BaseCommand, run_command
from cloudpuff.templates import TemplateReader
from cloudpuff.templates.errors import TemplateError, TemplateSyntaxError


class MakeDepends(BaseCommand):
    """Builds a Makefile-compatible dependencies file for a template."""

    def add_options(self, parser):
        parser.add_argument('filename',
                            help='The template file to process.')
        parser.add_argument('dest_filename',
                            help='The file that would be generated, for '
                                 'the dependency information.')

    def main(self):
        filename = self.options.filename

        reader = TemplateReader()

        try:
            reader.load_file(filename)
        except TemplateSyntaxError as e:
            sys.stderr.write('Template syntax error: %s\n' % e)
            sys.exit(1)
        except TemplateError as e:
            sys.stderr.write('Template error: %s\n' % e)
            sys.exit(1)

        deps = [filename]
        deps += reader.template_state.imported_files
        deps += reader.template_state.embedded_files

        print('%s: %s' % (self.options.dest_filename, ' '.join(deps)))


def main():
    run_command(MakeDepends)
