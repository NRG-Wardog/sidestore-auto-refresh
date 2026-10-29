"""Run the unchanged combined packager in a fresh, source-independent workspace."""
import hashlib,json,os,shutil,stat,subprocess,sys
from pathlib import Path
ASSETS=('.github/build_github.sh','.github/sidelc/LCAppInfo.plist','entitlements.xml',
        'LiveProcess/LiveProcess.entitlements','ShareExtension/ShareExtension.entitlements',
        'LaunchAppExtension/LaunchAppExtension.entitlements','.github/sidelc/LiveWidgetExtension_adhoc.xml')
def require(ok,why):
    if not ok:raise ValueError(why)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def inventory(root):
    """Include ignored/untracked content and link identity, but not Git metadata."""
    result={}
    def walk(folder):
        for path in sorted(folder.iterdir()):
            if path.name=='.git':continue
            mode=path.lstat().st_mode;name=path.relative_to(root).as_posix()
            if stat.S_ISLNK(mode):result[name]={'kind':'link','target':os.readlink(path)}
            elif stat.S_ISDIR(mode):result[name]={'kind':'directory','mode':stat.S_IMODE(mode)};walk(path)
            elif stat.S_ISREG(mode):result[name]={'kind':'file','mode':stat.S_IMODE(mode),'sha256':sha(path)}
            else:raise ValueError('Nonregular source input')
    walk(root);return result

def regular_asset(root,name):
    path=root/name
    require(path.is_relative_to(root),'Escaping packaging input')
    for item in (path,*path.parents):
        if item==root:break
        require(not item.is_symlink(),'Linked packaging input')
    require(path.is_file(),'Missing packaging input '+name)
    return path

def run(source,host,side,output,work,script):
    source=source.resolve(strict=True);host=host.resolve(strict=True);side=side.resolve(strict=True)
    output=output.absolute();work=work.absolute();script=script.resolve(strict=True)
    require(not work.exists() and not work.is_symlink(),'Packaging workspace must be fresh')
    require(not work.resolve().is_relative_to(source) and not output.resolve().is_relative_to(source),'Packaging outputs must be outside source')
    before=inventory(source)
    assets={name:dict(sha256=sha(regular_asset(source,name)),mode=stat.S_IMODE((source/name).stat().st_mode)) for name in ASSETS}
    work.mkdir(parents=True)
    try:
        for name,row in assets.items():
            destination=work/name;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(regular_asset(source,name),destination)
            require(sha(destination)==row['sha256'] and stat.S_IMODE(destination.stat().st_mode)==row['mode'],'Packaging input copy differs')
        subprocess.run([sys.executable,str(script),'--root',str(work),'--host',str(host),'--side',str(side),'--output',str(output)],check=True)
        for name,row in assets.items():
            copied=regular_asset(work,name)
            require(sha(copied)==row['sha256'] and stat.S_IMODE(copied.stat().st_mode)==row['mode'],'Packaging asset changed during execution')
    finally:
        require(inventory(source)==before,'Source changed during isolated packaging')
    return {'schema':'isolated-combined-packaging-workspace-v1','source_unchanged':True,'asset_closure':assets,
            'source_inventory_sha256':hashlib.sha256(json.dumps(before,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'packager_sha256':sha(script)}
