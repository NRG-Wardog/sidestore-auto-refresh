"""Same-run qualification admission controls, not native size/runtime evidence."""
import ast
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
class WidgetLocalPackagingWiringTests(unittest.TestCase):
    def test_default_off_and_invalid_partial_request_rejected_at_entry(self):
        spec=importlib.util.spec_from_file_location('widget_packager_control',(ROOT/'ci/packaging/native/package_candidate.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for kwargs in ({}, {'strip_unsigned_widget': True}):
            with self.assertRaisesRegex(ValueError, 'requires same-run qualification'):
                module.main(strip_widget_true_locals=True,**kwargs)
        for kwargs in ({'same_run_qualified': True},
                       {'same_run_qualified': True, 'strip_unsigned_widget': True}):
            with self.assertRaisesRegex(ValueError, 'requires the complete reviewed size pipeline'):
                module.main(strip_widget_true_locals=True, **kwargs)
        with self.assertRaises(TypeError):
            module.main(recover_fixed_native=True)

    def test_exact_cli_closure_and_helper_wiring(self):
        source=((ROOT/'ci/packaging/native/package_candidate.py')).read_text();tree=ast.parse(source)
        main=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='main')
        values=dict(zip((arg.arg for arg in main.args.kwonlyargs),main.args.kw_defaults))
        self.assertIs(values['strip_widget_true_locals'].value,False)
        calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='optimize_widget']
        self.assertEqual(len(calls),1)
        kwargs={row.arg:row.value for row in calls[0].keywords}
        self.assertEqual(kwargs['include_true_locals'].id,'strip_widget_true_locals')
        self.assertNotIn('--recover-fixed-native', source)
        self.assertIn("['--same-run-qualified-size']", source)
        self.assertNotIn('recover_fixed_native', values)
        cli_calls = [node for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                     and node.func.id == 'main' and node.keywords]
        self.assertEqual(len(cli_calls), 4)
        for call in cli_calls:
            flags = {kw.arg: kw.value for kw in call.keywords}
            for required in ('same_run_qualified', 'strip_unsigned_widget',
                             'strip_widget_true_locals', 'strip_openssl_true_locals'):
                self.assertIs(flags[required].value, True)
        self.assertIn("'debug_stabs': widget_report['debug_only_stage']['raw_bytes_saved']",source)
        self.assertIn("'true_locals': widget_report.get('true_local_stage', {}).get('raw_bytes_saved', 0)",source)
