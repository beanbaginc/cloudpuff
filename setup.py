#!/usr/bin/env python

from setuptools import setup, find_packages

from cloudformer import get_package_version


PACKAGE_NAME = 'cloudformer'


commands = {
    'cloudformer-compile-template': 'compile_template',
}

setup(
    name=PACKAGE_NAME,
    version=get_package_version(),
    description='Tools for deployment at Beanbag, Inc.',
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            '%s = cloudformer.commands.%s:main' % (name, mod)
            for name, mod in commands.iteritems()
        ],
    },
    install_requires=[
        'boto',
        'PyYAML>=3.11',
        'six',
    ],
    maintainer='Christian Hammond',
    maintainer_email='christian@beanbaginc.com'
)
