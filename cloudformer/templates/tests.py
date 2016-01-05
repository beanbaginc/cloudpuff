from __future__ import unicode_literals

import os
import shutil
import tempfile
from unittest import TestCase

from cloudformer.templates import TemplateCompiler, TemplateReader
from cloudformer.templates.state import IfCondition, VarReference


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
            '    key:\n'
            '        Type: String\n'
            '        Description: Test\n'
            'Mappings:\n'
            '    key: value\n'
            'Conditions:\n'
            '    key: value\n'
            'Resources:\n'
            '    key: value\n'
            'Outputs:\n'
            '    key: value\n'
            'junk: blah\n')

        doc = compiler.doc
        self.assertEqual(doc['AWSTemplateFormatVersion'], '2010-09-09')
        self.assertEqual(doc['Description'], 'My description. [v1.0]')
        self.assertEqual(
            doc['Parameters'],
            {
                'key': {
                    'Type': 'String',
                    'Description': 'Test',
                }
            })
        self.assertEqual(doc['Mappings'], {'key': 'value'})
        self.assertEqual(doc['Conditions'], {'key': 'value'})
        self.assertEqual(doc['Resources'], {'key': 'value'})
        self.assertEqual(doc['Outputs'], {'key': 'value'})
        self.assertFalse('junk' in doc)

    def test_compiling_with_if_expressions(self):
        """Testing TemplateCompiler with if expressions"""
        compiler = TemplateCompiler()
        compiler.load_string(
            '--- !vars\n'
            'foo: false\n'
            '---\n'
            'Meta:\n'
            '    Description: My description.\n'
            '    Version: 1.0\n'
            '\n'
            'Resources:\n'
            '    key: |\n'
            '        <% If ($$foo == true) { %>\n'
            '        foo is true\n'
            '        <% } %>\n')

        doc = compiler.doc
        self.assertEqual(doc['AWSTemplateFormatVersion'], '2010-09-09')
        self.assertEqual(doc['Description'], 'My description. [v1.0]')
        self.assertIn('Conditions', doc)
        self.assertEqual(
            doc['Conditions'],
            {
                'IfCondition1': {
                    'Fn::Equals': ['false', 'true'],
                }
            })
        self.assertEqual(
            doc['Resources'],
            {
                'key': {
                    'Fn::If': [
                        'IfCondition1',
                        'foo is true\n',
                        {
                            'Ref': 'AWS::NoValue',
                        }
                    ],
                },
            })

    def test_compiling_with_if_expressions_in_macro(self):
        """Testing TemplateCompiler with if expressions in macro"""
        compiler = TemplateCompiler()
        compiler.load_string(
            '--- !macros\n'
            'test-macro:\n'
            '    content: |\n'
            '        <% If ($$foo == true) { %>\n'
            '        foo is true\n'
            '        <% } %>\n'
            '---\n'
            'Meta:\n'
            '    Description: My description.\n'
            '    Version: 1.0\n'
            '\n'
            'Resources:\n'
            '    key: !call-macro\n'
            '        macro: test-macro\n'
            '        foo: true\n')

        doc = compiler.doc
        self.assertEqual(doc['AWSTemplateFormatVersion'], '2010-09-09')
        self.assertEqual(doc['Description'], 'My description. [v1.0]')
        self.assertIn('Conditions', doc)
        self.assertEqual(
            doc['Conditions'],
            {
                'IfCondition1': {
                    'Fn::Equals': ['true', 'true'],
                }
            })
        self.assertEqual(
            doc['Resources'],
            {
                'key': {
                    'Fn::If': [
                        'IfCondition1',
                        'foo is true\n',
                        {
                            'Ref': 'AWS::NoValue',
                        }
                    ],
                },
            })

    def test_compiling_with_lookup_from_stack_params(self):
        """Testing TemplateCompiler compiles with LookupFromStack parameters"""
        compiler = TemplateCompiler()
        compiler.load_string(
            'Meta:\n'
            '    Description: My description.\n'
            '    Version: 1.0\n'
            'Parameters:\n'
            '    key:\n'
            '        Type: String\n'
            '        Description: Test\n'
            '        LookupFromStack:\n'
            '            StackName: my-stack\n'
            '            OutputName: SomeOutput\n'
            '            MatchStackTags:\n'
            '                - Environment\n')

        doc = compiler.doc
        self.assertEqual(
            doc['Parameters'],
            {
                'key': {
                    'Type': 'String',
                    'Description': 'Test',
                },
            })
        self.assertEqual(
            compiler.stack_param_lookups,
            {
                'key': {
                    'StackName': 'my-stack',
                    'OutputName': 'SomeOutput',
                    'MatchStackTags': [
                        'Environment',
                    ],
                }
            })

    def test_get_tags(self):
        """Testing TemplateCompiler.get_tags"""
        compiler = TemplateCompiler()
        compiler.load_string(
            'Meta:\n'
            '    Name: my-stack\n'
            '    Version: 1.0\n'
            '    Tags:\n'
            '        MyKey: My value\n'
            '        MyRef: "@@MyParam"\n')

        params = (('MyParam', 'MyParamValue'),)

        self.assertEqual(
            compiler.get_tags(params),
            {
                'GenericStackName': 'my-stack',
                'StackVersion': '1.0',
                'MyKey': 'My value',
                'MyRef': 'MyParamValue',
            })


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

    def test_embed_refs_with_vars(self):
        """Testing TemplateReader with embedding @@$$RefVars"""
        reader = TemplateReader()
        reader.load_string('key: "@@$$refname"')

        self.assertEqual(reader.doc['key'], { 'Ref': VarReference('refname') })

    def test_embed_refs_with_braces(self):
        """Testing TemplateReader with embedding @@{References}"""
        reader = TemplateReader()
        reader.load_string('key: "@@{MyRef}"')

        self.assertEqual(reader.doc['key'], { 'Ref': 'MyRef' })

    def test_embed_refs_with_braces_and_vars(self):
        """Testing TemplateReader with embedding @@{$$RefVars}"""
        reader = TemplateReader()
        reader.load_string('key: "@@{$$path.to.refname}"')

        self.assertEqual(reader.doc['key'],
                         { 'Ref': VarReference('path.to.refname') })

    def test_embed_vars(self):
        """Testing TemplateReader with embedding $$variables"""
        reader = TemplateReader()
        reader.template_state.variables['myvar'] = '123'
        reader.load_string('key: $$myvar')

        self.assertEqual(reader.doc['key'], '123')

    def test_embed_vars_with_path(self):
        """Testing TemplateReader with embedding $$variables.with.paths"""
        reader = TemplateReader()
        reader.template_state.variables['myvar'] = {
            'a': {
                'b': '123'
            }
        }
        reader.load_string('key: $${myvar.a.b}')

        self.assertEqual(reader.doc['key'], '123')

    def test_embed_funcs(self):
        """Testing TemplateReader with embedding <% Functions %>"""
        reader = TemplateReader()
        reader.load_string('key: <% FindInMap(a, @@b, c) %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::FindInMap': [
                    'a',
                    { 'Ref': 'b' },
                    'c',
                ]
            })

    def test_embed_funcs_with_base64(self):
        """Testing TemplateReader with embedding Base64()"""
        reader = TemplateReader()
        reader.load_string('key: <% Base64("abc123") %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::Base64': 'abc123',
            })

    def test_embed_funcs_with_if(self):
        """Testing TemplateReader with embedding <% If %>"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    <% If (a) { %>\n'
            '    the line.\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    'a',
                    'the line.\n',
                    {
                        'Ref': 'AWS::NoValue',
                    }
                ]
            })

    def test_embed_funcs_with_if_vars_in_content(self):
        """Testing TemplateReader with embedding <% If %> and variables in content"""
        reader = TemplateReader()
        reader.template_state.variables['myvar'] = '123'
        reader.load_string(
            'key: |\n'
            '    <% If (a) { %>\n'
            '    this is $$myvar.\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    'a',
                    'this is 123.\n',
                    {
                        'Ref': 'AWS::NoValue',
                    }
                ]
            })

    def test_embed_funcs_with_if_refs_in_content(self):
        """Testing TemplateReader with embedding <% If %> and references in content"""
        reader = TemplateReader()
        reader.template_state.variables['myvar'] = '123'
        reader.load_string(
            'key: |\n'
            '    <% If (a) { %>\n'
            '    this is @@MyResource.\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    'a',
                    {
                        'Fn::Join': [
                            '',
                            [
                                'this is ',
                                {
                                    'Ref': 'MyResource',
                                },
                                '.\n'
                            ]
                        ]
                    },
                    {
                        'Ref': 'AWS::NoValue',
                    }
                ]
            })

    def test_embed_funcs_with_if_multiple_lines(self):
        """Testing TemplateReader with embedding <% If %> and multiple lines"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    <% If (a) { %>\n'
            '    a couple of\n'
            '    lines of content.\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    'a',
                    {
                        'Fn::Join': [
                            '',
                            [
                                'a couple of\n',
                                'lines of content.\n',
                            ],
                        ],
                    },
                    {
                        'Ref': 'AWS::NoValue',
                    }
                ]
            })

    def test_embed_funcs_with_if_else(self):
        """Testing TemplateReader with embedding <% If %> and <% Else %>"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    <% If (a) { %>\n'
            '    true_value\n'
            '    <% Else { %>\n'
            '    false_value\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    'a',
                    'true_value\n',
                    'false_value\n',
                ]
            })

    def test_embed_funcs_with_if_elseif(self):
        """Testing TemplateReader with embedding <% If %> and <% ElseIf %>"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    <% If (a) { %>\n'
            '    value1\n'
            '    <% ElseIf (b) { %>\n'
            '    value2\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    'a',
                    'value1\n',
                    {
                        'Fn::If': [
                            'b',
                            'value2\n',
                            {
                                'Ref': 'AWS::NoValue',
                            }
                        ]
                    }
                ]
            })

    def test_embed_funcs_with_if_elseif_else(self):
        """Testing TemplateReader with embedding <% If %>, <% ElseIf %>, <% Else %>"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    <% If (a) { %>\n'
            '    value1\n'
            '    <% ElseIf (b) { %>\n'
            '    value2\n'
            '    <% Else { %>\n'
            '    value3\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    'a',
                    'value1\n',
                    {
                        'Fn::If': [
                            'b',
                            'value2\n',
                            'value3\n',
                        ]
                    }
                ]
            })

    def test_embed_funcs_with_if_elseif_elseif_else(self):
        """Testing TemplateReader with embedding <% If %>, <% ElseIf %>, <% ElseIf %>, <% Else %>"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    <% If (a) { %>\n'
            '    value1\n'
            '    <% ElseIf (b) { %>\n'
            '    value2\n'
            '    <% ElseIf (c) { %>\n'
            '    value3\n'
            '    <% Else { %>\n'
            '    value4\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    'a',
                    'value1\n',
                    {
                        'Fn::If': [
                            'b',
                            'value2\n',
                            {
                                'Fn::If': [
                                    'c',
                                    'value3\n',
                                    'value4\n',
                                ]
                            }
                        ]
                    }
                ]
            })

    def test_embed_funcs_with_if_nested(self):
        """Testing TemplateReader with embedding nested <% If %> statements"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    <% If (a) { %>\n'
            '    Line 1.\n'
            '    <%   If (b) { %>\n'
            '    Line 2.\n'
            '    <%   } %>\n'
            '    Line 3.\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    'a',
                    {
                        'Fn::Join': [
                            '',
                            [
                                'Line 1.\n',
                                {
                                    'Fn::If': [
                                        'b',
                                        'Line 2.\n',
                                        {
                                            'Ref': 'AWS::NoValue',
                                        }
                                    ]
                                },
                                'Line 3.\n'
                            ]
                        ],
                    },
                    {
                        'Ref': 'AWS::NoValue',
                    }
                ]
            })

    def test_embed_funcs_with_if_equals(self):
        """Testing TemplateReader with embedding <% If (lhs == rhs) %>"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    <% If (a == b) { %>\n'
            '    the line.\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    IfCondition({
                        'Fn::Equals': ['a', 'b'],
                    }),
                    'the line.\n',
                    {
                        'Ref': 'AWS::NoValue',
                    }
                ]
            })

    def test_embed_funcs_with_if_equals_refs(self):
        """Testing TemplateReader with embedding <% If (@@lhs == rhs) %>"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    <% If (@@a == b) { %>\n'
            '    the line.\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    IfCondition({
                        'Fn::Equals': [
                            { 'Ref': 'a' },
                            'b'
                        ],
                    }),
                    'the line.\n',
                    {
                        'Ref': 'AWS::NoValue',
                    }
                ]
            })

    def test_embed_funcs_with_if_equals_vars(self):
        """Testing TemplateReader with embedding <% If ($$lhs == rhs) %>"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    <% If ($$a == b) { %>\n'
            '    the line.\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    IfCondition({
                        'Fn::Equals': [
                            VarReference('a'),
                            'b'
                        ],
                    }),
                    'the line.\n',
                    {
                        'Ref': 'AWS::NoValue',
                    }
                ]
            })

    def test_embed_funcs_with_if_not_equals(self):
        """Testing TemplateReader with embedding <% If (lhs != rhs) %>"""
        reader = TemplateReader()
        reader.load_string(
            'key: |\n'
            '    <% If (a != b) { %>\n'
            '    the line.\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    IfCondition({
                        'Fn::Not': [{
                            'Fn::Equals': ['a', 'b'],
                        }]
                    }),
                    'the line.\n',
                    {
                        'Ref': 'AWS::NoValue',
                    }
                ]
            })

    def test_embed_funcs_with_if_complex(self):
        """Testing TemplateReader with embedding complex <% If %>"""
        reader = TemplateReader()
        reader.template_state.variables['v'] = 'value'
        reader.load_string(
            'key: |\n'
            '    <% If ((i != 1 || s == "foo bar") && '
            '           @@r == 3 || $$v == true) { %>\n'
            '    the line.\n'
            '    <% } %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::If': [
                    IfCondition({
                        'Fn::Or': [
                            {
                                'Fn::And': [
                                    {
                                        'Fn::Or': [
                                            {
                                                'Fn::Not': [{
                                                    'Fn::Equals': ['i', '1'],
                                                }],
                                            },
                                            {
                                                'Fn::Equals': ['s', 'foo bar'],
                                            }
                                        ],
                                    },
                                    {
                                        'Fn::Equals': [
                                            { 'Ref': 'r' },
                                            '3',
                                        ]
                                    }
                                ]
                            },
                            {
                                'Fn::Equals': [
                                    'value',
                                    'true'
                                ],
                            }
                        ]
                    }),
                    'the line.\n',
                    {
                        'Ref': 'AWS::NoValue',
                    }
                ]
            })

    def test_embed_funcs_with_get_att(self):
        """Testing TemplateReader with embedding GetAtt"""
        reader = TemplateReader()
        reader.load_string(
            'key: <% GetAtt("MyResource", "MyProperty") %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::GetAtt': ['MyResource', 'MyProperty'],
            })

    def test_embed_funcs_with_get_att_with_refs(self):
        """Testing TemplateReader with embedding GetAtt with @@References"""
        reader = TemplateReader()
        reader.load_string(
            'key: <% GetAtt("MyResource", @@MyProperty) %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::GetAtt': [
                    'MyResource',
                    {
                        'Ref': 'MyProperty'
                    },
                ]
            })

    def test_embed_funcs_with_get_azs(self):
        """Testing TemplateReader with embedding GetAZs()"""
        reader = TemplateReader()
        reader.load_string(
            'key: <% GetAZs() %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::GetAZs': ''
            })

    def test_embed_funcs_with_get_azs_with_region(self):
        """Testing TemplateReader with embedding GetAZs() with a region"""
        reader = TemplateReader()
        reader.load_string(
            'key: <% GetAZs("us-east-1") %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::GetAZs': 'us-east-1'
            })

    def test_embed_funcs_with_get_azs_with_refs(self):
        """Testing TemplateReader with embedding GetAZs() with @@References"""
        reader = TemplateReader()
        reader.load_string(
            'key: <% GetAZs(@@MyReference) %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::GetAZs': {
                    'Ref': 'MyReference',
                }
            })

    def test_embed_funcs_with_select_and_array(self):
        """Testing TemplateReader with embedding Select() with arrays"""
        reader = TemplateReader()
        reader.template_state.variables['myvar'] = 'abc'
        reader.load_string(
            'key: <% Select(2, ["foo \'bar\'", \'"foo" bar\', $$myvar]) %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::Select': [
                    '2',
                    ["foo 'bar'", '"foo" bar', 'abc']
                ]
            })

    def test_embed_funcs_with_select_and_refs(self):
        """Testing TemplateReader with embedding Select() with @@References"""
        reader = TemplateReader()
        reader.load_string(
            'key: <% Select(2, @@MyReference) %>')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::Select': [
                    '2',
                    {
                        'Ref': 'MyReference',
                    }
                ]
            })

    def test_embed_vars_in_keys(self):
        """Testing TemplateReader with embedding $$variables in keys"""
        reader = TemplateReader()
        reader.template_state.variables['myvar'] = 'abc'
        reader.load_string('$$myvar: foo')

        self.assertTrue('abc' in reader.doc)
        self.assertEqual(reader.doc['abc'], 'foo')

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
        reader.template_state.variables['myvar'] = 'abc'
        reader.load_string('key: "foo - $$myvar - baz"')

        self.assertEqual(reader.doc['key'], 'foo - abc - baz')

    def test_process_strings_unresolved_vars(self):
        """Testing TemplateReader with processing strings with unresolved $$variables"""
        reader = TemplateReader()
        reader.load_string('key: "foo - $$myvar - baz"')

        self.assertEqual(
            reader.doc['key'],
            [
                'foo - ',
                VarReference('myvar'),
                ' - baz',
            ])

    def test_process_strings_with_vars_to_refs(self):
        """Testing TemplateReader with processing strings in macros"""
        reader = TemplateReader()
        reader.load_string(
            '--- !macros\n'
            'macro1:\n'
            '    content: "[$$myvar] test"\n'
            '\n'
            '---\n'
            'key: !call-macro\n'
            '    macro: macro1\n'
            '    myvar: "@@MyRef"\n')

        self.assertEqual(
            reader.doc['key'],
            {
                'Fn::Join': [
                    '',
                    [
                        '[',
                        {
                            'Ref': 'MyRef',
                        },
                        '] test'
                    ]
                ]
            })

    def test_process_strings_funcs(self):
        """Testing TemplateReader with processing strings with <% Functions %>"""
        reader = TemplateReader()
        reader.template_state.variables['myvar'] = 'abc'
        reader.load_string('key: foo - <% FindInMap(a, @@b, c) %> - baz')

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
            reader.template_state.macros['macro1'],
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
        self.assertEqual(reader.template_state.variables['var1'], 'value1')
        self.assertEqual(reader.template_state.variables['var2'], 'value2')

    def test_doc_vars_with_refs_in_doc(self):
        """Testing TemplateReader with '--- !vars' document with variable references within the document"""
        reader = TemplateReader()
        reader.load_string(
            '--- !vars\n'
            'var1: value1\n'
            'var2: $${var1}-foo\n'
            'var3: $${var2}bar\n')

        variables = reader.template_state.variables
        self.assertEqual(reader.doc, {})
        self.assertEqual(variables['var1'], 'value1')
        self.assertEqual(variables['var2'], 'value1-foo')
        self.assertEqual(variables['var3'], 'value1-foobar')

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

    def test_statement_call_macro_with_if_expressions(self):
        """Testing TemplateReader with !call-macro with If expressions"""
        reader = TemplateReader()
        reader.load_string(
            '--- !macros\n'
            'test-macro:\n'
            '    defaultParams:\n'
            '        param1: false\n'
            '\n'
            '    content:\n'
            '        string: |\n'
            '            <% If ($$param1 == true) { %>\n'
            '            param1 is true\n'
            '            <% } %>\n'
            '\n'
            '---\n'
            'key: !call-macro\n'
            '    macro: test-macro\n'
            '    param1: true\n')

        self.assertEqual(
            reader.doc['key'],
            {
                'string': {
                    'Fn::If': [
                        IfCondition({
                            'Fn::Equals': [
                                'true',
                                'true'
                            ],
                        }),
                        'param1 is true\n',
                        {
                            'Ref': 'AWS::NoValue',
                        }
                    ]
                }
            })

    def test_statement_call_macro_with_if_expressions_defaults(self):
        """Testing TemplateReader with !call-macro with If expressions and default value"""
        reader = TemplateReader()
        reader.load_string(
            '--- !macros\n'
            'test-macro:\n'
            '    defaultParams:\n'
            '        param1: false\n'
            '\n'
            '    content:\n'
            '        string: |\n'
            '            <% If ($$param1 == true) { %>\n'
            '            param1 is true\n'
            '            <% } %>\n'
            '\n'
            '---\n'
            'key: !call-macro\n'
            '    macro: test-macro\n')

        self.assertEqual(
            reader.doc['key'],
            {
                'string': {
                    'Fn::If': [
                        IfCondition({
                            'Fn::Equals': [
                                'false',
                                'true'
                            ],
                        }),
                        'param1 is true\n',
                        {
                            'Ref': 'AWS::NoValue',
                        }
                    ]
                }
            })

    def test_statement_import(self):
        """Testing TemplateReader with !import"""
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
                reader.template_state.variables,
                {
                    'var1': 'value1'
                })
            self.assertEqual(
                reader.template_state.macros,
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
