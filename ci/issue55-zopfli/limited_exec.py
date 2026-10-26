"""Own resource limiter: replaces itself with the sole reviewed compressor."""
import os,pathlib,resource,sys
if not __debug__:raise RuntimeError('Python optimization is not permitted for qualification')
root=pathlib.Path(__file__).resolve().parent
assert len(sys.argv)==2 and sys.argv[1].isdigit()
source=root/'inputs'/sys.argv[1]
assert source.is_file() and not source.is_symlink() and source.stat().st_size<=20*1024**2
resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
resource.setrlimit(resource.RLIMIT_CPU,(600,600))
resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024**2,64*1024**2))
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
binary=str(root/'zopfli')
os.execve(binary,[binary,'--deflate','--i5','-c',str(source)],{'PATH':'/usr/bin:/bin','LANG':'C'})
