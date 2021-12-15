#!/usr/bin/env python

from setuptools import setup, find_packages

from cloudpuff import get_package_version


PACKAGE_NAME = 'cloudpuff'


commands = {
    'cloudpuff-create-ami': 'create_ami',
    'cloudpuff-compile-template': 'compile_template',
    'cloudpuff-launch-stack': 'launch_stack',
    'cloudpuff-list-stacks': 'list_stacks',
    'cloudpuff-make-depends': 'make_depends',
}

setup(
    name=PACKAGE_NAME,
    version=get_package_version(),
    description='Powerful tools for working with AWS CloudFormation.',
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            '%s = cloudpuff.commands.%s:main' % (name, mod)
            for name, mod in commands.items()
        ],
    },
    install_requires=[
        'boto',
        'colorama',
        'PyYAML>=3.11',
        'six',
    ],
    maintainer='Christian Hammond',
    maintainer_email='christian@beanbaginc.com'
)
