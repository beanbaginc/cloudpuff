from __future__ import unicode_literals

import os
import shutil
import tempfile
from unittest import TestCase

from cloudformer.templates import TemplateCompiler, TemplateReader


class TemplateCompilerTests(TestCase):
    """Unit tests for TemplateCompiler."""

    def test_compiling(self):
        """Testing TemplateCompiler compiles"""
        compiler = TemplateCompiler()
        compiler.load_string(
            'Meta:\n'
            '    Description: My description.\n'
            '    Version: 1.0\n'
            'Parameters:\n'
            '    key: value\n'
            'Mappings:\n'
            '    key: value\n'
            'Resources:\n'
            '    key: value\n'
            'junk: blah\n')

        doc = compiler.doc
        self.assertEqual(doc['AWSTemplateFormatVersion'], '2010-09-09')
        self.assertEqual(doc['Description'], 'My description. [v1.0]')
        self.assertEqual(doc['Parameters'], {'key': 'value'})
        self.assertEqual(doc['Mappings'], {'key': 'value'})
        self.assertEqual(doc['Resources'], {'key': 'value'})
        self.assertFalse('junk' in doc)


class TemplateReaderTests(TestCase):
    """Unit tests for TemplateReader."""

    def test_norm_bool(self):
        """Testing TemplateReader with normalizing boolean values"""
        reader = TemplateReader()
        reader.load_string(
            'bool1: false\n'
            'bool2: true\n')

        self.assertEqual(reader.doc['bool1'], 'false')
        self.assertEqual(reader.doc['bool2'], 'true')

    def test_norm_int(self):
        """Testing TemplateReader with normalizing integer values"""
        reader = TemplateReader()
        reader.load_string('key: 123')

        self.assertEqual(reader.doc['key'], '123')

    def test_embed_refs(self):
        """Testing TemplateReader with embedding @@References"""
        reader = TemplateReader()
        reader.load_string('key: "@@MyRef"')

        self.assertEqual(reader.doc['key'], { 'Ref': 'MyRef' })

    def test_embed_vars(self):
        """Testing TemplateReader with embedding $$variables"""
        reader = TemplateReader()
        reader.variables['myvar'] = '123'
        reader.load_string('key: $$myvar')

        self.assertEqual(reader.doc['key'], '123')

    def test_embed_vars_with_path(self):
        """Testing TemplateReader with embedding $$variables.with.paths"""
        reader = TemplateReader()
        reader.variables['myvar'] = {
            'a': {
                'b': '123'
            }
        }
        reader.load_string('key: $${myvar.a.b}')

        self.assertEqual(reader.doc['key'], '123')

    def test_embed_funcs(self):
        """Testing TemplateReader with embedding !!Functions"""
        reader = TemplateReader()
        reader.load_string('key: "!!FindInMap(a, @@b, c)"')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::FindInMap': [
                    'a',
                    { 'Ref': 'b' },
                    'c',
                ]
            })

    def test_process_strings_refs(self):
        """Testing TemplateReader with processing strings with @@References"""
        reader = TemplateReader()
        reader.load_string('key: "foo - @@Bar - baz"')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::Join': [
                    '',
                    [
                        'foo - ',
                        { 'Ref': 'Bar' },
                        ' - baz',
                    ]
                ]
            })

    def test_process_strings_vars(self):
        """Testing TemplateReader with processing strings with $$variables"""
        reader = TemplateReader()
        reader.variables['myvar'] = 'abc'
        reader.load_string('key: "foo - $$myvar - baz"')

        self.assertEqual(reader.doc['key'], 'foo - abc - baz')

    def test_process_strings_funcs(self):
        """Testing TemplateReader with processing strings with !!Functions"""
        reader = TemplateReader()
        reader.variables['myvar'] = 'abc'
        reader.load_string('key: "foo - !!FindInMap(a, @@b, c) - baz"')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::Join': [
                    '',
                    [
                        'foo - ',
                        {
                            'Fn::FindInMap': [
                                'a',
                                { 'Ref': 'b' },
                                'c',
                            ]
                        },
                        ' - baz',
                    ]
                ]
            })

    def test_process_multiline_strings(self):
        """Testing TemplateReader with processing multi-line strings"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    This is line one.\n'
            '    This is line two.\n')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::Join': [
                    '',
                    [
                        'This is line one.\n',
                        'This is line two.\n'
                    ]
                ]
            })

    def test_process_base64_multiline_strings(self):
        """Testing TemplateReader with processing __base64__ strings"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    __base64__\n'
            '    This is line one.\n'
            '    This is line two.\n')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::Base64': {
                    'Fn::Join': [
                        '',
                        [
                            'This is line one.\n',
                            'This is line two.\n'
                        ]
                    ]
                }
            })

    def test_doc_macros(self):
        """Testing TemplateReader with '--- !macros' document"""
        reader = TemplateReader()
        reader.load_string(
            '--- !macros\n'
            'macro1:\n'
            '    content:\n'
            '        key: value\n')

        self.assertEqual(reader.doc, {})
        self.assertEqual(
            reader.macros['macro1'],
            {
                'content': {
                    'key': 'value'
                }
            })

    def test_doc_vars(self):
        """Testing TemplateReader with '--- !vars' document"""
        reader = TemplateReader()
        reader.load_string(
            '--- !vars\n'
            'var1: value1\n'
            'var2: value2\n')

        self.assertEqual(reader.doc, {})
        self.assertEqual(reader.variables['var1'], 'value1')
        self.assertEqual(reader.variables['var2'], 'value2')

    def test_statement_tags(self):
        """Testing TemplateReader with !tags"""
        reader = TemplateReader()
        reader.load_string(
            'key: !tags\n'
            '    tag1: value1\n'
            '    tag2: value2\n')

        self.assertEqual(
            reader.doc['key'],
            [
                {
                    'Key': 'tag1',
                    'Value': 'value1',
                },
                {
                    'Key': 'tag2',
                    'Value': 'value2',
                },
            ])

    def test_statement_call_macro(self):
        """Testing TemplateReader with !call-macro"""
        reader = TemplateReader()
        reader.load_string(
            '--- !macros\n'
            'test-macro:\n'
            '    defaultParams:\n'
            '        param1: default1\n'
            '        param2: default2\n'
            '\n'
            '    content:\n'
            '        key1: $$param1\n'
            '        key2: $$param2\n'
            '\n'
            '---\n'
            'key: !call-macro\n'
            '    macro: test-macro\n'
            '    param2: hello\n')

        self.assertEqual(
            reader.doc['key'],
            {
                'key1': 'default1',
                'key2': 'hello',
            })

    def test_statement_call_macro_nested(self):
        """Testing TemplateReader with !call-macro and nested path"""
        reader = TemplateReader()
        reader.load_string(
            '--- !macros\n'
            'my-macros:\n'
            '    test-macro:\n'
            '        defaultParams:\n'
            '            param1: default1\n'
            '            param2: default2\n'
            '\n'
            '        content:\n'
            '            key1: $$param1\n'
            '            key2: $$param2\n'
            '\n'
            '---\n'
            'key: !call-macro\n'
            '    macro: my-macros.test-macro\n'
            '    param2: hello\n')

        self.assertEqual(
            reader.doc['key'],
            {
                'key1': 'default1',
                'key2': 'hello',
            })

    def test_statement_call_macro_and_merge(self):
        """Testing TemplateReader with !call-macro and '<'"""
        reader = TemplateReader()
        reader.load_string(
            '--- !macros\n'
            'test-macro:\n'
            '    defaultParams:\n'
            '        param1: default1\n'
            '        param2: default2\n'
            '\n'
            '    content:\n'
            '        key1: $$param1\n'
            '        key2: $$param2\n'
            '\n'
            '---\n'
            '<: !call-macro\n'
            '    macro: test-macro\n'
            '    param2: hello\n')

        self.assertEqual(reader.doc['key1'], 'default1')
        self.assertEqual(reader.doc['key2'], 'hello')

    def test_statement_import(self):
        """Testing TemplateLoader with !import"""
        tempdir = tempfile.mkdtemp(prefix='cloudformer-tests')
        filename = os.path.join(tempdir, 'defs.yaml')

        with open(filename, 'w') as fp:
            fp.write('--- !vars\n'
                     'var1: value1\n'
                     '--- !macros\n'
                     'macro1:\n'
                     '    content:\n'
                     '        key1: $$var1\n')

        try:
            reader = TemplateReader()
            reader.load_string(
                '__imports__:\n'
                '    !import %s\n'
                '\n'
                'key: !call-macro\n'
                '    macro: macro1\n'
                % filename)

            self.assertEqual(
                reader.variables,
                {
                    'var1': 'value1'
                })
            self.assertEqual(
                reader.macros,
                {
                    'macro1': {
                        'content': {
                            'key1': 'value1',
                        }
                    }
                })
            self.assertEqual(
                reader.doc['key'],
                {
                    'key1': 'value1',
                })
        finally:
            shutil.rmtree(tempdir)
