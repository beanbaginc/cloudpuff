from __future__ import unicode_literals

import os
import sys

from cloudpuff.commands import BaseCommand, run_command
from cloudpuff.templates import TemplateCompiler
from cloudpuff.templates.errors import TemplateError, TemplateSyntaxError


class CompileTemplate(BaseCommand):
    """Compiles a CloudPuff template into a CloudFormation template."""

    def add_options(self, parser):
        parser.add_argument('-o', '--out',
                            dest='dest_filename',
                            metavar='FILENAME',
                            help='The file to output the template to.')
        parser.add_argument('filename',
                            help='The template file to compile.')

    def main(self):
        compiler = TemplateCompiler()

        try:
            compiler.load_file(self.options.filename)
        except TemplateSyntaxError as e:
            sys.stderr.write('Template syntax error: %s\n' % e)
            sys.exit(1)
        except TemplateError as e:
            sys.stderr.write('Template error: %s\n' % e)
            sys.exit(1)

        dumped = compiler.to_json()

        if self.options.dest_filename:
            dirname = os.path.dirname(self.options.dest_filename)

            if not os.path.exists(dirname):
                os.makedirs(dirname, 0o755)

            try:
                with open(self.options.dest_filename, 'w') as fp:
                    fp.write(dumped)
            except IOError as e:
                sys.stderr.write('Unable to write to "%s": %s\n'
                                 % (self.options.dest_filename, e))
                sys.exit(1)
        else:
            print(dumped)


def main():
    run_command(CompileTemplate)
