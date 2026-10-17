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
import p0_constructor_diagnostics as admission
import p0_maintained_sources as p0
from prepare_candidate_inventory import BASES, snapshot, git
from p0_maintained_sources import need, parse, digest, fullsha, relative, sha

REPOSITORY='NRG-Wardog/LiveContainer'
WORKFLOW='.github/workflows/p0-constructor-diagnostics.yml'
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
    def __init__(self,zip_path,run_path,artifact_path):
        self.run=load(run_path);self.artifact=load(artifact_path)
        run=self.run;artifact=self.artifact
        need(run.get('status')=='completed' and run.get('conclusion')=='success','GitHub run is not completed/success')
        need(type(run.get('id'))is int and run['id']>0 and type(run.get('run_attempt'))is int and run['run_attempt']>0,'invalid GitHub run identity')
        need(run.get('repository',{}).get('full_name')==REPOSITORY and run.get('path')==WORKFLOW and run.get('event')in ('push','workflow_dispatch') and run.get('head_branch')=='validation/p0-constructor-diagnostics','wrong GitHub repository/workflow/event')
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
        need(self.raw('logs/'+name+'.exit-code.txt')==b'0\n','failed command: '+name)
        return self.raw('logs/'+name+'.log').decode('utf-8',errors='strict')
    def identity(self,runner_repo):
        result=self.json('provenance/result.json');inputs=self.raw('provenance/approved-inputs.json')
        tree=git(runner_repo,'rev-parse',self.run['head_sha']+'^{tree}').decode().strip()
        expected={'url':self.run['html_url'],'attempt':self.run['run_attempt'],'host_commit':self.run['head_sha'],'host_tree':tree}
        need(result.get('run')==expected and result.get('approved_inputs_sha256')==sha(inputs),'capture identity/input hash differs')
        need(result.get('status')=='CAPTURED_FOR_REVIEW' and result.get('last_stage')=='captured' and result.get('source_basis')==admission.BASIS,'capture failed/incomplete')
        need(all(result.get(k)is False for k in ('production_ready','ipa_built','ADI_executed','device_validation')),'capture makes unsupported runtime/readiness claims')
        return {'run_url':self.run['html_url'],'run_attempt':self.run['run_attempt'],'host_commit':self.run['head_sha'],'host_tree':tree,
          'artifact_id':self.artifact['id'],'artifact_sha256':self.zip_sha256,'approved_inputs_sha256':sha(inputs),'evidence_index_sha256':sha(self.index_raw)}


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


def verify_runner_files(inputs,runner_repo,commit):
    files=dict(inputs['adapter_files'])
    for suite in inputs['test_suites']:
        for name,expected in suite['files'].items():
            need(name not in files or files[name]==expected,'contradictory sealed runner file')
            files[name]=expected
    if inputs.get('contracts'):
        for key in ('registry','validator'):files[inputs['contracts'][key]]=inputs['contracts'][key+'_sha256']
    for name,expected in files.items():
        relative(name);need(sha(git(runner_repo,'show',commit+':'+name))==digest(expected),'published runner file differs: '+name)


def verify_phase1(artifact,runner_repo,phase2_inputs):
    identity=artifact.identity(runner_repo);inputs=artifact.json('provenance/approved-inputs.json')
    expected=phase2_inputs['prior_sidesign']
    need({'index_sha256':identity['evidence_index_sha256'],'run_id':artifact.run['id'],'run_attempt':identity['run_attempt'],
          'host_commit':identity['host_commit'],'artifact_id':identity['artifact_id'],'artifact_sha256':identity['artifact_sha256']}==expected,'phase1 identity differs from sealed phase2 link')
    need(inputs.get('phase')=='sidesign' and inputs['owners']['AnisetteKit']==phase2_inputs['owners']['AnisetteKit'],'phase1 source/phase differs')
    verify_runner_files(inputs,runner_repo,identity['host_commit']);verify_source_reports(artifact,inputs)
    for name in ('sidesign-resolve','sidesign-frozen-resolve','sidesign-build','sidesign-tests','native-fork83-compile','constructor-diagnostics-compile','anisette-native-boundary'):artifact.command(name)
    ak=inputs['owners']['AnisetteKit'];source_hashes={n:v['after']['sha256']for n,v in ak['changes'].items()}
    need(artifact.command('native-fork83-run')=='PASS 83 full-production-loader synthetic checks; no ADI executed\n' and artifact.raw('logs/native-fork83-run.stderr.log')==b'','canonical83 failed or emitted stderr')
    native=artifact.json('provenance/native-fork83.json');counts(native,{'checks':83},'canonical83')
    digest(native.get('library_sha256'))
    need(native.get('archive_sha256')==p0.FORK_SHA256 and native.get('source_sha256')==source_hashes and native.get('sanitizers')==['address','undefined'] and native.get('ADI_executed')is False,'canonical83 source/engine differs')
    constructor=parse(artifact.command('constructor-diagnostics-run'));observed=artifact.json('provenance/constructor-diagnostics.json')
    counts(constructor,{'schema':1,'checks':2105,'failures':0,'skipped':0},'constructor')
    need(set(constructor)=={'schema','checks','failures','skipped','case_ids','ADI_executed','device_validation'} and constructor['case_ids']==list(admission.REQUIRED_DIAGNOSTIC_CASES),'constructor exact cases differ')
    need(constructor['ADI_executed']is False and constructor['device_validation']is False and artifact.raw('logs/constructor-diagnostics-run.stderr.log')==b'','constructor unsupported claim/stderr')
    need(all(observed.get(k)==v for k,v in constructor.items()) and observed.get('source_owner')==ak['commit'] and observed.get('source_sha256')==source_hashes and
         observed.get('engine_archive_sha256')==p0.FORK_SHA256 and observed.get('library_sha256')==native.get('library_sha256') and observed.get('sanitizers')==['address','undefined'],'constructor native binding differs')
    for name in ('native-fork83','constructor-diagnostics'):
        args=artifact.json('logs/'+name+'-compile.command.json')['argv'];need('-fsanitize=address,undefined'in args and '-fno-sanitize-recover=all'in args,'fail-fast sanitizer compile flags absent')
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
    return identity,inputs,observed


def verify_phase2(artifact,runner_repo,approved_bytes,approved_sha):
    need(sha(approved_bytes)==digest(approved_sha),'external approved input hash differs')
    need(artifact.raw('provenance/approved-inputs.json')==approved_bytes,'artifact input differs from independently approved bytes')
    inputs=parse(approved_bytes);identity=artifact.identity(runner_repo)
    need(inputs.get('source_basis')==admission.BASIS and inputs.get('integration_baseline')==admission.INTEGRATION_PARENT and inputs.get('phase')=='sidestore','wrong phase2 input basis')
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
    counts(contract,{'source_files':95,'contracts':13,'edges':29},'contract graph')
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
        'purpose':'independently_reviewed_p0_constructor_diagnostics_swiftpm_resolution','anisette_commit':inputs['owners']['AnisetteKit']['commit'],
        'resolver_tested_commit':inputs['owners'][owner]['commit'],'resolver_tested_tree':inputs['owners'][owner]['tree'],
        'lock_before_sha256':sha(before),'lock_sha256':sha(after),'origin_hash':observed['origin_hash']}


def verify_stage_sources(inputs,repos,owners):
    registry=parse(p0.read(STAGE,'compatibility-registry.json'))
    need(sha(p0.read(STAGE,'compatibility-registry.json'))==inputs['contracts']['registry_sha256'],'staged registry drift')
    refs={registry['evidence']['path']:registry['evidence']['sha256'],
          **{row['manifest_path']:row['manifest_sha256']for row in registry['owners'].values()}}
    for name,expected in refs.items():need(sha(p0.read(STAGE,name))==expected,'staged contract source/evidence drift')
    manifest=parse(p0.read(STAGE,'anisette-generated/maintained-source-manifest.json'))
    prior=parse(p0.read(ROOT/p0.DIRECTORY,'anisette-generated/maintained-source-manifest.json'))
    need(manifest.get('source_basis')==admission.BASIS and manifest.get('anisettekit_revision')==owners['AnisetteKit']['commit'] and
         manifest.get('anisettekit_tree')==owners['AnisetteKit']['tree'],'staged Anisette identity differs')
    rows=manifest['files'];need(len(rows)==len({row['path']for row in rows}),'duplicate staged Anisette file')
    need({row['path']for row in prior['files']}|{'Native/Loader/vm_initialization_diagnostics.h'}<={row['path']for row in rows},'staged Anisette source inventory incomplete')
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


def build_candidate(phase1,phase2,runner_repo,approved_bytes,approved_sha,repos,final_side_commit):
    identity2,inputs2,suites=verify_phase2(phase2,runner_repo,approved_bytes,approved_sha)
    identity1,inputs1,constructor=verify_phase1(phase1,runner_repo,inputs2)
    prior_identity=phase2.json('provenance/prior-github-identity.json')
    need(prior_identity.get('run',{}).get('id')==phase1.run['id'] and prior_identity['run'].get('head_sha')==identity1['host_commit'] and
         prior_identity['run'].get('run_attempt')==identity1['run_attempt'] and prior_identity['run'].get('conclusion')=='success' and
         prior_identity.get('artifact',{}).get('id')==identity1['artifact_id'] and prior_identity['artifact'].get('digest')=='sha256:'+identity1['artifact_sha256'],
         'phase2 observed prior GitHub identity differs')
    need(prior_identity.get('transport')=={'repository':REPOSITORY,'run_id':str(phase2.run['id']),
         'run_attempt':str(identity2['run_attempt']),'host_commit':identity2['host_commit']},'phase2 prior-identity transport differs')
    need(phase2.json('provenance/prior-sidesign-link.json')=={'index_sha256':identity1['evidence_index_sha256'],
         'tested_commit':inputs1['owners']['SideSign']['commit'],'final_commit':inputs2['owners']['SideSign']['commit'],
         'lock_only_descendant':True,'native83_reused_for_exact_unchanged_AK':True,'prior_run':phase1.json('provenance/result.json')['run']},
         'phase2 exact prior native link differs')
    p0root,prior,_=p0.approval(ROOT);owners=copy.deepcopy(prior['owners']);inventory=load(p0root/'source-inventory.json');transitions={}
    need(set(repos)==admission.CHANGED_OWNERS,'four changed-owner Git repositories required')
    fullsha(final_side_commit);need(final_side_commit!=inputs2['owners']['SideStore']['commit'],'published final SideStore lock-only successor required')
    for owner in admission.PARENTS:
        row=inputs2['owners'][owner];final=final_side_commit if owner=='SideStore'else row['commit']
        if owner in repos:
            repo=repos[owner]
            observed=snapshot(repo,admission.PARENTS[owner],row['commit']);need(observed['tree']==row['tree'] and observed['changes']==row['changes'],'actual reviewed candidate Git source differs')
            inventory[owner]=snapshot(repo,BASES[owner],final);transitions[owner]=snapshot(repo,admission.PARENTS[owner],final)
            git(repo,'merge-base','--is-ancestor',admission.PARENTS[owner],final)
            need(all(len(line.split())==2 for line in git(repo,'rev-list','--parents',admission.PARENTS[owner]+'..'+final).decode().splitlines()),'merged diagnostic source lane')
            owners[owner].update(commit=final,tree=inventory[owner]['tree'])
        else:
            need(row['commit']==admission.PARENTS[owner] and row['tree']==owners[owner]['tree'] and not row['changes'],'transport source drift')
            transitions[owner]={'baseline_commit':row['commit'],'commit':row['commit'],'tree':row['tree'],'changes':{}}
    verify_stage_sources(inputs2,repos,owners)
    resolvers={}
    for owner,artifact,identity,inputs in (('SideSign',phase1,identity1,inputs1),('SideStore',phase2,identity2,inputs2)):
        resolver=resolver_receipt(artifact,identity,inputs,owner);repo=repos[owner];lock=admission.LOCK_FILES[owner]
        need(git(repo,'show',resolver['resolver_tested_commit']+':'+lock)==artifact.raw('provenance/Package.resolved.before'),'resolver initial Git bytes differ')
        need(git(repo,'show',owners[owner]['commit']+':'+lock)==artifact.raw('provenance/Package.resolved.after'),'published final lock is not captured output')
        git(repo,'merge-base','--is-ancestor',resolver['resolver_tested_commit'],owners[owner]['commit'])
        need(set(snapshot(repo,resolver['resolver_tested_commit'],owners[owner]['commit'])['changes'])=={lock},'final resolver descendant is not exactly lock-only')
        resolvers[owner]=resolver
    children=copy.deepcopy(inputs2['submodules'])
    for owner in ('SideSign','minimuxer'):
        row=owners[owner];children['SideStore/Dependencies/'+owner]={'repository':row['repository'],'declared_repository':row['repository'],'commit':row['commit'],'tree':row['tree']}
    old_children=p0.child_map(p0root);need(set(children)==set(old_children),'focused child closure changed')
    for name in set(children)-{'SideStore/Dependencies/SideSign'}:need(children[name]==old_children[name],'unrelated child identity changed')
    phase_fields=(*admission.RUN_FIELDS,'resolver_tested_commit','resolver_tested_tree','lock_before_sha256','lock_sha256','origin_hash')
    native={**identity2,'source_basis':admission.BASIS,'inherited_p0_review_sha256':p0.P0_REVIEW_SHA256,
        'readiness_scope':'candidate_for_gated_full_build_review','review_sha256':None,
        'tested_owners':{o:{k:row[k]for k in ('commit','tree')}for o,row in inputs2['owners'].items()},
        'tested_children':{'SideSign':{},'SideStore':{'Dependencies/SideSign':owners['SideSign']['commit'],'Dependencies/minimuxer':owners['minimuxer']['commit']}}}
    consumers={'tested_owners':{o:native['tested_owners'][o]for o in ('AnisetteKit','SideStore','LiveContainer')}}
    for key,suite_id in (('init_diagnostics','initializer-diagnostics'),('adi_consumption','adi-consumption')):
        report=suites[suite_id];consumers[key]={'tests':report['tests'],'failures':report['failures'],'errors':report['errors'],'skipped':report['skips'],'test_ids':sorted(report['test_ids'])}
    receipt={'status':'CAPTURED_FOR_INDEPENDENT_REVIEW','source_basis':admission.BASIS,'native_validation':native,
        'native_builds':{'SideSign':'PASS_MACOS_BUILD_AND_TEST','SideStore':'PASS_UNSIGNED_IPHONEOS','LiveContainer':'PASS_UNSIGNED_IPHONEOS'},
        'canonical_native_fork83':{'checks':83,'failures':0,'source_owner':owners['AnisetteKit']['commit'],'engine_archive_sha256':p0.FORK_SHA256},
        'constructor_diagnostics':{k:constructor[k]for k in ('checks','failures','skipped','source_owner','case_ids')},
        'constructor_regression':{'tests':8,'failures':0,'skipped':0},'consumer_suites':consumers,
        'phase_evidence':{o:{'status':'CAPTURED_FOR_REVIEW',**{k:r[k]for k in phase_fields}}for o,r in resolvers.items()},
        'tested_submodules':children,'ADI_executed':False,'device_validation':False}
    files={name:p0.read(STAGE,name) for name in prior['files'] if (STAGE/name).is_file()}
    for name in ['reviewed-approval.json',*prior['files']]:files['inherited/p0-native-basis-v1/'+name]=(p0root/name).read_bytes()
    files['inherited/p0-pins.json']=p0.read(STAGE,'inherited/p0-pins.json')
    need(parse(files['inherited/p0-pins.json'])==admission.base_pins(ROOT),'staged inherited pins differ')
    for name in ('historical-inventory-references.json','p0-frozen-payload-manifest.json','p0-native-owner-identities.json',
                 'validate_contracts.py',*('owners/'+o+'/runtime-contract.json'for o in ('SideSign','minimuxer','idevice','jktcp'))):
        need(files[name]==p0.read(p0root,name),'unrelated staged P0 metadata differs')
    for name,value in {'source-transition.json':transitions,'source-inventory.json':inventory,'submodules.json':children,'native-receipt.json':receipt,
                       **{o+'-resolver.json':r for o,r in resolvers.items()}}.items():files[name]=dump_bytes(value)
    for owner,artifact in (('SideSign',phase1),('SideStore',phase2)):
        for suffix in ('before','after'):files['resolver-locks/'+owner+'.Package.resolved.'+suffix]=artifact.raw('provenance/Package.resolved.'+suffix)
    expected=set(prior['files'])|{'source-transition.json','inherited/p0-pins.json'}|{'inherited/p0-native-basis-v1/'+n for n in ['reviewed-approval.json',*prior['files']]}
    need(set(files)==expected,'candidate exact metadata file inventory incomplete')
    review={'schema_version':1,'source_basis':admission.BASIS,'integration_parent':admission.INTEGRATION_PARENT,'inherited_p0_review_sha256':p0.P0_REVIEW_SHA256,
            'owners':owners,'files':{n:sha(b)for n,b in sorted(files.items())},'native_validation':native}
    summary={'schema_version':1,'status':'CANDIDATE_AWAITING_INDEPENDENT_REVIEW','source_basis':admission.BASIS,'activated':False,'ADI_executed':False,'device_validation':False,
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
    for phase in ('phase1','phase2'):
        for name in ('zip','run-json','artifact-json'):parser.add_argument('--'+phase+'-'+name,type=Path,required=True)
    parser.add_argument('--runner-repo',type=Path,required=True);parser.add_argument('--approved-inputs',type=Path,required=True)
    parser.add_argument('--approved-inputs-sha256',required=True);parser.add_argument('--owner',action='append',default=[],metavar='NAME=/repo',required=True)
    parser.add_argument('--final-sidestore-commit',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);artifacts=[]
    try:
        output=validate_output(args.output)
        repos={}
        for value in args.owner:
            name,path=value.split('=',1);need(name not in repos,'duplicate source owner');repos[name]=Path(path)
        for phase in ('phase1','phase2'):artifacts.append(Artifact(getattr(args,phase+'_zip'),getattr(args,phase+'_run_json'),getattr(args,phase+'_artifact_json')))
        files,review,summary=build_candidate(*artifacts,args.runner_repo,args.approved_inputs.read_bytes(),args.approved_inputs_sha256,repos,args.final_sidestore_commit)
        write_candidate(output,files,review,summary)
        print(json.dumps(summary,sort_keys=True));return 0
    except (ValueError,OSError,KeyError,TypeError,zipfile.BadZipFile,subprocess.CalledProcessError)as error:
        print(json.dumps({'status':'REJECTED','error':str(error)},sort_keys=True),file=sys.stderr);return 1
    finally:
        for artifact in artifacts:artifact.close()
if __name__=='__main__':raise SystemExit(main())
