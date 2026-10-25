// The active normal-provider fix. This does not replace its VM or identity.
#if defined(__GNUC__)
__attribute__((used))
#endif
extern const char v3_checked_anisette_staging_marker[] = "V3_CHECKED_ANISETTE_STAGING_V1";

struct CheckedAnisetteStagingFailure {
    bool failed = false;
    int posix_error = 0;
    void record(int actual_errno) noexcept {
        if (failed) return;
        failed = true;
        if (actual_errno > 0 && actual_errno <= 4095) posix_error = actual_errno;
    }
    std::string message() const {
        if (!posix_error) return "Checked OTP staging failed";
        return "Checked OTP staging failed (errno " + std::to_string(posix_error) + ")";
    }
};

#if !defined(_WIN32) && !defined(_MSC_VER)
static int checked_anisette_make_uuid_directory(const char *root, const uint8_t *identifier,
    CheckedAnisetteStagingFailure &failure) {
    // Keep the original mkdir position in setup, while refusing a linked root.
    const std::string uuid = format_uuid_string(identifier);
    const int descriptor = open(root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (descriptor < 0) { failure.record(errno); return -1; }
    struct stat identity;
    if (fstat(descriptor, &identity) != 0) {
        failure.record(errno); close(descriptor); errno = EPERM; return -1;
    }
    if (!S_ISDIR(identity.st_mode) || identity.st_uid != geteuid() || (identity.st_mode & 0022) != 0) {
        failure.record(0);
        close(descriptor); errno = EPERM; return -1;
    }
    const int result = mkdirat(descriptor, uuid.c_str(), 0755);
    const int saved_errno = errno;
    if (result != 0 && saved_errno != EEXIST) failure.record(saved_errno);
    close(descriptor);
    errno = saved_errno;
    return result;
}

struct CheckedAnisetteStagingFile {
    int root = -1, directory = -1;
    bool owns_temporary = false;
    struct stat temporary_identity = {};
    char temporary_name[96] = {};

    bool remove_owned_temporary() noexcept {
        if (!owns_temporary) return true;
        struct stat current;
        if (fstatat(directory, temporary_name, &current, AT_SYMLINK_NOFOLLOW) != 0)
            return errno == ENOENT;
        if (!S_ISREG(current.st_mode) || current.st_dev != temporary_identity.st_dev ||
            current.st_ino != temporary_identity.st_ino) return false;
        if (unlinkat(directory, temporary_name, 0) != 0) return false;
        owns_temporary = false;
        return true;
    }
    ~CheckedAnisetteStagingFile() {
        remove_owned_temporary();
        if (directory >= 0) close(directory);
        if (root >= 0) close(root);
    }
};

static bool checked_anisette_staging(const char *root, const uint8_t *identifier,
    const uint8_t *bytes, uint32_t length, NativeOTPTrace &trace, CheckedAnisetteStagingFailure &failure) {
    CheckedAnisetteStagingFile owned;
    const std::string uuid = format_uuid_string(identifier);
    struct stat root_identity, directory_identity;
    owned.root = open(root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (owned.root < 0) failure.record(errno);
    bool root_ok = owned.root >= 0;
    if (root_ok && fstat(owned.root, &root_identity) != 0) { failure.record(errno); root_ok = false; }
    if (root_ok && (!S_ISDIR(root_identity.st_mode) || root_identity.st_uid != geteuid() ||
        (root_identity.st_mode & 0022) != 0)) { failure.record(0); root_ok = false; }
    trace.add(root_ok ? NativeOTPStage::RootOK : NativeOTPStage::RootFailed);
    if (!root_ok) return false;
    owned.directory = openat(owned.root, uuid.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (owned.directory < 0) failure.record(errno);
    bool directory_ok = owned.directory >= 0;
    if (directory_ok && fstat(owned.directory, &directory_identity) != 0) { failure.record(errno); directory_ok = false; }
    if (directory_ok && (!S_ISDIR(directory_identity.st_mode) || directory_identity.st_uid != geteuid() ||
        (directory_identity.st_mode & 0022) != 0)) { failure.record(0); directory_ok = false; }
    trace.add(directory_ok ? NativeOTPStage::DirectoryExists : NativeOTPStage::DirectoryFailed);
    if (!directory_ok) return false;

    // All names below are bounded leaf names relative to the verified UUID dir.
    // Never follow or overwrite a linked/special destination, and never remove
    // the previous usable adi.pb until the checked temporary is promoted.
    auto destination_ok = [&]() {
        struct stat destination;
        if (fstatat(owned.directory, kADISymbols.adi_pb_filename, &destination, AT_SYMLINK_NOFOLLOW) != 0) {
            const int result_errno = errno;
            if (result_errno == ENOENT) return true;
            failure.record(result_errno); return false;
        }
        const bool okay = S_ISREG(destination.st_mode) && destination.st_uid == geteuid() && destination.st_nlink == 1;
        if (!okay) failure.record(0);
        return okay;
    };
    if (!destination_ok()) { trace.add(NativeOTPStage::FileOpenFailed); return false; }
    // The normal native mutex protects this counter. Never reuse another call's
    // leaf, including a partial file left behind by process termination.
    static uint64_t next_temporary = 0;
    int descriptor = -1;
    for (unsigned attempt = 0; attempt < 16; ++attempt) {
        snprintf(owned.temporary_name, sizeof(owned.temporary_name), ".adi.pb.checked-%ld-%llu",
            static_cast<long>(getpid()), static_cast<unsigned long long>(next_temporary++));
        descriptor = openat(owned.directory, owned.temporary_name,
            O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (descriptor >= 0) break;
        const int open_errno = errno;
        if (open_errno != EEXIST || attempt == 15) { failure.record(open_errno); break; }
    }
    trace.add(descriptor >= 0 ? NativeOTPStage::FileOpenOK : NativeOTPStage::FileOpenFailed);
    if (descriptor < 0) return false;
    const int temporary_stat = fstat(descriptor, &owned.temporary_identity);
    if (temporary_stat != 0) failure.record(errno);
    if (temporary_stat != 0 || !S_ISREG(owned.temporary_identity.st_mode) ||
        owned.temporary_identity.st_uid != geteuid() || owned.temporary_identity.st_nlink != 1) {
        failure.record(0);
        close(descriptor);
        trace.add(NativeOTPStage::FileStreamFailed);
        return false;
    }
    owned.owns_temporary = true;
    FILE *file = fdopen(descriptor, "wb");
    if (!file) failure.record(errno);
    trace.add(file ? NativeOTPStage::FileStreamOK : NativeOTPStage::FileStreamFailed);
    if (!file) {
        const int closed = close(descriptor);
        trace.add(closed == 0 ? NativeOTPStage::FileCloseOK : NativeOTPStage::FileCloseFailed);
        return false;
    }
    // Clear errno before stdio calls whose short result can be semantic rather
    // than a POSIX error. Never attach a prior operation's stale errno.
    errno = 0;
    bool okay = fwrite(bytes, 1, length, file) == length;
    if (!okay) failure.record(errno);
    trace.add(okay ? NativeOTPStage::FileWriteOK : NativeOTPStage::FileWriteFailed);
    errno = 0;
    const int flushed = fflush(file);
    if (flushed != 0) failure.record(errno);
    trace.add(flushed == 0 ? NativeOTPStage::FileFlushOK : NativeOTPStage::FileFlushFailed);
    errno = 0;
    const int closed = fclose(file);
    if (closed != 0) failure.record(errno);
    trace.add(closed == 0 ? NativeOTPStage::FileCloseOK : NativeOTPStage::FileCloseFailed);
    if (!okay || flushed != 0 || closed != 0) return false;

    descriptor = openat(owned.directory, owned.temporary_name,
        O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (descriptor < 0) failure.record(errno);
    trace.add(descriptor >= 0 ? NativeOTPStage::FileReadOpenOK : NativeOTPStage::FileReadOpenFailed);
    if (descriptor < 0) return false;
    struct stat read_identity;
    const int read_stat = fstat(descriptor, &read_identity);
    if (read_stat != 0) failure.record(errno);
    okay = read_stat == 0 && S_ISREG(read_identity.st_mode) &&
        read_identity.st_dev == owned.temporary_identity.st_dev &&
        read_identity.st_ino == owned.temporary_identity.st_ino && read_identity.st_nlink == 1;
    if (!okay) failure.record(0);
    file = fdopen(descriptor, "rb");
    if (!file) {
        failure.record(errno);
        const int read_closed = close(descriptor);
        trace.add(NativeOTPStage::FileReadbackFailed);
        trace.add(read_closed == 0 ? NativeOTPStage::FileReadCloseOK : NativeOTPStage::FileReadCloseFailed);
        return false;
    }
    uint8_t chunk[4096];
    uint32_t offset = 0;
    while (okay && offset < length) {
        const size_t count = std::min(sizeof(chunk), static_cast<size_t>(length - offset));
        errno = 0;
        const size_t read_count = fread(chunk, 1, count, file);
        if (read_count != count) { failure.record(errno); okay = false; }
        else if (memcmp(chunk, bytes + offset, count) != 0) { failure.record(0); okay = false; }
        else offset += static_cast<uint32_t>(count);
    }
    errno = 0;
    const int end = fgetc(file);
    const int end_errno = errno;
    if (end != EOF) { failure.record(0); okay = false; }
    else if (ferror(file)) { failure.record(end_errno); okay = false; }
    trace.add(okay ? NativeOTPStage::FileReadbackOK : NativeOTPStage::FileReadbackFailed);
    errno = 0;
    const int read_closed = fclose(file);
    if (read_closed != 0) failure.record(errno);
    trace.add(read_closed == 0 ? NativeOTPStage::FileReadCloseOK : NativeOTPStage::FileReadCloseFailed);
    if (!okay || read_closed != 0) return false;

    // Detect a removed/replaced UUID directory or temporary before promotion.
    // The existing Swift directory-cleanup race after this point is unchanged.
    struct stat current_root, current_directory, current_temporary;
    bool still_owned = true;
    if (lstat(root, &current_root) != 0) { failure.record(errno); still_owned = false; }
    else if (!S_ISDIR(current_root.st_mode) || current_root.st_dev != root_identity.st_dev ||
        current_root.st_ino != root_identity.st_ino) { failure.record(0); still_owned = false; }
    if (still_owned && fstatat(owned.root, uuid.c_str(), &current_directory, AT_SYMLINK_NOFOLLOW) != 0) {
        failure.record(errno); still_owned = false;
    } else if (still_owned && (!S_ISDIR(current_directory.st_mode) || current_directory.st_dev != directory_identity.st_dev ||
        current_directory.st_ino != directory_identity.st_ino)) { failure.record(0); still_owned = false; }
    if (still_owned && fstatat(owned.directory, owned.temporary_name,
        &current_temporary, AT_SYMLINK_NOFOLLOW) != 0) { failure.record(errno); still_owned = false; }
    if (still_owned && (!S_ISREG(current_temporary.st_mode) || current_temporary.st_dev != owned.temporary_identity.st_dev ||
        current_temporary.st_ino != owned.temporary_identity.st_ino || current_temporary.st_nlink != 1)) {
        failure.record(0); still_owned = false;
    }
    bool promoted = still_owned && destination_ok();
    if (promoted && renameat(owned.directory, owned.temporary_name,
        owned.directory, kADISymbols.adi_pb_filename) != 0) { failure.record(errno); promoted = false; }
    trace.add(promoted ? NativeOTPStage::FileRenameOK : NativeOTPStage::FileRenameFailed);
    if (!promoted) return false;
    owned.owns_temporary = false;
    return true;
}
#else
static int checked_anisette_make_uuid_directory(const char *, const uint8_t *, CheckedAnisetteStagingFailure &failure) {
    failure.record(0);
    errno = EINVAL;
    return -1;
}
static bool checked_anisette_staging(const char *, const uint8_t *, const uint8_t *, uint32_t, NativeOTPTrace &trace,
    CheckedAnisetteStagingFailure &failure) {
    failure.record(0);
    trace.add(NativeOTPStage::FileOpenFailed);
    return false;
}
#endif
