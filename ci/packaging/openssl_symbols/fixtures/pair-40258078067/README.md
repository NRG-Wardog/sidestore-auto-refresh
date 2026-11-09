# Native baseline-equivalence regression

These eight unmodified logs came from run `40258078067`; `manifest.json` binds
the verified evidence ZIP and each log. Tests replay the exact row parser after
replacing only the known runner library paths with temporary fixture paths.
This is historical log replay, not another native execution.

Both original and transformed cases selected the first owner for `RTLD_DEFAULT`
after OpenSSL initialization, including the cases initially opened `RTLD_LOCAL`.
Every handle, version, symbol and provider row was identical before/after after
normalizing only the original/transformed path component. The acceptance
contract is exact same-source binding equivalence, **not local visibility
isolation**. Changed known-image bindings still fail.

Apple's [dlopen manual](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man3/dlopen.3.html)
documents GLOBAL as the default if neither LOCAL nor GLOBAL is supplied.
The SHA-verified official OpenSSL 3.5.9 archive
`603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a`
contains a retention path from `crypto/init.c` through `DSO_dsobyaddr` to
`crypto/dso/dso_dlfcn.c`, which can reopen an image with `RTLD_NOW` without LOCAL.
This is a possible source-supported explanation, **not a traced syscall claim**.
No conclusion that Darwin LOCAL libraries are generally globally visible follows.
