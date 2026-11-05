"""Exec only the reviewed compressor with clean environment and per-process bounds."""
import os,pathlib,resource,sys
from contract import require,ITERATIONS
require(__debug__,'Python optimization forbidden')
root=pathlib.Path(__file__).resolve().parent
require(len(sys.argv)==3 and sys.argv[1].isdigit() and sys.argv[2].isdigit(),'limiter arguments')
index,iterations=int(sys.argv[1]),int(sys.argv[2])
require(0<=index<512 and iterations in ITERATIONS,'limiter approved iteration/index')
source=root/'inputs'/str(index)
require(source.is_file() and not source.is_symlink() and source.stat().st_size<=20*1024**2,'owned bounded input required')
resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
resource.setrlimit(resource.RLIMIT_CPU,(1800,1800))
resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024**2,64*1024**2))
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
binary=str(root/'zopfli')
os.execve(binary,[binary,'--deflate',f'--i{iterations}','-c',str(source)],{'PATH':'/usr/bin:/bin','LANG':'C'})
