#!/usr/bin/env python
from __future__ import unicode_literals

import os
import sys

import nose


def run_tests():
    nose_argv = [
        'runtests.py',
        '-v',
        '--with-coverage',
        '--cover-package=cloudformer',
    ]

    if len(sys.argv) > 2:
        nose_argv += sys.argv[2:]

    nose.run(argv=nose_argv)


if __name__ == '__main__':
    os.chdir(os.path.join(os.path.dirname(__file__), '..'))
    sys.path.insert(0, os.getcwd())
    run_tests()
