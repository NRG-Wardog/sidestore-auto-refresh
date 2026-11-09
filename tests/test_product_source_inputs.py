"""Input slicing must preserve exact current owner bytes and fail closed."""
import unittest
from unittest.mock import patch
from tests.support import product_sources as sources


class ProductSourceInputsTests(unittest.TestCase):
    def test_comments_strings_and_nested_braces_do_not_truncate_declaration(self):
        source = '''enum Owner {
 // } ignored
 /* } /* nested } */ */
 let text = "}"
 let multiline = """ } { """
 func nested() { if true { } }
}
struct Other {}
'''
        end = sources.declaration_end(source, 0)
        self.assertEqual(source[end:], '\nstruct Other {}\n')

    def test_raw_strings_and_nested_interpolation_do_not_end_owner_early(self):
        source = r'''enum Owner {
 let raw = #"unescaped quote " } still literal"#
 let nested = "value: \(render("nested }", { return "}" }))"
 let multi = ##""" } " unescaped quote """##
}
struct Other {}
'''
        end = sources.declaration_end(source, 0)
        self.assertEqual(source[end:], '\nstruct Other {}\n')

    def test_malformed_lexical_regions_are_rejected(self):
        for source in ('enum X { /* }', 'enum X { let x = #" }',
                       'enum X { let x = "\\(call( }'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                sources.declaration_end(source, 0)

    def test_unterminated_declaration_is_rejected(self):
        with self.assertRaises(ValueError):
            sources.declaration_end('enum Owner { /* } */', 0)

    def test_range_is_exact_owner_text_not_legacy_template(self):
        fixture = ('enum First { let value = 123 }\n'
                   'enum NewlyAdded { let current = true }\n'
                   'enum Last { func finish() {} }\n'
                   'enum Outside {}')
        spec = ('SideStore', 'Owner.swift', 'enum First {', 'enum Last {', 'import Foundation')
        with patch.dict(sources.SOURCES, {'fixture': spec}), patch.object(
                sources, 'owner_source', return_value=fixture) as read:
            result = sources.read_product_source('fixture')
        read.assert_called_once_with('Owner.swift', 'SideStore')
        self.assertEqual(result, 'import Foundation\n\n' + fixture.split('\nenum Outside')[0] + '\n')

    def test_missing_duplicate_or_reversed_boundary_is_rejected(self):
        spec = ('SideStore', 'Owner.swift', 'enum First {', 'enum Last {', '')
        for fixture in ('enum First {}', 'enum Last {} enum First {}',
                        'enum First {} enum First {} enum Last {}'):
            with self.subTest(fixture=fixture), patch.dict(sources.SOURCES, {'fixture': spec}), patch.object(
                    sources, 'owner_source', return_value=fixture), self.assertRaises(ValueError):
                sources.read_product_source('fixture')

    def test_comment_string_and_nested_declarations_are_not_range_boundaries(self):
        spec = ('SideStore', 'Owner.swift', 'enum First {', 'enum Last {', '')
        for fixture in ('/* enum First { } enum Last { } */\nstruct Other {}',
                        'let text = "enum First { } enum Last { }"',
                        'struct Outer { enum First {} enum Last {} }'):
            with self.subTest(fixture=fixture), patch.dict(sources.SOURCES, {'fixture': spec}), patch.object(
                    sources, 'owner_source', return_value=fixture), self.assertRaises(ValueError):
                sources.read_product_source('fixture')

    def test_raw_regex_literal_is_explicitly_rejected(self):
        for fixture in ('enum Owner { let pattern = #/}/#\n func important() {}\n}',
                        'enum Owner { let pattern = ##/}/##\n func important() {}\n}'):
            with self.subTest(fixture=fixture), self.assertRaisesRegex(ValueError, 'regex'):
                sources.declaration_end(fixture, 0)

    def test_full_file_returns_unmodified_bytes_as_text(self):
        text = '/* owner */\nimport Foundation\nstruct Exact {}\n'
        with patch.dict(sources.SOURCES, {'fixture': ('LiveContainer', 'Exact.swift', None, None, '')}), patch.object(
                sources, 'owner_source', return_value=text):
            self.assertEqual(sources.read_product_source('fixture'), text)


class ProductSourceRoleTests(unittest.TestCase):
    def test_platform_libc_imports_are_mutually_exclusive(self):
        expected = "#if canImport(Darwin)\nimport Darwin\n#elseif canImport(Glibc)\nimport Glibc\n#endif"
        for role in ("v3_ipa_staging.swift", "v3_secret_handoff.swift"):
            with self.subTest(role=role):
                imports = sources.SOURCES[role][-1]
                self.assertIn(expected, imports)
                self.assertNotIn("import Darwin\nimport Glibc", imports)

    def test_role_never_resolves_a_local_template_path(self):
        role = sources.ProductSource('v3_wire_contract.swift')
        with patch.object(sources, 'read_product_source', return_value='exact owner') as read:
            self.assertEqual(role.read_text(), 'exact owner')
            self.assertEqual(role.read_bytes(), b'exact owner')
        self.assertEqual(read.call_count, 2)
        self.assertFalse(hasattr(role, '__fspath__'))
        with self.assertRaises(ValueError): role.read_text(encoding='latin1')
        with self.assertRaises(ValueError): sources.ProductSource('unreviewed.swift')
