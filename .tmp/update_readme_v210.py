from pathlib import Path
import base64
import re
import shutil
import zipfile

root = Path('.')
tmp = root / '.tmp'
screens = root / 'docs' / 'screenshots'
screens.mkdir(parents=True, exist_ok=True)

archive = tmp / 'screenshots-v2.1.0.zip'
archive.write_bytes(base64.b64decode((tmp / 'screenshots-v2.1.0.zip.b64').read_text().strip()))
extract = tmp / 'v210shots'
extract.mkdir(exist_ok=True)
with zipfile.ZipFile(archive) as z:
    z.extractall(extract)

mapping = {
    'guest-controls-web220.jpg': 'v2.1.0-guest-controls.jpg',
    'refresh-history-web220.jpg': 'v2.1.0-refresh-history.jpg',
    'refresh-schedule-web220.jpg': 'v2.1.0-refresh-schedule.jpg',
    'refresh-status-web220.jpg': 'v2.1.0-refresh-status.jpg',
}
for src, dst in mapping.items():
    shutil.copy2(extract / src, screens / dst)

path = root / 'README.md'
text = path.read_text(encoding='utf-8')

preview = '''## Preview

### Combined LiveContainer + SideStore v2.1.0

These screenshots show the current **combined v2.1.0** interface, including refresh scheduling, refresh status/history, and the new Guest Controls options.

<table>
<tr>
<td align="center"><img src="docs/screenshots/v2.1.0-refresh-status.jpg" width="220" alt="LiveContainer SideStore refresh status"><br><strong>Refresh Status</strong></td>
<td align="center"><img src="docs/screenshots/v2.1.0-refresh-schedule.jpg" width="220" alt="LiveContainer SideStore refresh schedule"><br><strong>Refresh Schedule</strong></td>
<td align="center"><img src="docs/screenshots/v2.1.0-refresh-history.jpg" width="220" alt="LiveContainer SideStore refresh history"><br><strong>Refresh History</strong></td>
<td align="center"><img src="docs/screenshots/v2.1.0-guest-controls.jpg" width="220" alt="LiveContainer Guest Controls with Start Collapsed and custom colors"><br><strong>Guest Controls</strong></td>
</tr>
</table>

v2.1.0 adds **Start Collapsed** and **Use Custom Colors** for the Return control while keeping the existing manual refresh, scheduled refresh, verification, history, and LocalDevVPN/CoreDevice flow.

### Standalone SideStore v1.0.2

The screenshots below show the standalone SideStore v1.0.2 interface.

<table>
<tr>
<td align="center"><img src="docs/screenshots/settings-refreshing-apps.png" width="230" alt="SideStore Refreshing Apps settings"><br><strong>Refreshing Apps</strong></td>
<td align="center"><img src="docs/screenshots/refresh-schedule-main.png" width="230" alt="SideStore Refresh Schedule"><br><strong>Refresh Schedule</strong></td>
<td align="center"><img src="docs/screenshots/refresh-history.png" width="230" alt="SideStore refresh history"><br><strong>Refresh History</strong></td>
</tr>
</table>

'''
text, count = re.subn(r'## Preview\n.*?(?=## Quick navigation)', preview, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit('Preview section anchor not found')

replacements = {
    '| LiveContainer with the modified SideStore built in | **Combined v2.0.1** | **[Download combined IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.0.1/LiveContainer-SideStore-AutoRefresh-v2.0.1.ipa)** |': '| LiveContainer with the modified SideStore built in | **Combined v2.1.0** | **[Download combined IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.1.0/LiveContainer-SideStore-AutoRefresh-v2.1.0.ipa)** |',
    'If you want LiveContainer, install **v2.0.1**.': 'If you want LiveContainer, install **v2.1.0**.',
    '**Combined:** [v2.0.1 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.0.1) | [SHA256SUMS](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.0.1/SHA256SUMS.txt)': '**Combined:** [v2.1.0 release](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.0) | [SHA256SUMS](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.1.0/SHA256SUMS.txt)',
    '**Combined LiveContainer + SideStore v2.0.1**': '**Combined LiveContainer + SideStore v2.1.0**',
    '[Download `LiveContainer-SideStore-AutoRefresh-v2.0.1.ipa`](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.0.1/LiveContainer-SideStore-AutoRefresh-v2.0.1.ipa)': '[Download `LiveContainer-SideStore-AutoRefresh-v2.1.0.ipa`](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v2.1.0/LiveContainer-SideStore-AutoRefresh-v2.1.0.ipa)',
    'Use this section only for **LiveContainer + SideStore v2.0.1**.': 'Use this section only for **LiveContainer + SideStore v2.1.0**.',
    '| Combined v2.0.1 build and packaging | Release builds and package/runtime checks passed |': '| Combined v2.1.0 build and packaging | Release builds and package/runtime checks passed |',
    'Source builds after v2.0.1 add these options in **LiveContainer Settings > Guest Controls**:': '**v2.1.0 adds these options in LiveContainer Settings > Guest Controls:**',
}
for old, new in replacements.items():
    if old not in text:
        print(f'warning: replacement anchor not found: {old[:100]}')
    text = text.replace(old, new)

text = text.replace(
    '| Guest navigation | Upstream LiveContainer provides its normal guest controls | Adds project-specific **Return** controls for the supported LiveProcess flows |',
    '| Guest navigation | Upstream LiveContainer provides its normal guest controls | Adds project-specific **Return** controls, plus v2.1.0 **Start Collapsed** and custom icon/background colors |'
)

text = text.replace(
    '- v2.0.1 guest-signature fix so advisory guest checks do not overwrite a verified successful refresh',
    '- v2.0.1 guest-signature fix so advisory guest checks do not overwrite a verified successful refresh\n- v2.1.0 **Start Collapsed** Return control option\n- v2.1.0 custom Return icon and button background colors with saved preferences'
)

provenance_anchor = '### v2.0.1 provenance\n'
v210 = '''### v2.1.0 provenance

- IPA: `LiveContainer-SideStore-AutoRefresh-v2.1.0.ipa`
- Builder/tag commit: `2372c3e96132cb06394dafdd9ff74aeca416dd1c`
- Combined CI run: [34497164738](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34497164738)
- SHA-256: `6493e1e8c525a3b1ea343973bf199e30069f1c837f1a6175f3befa05d7a0eb50`
- Release: [LiveContainer + SideStore Auto-Refresh v2.1.0](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.0)

'''
if provenance_anchor not in text:
    raise SystemExit('v2.0.1 provenance anchor not found')
text = text.replace(provenance_anchor, v210 + provenance_anchor, 1)

path.write_text(text, encoding='utf-8')

placeholder = screens / 'v2.1.0-guest-controls.jpg.b64'
if placeholder.exists():
    placeholder.unlink()

for p in [tmp / 'screenshots-v2.1.0.zip.b64', archive]:
    if p.exists():
        p.unlink()
shutil.rmtree(extract, ignore_errors=True)

workflow = root / '.github' / 'workflows' / 'update-readme-v2.1.yml'
if workflow.exists():
    workflow.unlink()

Path(__file__).unlink()
