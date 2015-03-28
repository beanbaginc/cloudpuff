from __future__ import print_function, unicode_literals

from cloudformer.commands import BaseCommand, run_command
from cloudformer.templates import TemplateReader


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
        reader.load_file(filename)

        deps = [filename]
        deps += reader.template_state.imported_files

        print('%s: %s' % (self.options.dest_filename, ' '.join(deps)))


def main():
    run_command(MakeDepends)
