from __future__ import unicode_literals

import os
import sys

from cloudformer.commands import BaseCommand, run_command
from cloudformer.templates import TemplateCompiler


class CompileTemplate(BaseCommand):
    """Compiles a CloudFormer template into a CloudFormation template."""

    def add_options(self, parser):
        parser.add_argument('-o', '--out',
                            dest='dest_filename',
                            metavar='FILENAME',
                            help='The file to output the template to.')
        parser.add_argument('filename',
                            help='The template file to compile.')

    def main(self):
        compiler = TemplateCompiler()
        compiler.load_file(self.options.filename)
        dumped = compiler.to_json()

        if self.options.dest_filename:
            dirname = os.path.dirname(self.options.dest_filename)

            if not os.path.exists(dirname):
                os.makedirs(dirname, 0755)

            try:
                with open(self.options.dest_filename, 'w') as fp:
                    fp.write(dumped)
            except IOError as e:
                sys.stderr.write('Unable to write to "%s": %s\n'
                                 % (self.options.dest_filename, e))
                sys.exit(1)
        else:
            print dumped


def main():
    run_command(CompileTemplate)
