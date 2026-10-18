#!/usr/bin/env python3
"""Offline deterministic candidate finalization; never approves or activates.

Raw GitHub API responses must be fetched and verified independently. This tool
checks their binding to actual ZIP bytes, sealed inputs and local Git objects.
It writes a fresh directory outside the builder only after every check passes.
No network, builds, source writes, publication or production-anchor changes.
"""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT)]
import p0_cxx_allocation as admission
import p0_elf_metadata_bounds as historical
import p0_maintained_sources as p0
from prepare_candidate_inventory import BASES, snapshot, git
from p0_maintained_sources import need, parse, digest, fullsha, relative, sha

REPOSITORY='NRG-Wardog/LiveContainer'
WORKFLOW='.github/workflows/p0-cxx-allocation.yml'
BRANCH='validation/p0-cxx-allocation'
STAGE=ROOT/admission.DIRECTORY
BOUNDARY_IDS=tuple(sorted((
 'test_actual_consumer_hooks_observe_without_changing_results','test_actual_loader_lifetime_and_read_only_callbacks',
 'test_actual_native_boundary_success_faults_and_concurrent_calls','test_actual_swift_consumer_decoder_rejects_private_or_malformed_values',
 'test_native_trace_is_bounded_and_can_be_disabled_without_changing_results',
 'test_normal_checked_staging_blocks_otp_and_preserves_previous_blob_on_every_io_failure',
 'test_swift_and_native_metadata_allowlists_are_identical','test_constructor_diagnostic_reaches_actual_json_boundary_and_preserves_poison')))
SUITE_IDS = {'pending-certificate-creation': ('test_pending_certificate_creation.PendingCertificateCreationTests.test_dispatch_requires_verified_retention_and_preserves_late_result', 'test_pending_certificate_creation.PendingCertificateCreationTests.test_existing_api_wrapper_and_app_entrypoints', 'test_pending_certificate_creation.PendingCertificateCreationTests.test_native_exact_pending_activation_consumer', 'test_pending_certificate_creation.PendingCertificateCreationTests.test_native_exact_proxy_create_and_reconciliation', 'test_pending_certificate_creation.PendingCertificateCreationTests.test_native_exact_record_core_fault_matrix', 'test_pending_certificate_creation.PendingCertificateCreationTests.test_native_exact_security_query_scope', 'test_pending_certificate_creation.PendingCertificateCreationTests.test_native_security_matching_mismatching_and_invalid_keys', 'test_pending_certificate_creation.PendingCertificateCreationTests.test_reconciliation_is_read_only_and_cryptographically_bound', 'test_pending_certificate_creation.PendingCertificateCreationTests.test_secure_journal_is_separate_device_local_and_strict'), 'created-certificate-preservation': ('test_signin_certificate_creation_result.SignInCertificateCreationResultTests.test_existing_activation_retry_retains_result', 'test_signin_certificate_creation_result.SignInCertificateCreationResultTests.test_invalidates_list_without_claiming_local_activation', 'test_signin_certificate_creation_result.SignInCertificateCreationResultTests.test_native_request_and_local_retry', 'test_signin_certificate_creation_result.SignInCertificateCreationResultTests.test_returns_full_result_without_second_fallible_request'), 'udid-diagnostics': ('test_signin_udid_failure_consumer.SignInUDIDFailureConsumerTests.test_cellular_restore_boundary_unchanged', 'test_signin_udid_failure_consumer.SignInUDIDFailureConsumerTests.test_extracted_native_consumer', 'test_signin_udid_failure_consumer.SignInUDIDFailureConsumerTests.test_logs_do_not_include_identifiers_or_raw_errors', 'test_signin_udid_failure_consumer.SignInUDIDFailureConsumerTests.test_preserves_first_failure_and_valid_fallback'), 'descriptor-progression': ('test_dead10cc_scan_progression.Dead10ccScanProgressionTests.test_empty_path_advances', 'test_dead10cc_scan_progression.Dead10ccScanProgressionTests.test_failed_path_conversion_advances', 'test_dead10cc_scan_progression.Dead10ccScanProgressionTests.test_lookup_failure_advances', 'test_dead10cc_scan_progression.Dead10ccScanProgressionTests.test_negative_lookup_advances', 'test_dead10cc_scan_progression.Dead10ccScanProgressionTests.test_non_vnode_advances', 'test_dead10cc_scan_progression.Dead10ccScanProgressionTests.test_short_lookup_advances', 'test_dead10cc_scan_progression.Dead10ccScanProgressionTests.test_success_enumerates_every_entry_once'), 'pending-certificate-host-recovery': ('test_pending_certificate_host_recovery.PendingCertificateHostRecoveryTests.test_distinct_capability_is_decoded_in_account_and_auth_snapshots', 'test_pending_certificate_host_recovery.PendingCertificateHostRecoveryTests.test_pending_recovery_policy_executes_owner_and_capability_matrix', 'test_pending_certificate_host_recovery.PendingCertificateHostRecoveryTests.test_reconciliation_never_dispatches_create_revoke_or_auth_begin', 'test_pending_certificate_host_recovery.PendingCertificateHostRecoveryTests.test_recovery_route_survives_signout_and_has_an_explicit_owner_bound_action'), 'test-reconstructed-completion': ('test_reconstructed_completion.CompletionTests.test_index_proof_inside_locked_callback', 'test_reconstructed_completion.CompletionTests.test_native_index_missing_retains_journal'), 'test-pending-pkcs8-proof': ('test_pending_pkcs8_proof.PendingPKCS8Proof.test_native_exact_pkcs8_proof',), 'initializer-diagnostics': ('test_contract.InitializerDiagnosticContract.test_actual_swift_parser_service_wire_and_host_copy', 'test_contract.InitializerDiagnosticContract.test_bounded_primary_only_contract_and_no_recovery_mutation', 'test_contract.InitializerDiagnosticContract.test_real_native_producer_fixture', 'test_contract.InitializerDiagnosticContract.test_shared_capture_validation_and_copy_contracts_match', 'test_contract.InitializerDiagnosticContract.test_trace_disabled_preserves_constructor_evidence'), 'adi-consumption': ('test_contract.ConsumerContractTests.test_disabled_trace_preserves_classification_and_drops_diagnostics', 'test_contract.ConsumerContractTests.test_legacy_comparison_is_read_only_snapshot_bound_and_failure_scoped', 'test_contract.ConsumerContractTests.test_native_pipeline_wire_copy_and_importing_host', 'test_contract.ConsumerContractTests.test_real_cpp_producer_descriptor_and_pinned_schema', 'test_contract.ConsumerContractTests.test_shared_parser_and_schema_are_exact_across_owners', 'test_contract.ConsumerContractTests.test_wire_and_render_use_real_finite_contract')}


def dump_bytes(value):return (json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
def load(path):return parse(Path(path).read_bytes())
def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
def counts(value,expected,label):admission.exact_counts(value,expected,label)


class Artifact:
    workflow = WORKFLOW
    branch = BRANCH
    def __init__(self,zip_path,run_path,artifact_path):
        self.run=load(run_path);self.artifact=load(artifact_path)
        run=self.run;artifact=self.artifact
        need(run.get('status')=='completed' and run.get('conclusion')=='success','GitHub run is not completed/success')
        need(type(run.get('id'))is int and run['id']>0 and type(run.get('run_attempt'))is int and run['run_attempt']>0,'invalid GitHub run identity')
        need(run.get('repository',{}).get('full_name')==REPOSITORY and run.get('path')==self.workflow and run.get('event')in ('push','workflow_dispatch') and run.get('head_branch')==self.branch,'wrong GitHub repository/workflow/event')
        fullsha(run.get('head_sha'))
        need(run.get('html_url')=='https://github.com/'+REPOSITORY+'/actions/runs/'+str(run['id']),'wrong GitHub run URL')
        need(type(artifact.get('id'))is int and artifact['id']>0 and artifact.get('expired')is False,'invalid/expired GitHub artifact')
        need(artifact.get('workflow_run',{}).get('id')==run['id'] and artifact['workflow_run'].get('head_sha')==run['head_sha'],'artifact/run identity mismatch')
        self.zip_sha256=file_sha(zip_path)
        need(artifact.get('digest')=='sha256:'+self.zip_sha256,'GitHub artifact digest differs from downloaded ZIP')
        self.metadata_sha256={'run':file_sha(run_path),'artifact':file_sha(artifact_path)}
        self.archive=zipfile.ZipFile(zip_path)
        try:
            entries=self.archive.infolist();need(0<len(entries)<=10000,'artifact entry count outside bounds')
            need(sum(i.file_size for i in entries)<=1024*1024*1024,'artifact expanded bytes outside bounds')
            names=[]
            for item in entries:
                relative(item.filename.rstrip('/'))
                need(not item.flag_bits&1 and not stat.S_ISLNK(item.external_attr>>16),'encrypted/linked artifact entry')
                need(item.file_size<=256*1024*1024,'artifact member exceeds bounds')
                names.append(item.filename)
            need(len(names)==len(set(names)),'duplicate artifact paths')
            need('evidence-index.json'in names,'artifact index absent')
            need(self.archive.getinfo('evidence-index.json').file_size<=8*1024*1024,'artifact index exceeds bounds')
            self.index_raw=self.archive.read('evidence-index.json');self.index=parse(self.index_raw)
            need(isinstance(self.index,dict) and bool(self.index),'empty artifact index')
            need({i.filename for i in entries if not i.is_dir()}==set(self.index)|{'evidence-index.json'},'artifact/index exact file inventory differs')
            for name,expected in self.index.items():
                relative(name);digest(expected);h=hashlib.sha256()
                with self.archive.open(name)as stream:
                    for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
                need(h.hexdigest()==expected,'indexed evidence drift: '+name)
        except BaseException:
            self.archive.close();raise
    def close(self):self.archive.close()
    def raw(self,name):
        need(name in self.index,'missing indexed evidence: '+name)
        return self.archive.read(name)
    def json(self,name):return parse(self.raw(name))
    def command(self,name):
        verify_command_settlement(self,name)
        need(self.raw('logs/'+name+'.exit-code.txt')==b'0\n','failed command: '+name)
        return self.raw('logs/'+name+'.log').decode('utf-8',errors='strict')
    def identity(self,runner_repo):
        result=self.json('provenance/result.json');inputs=self.raw('provenance/approved-inputs.json')
        tree=git(runner_repo,'rev-parse',self.run['head_sha']+'^{tree}').decode().strip()
        expected={'url':self.run['html_url'],'attempt':self.run['run_attempt'],'host_commit':self.run['head_sha'],'host_tree':tree}
        need(result.get('run')==expected and result.get('approved_inputs_sha256')==sha(inputs),'capture identity/input hash differs')
        need(result.get('status')=='CAPTURED_FOR_REVIEW' and result.get('last_stage')=='captured' and result.get('source_basis')==admission.BASIS,'capture failed/incomplete')
        need(all(result.get(k)is False for k in ('production_ready','ipa_built','guest_execution','ADI_executed','device_validation')),'capture makes unsupported runtime/readiness claims')
        verify_quiescence(self)
        return {'run_url':self.run['html_url'],'run_attempt':self.run['run_attempt'],'host_commit':self.run['head_sha'],'host_tree':tree,
          'artifact_id':self.artifact['id'],'artifact_sha256':self.zip_sha256,'approved_inputs_sha256':sha(inputs),'evidence_index_sha256':sha(self.index_raw)}


def required_command_names(inputs):
    if inputs.get('phase') == 'allocation':
        return {'compiler-version','python-version','network-sandbox','fetch-anisettekit','fetch-exact-anisettekit','checkout-anisettekit','disable-push-anisettekit','unicorn-header-download','allocation-units'}
    need(inputs.get('phase') in ('sidesign','sidestore'), 'settlement capture phase missing')
    phase=inputs['phase']
    names={'xcode-version','swift-version','clang-version','network-sandbox'}
    owners=set(inputs['owners'])
    if phase=='sidestore':owners-={'SideSign','minimuxer'}
    sources={name.lower() for name in owners}
    children=set(inputs['submodules'])
    if phase=='sidestore':children|={'SideStore/Dependencies/SideSign','SideStore/Dependencies/minimuxer'}
    sources|={name.replace('/','-').lower() for name in children}
    for source in sources:
        names|={prefix+source for prefix in ('fetch-','fetch-exact-','checkout-','disable-push-')}
    if phase=='sidesign':
        names|={'sidesign-resolve','sidesign-frozen-resolve','elf-metadata-units','anisette-native-boundary','sidesign-build','sidesign-tests'}
    else:
        names|={'p0-contracts','rust-target','bindgen-version','iphoneos-sdk-path','idevice-fetch','idevice-build','idevice-xcframework',
                'sidestore-resolve','sidestore-frozen-resolve','sidestore-build','livecontainer-build'}
        names|={'suite-'+name for name in SUITE_IDS}
    return names


def verify_command_settlement(artifact,label):
    need(isinstance(label,str) and re.fullmatch('[a-z0-9_-]+',label), 'invalid captured command label')
    command=artifact.json('logs/'+label+'.command.json')
    need(set(command)=={'argv','cwd'} and isinstance(command['argv'],list) and command['argv'] and
         all(isinstance(arg,str) for arg in command['argv']) and (command['cwd'] is None or isinstance(command['cwd'],str)), 'captured command identity malformed')
    groups=artifact.json('provenance/owned-command-groups.json')
    need(isinstance(groups,list) and all(isinstance(row,dict) for row in groups), 'owned command group inventory malformed')
    rows=[row for row in groups if row.get('label')==label]
    need(len(rows)==1, 'consumed command lacks unique owned-group settlement')
    state={k:v for k,v in rows[0].items() if k!='label'}
    need(all(state.get(k) is True for k in ('quiescent','pipesJoined','leaderReaped')) and state.get('errors')==[] and state.get('postReapPolicy')=='read-only-group-disappearance', 'command writers not settled')
    need(type(state.get('pgid'))is int and state['pgid']>0 and type(state.get('reapedBeforeSignals'))is bool and type(state.get('identityRetainedForSignals'))is bool, 'owned command identity malformed')
    need(isinstance(state.get('signals'),list), 'owned command signals malformed')
    need(not state['signals'] or (state['identityRetainedForSignals']is True and state['reapedBeforeSignals']is False), 'post-reap or unowned signal reported')
    settlement=artifact.json('logs/'+label+'.settlement.json')
    need(settlement.get('ownedProcessGroup')==state and type(settlement.get('returncode'))is int and settlement['returncode']==0 and
         type(settlement.get('terminationReturncode'))is int and settlement['terminationReturncode']==0 and not settlement.get('quiescenceError') and not settlement.get('error'), 'command settlement failed')
    need(artifact.raw('logs/'+label+'.exit-code.txt')==b'0\n', 'command exit receipt differs')


def verify_quiescence(artifact):
    result=artifact.json('provenance/result.json')
    need(result.get('writers_quiescent') is True and result.get('retention_errors')==[], 'capture writers unsettled or retention incomplete')
    inputs=artifact.json('provenance/approved-inputs.json')
    required=required_command_names(inputs)
    groups=artifact.json('provenance/owned-command-groups.json')
    need(isinstance(groups,list) and groups and all(isinstance(row,dict) for row in groups), 'owned command settlement inventory missing')
    observed={name.removeprefix('logs/').removesuffix('.command.json') for name in artifact.index if name.startswith('logs/') and name.endswith('.command.json')}
    need(observed==required and len(groups)==len(required) and {row.get('label') for row in groups}==required, 'phase-specific command settlement inventory differs')
    for label in required:
        verify_command_settlement(artifact,label)
        artifact.raw('logs/'+label+'.log')


def verify_package_graph(artifact,inputs):
    lock=artifact.json('provenance/Package.resolved.after');pins={row['identity']:row for row in lock['pins']}
    need(len(pins)==len(lock['pins']), 'duplicate resolved package identity')
    graph=artifact.json('provenance/resolved-package-graph.json')
    local={} if inputs['phase']=='sidesign' else {'sidesign':'SideSign','minimuxer':'minimuxer','common':'minimuxer/Common','devicegateway':'minimuxer/DeviceGateway'}
    need(set(graph)==set(pins)|set(local), 'resolved graph/package inventory differs')
    for name,pin in pins.items():
        row=graph[name];fullsha(row.get('commit'));fullsha(row.get('tree'))
        need(row['commit']==pin['state']['revision'] and type(row.get('tracked_entries'))is int and row['tracked_entries']>0 and isinstance(row.get('submodules'),list), 'resolved package source proof differs')
        origin=row.get('origin',{})
        need(origin.get('kind')=='swiftpm-local-mirror' and origin.get('repository')==pin['location'] and origin.get('commit')==row['commit'] and origin.get('tree')==row['tree'] and isinstance(origin.get('mirror'),str) and origin['mirror'], 'resolved package origin differs')
    for name,suffix in local.items():
        row=graph[name]
        need(set(row)=={'kind','path'} and row['kind']=='fileSystem' and isinstance(row['path'],str) and row['path'].endswith('/sources/SideStore/Dependencies/'+suffix), 'resolved local package differs')
    need(graph['anisettekit']['commit']==admission.AK_COMMIT and graph['anisettekit']['tree']==admission.AK_TREE, 'resolved helper source tuple differs')


def verify_suites(artifact,inputs):
    rows=inputs.get('test_suites');need(isinstance(rows,list),'suite contracts absent')
    need(len(rows)==len(SUITE_IDS) and {row['id']for row in rows}==set(SUITE_IDS),'exact suite inventory differs')
    all_ids=[];reports={}
    for row in rows:
        name=row['id'];ids=sorted(SUITE_IDS[name]);need(sorted(row['expected_test_ids'])==ids,'sealed suite test IDs differ')
        result=artifact.json('provenance/suites/'+name+'.json')
        need(set(result)=={'tests','failures','errors','skips','expected_failures','unexpected_successes','test_ids'},'suite report schema differs')
        counts(result,{'tests':len(ids),'failures':0,'errors':0,'skips':0,'expected_failures':0,'unexpected_successes':0},name)
        need(sorted(result['test_ids'])==ids,'actual suite test IDs differ')
        report=parse(artifact.command('suite-'+name).splitlines()[-1]);need(report==result,'suite output differs from receipt')
        all_ids+=result['test_ids'];reports[name]=result
    need(len(all_ids)==len(set(all_ids))==42,'exact42 unique executed tests required')
    return reports


def verify_source_reports(artifact,inputs):
    need(artifact.json('provenance/result.json')['owners']==inputs['owners'],'capture owner graph differs')
    children=copy.deepcopy(inputs['submodules'])
    if inputs['phase']=='sidestore':
        for owner in ('SideSign','minimuxer'):
            row=inputs['owners'][owner]
            children['SideStore/Dependencies/'+owner]={'repository':row['repository'],'declared_repository':row['repository'],
                                                      'commit':row['commit'],'tree':row['tree']}
    previous=None
    for name in ('source-before.json','source-after.json'):
        report=artifact.json('provenance/'+name);need(set(report)==set(inputs['owners']),'native source proof owner inventory differs')
        if previous is not None:need(report==previous,'native source proof changed during execution')
        previous=report;seen=set()
        def visit(prefix,node):
            need(isinstance(node.get('submodules'),list),'native child source proof absent')
            for child in node['submodules']:
                relative(child['path']);path=prefix+'/'+child['path']
                need(path in children and path not in seen,'native child closure differs')
                seen.add(path);expected=children[path]
                need(child.get('commit')==expected['commit'] and child.get('tree')==expected['tree'],'native child source identity differs')
                need(type(child.get('tracked_entries'))is int and child['tracked_entries']>0,'native child proof empty')
                visit(path,child)
        for owner,row in inputs['owners'].items():
            observed=report[owner];need(observed.get('commit')==row['commit'] and observed.get('tree')==row['tree'],'native source proof tuple differs')
            need(observed.get('origin')=={'kind':'direct','repository':row['repository']},'native source origin differs')
            need(type(observed.get('tracked_entries'))is int and observed['tracked_entries']>0,'native source proof empty')
            visit(owner,observed)
        need(seen==set(children),'native complete child proof missing')
    resolved=artifact.json('provenance/resolved-anisette.json')
    ak=inputs['owners']['AnisetteKit'];need(resolved.get('commit')==ak['commit'] and resolved.get('tree')==ak['tree'],'resolved AK identity differs')
    need(resolved.get('source_sha256')=={n:v['after']['sha256']for n,v in ak['changes'].items()},'resolved AK source bytes differ')
    verify_package_graph(artifact,inputs)


def verify_runner_files(inputs,runner_repo,commit):
    files=dict(inputs['adapter_files'])
    files[inputs['historical_basis']['fingerprints_path']]=inputs['historical_basis']['fingerprints_sha256']
    for suite in inputs['test_suites']:
        for name,expected in suite['files'].items():
            need(name not in files or files[name]==expected,'contradictory sealed runner file')
            files[name]=expected
    if inputs.get('contracts'):
        for key in ('registry','validator'):files[inputs['contracts'][key]]=inputs['contracts'][key+'_sha256']
    for name,expected in files.items():
        relative(name);need(sha(git(runner_repo,'show',commit+':'+name))==digest(expected),'published runner file differs: '+name)


def verify_helper_artifact(artifact):
    helper=admission.validate_helper(artifact.json('metadata-units/result.json'))
    need(artifact.json('provenance/elf-metadata-bounds.json') == dict(helper,status='CAPTURED_FOR_REVIEW',source_owner=admission.AK_COMMIT,source_tree=admission.AK_TREE), 'fresh helper source/capture binding differs')
    for variant, flags in (('normal',()),('optimized',('-O2','-DNDEBUG')),('sanitized',('-O1','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer'))):
        for step in ('compile','run'):
            label='metadata-units/'+variant+'-'+step
            need(artifact.raw(label+'.exit-code.txt')==b'0\n' and artifact.raw(label+'.stderr')==b'', 'fresh helper command failed or emitted stderr')
        args=artifact.json('metadata-units/'+variant+'-compile.command.json')
        need(isinstance(args,list) and all(isinstance(a,str) for a in args) and all(f in args for f in ('-std=c++17','-Wall','-Wextra','-Werror',*flags)), 'fresh helper compile flags differ')
        source=[a for a in args if a.endswith('/.ci/native-tests/fixtures/elf_metadata_bounds_unit.cpp')]
        need(len(source)==1 and '-o' in args and args.count('-o')==1, 'actual helper fixture/output differs')
        executable=args[args.index('-o')+1]
        need(artifact.json('metadata-units/'+variant+'-run.command.json')==[executable], 'helper did not execute compiled output')
        actual=parse(artifact.raw('metadata-units/'+variant+'-run.stdout'))
        need(actual==admission.METADATA_EXPECTED and all(type(actual[k]) is type(v) for k,v in admission.METADATA_EXPECTED.items()), 'helper raw output differs')
    need(parse(artifact.command('elf-metadata-units')) == {'variants':3,'checks_per_variant':713,'failures':0,'skipped':0}, 'helper aggregate output differs')
    forbidden=('native-fork83','constructor-diagnostics')
    need(not any(name.startswith('logs/'+prefix) or name=='provenance/'+prefix+'.json' for name in artifact.index for prefix in forbidden), 'fresh artifact contains prohibited guest executable evidence')
    return helper


def verify_phase1(artifact,runner_repo,phase2_inputs):
    identity=artifact.identity(runner_repo);inputs=artifact.json('provenance/approved-inputs.json')
    expected=phase2_inputs['prior_sidesign']
    need({'index_sha256':identity['evidence_index_sha256'],'run_id':artifact.run['id'],'run_attempt':identity['run_attempt'],
          'host_commit':identity['host_commit'],'artifact_id':identity['artifact_id'],'artifact_sha256':identity['artifact_sha256']}==expected,'phase1 identity differs from sealed phase2 link')
    need(inputs.get('source_basis')==admission.BASIS and inputs.get('integration_baseline')==admission.BASELINE and inputs.get('phase')=='sidesign' and inputs['owners']['AnisetteKit']==phase2_inputs['owners']['AnisetteKit'],'phase1 source/phase differs')
    need(set(inputs['owners'])=={'AnisetteKit','SideSign'} and not inputs['test_suites'] and inputs['contracts'] is None and inputs['prior_sidesign'] is None, 'phase1 owner or suite scope differs')
    verify_runner_files(inputs,runner_repo,identity['host_commit']);verify_source_reports(artifact,inputs)
    for name in ('sidesign-resolve','sidesign-frozen-resolve','sidesign-build','sidesign-tests','anisette-native-boundary'):artifact.command(name)
    helper=verify_helper_artifact(artifact)
    boundary=artifact.json('provenance/anisette-native-boundary.json');counts(boundary,{'checks':8,'failures':0,'skipped':0},'native boundary')
    need(boundary['case_ids']==list(BOUNDARY_IDS) and boundary['ADI_executed']is False and boundary['device_validation']is False,'native boundary cases/claims differ')
    text=artifact.command('anisette-native-boundary')
    matches=re.findall(r'^(test_[A-Za-z0-9_]+) \(__main__\.MaintainedAnisetteNativeTests\.\1\) \.\.\. ok$',text,re.M)
    need(sorted(matches)==list(BOUNDARY_IDS) and re.findall(r'^Ran ([0-9]+) tests? in ',text,re.M)==['8'] and re.findall(r'^OK.*$',text,re.M)==['OK'],'actual native boundary output differs')
    tests=artifact.json('provenance/sidesign-selected-tests.json');expected_tests=sorted(('deviceInitializationAndFiltering','certificateRequestCSRGeneration','developerPortalSingleton','archiveStoreRoundtrip','archiveDeflateRoundtrip'))
    need(tests=={'executed':5,'scope':'sidesign','status':'CAPTURED_FOR_REVIEW','tests':expected_tests},'SideSign selected native tests differ')
    text=re.sub(r'\x1b\[[0-9;]*m','',artifact.command('sidesign-tests'))
    need(sorted(re.findall(r'Test ([A-Za-z0-9_]+)\(\) passed after',text))==expected_tests and sorted(re.findall(r'Test ([A-Za-z0-9_]+)\(\) started',text))==expected_tests and re.findall(r'Test run with (\d+) tests(?: in \d+ suites?)? passed after',text)==['5'],'actual SideSign test execution differs')
    need(not re.search(r'(?:Test .*\b(?:failed|skipped)\b|\b[1-9][0-9]* (?:tests? )?(?:failed|skipped)\b)',text,re.I),'SideSign failure/skip output')
    return identity,inputs,helper


def verify_phase2(artifact,runner_repo,approved_bytes,approved_sha):
    need(sha(approved_bytes)==digest(approved_sha),'external approved input hash differs')
    need(artifact.raw('provenance/approved-inputs.json')==approved_bytes,'artifact input differs from independently approved bytes')
    inputs=parse(approved_bytes);identity=artifact.identity(runner_repo)
    need(inputs.get('source_basis')==admission.BASIS and inputs.get('integration_baseline')==admission.BASELINE and inputs.get('phase')=='sidestore','wrong phase2 input basis')
    need(set(inputs['owners'])==set(admission.PARENTS),'phase2 seven-owner inventory differs')
    for owner,parent in admission.PARENTS.items():
        row=inputs['owners'][owner];fullsha(row['commit']);fullsha(row['tree'])
        need(row['repository']=='https://github.com/NRG-Wardog/'+owner+'.git','phase2 reviewed source origin differs')
        need((row['commit']!=parent) if owner in admission.CHANGED_OWNERS else (row['commit']==parent),'phase2 successor/unchanged-owner constraint differs')
    verify_runner_files(inputs,runner_repo,identity['host_commit']);verify_source_reports(artifact,inputs)
    suites=verify_suites(artifact,inputs)
    for name in ('idevice-build','idevice-xcframework','sidestore-resolve','sidestore-frozen-resolve','sidestore-build','livecontainer-build','p0-contracts'):artifact.command(name)
    for name in ('sidestore-build','livecontainer-build'):
        args=artifact.json('logs/'+name+'.command.json')['argv']
        need(all(v in args for v in ('iphoneos','Release','generic/platform=iOS','CODE_SIGNING_ALLOWED=NO','CODE_SIGNING_REQUIRED=NO','build')),'unsigned iPhoneOS build flags differ')
    products=artifact.json('provenance/unsigned-products.json');need(set(products)=={'SideStore','LiveContainer'},'unsigned product inventory differs')
    for owner,files in products.items():
        need(isinstance(files,dict) and owner in files and 'Info.plist'in files,'unsigned product lacks executable/plist')
        for name,h in files.items():relative(name);digest(h)
    contract=parse(artifact.command('p0-contracts').splitlines()[-1]);registry=load(STAGE/'compatibility-registry.json')
    need(contract.get('status')=='pass' and contract.get('registry_sha256')==sha((STAGE/'compatibility-registry.json').read_bytes()),'actual contract validation differs')
    counts(contract,{'source_files':sum(len(load(STAGE/pin['manifest_path'])['sources']) for pin in registry['owners'].values()),'contracts':len(registry['contracts']),'edges':len(registry['edges'])},'contract graph')
    need(contract['owners']=={owner:len(load(STAGE/pin['manifest_path'])['sources'])for owner,pin in registry['owners'].items()},'contract owner/source counts differ')
    need(inputs['contracts']['registry_sha256']==sha((STAGE/'compatibility-registry.json').read_bytes()) and inputs['contracts']['validator_sha256']==sha((STAGE/'validate_contracts.py').read_bytes()),'sealed phase2 contracts differ')
    return identity,inputs,suites


def resolver_receipt(artifact,identity,inputs,owner):
    before=artifact.raw('provenance/Package.resolved.before');after=artifact.raw('provenance/Package.resolved.after');lock=parse(after)
    old=parse(before)
    need(type(old.get('version'))is int and type(lock.get('version'))is int and isinstance(old.get('pins'),list) and isinstance(lock.get('pins'),list),'resolver lock schema differs')
    observed={'sha256':sha(after),'origin_hash':{'present':'originHash'in lock,'value':lock.get('originHash')}}
    need(artifact.json('provenance/result.json')['lock']==observed,'captured lock result differs')
    pins={row['identity']:row for row in lock['pins']};need(len(pins)==len(lock['pins']),'duplicate resolved package identity')
    old_pins={row['identity']:row for row in old['pins']}
    need(len(old_pins)==len(old['pins']) and set(old_pins)==set(pins),'resolver package inventory changed')
    need(all(old_pins[n]==pins[n]for n in pins if n!='anisettekit'),'unrelated resolved package changed')
    need({k:v for k,v in old.items()if k not in ('pins','originHash')}=={k:v for k,v in lock.items()if k not in ('pins','originHash')},'resolver lock metadata changed')
    need(pins.get('anisettekit',{}).get('location')=='https://github.com/NRG-Wardog/AnisetteKit.git','resolved Anisette origin differs')
    need(pins.get('anisettekit',{}).get('state')=={'revision':inputs['owners']['AnisetteKit']['commit']},'resolved Anisette revision differs')
    return {**identity,'status':'CAPTURED_FOR_REVIEW','source_basis':admission.BASIS,'owner':owner,
        'purpose':'independently_reviewed_p0_cxx_allocation_swiftpm_resolution','anisette_commit':inputs['owners']['AnisetteKit']['commit'],
        'resolver_tested_commit':inputs['owners'][owner]['commit'],'resolver_tested_tree':inputs['owners'][owner]['tree'],
        'lock_before_sha256':sha(before),'lock_sha256':sha(after),'origin_hash':observed['origin_hash']}


def verify_stage_sources(inputs,repos,owners):
    registry=parse(p0.read(STAGE,'compatibility-registry.json'))
    need(sha(p0.read(STAGE,'compatibility-registry.json'))==inputs['contracts']['registry_sha256'],'staged registry drift')
    refs={registry['evidence']['path']:registry['evidence']['sha256'],
          **{row['manifest_path']:row['manifest_sha256']for row in registry['owners'].values()}}
    for name,expected in refs.items():need(sha(p0.read(STAGE,name))==expected,'staged contract source/evidence drift')
    manifest=parse(p0.read(STAGE,'anisette-generated/maintained-source-manifest.json'))
    prior=parse(p0.read(ROOT/historical.DIRECTORY,'anisette-generated/maintained-source-manifest.json'))
    need(manifest.get('source_basis')==admission.BASIS and manifest.get('anisettekit_revision')==owners['AnisetteKit']['commit'] and
         manifest.get('anisettekit_tree')==owners['AnisetteKit']['tree'],'staged Anisette identity differs')
    rows=manifest['files'];need(len(rows)==len({row['path']for row in rows}),'duplicate staged Anisette file')
    need({row['path']for row in prior['files']}|admission.ALLOCATION_FILES<={row['path']for row in rows},'staged Anisette source inventory incomplete')
    literals=manifest['required_compiled_evidence']['literals']
    need(all(row in literals for row in prior['required_compiled_evidence']['literals']) and
         set(admission.REQUIRED_DIAGNOSTIC_LITERALS)<={row.get('value')for row in literals},'staged compiled evidence missing')
    for owner,repo in repos.items():
        records=list(parse(p0.read(STAGE,registry['owners'][owner]['manifest_path']))['sources'])
        if owner=='AnisetteKit':records+=rows
        for row in records:
            relative(row['path']);ref=owners[owner]['commit']
            need(sha(git(repo,'show',ref+':'+row['path']))==row['sha256'],'staged published source bytes differ')
            raw_entry=git(repo,'ls-tree','-z',ref,'--',row['path'])
            need(raw_entry.count(b'\0')==1 and raw_entry.endswith(b'\0'),'staged published source entry differs')
            head,separator,path=raw_entry[:-1].partition(b'\t');entry=head.decode().split()
            need(separator and len(entry)==3 and entry[1]=='blob' and path.decode()==row['path'],'staged published source entry differs')
            need(entry[0]==row.get('mode','100644'),'staged published source mode differs')


def complete_definition(raw, signature):
    """Extract a whole C/C++ definition, ignoring braces in comments/literals."""
    token=signature.encode();need(raw.count(token)==1,'function signature is not unique')
    start=raw.index(token);opening=raw.index(b'{',start);depth=0;i=opening;state='code'
    while i<len(raw):
        pair=raw[i:i+2];ch=raw[i:i+1]
        if state=='line':
            if ch==b'\n':state='code'
        elif state=='comment':
            if pair==b'*/':state='code';i+=1
        elif state in ('string','char'):
            if ch==b'\\':i+=1
            elif ch==(b'"' if state=='string' else b"'"):state='code'
        elif pair==b'//':state='line';i+=1
        elif pair==b'/*':state='comment';i+=1
        elif ch==b'"':state='string'
        elif ch==b"'":state='char'
        elif ch==b'{':depth+=1
        elif ch==b'}':
            depth-=1
            if depth==0:return start,raw[start:i+1]
        i+=1
    raise ValueError('unterminated function definition')


def verify_fingerprints(artifact,inputs,repo):
    raw=artifact.raw('provenance/historical-source-fingerprints.json');value=parse(raw)
    historical_basis=inputs['historical_basis']
    need(historical_basis=={'capture_commit':'6edd04bdeca008faf0f5e3c56d0ff42ac097e7d8','role':'historical-only','fresh_guest_execution':False,
        'fingerprints_path':'ci/p0cxx/historical-source-fingerprints.json','fingerprints_sha256':sha(raw)}, 'historical input identity differs')
    need(set(value)=={'schema','role','historical_source','current_source','fresh_guest_execution','full_loader_changed','unchanged_files','unchanged_function_definitions'} and
        type(value['schema'])is int and value['schema']==1 and value['historical_source']==admission.PARENTS['AnisetteKit'] and value['current_source']==admission.AK_COMMIT and
        value['role']=='historical-only narrow byte identity; no current guest execution claim' and value['fresh_guest_execution']is False and value['full_loader_changed']is True,'historical fingerprint schema/claim differs')
    need(set(value['unchanged_files'])=={'Native/Loader/android_tls_runtime.h','Native/Loader/vm_initialization_diagnostics.h'}, 'historical TLS/diagnostic file coverage differs')
    definitions=value['unchanged_function_definitions'];path='Native/Loader/elf_loader_emulator.cpp'
    need(set(definitions)=={path} and set(definitions[path])=={'bool run_library_constructors('}, 'historical actual unchanged constructor coverage differs')
    for name,expected in value['unchanged_files'].items():
        before=git(repo,'show',value['historical_source']+':'+name);after=git(repo,'show',value['current_source']+':'+name)
        need(before==after and sha(after)==digest(expected),'historical complete source file changed')
    before=git(repo,'show',value['historical_source']+':'+path);after=git(repo,'show',value['current_source']+':'+path)
    need(before!=after,'bounds repair must change the full loader')
    for signature,row in definitions[path].items():
        need(set(row)=={'bytes','sha256','historical_offset','current_offset'} and all(type(row[k])is int and row[k]>=0 for k in ('bytes','historical_offset','current_offset')), 'historical slice schema differs')
        old_offset,old=complete_definition(before,signature);new_offset,new=complete_definition(after,signature)
        need(old_offset==row['historical_offset'] and new_offset==row['current_offset'] and old==new and len(new)==row['bytes'] and sha(new)==digest(row['sha256']), 'historical full function differs')
    need(artifact.json('provenance/historical-source-comparison.json')=={'status':'BYTE_IDENTITY_VERIFIED_ONLY','fingerprints_sha256':sha(raw),
         'historical_source':admission.PARENTS['AnisetteKit'],'current_source':admission.AK_COMMIT,'fresh_guest_execution':False,'full_loader_changed':True}, 'historical source comparison differs')
    return raw


def verify_historical_artifacts(artifacts,prior_root,prior):
    need(len(artifacts)==2,'both genuine historical artifacts required')
    expected=[parse(p0.read(prior_root,'SideSign-resolver.json')),prior['native_validation']]
    for artifact,row in zip(artifacts,expected):
        need(artifact.run['html_url']==row['run_url'] and artifact.run['run_attempt']==row['run_attempt'] and artifact.run['head_sha']==row['host_commit'] and
             artifact.artifact['id']==row['artifact_id'] and artifact.zip_sha256==row['artifact_sha256'] and sha(artifact.index_raw)==row['evidence_index_sha256'] and
             sha(artifact.raw('provenance/approved-inputs.json'))==row['approved_inputs_sha256'], 'historical artifact bytes differ from immutable review')


def build_candidate(phase1,phase2,runner_repo,approved_bytes,approved_sha,repos,final_side_commit,historical_artifacts,allocation):
    identity2,inputs2,suites=verify_phase2(phase2,runner_repo,approved_bytes,approved_sha)
    identity1,inputs1,helper=verify_phase1(phase1,runner_repo,inputs2)
    prior_root,prior,prior_receipt=historical.approval(ROOT)
    verify_historical_artifacts(historical_artifacts,prior_root,prior)
    old_runs=[prior['native_validation'],parse(p0.read(prior_root,'SideSign-resolver.json'))]
    need(all(new['run_url']!=old['run_url'] and new['artifact_id']!=old['artifact_id'] for new in (identity1,identity2) for old in old_runs),'historical artifact cannot substitute for new capture')
    need(identity1['run_url']!=identity2['run_url'] and identity1['artifact_id']!=identity2['artifact_id'],'two distinct capture phases required')
    prior_identity=phase2.json('provenance/prior-github-identity.json')
    need(prior_identity.get('run',{}).get('id')==phase1.run['id'] and prior_identity['run'].get('head_sha')==identity1['host_commit'] and
         prior_identity['run'].get('run_attempt')==identity1['run_attempt'] and prior_identity['run'].get('conclusion')=='success' and
         prior_identity.get('artifact',{}).get('id')==identity1['artifact_id'] and prior_identity['artifact'].get('digest')=='sha256:'+identity1['artifact_sha256'], 'phase2 observed prior GitHub identity differs')
    need(prior_identity.get('transport')=={'repository':REPOSITORY,'run_id':str(phase2.run['id']),'run_attempt':str(identity2['run_attempt']),'host_commit':identity2['host_commit']}, 'phase2 prior transport differs')
    need(phase2.json('provenance/prior-sidesign-link.json')=={'index_sha256':identity1['evidence_index_sha256'],'tested_commit':inputs1['owners']['SideSign']['commit'],
         'final_commit':inputs2['owners']['SideSign']['commit'],'lock_only_descendant':True,'host_metadata_units_reused_for_exact_unchanged_AK':True,'guest_execution':False,
         'prior_run':phase1.json('provenance/result.json')['run']}, 'phase2 exact prior link differs')
    owners=copy.deepcopy(prior['owners']);inventory=load(prior_root/'source-inventory.json');transitions={}
    need(set(repos)==admission.CHANGED_OWNERS,'exact three changed-owner Git repositories required')
    fullsha(final_side_commit);need(final_side_commit!=inputs2['owners']['SideStore']['commit'],'actual final SideStore lock-only descendant required')
    for owner in admission.PARENTS:
        row=inputs2['owners'][owner];final=final_side_commit if owner=='SideStore'else row['commit']
        if owner in repos:
            repo=repos[owner];observed=snapshot(repo,admission.PARENTS[owner],row['commit'])
            need(observed['tree']==row['tree'] and observed['changes']==row['changes'],'actual reviewed Git source differs')
            inventory[owner]=snapshot(repo,BASES[owner],final);transitions[owner]=snapshot(repo,admission.PARENTS[owner],final)
            git(repo,'merge-base','--is-ancestor',admission.PARENTS[owner],final)
            need(all(len(line.split())==2 for line in git(repo,'rev-list','--parents',admission.PARENTS[owner]+'..'+final).decode().splitlines()),'merged source lane')
            owners[owner].update(commit=final,tree=inventory[owner]['tree'])
        else:
            need(row['commit']==admission.PARENTS[owner] and row['tree']==owners[owner]['tree'] and not row['changes'],'unrelated source drift')
            transitions[owner]={'baseline_commit':row['commit'],'commit':row['commit'],'tree':row['tree'],'changes':{}}
    admission.validate_delta(transitions, {'owners':owners}, load(STAGE/'reviewed-ak-manifest.json'))
    need(git(repos['AnisetteKit'],'rev-list','--parents','--max-count=1',admission.AK_COMMIT).decode().split()==[admission.AK_COMMIT,admission.PARENTS['AnisetteKit']], 'C++ AK must be direct successor')
    admission.verify_manifest_pin(git(repos['SideSign'],'show',admission.PARENTS['SideSign']+':Package.swift'),git(repos['SideSign'],'show',owners['SideSign']['commit']+':Package.swift'))
    verify_stage_sources(inputs2,repos,owners)
    fingerprints=verify_fingerprints(phase1,inputs1,repos['AnisetteKit'])
    need(verify_fingerprints(phase2,inputs2,repos['AnisetteKit'])==fingerprints,'phases disagree on historical source fingerprints')
    resolvers={}
    for owner,artifact,identity,inputs in (('SideSign',phase1,identity1,inputs1),('SideStore',phase2,identity2,inputs2)):
        resolver=resolver_receipt(artifact,identity,inputs,owner);repo=repos[owner];lock=admission.LOCK_FILES[owner]
        need(git(repo,'show',resolver['resolver_tested_commit']+':'+lock)==artifact.raw('provenance/Package.resolved.before'),'resolver original Git bytes differ')
        need(git(repo,'show',owners[owner]['commit']+':'+lock)==artifact.raw('provenance/Package.resolved.after'),'final lock differs from genuine captured bytes')
        git(repo,'merge-base','--is-ancestor',resolver['resolver_tested_commit'],owners[owner]['commit'])
        need(set(snapshot(repo,resolver['resolver_tested_commit'],owners[owner]['commit'])['changes'])=={lock},'resolver descendant is not exactly lock-only')
        resolvers[owner]=resolver
    children=copy.deepcopy(inputs2['submodules'])
    for owner in ('SideSign','minimuxer'):
        row=owners[owner];children['SideStore/Dependencies/'+owner]={'repository':row['repository'],'declared_repository':row['repository'],'commit':row['commit'],'tree':row['tree']}
    old_children=p0.child_map(prior_root);need(set(children)==set(old_children),'focused child closure changed')
    for name in set(children)-{'SideStore/Dependencies/SideSign'}:need(children[name]==old_children[name],'unrelated child changed')
    phase_fields=(*admission.RUN_FIELDS,'resolver_tested_commit','resolver_tested_tree','lock_before_sha256','lock_sha256','origin_hash')
    native={**identity2,'source_basis':admission.BASIS,'inherited_bounds_review_sha256':historical.REVIEW_SHA256,
        'readiness_scope':'candidate_for_gated_full_build_review','review_sha256':None,
        'tested_owners':{o:{k:row[k]for k in ('commit','tree')}for o,row in inputs2['owners'].items()},
        'tested_children':{'SideSign':{},'SideStore':{'Dependencies/SideSign':owners['SideSign']['commit'],'Dependencies/minimuxer':owners['minimuxer']['commit']}}}
    consumers={'tested_owners':{o:native['tested_owners'][o]for o in ('AnisetteKit','SideStore','LiveContainer')}}
    for key,suite_id in (('init_diagnostics','initializer-diagnostics'),('adi_consumption','adi-consumption')):
        report=suites[suite_id];consumers[key]={'tests':report['tests'],'failures':report['failures'],'errors':report['errors'],'skipped':report['skips'],'test_ids':sorted(report['test_ids'])}
    historical_evidence={'role':'historical-only','review_sha256':historical.REVIEW_SHA256,'native_receipt_sha256':sha(p0.read(prior_root,'native-receipt.json')),'fresh_guest_execution':False,'full_loader_changed':True}
    helper_bytes=phase1.raw('metadata-units/result.json')
    receipt={'status':'CAPTURED_FOR_INDEPENDENT_REVIEW','source_basis':admission.BASIS,'native_validation':native,
        'native_builds':{'SideSign':'PASS_MACOS_BUILD_AND_TEST','SideStore':'PASS_UNSIGNED_IPHONEOS','LiveContainer':'PASS_UNSIGNED_IPHONEOS'},
        'metadata_helper':{'source_owner':admission.AK_COMMIT,'source_tree':admission.AK_TREE,'receipt_sha256':sha(helper_bytes),'checks_per_variant':713,'variants':['normal','optimized','sanitized'],'skipped':0},
        'historical_evidence':historical_evidence,'ordinary_native_boundary':{'checks':8,'failures':0,'skipped':0},'consumer_suites':consumers,
        'phase_evidence':{o:{'status':'CAPTURED_FOR_REVIEW',**{k:r[k]for k in phase_fields}}for o,r in resolvers.items()},
        'tested_submodules':children,'guest_execution':False,'ADI_executed':False,'device_validation':False}
    allocation_bytes, allocation_run = allocation
    need(allocation_run['source_commit']==admission.AK_COMMIT and allocation_run['source_tree']==admission.AK_TREE, 'Linux allocation source differs')
    allocation_run_bytes=dump_bytes(allocation_run)
    receipt['allocation_helper']={'source_owner':admission.AK_COMMIT,'source_tree':admission.AK_TREE,
        'receipt_sha256':sha(allocation_bytes),'run_sha256':sha(allocation_run_bytes),'checks_per_variant':83,
        'variants':['normal','optimized','sanitized'],'skipped':0}
    staged={'compatibility-registry.json','provenance/source-evidence.json','validate_contracts.py',
        'anisette-generated/maintained-source-manifest.json','reviewed-ak-manifest.json',
        'historical-bounds-approval.json','historical-bounds-pins.json'}
    staged|={'owners/'+owner+'/runtime-contract.json' for owner in admission.PARENTS}
    files={name:p0.read(STAGE,name) for name in staged}
    need(sha(files['reviewed-ak-manifest.json'])==admission.MANIFEST_SHA256, 'exact C++ source manifest differs')
    need(files['historical-bounds-approval.json']==p0.read(prior_root,'reviewed-approval.json'), 'historical bounds review differs')
    historical.validate_pins(parse(files['historical-bounds-pins.json']),prior)
    for name in ['validate_contracts.py', *('owners/'+owner+'/runtime-contract.json' for owner in set(admission.PARENTS)-{'AnisetteKit'})]:
        need(files[name]==p0.read(prior_root,name), 'unrelated historical contract changed')
    files['allocation-helper.json']=allocation_bytes
    files['allocation-helper-run.json']=allocation_run_bytes
    files['metadata-helper.json']=helper_bytes
    files['historical-evidence.json']=dump_bytes(historical_evidence)
    for name,value in {'source-transition.json':transitions,'source-inventory.json':inventory,'submodules.json':children,'native-receipt.json':receipt,
                      **{owner+'-resolver.json':row for owner,row in resolvers.items()}}.items():files[name]=dump_bytes(value)
    for owner,artifact in (('SideSign',phase1),('SideStore',phase2)):
        for suffix in ('before','after'):files['resolver-locks/'+owner+'.Package.resolved.'+suffix]=artifact.raw('provenance/Package.resolved.'+suffix)
    review={'schema_version':1,'source_basis':admission.BASIS,'integration_parent':admission.INTEGRATION_PARENT,'inherited_bounds_review_sha256':historical.REVIEW_SHA256,
            'owners':owners,'files':{n:sha(b)for n,b in sorted(files.items())},'native_validation':native}
    summary={'schema_version':1,'status':'CANDIDATE_AWAITING_INDEPENDENT_REVIEW','source_basis':admission.BASIS,'activated':False,'guest_execution':False,'ADI_executed':False,'device_validation':False,
             'phase1':identity1,'phase2':identity2,'github_metadata_sha256':{'phase1':phase1.metadata_sha256,'phase2':phase2.metadata_sha256},
             'suites':suites,'exact_phase2_tests':42,'candidate_review_sha256':sha(dump_bytes(review))}
    return files,review,summary


def validate_output(path):
    output=Path(path).absolute()
    need(not output.exists() and not output.is_symlink(),'output must be fresh')
    need(output.parent.is_dir(),'output parent must already exist')
    need(not any(parent.is_symlink()for parent in output.parents),'output parent cannot be linked')
    need(output.resolve()!=ROOT and ROOT not in output.resolve().parents,'output must be outside the builder')
    return output


def write_candidate(output,files,review,summary):
    output=validate_output(output)
    with tempfile.TemporaryDirectory(prefix='.p0-review-candidate-',dir=output.parent)as temp:
        staging=Path(temp)/'candidate';staging.mkdir()
        for name,data in sorted(files.items()):
            relative(name);path=staging/'metadata'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            need(sha(path.read_bytes())==review['files'][name],'candidate write verification failed')
        (staging/'candidate-review.json').write_bytes(dump_bytes(review))
        (staging/'candidate-summary.json').write_bytes(dump_bytes(summary))
        need(not output.exists(),'output appeared during validation')
        staging.rename(output)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for phase in ('phase1','phase2','allocation'):
        for name in ('zip','run-json','artifact-json'):parser.add_argument('--'+phase+'-'+name,type=Path,required=True)
    parser.add_argument('--runner-repo',type=Path,required=True);parser.add_argument('--approved-inputs',type=Path,required=True)
    parser.add_argument('--approved-inputs-sha256',required=True);parser.add_argument('--allocation-approved-inputs',type=Path,required=True);parser.add_argument('--allocation-approved-inputs-sha256',required=True);parser.add_argument('--owner',action='append',default=[],metavar='NAME=/repo',required=True)
    parser.add_argument('--historical-directory',type=Path,required=True);parser.add_argument('--final-sidestore-commit',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);artifacts=[];old_artifacts=[]
    try:
        output=validate_output(args.output)
        repos={}
        for value in args.owner:
            name,path=value.split('=',1);need(name not in repos,'duplicate source owner');repos[name]=Path(path)
        for phase in ('phase1','phase2'):artifacts.append(Artifact(getattr(args,phase+'_zip'),getattr(args,phase+'_run_json'),getattr(args,phase+'_artifact_json')))
        import finalize_p0_elf_metadata_bounds as old_finalizer
        for run in ('39123382040','39124892210'):
            old_artifacts.append(old_finalizer.Artifact(args.historical_directory/(run+'.zip'),args.historical_directory/(run+'-github-run.json'),args.historical_directory/(run+'-github-artifact.json')))
        allocation_artifact=LinuxArtifact(args.allocation_zip,args.allocation_run_json,args.allocation_artifact_json)
        old_artifacts.append(allocation_artifact)
        allocation=verify_allocation_artifact(allocation_artifact,args.runner_repo,args.allocation_approved_inputs.read_bytes(),args.allocation_approved_inputs_sha256)
        files,review,summary=build_candidate(*artifacts,args.runner_repo,args.approved_inputs.read_bytes(),args.approved_inputs_sha256,repos,args.final_sidestore_commit,old_artifacts[:2],allocation)
        write_candidate(output,files,review,summary)
        print(json.dumps(summary,sort_keys=True));return 0
    except (ValueError,OSError,KeyError,TypeError,zipfile.BadZipFile,subprocess.CalledProcessError)as error:
        print(json.dumps({'status':'REJECTED','error':str(error)},sort_keys=True),file=sys.stderr);return 1
    finally:
        for artifact in [*artifacts,*old_artifacts]:artifact.close()


class LinuxArtifact(Artifact):
    workflow = '.github/workflows/p0-cxx-allocation-linux.yml'
    branch = 'validation/p0-cxx-allocation-linux'


# Independently derived from the checksum-verified immutable app fork archive.
# Exact equality rejects missing, added, renamed, or replaced external headers.
ALLOCATION_HEADER_SHA256 = {'arm.h': 'de68a0b8f91035baa32cf76fdbd677a16dac433e93c75b084ecd0eeef67be7de', 'arm64.h': '7fc1a00cfe6234ea8859026e4e27cb625e4cecedbc95a06bec9c694137831981', 'm68k.h': '1511ee296557a3de03f71e25b1378761dc7213bfb2f5b2df7aa2d3ef8722b449', 'mips.h': 'f6a8d3a5056330a7431954cfe19ec1b4927fa0e8f0a9d83ceac8aeeb98a2a030', 'platform.h': '10ec4c482940d039fcbbf2f7e5791d3dc3f991f0a3c5b16e22f378ae1829fdde', 'ppc.h': '2645d9f4448ab0c1ff28d2096262335d53a9479f210567d546cd76de85dbd530', 'riscv.h': 'c2efc0644c236feca3f17e2957a28149498b9f6e06772ced7e11204797fd0327', 's390x.h': '43ba5d9eed2495ae129da50a8e3f59902d05dc4601bfad12d2b3f4454953d0b4', 'sparc.h': 'f30427a1738b7bc9c46dd00cee2097ae375f95ad8db9c386f3d61a14574b3624', 'tricore.h': '053f337160ab3b0cb29bc564e301950fed4c2f93bde50918a3d428b85d0a3f45', 'unicorn.h': '66fdf8f39df34e507a163eb550549669cec3399c2103b8dc84bc4131f600e55a', 'x86.h': '6b01b368f4d1bd0e0ac93a6107cd2edb3b5abfef578f94bf82a00e0ae34ed76f'}
ALLOCATION_FORK_URL = 'https://github.com/mahee96/unicorn/releases/download/2.1.4-multiarch/Unicorn.xcframework.zip'
ALLOCATION_FORK_SHA256 = '4f61907db6aafc56fb3e336b524d742342312f498bb40739f1da55fb4a24614a'


def verify_allocation_headers(headers):
    need(isinstance(headers,dict) and set(headers)=={'url','archive_sha256','headers_sha256','engine_linked','engine_executed'} and
         headers['url']==ALLOCATION_FORK_URL and headers['archive_sha256']==ALLOCATION_FORK_SHA256 and
         headers['engine_linked']is False and headers['engine_executed']is False and
         headers['headers_sha256']==ALLOCATION_HEADER_SHA256, 'allocation exact engine-header provenance differs')


def capture_absolute_path(value):
    need(isinstance(value,str) and value.startswith('/') and not value.startswith('//') and
         '\\' not in value and all(part not in ('','.','..') for part in value[1:].split('/')), 'allocation capture path is not canonical absolute')
    return Path(value)


def verify_allocation_context(artifact):
    """Link host commands to the retained acquisition, extraction and invocation."""
    def command(label):
        value=artifact.json('logs/'+label+'.command.json')
        need(isinstance(value,dict) and set(value)=={'argv','cwd'} and value['cwd']is None and
             isinstance(value['argv'],list) and value['argv'] and all(isinstance(arg,str)for arg in value['argv']), 'allocation capture command schema differs')
        return value['argv']
    prefix=['git','--no-replace-objects','-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false']
    checkout=command('checkout-anisettekit')
    need(len(checkout)==len(prefix)+5 and checkout[:len(prefix)]==prefix and checkout[len(prefix)]=='-C' and
         checkout[-3:]==['checkout','--detach',admission.AK_COMMIT], 'allocation acquired checkout command differs')
    source=capture_absolute_path(checkout[len(prefix)+1])
    need(source.name=='AnisetteKit' and source.parent.name=='sources', 'allocation acquired source location differs')
    work=source.parent.parent
    repository='https://github.com/NRG-Wardog/AnisetteKit.git'
    need(command('fetch-anisettekit')==prefix+['clone','--no-checkout',repository,str(source)], 'allocation source clone target differs')
    need(command('fetch-exact-anisettekit')==prefix+['-C',str(source),'fetch','--no-tags','--no-recurse-submodules','origin',admission.AK_COMMIT], 'allocation exact source fetch differs')
    need(command('disable-push-anisettekit')==prefix+['-C',str(source),'config','remote.origin.pushurl','disabled://p0-capture-readonly'], 'allocation source push restriction differs')
    headers=work/'results/unicorn-headers';archive=work/'results/Unicorn.xcframework.zip'
    need(command('unicorn-header-download')==['curl','--fail','--location','--proto','=https','--proto-redir','=https','--output',str(archive),ALLOCATION_FORK_URL], 'allocation verified header download destination differs')
    argv=command('allocation-units')
    need(len(argv)==12, 'allocation exact driver invocation differs')
    interpreter=capture_absolute_path(argv[5]);output=capture_absolute_path(argv[-1])
    need(output.name=='allocation-units', 'allocation output location differs')
    need(command('python-version')==[str(interpreter),'--version'], 'allocation interpreter differs from captured toolchain')
    need(argv==['sudo','-n','unshare','--net','--',str(interpreter),'-B',str(source/'.ci/native-tests/test_cxx_allocation.py'),
                '--unicorn-include',str(headers),'--output',str(output)], 'allocation source/header/output invocation binding differs')
    verify_allocation_headers(artifact.json('provenance/unicorn-headers.json'))
    return str(source),str(headers),str(output)


def verify_allocation_commands(artifact, helper):
    """Bind each captured output to the exact reviewed host-only compile/run graph."""
    source_root,header_root,output_root=verify_allocation_context(artifact)
    for variant,flags in [('normal',[]),('optimized',['-O2','-DNDEBUG']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all','-fno-omit-frame-pointer'])]:
        arrays={kind:artifact.json('allocation-units/'+variant+suffix+'.json') for kind,suffix in [('allocator',''),('hooks','-hooks')]}
        need(isinstance(arrays['allocator'],list) and len(arrays['allocator'])==3 and isinstance(arrays['hooks'],list) and len(arrays['hooks'])==2, 'allocation raw command counts differ')
        for kind,commands in arrays.items():
            for row in commands:
                need(set(row)=={'command','exit_code','stdout','stderr'} and type(row['exit_code'])is int and row['exit_code']==0 and
                     row['stderr']=='' and isinstance(row['stdout'],str) and isinstance(row['command'],list) and row['command'] and
                     all(isinstance(arg,str) for arg in row['command']), 'allocation raw command failure/schema differs')
            expected=helper['results'][variant][kind]
            actual=parse(commands[-1]['stdout'])
            need(actual==expected and all(type(actual[k])is type(v)for k,v in expected.items()), 'allocation raw output differs')
        compile,link,execute=[row['command']for row in arrays['allocator']]
        hook,hook_execute=[row['command']for row in arrays['hooks']]
        prefix=['c++','-std=c++17','-ffunction-sections','-fdata-sections',*flags]
        need(compile[:len(prefix)]==prefix and compile.count('-c')==1 and compile.count('-o')==1, 'allocation compiler configuration differs')
        cut=compile.index('-c');common=compile[:cut]
        includes=common[len(prefix):]
        need(len(includes)==8 and includes[::2]==['-I']*4, 'allocation exact header include inventory differs')
        loader=compile[cut+1]
        need(loader.endswith('/Native/Loader/elf_loader_emulator.cpp') and compile[cut+2]=='-o' and len(compile)==cut+4, 'allocation actual loader not compiled')
        need(loader==source_root+'/Native/Loader/elf_loader_emulator.cpp', 'allocation actual source differs from acquired checkout')
        need(includes[1::2]==[source_root+'/Native/Loader',source_root+'/Native',source_root+'/Native/include',header_root], 'allocation verified repository/header include roots differ')
        obj=compile[-1];binary=obj[:-2] if obj.endswith('.o') else ''
        need(binary==output_root+'/'+variant, 'allocation variant output differs from captured driver destination')
        need(link==common+['-Wl,--gc-sections',source_root+'/.ci/native-tests/fixtures/cxx_allocation_unit.cpp',obj,'-o',binary], 'allocation host-only link graph differs')
        need(execute==[binary] and hook==common+[source_root+'/.ci/native-tests/fixtures/cxx_allocation_hook_unit.cpp','-o',binary+'-hooks'] and hook_execute==[binary+'-hooks'], 'allocation actual hook compile/run graph differs')


def verify_allocation_artifact(artifact,runner_repo,approved_bytes,approved_sha):
    need(sha(approved_bytes)==digest(approved_sha) and artifact.raw('provenance/approved-inputs.json')==approved_bytes, 'Linux artifact input differs from independently approved bytes')
    inputs=parse(approved_bytes)
    identity=artifact.identity(runner_repo)
    need(inputs.get('source_basis')==admission.BASIS and inputs.get('integration_baseline')==admission.BASELINE and inputs.get('phase')=='allocation', 'Linux capture basis differs')
    need(set(inputs['owners'])=={'AnisetteKit'} and not inputs['submodules'] and not inputs['test_suites'] and inputs['contracts']is None and inputs['prior_sidesign']is None, 'Linux capture scope differs')
    owner=inputs['owners']['AnisetteKit'];manifest=load(STAGE/'reviewed-ak-manifest.json')
    need(owner['commit']==admission.AK_COMMIT and owner['tree']==admission.AK_TREE and owner['changes']==manifest['changes'] and owner['repository']=='https://github.com/NRG-Wardog/AnisetteKit.git', 'Linux actual reviewed AK source differs')
    verify_runner_files(inputs,runner_repo,identity['host_commit'])
    result=artifact.json('provenance/result.json')
    need(result['platform']=='linux' and result['source_commit']==admission.AK_COMMIT and result['source_tree']==admission.AK_TREE and result['source_sha256']==admission.ALLOCATION_HELPER_SOURCES and result['owners']==inputs['owners'], 'Linux captured source binding differs')
    before=artifact.json('provenance/source-before.json');after=artifact.json('provenance/source-after.json')
    need(before==after and set(before)=={'AnisetteKit'}, 'Linux source changed during host capture')
    proof=before['AnisetteKit']
    need(proof['commit']==admission.AK_COMMIT and proof['tree']==admission.AK_TREE and proof['origin']=={'kind':'direct','repository':owner['repository']} and proof.get('submodules')==[] and type(proof.get('tracked_entries'))is int and proof['tracked_entries']>0, 'Linux source checkout proof differs')
    raw=artifact.raw('provenance/allocation-helper.json')
    need(raw==artifact.raw('allocation-units/result.json'), 'Linux raw driver result differs')
    helper=admission.validate_allocation_helper(parse(raw),admission.ALLOCATION_HELPER_SOURCES)
    verify_allocation_commands(artifact,helper)
    for label in required_command_names(inputs):artifact.command(label)
    return raw,{**identity,'source_commit':admission.AK_COMMIT,'source_tree':admission.AK_TREE,'platform':'linux','source_sha256':admission.ALLOCATION_HELPER_SOURCES}


if __name__=='__main__':raise SystemExit(main())
