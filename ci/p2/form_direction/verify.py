"""Fail-closed collection validation; raster acceptance remains independent."""
import hashlib
import json
import math
import re
from pathlib import Path
from uuid import UUID

ARMS = {'control': 'testControlRoundTrip', 'candidate': 'testCandidateRoundTrip'}
STAGES = ['en-initial-immediate', 'en-initial-later', 'he-selected-immediate',
          'he-selected-later', 'en-return-immediate', 'en-return-later']
LANGUAGES = ['en', 'en', 'he', 'he', 'en', 'en']
PROBES = {'inherited', 'container', 'productionSectionBoundary', 'header', 'picker', 'content'}
ELEMENTS = ['settings.language', 'p2-witness-header', 'p2-witness-picker', 'p2-witness-navigation',
            'p2-direction-first', 'p2-direction-last', 'p2-outside-first', 'p2-outside-last', 'p2-existing-message']
VISUAL_PENDING = 'not-evaluated-requires-independent-raster-review'
LANGUAGE_LABELS = {'en': 'Language', 'he': 'שפה'}
NATIVE_NAMES = {'en': 'English', 'he': 'עברית'}
ENVIRONMENT_KEYS = {'AppleLanguages', 'AppleLocale', 'NSLanguages', 'LANG', 'LANGUAGE', 'LC_ALL', 'LC_MESSAGES'}

class VerificationError(ValueError):
    pass

def need(value, message):
    if not value:
        raise VerificationError(message)

def number(value):
    need(type(value) in (int, float) and math.isfinite(value), 'Nonfinite or nonnumeric value')
    return value

def finite_tree(value):
    if isinstance(value, dict):
        need(all(isinstance(k, str) for k in value), 'Nonstring JSON key')
        for item in value.values(): finite_tree(item)
    elif isinstance(value, list):
        for item in value: finite_tree(item)
    elif type(value) in (int, float): number(value)
    else: need(value is None or type(value) in (str, bool), 'Invalid JSON value')

def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'Duplicate JSON key: ' + key)
            result[key] = value
        return result
    result = json.loads(text, object_pairs_hook=pairs,
                        parse_constant=lambda x: (_ for _ in ()).throw(VerificationError('Invalid JSON constant ' + x)))
    finite_tree(result)
    return result

def uuid(value):
    need(isinstance(value, str), 'Missing UUID')
    try: parsed = UUID(value)
    except (ValueError, AttributeError): raise VerificationError('Malformed UUID')
    need(str(parsed).lower() == value.lower(), 'Noncanonical UUID')
    return value

def rect(value, viewport=None):
    need(isinstance(value, dict) and set(value) == {'x', 'y', 'width', 'height'}, 'Malformed rectangle')
    for item in value.values(): number(item)
    need(value['width'] > 0 and value['height'] > 0, 'Empty rectangle')
    if viewport is not None:
        need(value['x'] < viewport['x'] + viewport['width'] and value['x'] + value['width'] > viewport['x']
             and value['y'] < viewport['y'] + viewport['height'] and value['y'] + value['height'] > viewport['y'],
             'Rectangle outside viewport')
    return value

def node(value, viewport=None, identifier=None, tap=False):
    need(isinstance(value, dict), 'Missing AX node')
    need(type(value.get('exists')) is bool and value['exists'], 'Missing AX element')
    need(type(value.get('hittable')) is bool, 'Missing hittability')
    if tap: need(value['hittable'], 'Tap target not hittable')
    need(type(value.get('role')) is int and value['role'] >= 0, 'Missing native AX role')
    need(isinstance(value.get('label'), str) and isinstance(value.get('identifier'), str), 'Missing AX labels')
    if identifier is not None: need(value['identifier'] == identifier, 'Wrong AX identifier')
    rect(value.get('frame'), viewport)
    return value

def state(value, arm, language, baseline=None, capture_time=None):
    need(isinstance(value, dict) and value.get('schema') == 'p2-form-direction-state-v1', 'Wrong app state schema')
    finite_tree(value)
    direction = 'rtl' if language == 'he' else 'ltr'
    need(value.get('arm') == arm and value.get('language') == language and value.get('hostDefaultLanguage') == language,
         'Wrong arm/persisted language')
    need(value.get('locale', '').replace('_', '-') == language and value.get('direction') == direction, 'Wrong root environment')
    need(value.get('bootstrapLanguage') == 'en' and value.get('bootstrapScope') == 'fixture-domain-before-view-construction'
         and isinstance(value.get('bootstrapPriorLanguage'), str), 'Missing fixture-only bootstrap provenance')
    for key in ['processToken', 'sessionToken']: uuid(value.get(key))
    need(type(value.get('processIdentifier')) is int and value['processIdentifier'] > 0, 'Missing app PID')
    need(value.get('text') == '' and isinstance(value.get('originalMessage'), str) and value['originalMessage'], 'Wrong retained fixture content')
    for key in ['confirmStarted', 'confirmCompleted', 'errorStarted', 'textStarted', 'textCompleted', 'witnessChoice']:
        need(type(value.get(key)) is int and value[key] == 0, 'Unexpected fixture action: ' + key)
    args = value.get('processArguments')
    need(isinstance(args, list) and len(args) >= 3 and all(isinstance(x, str) for x in args) and args[0], 'Missing actual arguments')
    need(args.count('--form-arm') == 1, 'Missing fixed arm')
    index = args.index('--form-arm')
    need(index + 1 < len(args) and args[index + 1] == arm, 'Wrong fixed launch arm')
    need(not any(re.search(r'^-{1,2}(applelanguages|applelocale|nslanguages|language|locale)(=|$)', x, re.I) for x in args[1:]), 'Language launch override')
    env = value.get('languageEnvironment')
    need(isinstance(env, dict) and all(isinstance(v, str) for v in env.values()), 'Missing actual environment receipt')
    need(set(env).issubset(ENVIRONMENT_KEYS), 'Foreign language environment key')
    need(not {'AppleLanguages', 'AppleLocale', 'NSLanguages'} & set(env), 'Apple language environment override')
    probes = value.get('probes')
    need(isinstance(probes, dict) and set(probes) == PROBES, 'Incomplete/foreign probe set')
    ids = []
    for name, probe in probes.items():
        need(isinstance(probe, dict) and set(probe) == {'identity', 'locale', 'direction', 'uptime', 'revision'}, 'Malformed environment probe')
        ids.append(uuid(probe['identity']))
        need(probe['locale'].replace('_', '-') == language, 'Wrong actual probe locale')
        expected = 'ltr' if arm == 'candidate' and name == 'container' else direction
        need(probe['direction'] == expected, 'Wrong actual probe direction: ' + name)
        need(number(probe['uptime']) > 0, 'Invalid observation uptime')
        need(type(probe['revision']) is int and probe['revision'] > 0, 'Invalid app-process probe revision')
        # App-process probe uptime is compared only with prior app-process
        # probe readings, never with the external runner's clock.
    need(len(set(ids)) == len(PROBES), 'Probe identities are not distinct')
    if baseline is not None:
        for key in ['arm', 'processToken', 'sessionToken', 'processIdentifier', 'text', 'originalMessage',
                    'bootstrapPriorLanguage', 'bootstrapLanguage', 'bootstrapScope', 'processArguments', 'languageEnvironment']:
            need(value[key] == baseline[key], 'State identity/provenance changed: ' + key)
        for name in PROBES: need(probes[name]['identity'] == baseline['probes'][name]['identity'], 'Probe identity reset: ' + name)
    return value

def geometry(value, language):
    need(isinstance(value, dict), 'Missing geometry')
    viewport = rect(value.get('viewport'))
    for name in ELEMENTS: node(value.get(name), viewport, name)
    headers = value.get('productionHeaderMatches')
    need(isinstance(headers, list) and 0 < len(headers) <= 20, 'Missing production header geometry')
    for header in headers:
        node(header, viewport)
        need(header['label'] == LANGUAGE_LABELS[language], 'Wrong pinned production header label')
    label = LANGUAGE_LABELS[language]
    allowed_picker_labels = {label, label + ', ' + NATIVE_NAMES[language]}
    if language == 'he': allowed_picker_labels.add(label + '\u200f, ' + NATIVE_NAMES[language])
    need(value['settings.language']['label'] in allowed_picker_labels, 'Wrong pinned production Picker label')
    descendants = value.get('nativeDisclosureDescendants')
    need(isinstance(descendants, list) and len(descendants) <= 50, 'Missing native disclosure descendants')
    for child in descendants: node(child, viewport)
    need(isinstance(value.get('disclosureObservation'), str) and value['disclosureObservation'], 'Missing native chrome caveat')
    def midpoint(name):
        frame = value[name]['frame']; return frame['x'] + frame['width'] / 2
    expected_content = midpoint('p2-direction-first') > midpoint('p2-direction-last') if language == 'he' else midpoint('p2-direction-first') < midpoint('p2-direction-last')
    expected_outside = midpoint('p2-outside-first') > midpoint('p2-outside-last') if language == 'he' else midpoint('p2-outside-first') < midpoint('p2-outside-last')
    need(type(value.get('contentOrderCorrect')) is bool and value['contentOrderCorrect'] == expected_content, 'Forged content order verdict')
    need(type(value.get('outsideOrderCorrect')) is bool and value['outsideOrderCorrect'] == expected_outside, 'Forged outside order verdict')
    return expected_content and expected_outside

def name_matches(actual, expected):
    if actual == expected: return True
    suffix = Path(expected).suffix; stem = expected[:-len(suffix)]
    match = re.fullmatch(re.escape(stem) + '(?:' + re.escape(suffix) + r')?_([0-9]+)_([0-9A-Fa-f-]{36})' + re.escape(suffix), actual or '')
    if not match: return False
    try: uuid(match.group(2)); return True
    except VerificationError: return False

def _verify_arm(report, groups, root, valid_png, arm):
    need(arm in ARMS and isinstance(report, dict), 'Wrong requested arm')
    finite_tree(report)
    need(report.get('schema') == 'p2-form-direction-v1' and report.get('arm') == arm and report.get('method') == ARMS[arm], 'Wrong report identity')
    need(report.get('completed') is True and report.get('failures') == [], 'Incomplete/failed native collection')
    need(report.get('visualVerdict') == VISUAL_PENDING and isinstance(report.get('scope'), str) and report['scope'], 'Unsupported visual acceptance/scope')
    rows = report.get('observations')
    need(isinstance(rows, list) and len(rows) == 6 and [r.get('stage') for r in rows] == STAGES, 'Wrong observation set/order')
    root = Path(root).resolve()
    association = 'FormDirectionTests/' + ARMS[arm] + '()'
    expected = [arm + '-form-direction.json'] + [arm + '-' + stage + suffix for stage in STAGES for suffix in ['.png', '.txt']]
    matched = {name: [] for name in expected}; seen_paths = set()
    need(isinstance(groups, list), 'Invalid attachment manifest')
    for group in groups:
        need(isinstance(group, dict) and isinstance(group.get('attachments'), list), 'Malformed attachment group')
        for metadata in group['attachments']:
            need(isinstance(metadata, dict), 'Malformed attachment metadata')
            suggested = metadata.get('suggestedHumanReadableName')
            need(isinstance(suggested, str), 'Missing suggested attachment name')
            matches = [name for name in expected if name_matches(suggested, name)]
            need(len(matches) <= 1, 'Ambiguous attachment name')
            filename = metadata.get('exportedFileName')
            need(isinstance(filename, str) and Path(filename).name == filename, 'Unsafe exported attachment name')
            path = (root / filename).resolve()
            need(path.is_relative_to(root) and path.is_file() and path.stat().st_size > 0, 'Missing/unsafe/empty exported attachment')
            if not matches:
                need(not suggested.startswith(('control-', 'candidate-')), 'Foreign/extra experimental attachment')
                if path.suffix == '.json':
                    other = strict_json(path.read_text())
                    need(not isinstance(other, dict) or other.get('schema') != 'p2-form-direction-v1', 'Foreign report payload')
                continue
            need(group.get('testIdentifier') == association, 'Foreign attachment test association')
            need(metadata.get('isAssociatedWithFailure') is False, 'Failure-associated required attachment')
            need(isinstance(metadata.get('deviceId'), str) and metadata['deviceId'], 'Missing attachment device')
            need(number(metadata.get('timestamp')) > 0, 'Invalid attachment timestamp')
            name = matches[0]
            need(path.suffix == Path(name).suffix and path not in seen_paths, 'Attachment type mismatch/reused file')
            seen_paths.add(path); matched[name].append((path, metadata))
    need(all(len(items) == 1 for items in matched.values()), 'Missing/duplicate mandatory attachment')
    device_ids = {items[0][1]['deviceId'] for items in matched.values()}
    need(len(device_ids) == 1, 'Mixed attachment devices')
    report_path = matched[arm + '-form-direction.json'][0][0]
    need(strict_json(report_path.read_text()) == report, 'Report JSON differs from associated attachment')
    baseline = rows[0].get('stateBefore'); state(baseline, arm, 'en')
    previous_end = -1; geometry_results = []
    for index, (row, language) in enumerate(zip(rows, LANGUAGES)):
        need(row.get('language') == language, 'Wrong observation language')
        started, png_end, ended = [number(row.get(key)) for key in ['captureStartedUptime', 'pngFinishedUptime', 'captureFinishedUptime']]
        need(0 < started <= png_end <= ended and ended - started <= 60 and started >= previous_end, 'Invalid/overlapping capture timing')
        if index % 2: need(started - previous_end >= 0.8 - 1e-6, 'Later capture too early')
        state(row.get('stateBefore'), arm, language, baseline, started)
        state(row.get('stateAfter'), arm, language, baseline, started)
        need(row['stateBefore'] == row['stateAfter'], 'State changed within capture')
        for key, suffix in [('png', '.png'), ('ax', '.txt')]:
            name = arm + '-' + row['stage'] + suffix
            need(row.get(key) == name, 'Wrong screenshot/AX name in observation')
            path = matched[name][0][0]
            if suffix == '.png': need(valid_png(path) is True, 'Invalid PNG or ambiguous validator result')
            else: need(path.read_text().strip(), 'Empty AX hierarchy')
        geometry_results.append(geometry(row.get('geometry'), language))
        previous_end = ended
    need(rows[-1]['captureFinishedUptime'] - rows[0]['captureStartedUptime'] <= 240, 'Collection exceeded arm budget')
    transitions = report.get('transitions')
    need(isinstance(transitions, list) and len(transitions) == 2, 'Missing/extra transitions')
    for index, (change, before_index, after_index, old, new) in enumerate(zip(transitions, [1, 3], [2, 4], ['en', 'he'], ['he', 'en'])):
        need(change.get('from') == old and change.get('to') == new, 'Wrong production Picker transition')
        need(change.get('before') == rows[before_index]['stateAfter'] and change.get('after') == rows[after_index]['stateBefore'], 'Unbound transition state')
        picker = node(change.get('pickerTap'), identifier='settings.language', tap=True)
        option = node(change.get('optionTap'), tap=True)
        need(change.get('pickerCandidates') == [picker], 'Missing/ambiguous live Picker inventory')
        buttons, texts = change.get('buttonCandidates'), change.get('textCandidates')
        need(isinstance(buttons, list) and isinstance(texts, list) and len(buttons) <= 1 and len(texts) <= 1,
             'Missing/ambiguous actionable option inventory')
        descendants = change.get('buttonTextDescendants')
        if len(buttons) == 1 and len(texts) == 1:
            need(change.get('optionContainment') == 'unique-button-contains-unique-text' and descendants == texts,
                 'Unrelated button/text pair lacks exact native descendant proof')
        else:
            need(len(buttons) + len(texts) == 1 and change.get('optionContainment') == 'single-actionable-target'
                 and descendants == [], 'Missing sole actionable option provenance')
        for item in buttons + texts:
            node(item, tap=True)
            need(item['label'] == ('עברית' if new == 'he' else 'English'), 'Foreign option inventory')
        role = 'button' if buttons else 'staticText'
        need(change.get('optionRole') == role and (buttons if buttons else texts) == [option], 'Option tap does not match unique live inventory')
        need(option['label'] == ('עברית' if new == 'he' else 'English'), 'Wrong genuine Picker option')
        prior_picker = rows[before_index]['geometry']['settings.language']
        need(picker['label'] == prior_picker['label'] and picker['frame'] == prior_picker['frame'], 'Picker tap label/geometry not bound')
        times = [number(change.get(k)) for k in ['pickerTapStartedUptime', 'tapStartedUptime', 'tapFinishedUptime', 'readyUptime']]
        need(rows[before_index]['captureFinishedUptime'] <= times[0] <= times[1] <= times[2] <= times[3] <= rows[after_index]['captureStartedUptime'], 'Invalid transition timing')
        for name in PROBES:
            old_probe, new_probe = change['before']['probes'][name], change['after']['probes'][name]
            need(new_probe['revision'] > old_probe['revision'] and new_probe['uptime'] > old_probe['uptime'],
                 'Stale app-process probe across transition: ' + name)
    need(report.get('finalState') == rows[-1]['stateAfter'], 'Final state is not final captured state')
    return {'status': 'collection-complete', 'arm': arm, 'method': ARMS[arm], 'deviceId': next(iter(device_ids)),
            'processToken': baseline['processToken'], 'sessionToken': baseline['sessionToken'], 'processIdentifier': baseline['processIdentifier'],
            'observationCount': 6, 'pngCount': 6, 'axCount': 6,
            'geometryAcceptance': all(geometry_results), 'geometryByStage': dict(zip(STAGES, geometry_results)),
            'visualVerdict': VISUAL_PENDING, 'reportSHA256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
            'artifacts': {name: {'file': items[0][0].name, 'sha256': hashlib.sha256(items[0][0].read_bytes()).hexdigest()} for name, items in matched.items()}}

def verify_arm(directory, arm, valid_png):
    """Resolve report from the raw xcresult manifest, never from a supplied path."""
    need(arm in ARMS, 'Wrong requested arm')
    root = Path(directory).resolve()
    groups = strict_json((root / 'manifest.json').read_text())
    need(isinstance(groups, list), 'Invalid attachment manifest')
    reports = []
    for group in groups:
        need(isinstance(group, dict) and isinstance(group.get('attachments'), list), 'Malformed attachment group')
        for metadata in group['attachments']:
            need(isinstance(metadata, dict), 'Malformed attachment metadata')
            if name_matches(metadata.get('suggestedHumanReadableName'), arm + '-form-direction.json'):
                need(group.get('testIdentifier') == 'FormDirectionTests/' + ARMS[arm] + '()', 'Wrong report association')
                filename = metadata.get('exportedFileName')
                need(isinstance(filename, str) and Path(filename).name == filename, 'Unsafe report filename')
                path = (root / filename).resolve()
                need(path.is_relative_to(root) and path.is_file() and path.suffix == '.json', 'Missing/unsafe report')
                reports.append(strict_json(path.read_text()))
    need(len(reports) == 1, 'Missing/duplicate associated report')
    return _verify_arm(reports[0], groups, root, valid_png, arm)

def verify_pair(control, candidate):
    need(isinstance(control, dict) and isinstance(candidate, dict), 'Missing paired arm results')
    for value, arm in [(control, 'control'), (candidate, 'candidate')]:
        need(value.get('status') == 'collection-complete' and value.get('arm') == arm and value.get('method') == ARMS[arm], 'Wrong verified arm result')
    need(control['deviceId'] == candidate['deviceId'], 'Different simulator devices')
    for key in ['processToken', 'sessionToken', 'processIdentifier']: need(control[key] != candidate[key], 'Arms reused a live process/session')
    return {'status': 'paired-collection-complete', 'control': control, 'candidate': candidate,
            'candidateGeometryAccepted': candidate['geometryAcceptance'], 'visualVerdict': VISUAL_PENDING,
            'scope': 'No full-host, full-state-matrix or product-fix acceptance; runner separately binds same toolchain/runtime'}

def visual_verdict(pair, review=None):
    need(pair.get('status') == 'paired-collection-complete', 'Visual review lacks complete paired evidence')
    if review is None: return {'status': 'pending-independent-raster-review', 'productFixAccepted': False}
    keys = ['controlReproduced', 'candidateEnglishReadable', 'candidateHebrewReadable', 'candidateHebrewLayoutAppropriate']
    need(isinstance(review, dict) and all(type(review.get(k)) is bool for k in keys), 'Incomplete independent raster review')
    need(isinstance(review.get('reviewer'), str) and review['reviewer'] and isinstance(review.get('evidence'), list)
         and all(isinstance(x, str) and x for x in review['evidence']) and review['evidence'], 'Missing independent review provenance')
    if not review['controlReproduced']: status = 'inconclusive-control-did-not-reproduce'
    elif not pair['candidateGeometryAccepted'] or not all(review[k] for k in keys[1:]): status = 'candidate-rejected'
    else: status = 'bounded-fixture-improvement-only'
    return {'status': status, 'productFixAccepted': False, 'review': review}

# Failure diagnostics are deliberately separate from verify_arm/verify_pair.
# A valid receipt never changes the native/capture/visual failure verdict.
DIFFERENCE_ROOTS = {'schema','arm','processToken','sessionToken','processIdentifier','processArguments',
    'languageEnvironment','bootstrapPriorLanguage','bootstrapLanguage','bootstrapScope','language',
    'hostDefaultLanguage','locale','direction','text','originalMessage','confirmStarted','confirmCompleted',
    'errorStarted','textStarted','textCompleted','witnessChoice'}

def verify_state_difference(event, arm, stage, phase):
    need(arm in ARMS and stage in STAGES and phase in {'before-assertion','finalization'}, 'Unknown diagnostic association')
    need(isinstance(event,dict) and set(event)=={'phase','receipt'} and event['phase']==phase,'Wrong diagnostic phase')
    finite_tree(event)
    receipt=event['receipt']
    keys={'schema','arm','method','stage','foundationEqual','complete','canonicalEqual','rawJSONEqual',
          'beforeSHA256','afterSHA256','changedPaths','numericProbeChanges'}
    need(isinstance(receipt,dict) and set(receipt)==keys,'Incomplete/foreign diagnostic receipt')
    need(receipt['schema']=='p2-form-state-difference-v1' and receipt['arm']==arm and receipt['method']==ARMS[arm] and receipt['stage']==stage,'Foreign state diagnostic')
    need(receipt['complete'] is True and receipt['foundationEqual'] is False,'Not a complete failed-equality diagnostic')
    need(type(receipt['canonicalEqual']) is bool and type(receipt['rawJSONEqual']) is bool,'Invalid diagnostic equality types')
    for key in ['beforeSHA256','afterSHA256']:
        need(isinstance(receipt[key],str) and re.fullmatch('[0-9a-f]{64}',receipt[key]) is not None,'Invalid diagnostic digest')
    need(receipt['canonicalEqual']==(receipt['beforeSHA256']==receipt['afterSHA256']),'Canonical hash/equality disagreement')
    need(not receipt['rawJSONEqual'] or receipt['canonicalEqual'],'Identical raw JSON with different deterministic bytes')
    paths=receipt['changedPaths']
    allowed={'/'+key for key in DIFFERENCE_ROOTS}|{'/probes/'+name+'/'+field for name in PROBES for field in ['identity','locale','direction','revision','uptime']}
    need(isinstance(paths,list) and all(isinstance(p,str) and p in allowed for p in paths) and paths==sorted(set(paths)),'Invalid/duplicate/foreign changed paths')
    need((not paths)==receipt['canonicalEqual'],'Changed path/equality disagreement')
    rows=receipt['numericProbeChanges'];need(isinstance(rows,list),'Invalid numeric diagnostic changes')
    required={p for p in paths if p.startswith('/probes/') and p.rsplit('/',1)[1] in {'uptime','revision'}}
    seen=[]
    for row in rows:
        need(isinstance(row,list) and len(row)==4,'Malformed numeric diagnostic row')
        name,field,left,right=row
        need(isinstance(name,str) and name in PROBES and isinstance(field,str) and field in {'uptime','revision'},'Foreign numeric diagnostic row')
        path='/probes/'+name+'/'+field;seen.append(path)
        number(left); number(right)
        need(left!=right,'Unchanged numeric values')
        if field=='revision':need(type(left) is int and type(right) is int,'Noninteger probe revision')
    need(len(seen)==len(set(seen)) and set(seen)==required,'Missing/duplicate numeric deltas')
    # This compact transport intentionally contains no arbitrary state string values.
    need(len(json.dumps(event,separators=(',',':'),ensure_ascii=False).encode())<=1536,'Diagnostic exceeds transport bound')
    return {'status':'failed-capture-diagnostic-only','changedPaths':paths,'nativeAcceptance':False,'visualAcceptance':False}
