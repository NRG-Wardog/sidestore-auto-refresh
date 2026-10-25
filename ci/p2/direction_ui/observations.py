"""Five-language-stage receipt verification; pixels still require review."""
import math,re
from pathlib import Path
STAGES=[('en-before','en','English','Settings','en-seeded'),('he-selected','he','עברית','הגדרות','he-retained'),('hans-selected','zh-Hans','简体中文','设置','hans-retained'),('hant-selected','zh-Hant','繁體中文','設定','hant-retained'),('en-returned','en','English','Settings','en-retained')]
PICKER_LABELS={'en':'Language, English','he':'שפה\u200f, עברית','zh-Hans':'语言、简体中文','zh-Hant':'語言、繁體中文'}
PREFERENCE_LABELS={'en':'Start Collapsed','he':'פתיחה במצב מכווץ','zh-Hans':'初始状态设为收起','zh-Hant':'初始狀態設為收起'}
def require(value,message):
 if not value:raise ValueError(message)
def verify_rows(report,identity):
 require(report.get('identity')==identity,'Product observation identity differs')
 require(report.get('status')=='observed' and type(report.get('launchCount')) is int and report['launchCount']==1,'Incomplete single launch')
 rows=report.get('observations');prefs=report.get('preferenceReceipts')
 require(isinstance(rows,list) and len(rows)==5 and isinstance(prefs,list) and len(prefs)==5,'Incomplete language/preference sequence')
 for index,(row,pref,expected) in enumerate(zip(rows,prefs,STAGES)):
  stage,code,native,title,pstage=expected
  require(row.get('stage')==stage and row.get('language')==code and row.get('selectedNativeName')==native,'Language sequence differs')
  require(all(row.get(k) is True for k in ['selectedValueVisible','settingsTabVisible','applicationForeground']),'Language observation failed')
  label,value,children=row.get('pickerLabel'),row.get('pickerValue'),row.get('pickerChildLabels')
  require(type(label)is str and type(value)is str and isinstance(children,list) and all(type(x)is str for x in children),'Malformed live picker evidence')
  require(value==native or children.count(native)==1 or label==PICKER_LABELS[code],'Selected native name absent')
  require(row.get('settingsTabLabel')==title,'Translated Settings tab differs')
  frame=row.get('pickerFrame')
  require(isinstance(frame,dict) and set(frame)=={'x','y','width','height'} and all(type(v)in(int,float) and math.isfinite(v)for v in frame.values()) and frame['width']>0 and frame['height']>0,'Invalid picker frame')
  require(pref=={'stage':pstage,'label':PREFERENCE_LABELS[code],'before':'0'if index==0 else'1','after':'1','seededThroughUI':index==0},'Preference edit/retention differs')
  require(type(pref['seededThroughUI']) is bool,'Preference flag type differs')
 return {'scope':'Five real Settings language stages and one UI-edited persisted preference; no transient state or device authentication acceptance','languages':['en','he','zh-Hans','zh-Hant'],'observations':5,'preferenceEditedThroughUI':True}

# The caller supplies the existing strict JSON/path helpers.

def attach_helpers(host):
 global strict_json,safe_relative,sha
 strict_json,safe_relative,sha=host.strict_json,host.safe_relative,host.sha

def attachment_map(directory):
    directory = Path(directory)
    manifest = strict_json((directory / 'manifest.json').read_text())
    require(isinstance(manifest, list), 'Unexpected attachment export schema')
    rows = {}
    for group in manifest:
        require(isinstance(group.get('attachments'), list), 'Malformed attachment group')
        for entry in group['attachments']:
            name = entry.get('suggestedHumanReadableName', '')
            require(isinstance(name, str), 'Invalid attachment display name')
            name = re.sub(r'_0_[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?=\.[a-z]+$)', '', name)
            if not name.startswith('host-probe-'):
                continue
            require(group.get('testIdentifier') in ('HostSettingsProbeTests/testUnchangedHostSettingsLanguageRoundTrip()', 'HostSettingsProbeUITests/HostSettingsProbeTests/testUnchangedHostSettingsLanguageRoundTrip()'), 'Probe attachment belongs to another test')
            require(name not in rows, 'Duplicate probe attachment name')
            relative = entry['exportedFileName']
            safe_relative(relative)
            path = directory / relative
            require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(directory.resolve()), 'Attachment missing or escapes export')
            require(path.stat().st_size <= 16 * 1024 * 1024, 'Oversized probe attachment')
            rows[name] = path
    require(len(rows) <= 38, 'Too many probe attachments')
    require(len({str(v) for v in rows.values()}) == len(rows), 'Aliased probe attachments')
    return rows


def verify_artifacts(directory,identity,builder,host,startup):
 attach_helpers(host)
 rows=attachment_map(directory)
 report=strict_json(rows['host-probe-observations.json'].read_text())
 expected={'sourceCommit':host.SOURCE,'sourceTree':host.TREE,'builderCommit':builder,'bundleIdentifier':host.BUNDLE_ID,'executableSHA256':identity['executableSHA256']}
 result=verify_rows(report,expected)
 names={'host-probe-observations.json','host-probe-launch.png','host-probe-launch.txt'}|startup.verify_navigation(report)
 names|=verify_recurrence(report,rows)
 for stage,code,native,title,pstage in STAGES:
  for prefix in [stage,'preference-'+pstage]:
   names|={'host-probe-'+prefix+'.png','host-probe-'+prefix+'.txt'}
 require(set(rows)==names,'Missing or unexpected observation attachments')
 for name,path in rows.items():
  if name.endswith('.png'):require(path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'),'Invalid screenshot')
  elif name.endswith('.txt'):require(0<path.stat().st_size<=512*1024 and path.read_text().strip(),'Missing/oversized AX')
 return {'report':report,'scope':result,'attachments':{n:{'sha256':sha(p),'bytes':p.stat().st_size}for n,p in rows.items()}}

def verify_recurrence(report,attachments):
 events=report.get('settingsRecurrence')
 require(isinstance(events,list) and len(events) in (0,2),'Incomplete or repeated Settings acknowledgement')
 if not events:return set()
 fault=report.get('serviceFailure')
 require(isinstance(fault,dict) and fault.get('alertAbsentAfterAcknowledgement')is True,'Recurrence has no completed startup predecessor')
 flags=['preTapInventoryVerified','tapAttempted','tapReturned','allAlertsAbsentAfterTap']
 names=set()
 for event,phase in zip(events,['identified','acknowledged']):
  expected={'phase':phase,'stage':'settings-entry-service-recurrence','diagnosticCode':fault['diagnosticCode'],'title':fault['title'],'body':fault['body'],'actualStaticTextLabels':sorted([fault['title'],fault['body']]),'buttons':['Copy Diagnostics','OK'],'presentationOnly':True,'resolved':False,'backendReadinessProven':False,**{k:phase=='acknowledged'for k in flags}}
  require(event==expected and all(type(event.get(k))is bool for k in flags+['presentationOnly','resolved','backendReadinessProven']),'Changed or incomplete Settings recurrence')
  name='host-probe-settings-recurrence-'+phase+'.json';names.add(name)
  require(name in attachments,'Missing durable recurrence receipt')
  require(strict_json(attachments[name].read_text())=={'identity':report['identity'],'receipt':event},'Recurrence receipt differs from final report')
 for phase in ['before','after']:
  names|={'host-probe-settings-recurrence-'+phase+'.png','host-probe-settings-recurrence-'+phase+'.txt'}
 return names
