import copy
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
import verify as v

PNG = b'portable-test-png-callback-witness'

class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.arm = 'control'
        self.report, self.groups = self.fixture(self.arm)
        self.save()
    def fixture(self, arm):
        process, session = str(uuid4()), str(uuid4())
        ids = {name: str(uuid4()) for name in v.PROBES}
        def state(language, uptime):
            direction = 'rtl' if language == 'he' else 'ltr'
            return dict(schema='p2-form-direction-state-v1', arm=arm, processToken=process,
                sessionToken=session, processIdentifier=111 if arm == 'control' else 222,
                processArguments=['/fixture/P2SettingsHost', '--form-arm', arm], languageEnvironment={},
                bootstrapLanguage='en', bootstrapScope='fixture-domain-before-view-construction', bootstrapPriorLanguage='<missing>',
                language=language, hostDefaultLanguage=language, locale=language, direction=direction,
                text='', originalMessage='original synthetic message', confirmStarted=0, confirmCompleted=0,
                errorStarted=0, textStarted=0, textCompleted=0, witnessChoice=0,
                probes={name: dict(identity=ids[name], locale=language,
                    direction='ltr' if arm == 'candidate' and name == 'container' else direction, uptime=uptime,
                    revision=1 if uptime == 1 else (2 if uptime == 19 else 3)) for name in v.PROBES})
        states = [state('en', 1), state('he', 19), state('en', 39)]
        def node(identifier, x=10, label='Label'):
            return dict(identifier=identifier, label=label, value='', role=9, exists=True, hittable=True,
                frame=dict(x=x, y=10, width=30, height=20))
        def geometry(language):
            result = {'viewport': dict(x=0, y=0, width=400, height=900)}
            result.update({name: node(name) for name in v.ELEMENTS})
            label = 'שפה' if language == 'he' else 'Language'
            result['settings.language']['label'] = label + ', ' + ('עברית' if language == 'he' else 'English')
            result['productionHeaderMatches'] = [node('', label=label)]
            result['nativeDisclosureDescendants'] = []
            result['disclosureObservation'] = 'native AX descendants only; chevron may not be exposed'
            for first, last in [('p2-direction-first', 'p2-direction-last'), ('p2-outside-first', 'p2-outside-last')]:
                result[first]['frame']['x'], result[last]['frame']['x'] = (80, 10) if language == 'he' else (10, 80)
            result['contentOrderCorrect'] = result['outsideOrderCorrect'] = True
            return result
        rows=[]
        for i, stage in enumerate(v.STAGES):
            s=states[i//2]; start=10 + (i//2)*20 + (i%2)*2
            rows.append(dict(stage=stage, language=v.LANGUAGES[i], stateBefore=copy.deepcopy(s), stateAfter=copy.deepcopy(s),
                captureStartedUptime=start, pngFinishedUptime=start+.1, captureFinishedUptime=start+.2,
                png=arm+'-'+stage+'.png', ax=arm+'-'+stage+'.txt', geometry=geometry(v.LANGUAGES[i])))
        transitions=[]
        for before, after, old, new, base in [(1, 2, 'en', 'he', 13), (3, 4, 'he', 'en', 33)]:
            transitions.append(dict(before=copy.deepcopy(rows[before]['stateAfter']), after=copy.deepcopy(rows[after]['stateBefore']),
                pickerTap=copy.deepcopy(rows[before]['geometry']['settings.language']), optionTap=node('', label='עברית' if new=='he' else 'English'),
                pickerTapStartedUptime=base, tapStartedUptime=base+1, tapFinishedUptime=base+2, readyUptime=base+7,
                **{'from': old, 'to': new}))
            transitions[-1]['pickerCandidates']=[copy.deepcopy(transitions[-1]['pickerTap'])]
            transitions[-1]['buttonCandidates']=[copy.deepcopy(transitions[-1]['optionTap'])]
            transitions[-1]['textCandidates']=[]
            transitions[-1]['optionRole']='button'
            transitions[-1]['optionContainment']='single-actionable-target'
            transitions[-1]['buttonTextDescendants']=[]
        report=dict(schema='p2-form-direction-v1', arm=arm, method=v.ARMS[arm], completed=True, failures=[],
            observations=rows, transitions=transitions, finalState=copy.deepcopy(rows[-1]['stateAfter']),
            visualVerdict=v.VISUAL_PENDING, scope='Standalone fixture only')
        names=[arm+'-form-direction.json']+[arm+'-'+s+e for s in v.STAGES for e in ['.png','.txt']]
        attachments=[dict(suggestedHumanReadableName=name, exportedFileName=str(uuid4())+Path(name).suffix,
            deviceId='same-simulator', timestamp=1000+i, isAssociatedWithFailure=False) for i,name in enumerate(names)]
        return report,[dict(testIdentifier='FormDirectionTests/'+v.ARMS[arm]+'()', attachments=attachments)]
    def save(self):
        for group in self.groups:
            for meta in group['attachments']:
                name=meta['exportedFileName']
                if Path(name).name!=name: continue
                path=self.root/name
                if meta['suggestedHumanReadableName']==self.arm+'-form-direction.json': path.write_text(json.dumps(self.report))
                elif path.suffix=='.png': path.write_bytes(PNG)
                elif path.suffix=='.txt': path.write_text('Application AX hierarchy')
        (self.root/'manifest.json').write_text(json.dumps(self.groups))
    def check(self): return v.verify_arm(self.root,self.arm,lambda path:path.read_bytes()==PNG)
    def bad(self, mutate):
        mutate();self.save()
        with self.assertRaises((ValueError,KeyError,TypeError,AttributeError)):self.check()
    def test_valid_collection_pending_visual(self):
        result=self.check();self.assertEqual(result['status'],'collection-complete')
        self.assertEqual(result['pngCount'],6);self.assertEqual(result['axCount'],6)
    def test_control_defect_remains_collectable(self):
        for row in self.report['observations'][4:]:
            row['geometry']['p2-direction-first']['frame']['x']=100
            row['geometry']['contentOrderCorrect']=False
        self.save();self.assertFalse(self.check()['geometryAcceptance'])
    def test_forged_order_rejected(self):
        self.bad(lambda:self.report['observations'][0]['geometry'].__setitem__('contentOrderCorrect',False))
    def test_duplicate_attachment(self):
        self.bad(lambda:self.groups[0]['attachments'].append(copy.deepcopy(self.groups[0]['attachments'][1])))
    def test_missing_attachment(self): self.bad(lambda:self.groups[0]['attachments'].pop())
    def test_foreign_method(self): self.bad(lambda:self.groups[0].__setitem__('testIdentifier','FormDirectionTests/testCandidateRoundTrip()'))
    def test_foreign_report_arm(self):self.bad(lambda:self.report.__setitem__('arm','candidate'))
    def test_duplicate_stage(self):self.bad(lambda:self.report['observations'][1].__setitem__('stage',v.STAGES[0]))
    def test_extra_stage(self):self.bad(lambda:self.report['observations'].append(copy.deepcopy(self.report['observations'][0])))
    def test_wrong_environment(self):self.bad(lambda:self.report['observations'][2]['stateBefore']['probes']['container'].__setitem__('direction','ltr'))
    def test_identity_reset(self):self.bad(lambda:self.report['observations'][4]['stateBefore']['probes']['content'].__setitem__('identity',str(uuid4())))
    def test_nan_timing(self):self.bad(lambda:self.report['observations'][0].__setitem__('captureStartedUptime',float('nan')))
    def test_boolean_timing(self):self.bad(lambda:self.report['observations'][0].__setitem__('captureStartedUptime',True))
    def test_later_too_early(self):self.bad(lambda:self.report['observations'][1].__setitem__('captureStartedUptime',10.5))
    def test_missing_transition(self):self.bad(lambda:self.report['transitions'].pop())
    def test_fake_picker_option(self):self.bad(lambda:self.report['transitions'][0]['optionTap'].__setitem__('label','Synthetic language override'))
    def test_ambiguous_live_option(self):
        self.bad(lambda:self.report['transitions'][0]['buttonCandidates'].append(copy.deepcopy(self.report['transitions'][0]['optionTap'])))
    def test_unrelated_button_and_same_label_text(self):
        def mutate():
            text=copy.deepcopy(self.report['transitions'][0]['optionTap'])
            text['role']=48
            text['frame']['x']=200
            self.report['transitions'][0]['textCandidates']=[text]
        self.bad(mutate)
    def test_unique_native_button_text_containment(self):
        change=self.report['transitions'][0]
        text=copy.deepcopy(change['optionTap']);text['role']=48
        change['textCandidates']=[text]
        change['buttonTextDescendants']=[copy.deepcopy(text)]
        change['optionContainment']='unique-button-contains-unique-text'
        self.save();self.assertEqual(self.check()['status'],'collection-complete')
    def test_mismatched_native_descendant_receipt(self):
        def mutate():
            change=self.report['transitions'][0]
            text=copy.deepcopy(change['optionTap']);text['role']=48
            change['textCandidates']=[text]
            change['buttonTextDescendants']=[copy.deepcopy(text)]
            change['buttonTextDescendants'][0]['frame']['x']=200
            change['optionContainment']='unique-button-contains-unique-text'
        self.bad(mutate)
    def test_unbound_live_picker(self):self.bad(lambda:self.report['transitions'][0].__setitem__('pickerCandidates',[]))
    def test_forged_hebrew_header(self):self.bad(lambda:self.report['observations'][2]['geometry']['productionHeaderMatches'][0].__setitem__('label','Language'))
    def test_forged_picker_label(self):self.bad(lambda:self.report['observations'][0]['geometry']['settings.language'].__setitem__('label','Fake Language'))
    def test_unknown_environment_key(self):self.bad(lambda:self.report['observations'][0]['stateBefore']['languageEnvironment'].__setitem__('FOREIGN','x'))
    def test_missing_environment(self):self.bad(lambda:self.report['observations'][0]['stateBefore'].pop('languageEnvironment'))
    def test_wrong_environment_type(self):self.bad(lambda:self.report['observations'][0]['stateBefore'].__setitem__('languageEnvironment',[]))
    def test_stale_probe_revision(self):
        def mutate():
            for row in self.report['observations'][2:4]:
                row['stateBefore']['probes']['container']['revision']=1
                row['stateAfter']['probes']['container']['revision']=1
            self.report['transitions'][0]['after']['probes']['container']['revision']=1
            self.report['transitions'][1]['before']['probes']['container']['revision']=1
        self.bad(mutate)
    def test_programmatic_bootstrap_scope(self):self.bad(lambda:self.report['observations'][0]['stateBefore'].__setitem__('bootstrapScope','product-domain'))
    def test_unsafe_export(self):self.bad(lambda:self.groups[0]['attachments'][1].__setitem__('exportedFileName','../outside.png'))
    def test_outside_geometry(self):self.bad(lambda:self.report['observations'][0]['geometry']['p2-witness-header']['frame'].__setitem__('x',9999))
    def test_wrong_png_name(self):self.bad(lambda:self.report['observations'][0].__setitem__('png','candidate-wrong.png'))
    def test_invalid_png_bytes(self):
        path=self.root/self.groups[0]['attachments'][1]['exportedFileName'];path.write_bytes(b'bad')
        with self.assertRaises(ValueError):self.check()
    def test_ambiguous_png_validator(self):
        with self.assertRaises(ValueError):v.verify_arm(self.root,self.arm,lambda path:None)
    def test_duplicate_json_key(self):
        meta=self.groups[0]['attachments'][0];path=self.root/meta['exportedFileName']
        path.write_text(path.read_text().replace('"arm": "control"','"arm":"control","arm":"control"',1))
        with self.assertRaises(ValueError):self.check()
    def test_decorated_names(self):
        for meta in self.groups[0]['attachments']:
            p=Path(meta['suggestedHumanReadableName']);meta['suggestedHumanReadableName']=p.stem+'_0_'+str(uuid4())+p.suffix
        (self.root/'manifest.json').write_text(json.dumps(self.groups));self.assertEqual(self.check()['status'],'collection-complete')
    def test_pair_and_independent_review(self):
        control=self.check()
        self.arm='candidate';self.report,self.groups=self.fixture('candidate');self.save();candidate=self.check()
        pair=v.verify_pair(control,candidate)
        self.assertEqual(v.visual_verdict(pair)['status'],'pending-independent-raster-review')
        review=dict(controlReproduced=True,candidateEnglishReadable=True,candidateHebrewReadable=True,
            candidateHebrewLayoutAppropriate=True,reviewer='independent-review',evidence=['bound raster comparisons'])
        self.assertEqual(v.visual_verdict(pair,review)['status'],'bounded-fixture-improvement-only')
        self.assertFalse(v.visual_verdict(pair,review)['productFixAccepted'])
        review['controlReproduced']=False
        self.assertEqual(v.visual_verdict(pair,review)['status'],'inconclusive-control-did-not-reproduce')
        candidate['processToken']=control['processToken']
        with self.assertRaises(ValueError):v.verify_pair(control,candidate)

if __name__=='__main__':unittest.main()

class StateDifferenceTests(unittest.TestCase):
    def event(self):
        return {'phase':'before-assertion','receipt':{'schema':'p2-form-state-difference-v1','arm':'control',
            'method':'testControlRoundTrip','stage':'en-initial-immediate','foundationEqual':False,'complete':True,
            'canonicalEqual':False,'rawJSONEqual':False,'beforeSHA256':'a'*64,'afterSHA256':'b'*64,
            'changedPaths':['/probes/content/revision','/probes/content/uptime'],
            'numericProbeChanges':[['content','revision',1,3],['content','uptime',1.5,2.5]]}}
    def verify(self,event):return v.verify_state_difference(event,'control','en-initial-immediate','before-assertion')
    def test_complete_numeric_receipt_is_diagnostic_only(self):
        out=self.verify(self.event());self.assertFalse(out['nativeAcceptance']);self.assertFalse(out['visualAcceptance'])
    def test_canonical_equal_foundation_unequal_is_retained(self):
        e=self.event();r=e['receipt'];r.update(canonicalEqual=True,rawJSONEqual=True,afterSHA256='a'*64,changedPaths=[],numericProbeChanges=[])
        self.assertEqual(self.verify(e)['changedPaths'],[])
    def test_foreign_arm_method_stage_phase_reject(self):
        for key,value in [('arm','candidate'),('method','testCandidateRoundTrip'),('stage','he-selected-immediate')]:
            e=self.event();e['receipt'][key]=value
            with self.assertRaises(ValueError):self.verify(e)
        e=self.event();e['phase']='finalization'
        with self.assertRaises(ValueError):self.verify(e)
    def test_incomplete_and_extra_values_reject(self):
        for mutation in [lambda r:r.update(complete=False),lambda r:r.update(rawEnvironment={'secret':'forbidden'}),lambda r:r.pop('changedPaths')]:
            e=self.event();mutation(e['receipt'])
            with self.assertRaises(ValueError):self.verify(e)
    def test_hash_and_equality_disagreement_reject(self):
        for mutation in [lambda r:r.update(afterSHA256='a'*64),lambda r:r.update(canonicalEqual=True),lambda r:r.update(rawJSONEqual=True),lambda r:r.update(foundationEqual=True)]:
            e=self.event();mutation(e['receipt'])
            with self.assertRaises(ValueError):self.verify(e)
    def test_foreign_duplicate_and_unsorted_paths_reject(self):
        for paths in [['/unknown'],['/probes/foreign/revision'],['/probes/content/revision']*2,['/probes/content/uptime','/probes/content/revision']]:
            e=self.event();e['receipt']['changedPaths']=paths
            with self.assertRaises(ValueError):self.verify(e)
    def test_missing_duplicate_and_unchanged_numeric_rows_reject(self):
        for mutation in [lambda r:r['numericProbeChanges'].pop(),lambda r:r['numericProbeChanges'].append(r['numericProbeChanges'][0]),lambda r:r['numericProbeChanges'][0].__setitem__(3,1)]:
            e=self.event();mutation(e['receipt'])
            with self.assertRaises(ValueError):self.verify(e)
    def test_boolean_float_revision_and_nonfinite_reject(self):
        for value in [True,1.5,float('inf'),float('nan')]:
            e=self.event();e['receipt']['numericProbeChanges'][0][2]=value
            with self.assertRaises(ValueError):self.verify(e)
    def test_changed_arguments_are_paths_without_values(self):
        e=self.event();r=e['receipt'];r.update(changedPaths=['/processArguments'],numericProbeChanges=[])
        self.assertEqual(self.verify(e)['changedPaths'],['/processArguments'])
        r['argumentValues']=['unexpected private value']
        with self.assertRaises(ValueError):self.verify(e)
    def test_original_acceptance_does_not_use_diagnostic_verifier(self):
        import inspect
        self.assertNotIn('verify_state_difference',inspect.getsource(v.verify_arm))
        self.assertNotIn('verify_state_difference',inspect.getsource(v.verify_pair))
    def test_swift_operands_and_receipt_precede_assertion(self):
        s=(Path(__file__).parent/'FormDirectionTests.swift.in').read_text()
        part=s[s.index('@discardableResult private func capture'):s.index('private func pair')]
        self.assertIn('let equal = NSDictionary(dictionary: before).isEqual(to: after)',part)
        self.assertLess(part.index('captureFailure ='),part.index('try checkState(after'))
        self.assertLess(part.index('phase: "before-assertion"'),part.index('try require(equal'))
        self.assertIn('"before": before, "after": after',part)
        self.assertIn('"rawBefore": rawBefore',part);self.assertIn('"rawAfter": rawAfter',part)
        self.assertEqual(part.count('try state()'),2)
    def test_swift_bounds_unbuffered_output_and_final_receipt(self):
        s=(Path(__file__).parent/'FormDirectionTests.swift.in').read_text()
        self.assertIn('FileHandle.standardOutput.write',s)
        self.assertIn('data.count <= 1536',s);self.assertIn('json(receipt).count <= 1400',s)
        self.assertIn('state-difference-unavailable-or-over-bound',s)
        finish=s[s.index('private func finish()'):s.index('private func run(')]
        self.assertLess(finish.index('result["captureFailure"]'),finish.index('XCTAttachment(data:'))
        self.assertLess(finish.index('phase: "finalization"'),finish.index('app.terminate()'))

    def test_six_probe_revision_time_changes_fit_compact_receipt(self):
        e=self.event();r=e['receipt']
        r['changedPaths']=sorted('/probes/'+name+'/'+field for name in v.PROBES for field in ['revision','uptime'])
        r['numericProbeChanges']=[[name,field,1,3] if field=='revision' else [name,field,238.838838884,241.878978783] for name in sorted(v.PROBES) for field in ['revision','uptime']]
        self.verify(e)
        self.assertLessEqual(len(json.dumps(r,sort_keys=True,separators=(',',':')).replace('/','\\/').encode()),1400)

    def test_invalid_application_values_remain_failure_diagnostics(self):
        e=self.event();e['receipt']['numericProbeChanges'][0][3]=-1
        self.assertFalse(self.verify(e)['nativeAcceptance'])
        e=self.event();e['receipt']['numericProbeChanges'][1][3]=0
        self.assertFalse(self.verify(e)['nativeAcceptance'])
    def test_malformed_field_type_rejects_cleanly(self):
        for value in [[],{},False,None]:
            e=self.event();e['receipt']['numericProbeChanges'][0][1]=value
            with self.assertRaises(ValueError):self.verify(e)
