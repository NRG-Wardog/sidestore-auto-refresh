#!/usr/bin/env python3
"""Offline parser-only replay of authenticated P2 capture bytes.

API JSON must be retrieved independently through the authenticated GitHub route.
No network, extraction, builds, artifact code, native binaries or guest execution.
Fresh native output remains CAPTURED_FOR_INDEPENDENT_REVIEW, never admission.
Parent UI is a separately authenticated, already independently reviewed basis.
"""
import argparse
import json
from pathlib import Path
import re
import tempfile

from p2_archive import Archive, archive_receipt, need, parse, sha, stream_digest
from p2_sources import PINS, blob, git, source_proof, relative
from p2_commands import CONSUMER_RESULT, verify_commands
from p2_product import verify_product

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPOSITORY = 'NRG-Wardog/sidestore-auto-refresh'
BRANCH = 'validation/p2-alert-lifecycle'
HOST = '1d09456b441912c12227052eb21a26389ac0a203'
HOST_TREE = '8e12c5938e30b761d1234506e2a6fdb6c4f66e1c'
INPUT_SHA = '5d1f466bae1fa20c707a8e164b05e33a1e8a92e99be91e73db592ad032d308a8'
RUN_ID = 39172103613
WORKFLOW = '.github/workflows/p2-composed-host.yml'
INPUT_PATH = 'ci/p2/composed_host/inputs.json'
FRESH_SCOPE = 'Exact composed Release simulator compilation, compiled localization/plural/permission resources, and five native initializer consumer tests'
RAW_SCOPE = 'Composed Release simulator compile and five native initializer consumer tests; no new UI, device authentication or IPA acceptance'
UI_SCOPE = 'Reviewed parent visible Settings picker and persisted preference in four languages; no composed UI rerun or all-app/error-string visual claim'
UI_RUN = 39169380159
UI_HOST = 'b40169c9153f604e48e497c67ccbe1f1dff80240'
UI_HOST_TREE = '68c13fea3d4c48bfedfda0dd381a1ef6c214f086'
UI_ARTIFACT = 12152372036
UI_SHA = 'e37521f34064bf1683779a99486f53a40c12f6b971b400ccc8fee5b936744903'
UI_INDEX = 'fd92f05e3d842da8e4d9a63083fc840574dcdcccdc2189c368c4fb77f095f672'
UI_SOURCE = 'f46a3e14c0752714144aa076355663c79a54f1a9'
UI_TREE = 'f18b17b69ef3bdd061328c4bf4d14e8b0cc5f330'
EVIDENCE_NAMES = {'result.json','inputs.json','source-before.json','source-after.json','consumer-sources-before.json',
                  'consumer-sources-after.json','consumer-tests.json','product-identity.json','compiled-resources.json'}
EXTRA_NATIVE = {'created-simulator.json','build-settings.json','OpenSSL-provenance.json','litehook-provenance.json',
                'LiveContainer.app.zip','LiveContainer.app.before-inventory.json','LiveContainer.app.after-inventory.json'}


def dump(value):
    return (json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n').encode()


def metadata(args, *, ui=False):
    run = parse(args.run_json.read_bytes()); artifact = parse(args.artifact_json.read_bytes())
    expected_id, expected_host = (UI_RUN,UI_HOST) if ui else (RUN_ID,HOST)
    workflow = '.github/workflows/p2-direction-ui.yml' if ui else WORKFLOW
    name = 'p2-direction-ui' if ui else 'p2-composed-host'
    need(run.get('status') == 'completed' and run.get('conclusion') == 'success', 'capture run is not completed/success')
    need(type(run.get('id')) is int and run['id'] == expected_id and type(run.get('run_attempt')) is int and run['run_attempt'] == 1,
         'exact reviewed run/attempt differs')
    need(run.get('head_sha') == expected_host and run.get('repository',{}).get('full_name') == REPOSITORY and
         run.get('head_branch') == BRANCH and run.get('path') == workflow and run.get('event') == 'push' and
         run.get('html_url') == 'https://github.com/'+REPOSITORY+'/actions/runs/'+str(expected_id), 'authenticated workflow/run identity differs')
    need(type(artifact.get('id')) is int and artifact['id'] > 0 and artifact.get('expired') is False and
         artifact.get('name') == name+'-'+expected_host+'-'+str(expected_id)+'-1', 'artifact name/identity differs')
    observed = artifact.get('workflow_run',{})
    need(observed.get('id') == expected_id and observed.get('head_sha') == expected_host and observed.get('head_branch') == BRANCH and
         observed.get('repository_id') == run['repository']['id'] and observed.get('head_repository_id') == run['repository']['id'], 'artifact workflow binding differs')
    with args.zip.open('rb') as stream:
        digest, size = stream_digest(stream)
    need(artifact.get('digest') == 'sha256:'+digest and artifact.get('size_in_bytes') == size, 'API artifact digest/size differs from actual ZIP')
    if ui:
        need(artifact['id'] == UI_ARTIFACT and digest == UI_SHA, 'independently reviewed parent UI artifact differs')
    return run,artifact,digest,{'run':sha(args.run_json.read_bytes()),'artifact':sha(args.artifact_json.read_bytes())}


def sealed_inputs(runner):
    need(git(runner,'rev-parse',HOST+'^{tree}').decode().strip() == HOST_TREE, 'published capture tree differs')
    raw = blob(runner,HOST,INPUT_PATH)
    need(sha(raw) == INPUT_SHA, 'independently approved input digest differs')
    inputs = parse(raw)
    need(set(inputs) == {'files','sourceCommit','sourceTree'} and
         (inputs['sourceCommit'],inputs['sourceTree']) == PINS['LiveContainer'][:2], 'sealed input schema/source differs')
    need(isinstance(inputs['files'],dict) and len(inputs['files']) == 14, 'sealed runner source closure differs')
    for name,digest in inputs['files'].items():
        relative(name); need(re.fullmatch('[a-f0-9]{64}',digest), 'invalid sealed runner digest')
        value = blob(runner,HOST,name)
        if name == WORKFLOW:
            value,count = re.subn(rb'APPROVED_HOST_INPUTS: '+INPUT_SHA.encode(),b'APPROVED_HOST_INPUTS: TO_BE_SEALED',value)
            need(count == 1, 'workflow approval occurrence differs')
        need(sha(value) == digest, 'published sealed runner source differs: '+name)
    return raw,inputs


def native(args):
    run,artifact,zip_sha,api_hashes = metadata(args)
    approved,inputs = sealed_inputs(args.runner)
    with Archive(args.zip) as outer:
        need(set(outer.files) == {'evidence.zip','transport.json'} and len(outer.entries) == 2, 'native outer ZIP inventory differs')
        transport = outer.json('transport.json')
        need(set(transport) == {'status','ownedWritersSettled','archive'} and transport['status'] == 'stable-evidence-retained' and
             transport['ownedWritersSettled'] is True, 'native evidence transport not settled')
        with outer.nested('evidence.zip') as evidence:
            archive_receipt(transport['archive'],evidence,outer.files['evidence.zip'],outer.entries['evidence.zip'].file_size)
            index_raw,index = evidence.index()
            need({n for n in index if not n.startswith('commands/')} == EVIDENCE_NAMES|EXTRA_NATIVE, 'native exact evidence closure differs')
            need(evidence.raw('inputs.json') == approved and evidence.json('inputs.json') == inputs, 'captured input bytes differ from approved runner')
            result = evidence.json('result.json')
            need(set(result) == {'status','scope','sourceCommit','sourceTree','errors','nativeConsumerTests','machOPlatforms','appArchive'} and
                 result['status'] == 'compile-passed' and result['scope'] == RAW_SCOPE and result['errors'] == [] and
                 (result['sourceCommit'],result['sourceTree']) == PINS['LiveContainer'][:2] and result['nativeConsumerTests'] == CONSUMER_RESULT,
                 'native capture failed, incomplete, or overclaims scope')
            host_before = evidence.json('source-before.json'); host_after = evidence.json('source-after.json')
            peers_before = evidence.json('consumer-sources-before.json'); peers_after = evidence.json('consumer-sources-after.json')
            need(host_before == host_after and peers_before == peers_after and set(peers_before) == {'SideStore','AnisetteKit'}, 'sources changed or owner proof closure differs')
            proofs = {'LiveContainer':source_proof(host_before,'LiveContainer',args.host),
                      'SideStore':source_proof(peers_before['SideStore'],'SideStore',args.peer),
                      'AnisetteKit':source_proof(peers_before['AnisetteKit'],'AnisetteKit',args.producer)}
            for owner in ('OpenSSL','litehook'):
                proofs[owner] = source_proof(evidence.json(owner+'-provenance.json'),owner)
            commands = verify_commands(evidence,proofs,HOST,RUN_ID,args.host)
            product = verify_product(evidence,args.runner,args.host,HOST,result)
            identity = {'run_url':run['html_url'],'run_attempt':run['run_attempt'],'artifact_id':artifact['id'],'artifact_sha256':zip_sha,
                        'evidence_index_sha256':sha(index_raw),'host_commit':HOST,'host_tree':HOST_TREE,'approved_inputs_sha256':INPUT_SHA}
            candidate = {'schema_version':1,'status':'CAPTURED_FOR_INDEPENDENT_REVIEW','scope':FRESH_SCOPE,'identity':identity,
                'tested_owners':{o:{'commit':PINS[o][0],'tree':PINS[o][1]} for o in ('LiveContainer','SideStore','AnisetteKit')},
                'consumer_tests':{'tests':5,'failures':0,'errors':0,'skipped':0,'test_ids':commands['test_ids']},
                'product':{'configuration':'Release','sdk':'iphonesimulator','compile':'passed','localization':'passed','exact_plural_values':'passed','permissions':'passed'},
                'claims':{k:False for k in ('fresh_ui_execution','guest_execution','ADI_executed','device_validation','ipa_acceptance')},
                'evidence_files':{name:index[name] for name in sorted(EVIDENCE_NAMES)}}
            summary = {'status':'PARSER_VERIFIED_CANDIDATE_AWAITING_INDEPENDENT_REVIEW','identity':identity,
                'github_metadata_sha256':api_hashes,'indexed_files':len(index),'commands':commands,'retained_product':product,
                'source_files':{o:len(v['files']) for o,v in proofs.items()},'local_git_blob_sources':['LiveContainer','SideStore','AnisetteKit'],
                'dependency_git_tree_and_raw_hash_command_sources':['OpenSSL','litehook'],
                'redundant_dependency_regular_blob_sha256_verified':False,
                'unverified_observation_fields':['OpenSSL-provenance.json:files[regular_blob].sha256','litehook-provenance.json:files[regular_blob].sha256'],
                'artifact_binaries_executed':False,'artifact_code_imported':False,'guest_execution':False,'activated':False}
            return {'native-capture-candidate.json':candidate,'native-replay-summary.json':summary}


def validate_ui_result(result):
    need(result['status'] == 'BOUNDED_SETTINGS_ROUTE_OBSERVED' and result['scope'] == 'Genuine four-language Settings UI and persisted preference observation' and
         result['sourceCommit'] == UI_SOURCE and result['sourceTree'] == UI_TREE and result['probeBuilderCommit'] == UI_HOST and
         result['probeBuilderTree'] == UI_HOST_TREE and result['errors'] == [] and result['retentionErrors'] == [] and result['diagnosticCaptureErrors'] == [],
         'parent UI result/source identity differs')
    need(result['serviceFailureUnresolved'] is True and all(result[k] is False for k in ('backendReadinessProven','fullAppAcceptance','finalCompositionAcceptance','hostBuiltThisRun')),
         'parent UI scope/service boundary differs')


def parent_ui(args):
    run,artifact,zip_sha,api_hashes = metadata(args,ui=True)
    with Archive(args.zip) as outer:
        names = {'p2-ui-publish/evidence.zip','p2-ui-publish/transport.json','p2-ui-input/download.json'}
        need(set(outer.files) == names and len(outer.entries) == 3, 'parent UI outer ZIP inventory differs')
        transport = outer.json('p2-ui-publish/transport.json')
        need(set(transport) == {'status','ownedWritersSettled','archive'} and transport['status'] == 'stable-evidence-retained' and
             transport['ownedWritersSettled'] is True, 'parent UI evidence writers unsettled')
        nested = 'p2-ui-publish/evidence.zip'
        with outer.nested(nested) as evidence:
            archive_receipt(transport['archive'],evidence,outer.files[nested],outer.entries[nested].file_size)
            index_raw,index = evidence.index()
            need(sha(index_raw) == UI_INDEX and len(index) == 1444, 'reviewed parent UI index differs')
            result = evidence.json('ui-result.json')
            validate_ui_result(result)
            before = evidence.json('fresh-source-before.json'); after = evidence.json('fresh-source-after.json')
            need(before == after, 'parent UI source changed')
            source_proof(before,'LiveContainer',args.host,identity=(UI_SOURCE,UI_TREE,PINS['LiveContainer'][2]))
            observation = evidence.json('native-observation.json')
            need(observation['status'] == 'observed' and observation['stage'] == 'en-returned' and observation['launchCount'] == 1,
                 'parent Settings observation incomplete')
            observed = observation['observations']; languages = ['en','he','zh-Hans','zh-Hant','en']
            need([r['language'] for r in observed] == languages and all(r['applicationForeground'] is True and r['selectedValueVisible'] is True and
                 r['settingsTabVisible'] is True for r in observed), 'parent Settings language observations differ')
            preferences = observation['preferenceReceipts']
            need(len(preferences) == 5 and all(r['after'] == '1' for r in preferences) and preferences[0]['before'] == '0' and
                 preferences[0]['seededThroughUI'] is True and all(r['before'] == '1' and r['seededThroughUI'] is False for r in preferences[1:]),
                 'parent preference observations differ')
            basis = {'status':'INDEPENDENTLY_REVIEWED_PARENT_UI','scope':UI_SCOPE,'source_commit':UI_SOURCE,'source_tree':UI_TREE,
                'run_url':run['html_url'],'run_attempt':1,'artifact_id':artifact['id'],'artifact_sha256':zip_sha,
                'evidence_index_sha256':sha(index_raw),'languages':['en','zh-Hans','zh-Hant','he'],'composed_ui_reexecuted':False,
                'service_failure_unresolved':True,'full_app_acceptance':False,'final_composition_acceptance':False}
            return {'parent-ui-basis.json':basis,'parent-ui-replay-summary.json':{'status':'AUTHENTICATED_PRIOR_REVIEW_BYTES_REPLAYED',
                'github_metadata_sha256':api_hashes,'indexed_files':len(index),'source_files':len(before['files']),
                'independent_pixel_review_reperformed':False,'review_scope':UI_SCOPE,'unresolved_service_code':'SS-LAUNCH-C11-L15',
                'artifact_binaries_executed':False,'guest_execution':False,'activated':False}}


def write_output(output, files):
    output = output.absolute()
    need(not output.exists() and not output.is_symlink() and output.parent.is_dir() and
         not any(p.is_symlink() for p in output.parents), 'fresh unlinked output directory required')
    with tempfile.TemporaryDirectory(prefix='p2-replay-',dir=output.parent) as temporary:
        staging = Path(temporary)/'result'; staging.mkdir()
        for name,value in files.items():
            relative(name); (staging/name).write_bytes(dump(value))
        need(not output.exists(), 'output appeared during replay')
        staging.rename(output)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('native','parent-ui'))
    p.add_argument('--zip',type=Path,required=True); p.add_argument('--run-json',type=Path,required=True)
    p.add_argument('--artifact-json',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--runner',type=Path,required=True)
    p.add_argument('--host',type=Path,required=True)
    p.add_argument('--peer',type=Path,required=True)
    p.add_argument('--producer',type=Path,required=True)
    args = p.parse_args()
    try:
        need(not args.output.exists(), 'output already exists')
        files = native(args) if args.mode == 'native' else parent_ui(args)
        write_output(args.output,files)
        print(json.dumps({'status':'REPLAY_COMPLETE','mode':args.mode,'output_files':sorted(files),'activated':False},sort_keys=True))
    except Exception as error:
        p.exit(2,'REJECTED: '+str(error)+'\n')


if __name__ == '__main__':
    main()
