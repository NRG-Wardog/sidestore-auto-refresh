"""Collection-only checks. A pass cannot certify raster direction/glyph rendering."""
import json, re
from uuid import UUID
from pathlib import Path

def strict_json(text):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    return json.loads(text, object_pairs_hook=pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))

def name_matches(actual,expected):
    if actual==expected: return True
    suffix=Path(expected).suffix;stem=expected[:-len(suffix)]
    match=re.fullmatch(re.escape(stem)+'(?:'+re.escape(suffix)+r')?_[0-9]+_([0-9A-Fa-f-]{36})'+re.escape(suffix),actual or '')
    if not match:return False
    try: UUID(match[1]);return True
    except ValueError:return False

def verify_arm(directory,arm,valid_png):
    if arm!='candidate': raise ValueError('Unexpected arm')
    observations=[]
    manifest=strict_json((Path(directory)/'manifest.json').read_text())
    if not isinstance(manifest,list): raise ValueError('Invalid attachment manifest')
    rows=[]
    for group in manifest:
        if not isinstance(group,dict) or not isinstance(group.get('attachments'),list): raise ValueError('Invalid attachment group')
        for record in group['attachments']:
            if not isinstance(record,dict): raise ValueError('Invalid attachment')
            rows.append((group.get('testIdentifier'),record))
    for stage,language in [('candidate-en-initial','en'),('candidate-he','he'),('candidate-en-return','en')]:
        result={}
        for ext in ['png','json']:
            matches=[(test,r) for test,r in rows if name_matches(r.get('suggestedHumanReadableName'),stage+'.'+ext)]
            if len(matches)!=1: raise ValueError('Missing/ambiguous attachment '+stage+'.'+ext)
            test,record=matches[0]
            if test!='FormDirectionTests/testCandidateRoundTrip()': raise ValueError('Wrong attachment test association')
            exported=record.get('exportedFileName')
            if not isinstance(exported,str) or Path(exported).name!=exported: raise ValueError('Unsafe/missing exported name')
            path=Path(directory)/exported
            if not path.is_file() or path.is_symlink(): raise ValueError('Missing attachment file')
            if ext=='png':
                if valid_png(path) is not True: raise ValueError('Invalid PNG')
                result['png']=exported
            else:
                data=strict_json(path.read_text())
                if data!={'stage':stage,'language':language,'state':language+'|Preserved user text 42|true','scope':'candidate-component-only'}: raise ValueError('Wrong state receipt')
                result.update(data)
        observations.append(result)
    return {'collection':'complete','visualAcceptance':False,'observations':observations}
