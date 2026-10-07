// V3_ISOLATED_ANISETTE_OTP_V1. A disposable VM and private copy only.
// Dormant API provenance; the active build gate requires checked normal staging.
extern const char v3_isolated_anisette_otp_marker[] = "V3_ISOLATED_ANISETTE_OTP_V1";

int32_t get_anisette_headers_uc(
    const char *lib_dir, const char *provisioning_dir, const uint8_t *identifier,
    const uint8_t *adi_pb, uint32_t adi_pb_len, char **out_json
) {
    NativeOTPTrace trace(nullptr);
    std::lock_guard<std::mutex> lock(g_vm_mutex);
    const int32_t result = get_anisette_headers_uc_locked(lib_dir, provisioning_dir, identifier,
                                         adi_pb, adi_pb_len, out_json, false, trace);
    // Invalid original arguments do not initialize the caller's output pointer.
    if (result != ANISETTE_ERR_INVALID_ARGUMENT) trace.output = out_json;
    // No UUID-directory cleanup here. Checked staging owns only its temporary;
    // the existing Swift directory-cleanup race remains outside this mutex.
    trace.add(NativeOTPStage::CleanupNotRequested);
    return result;
}

static int32_t isolated_otp_error(char **out_json, int32_t code, const char *message) {
    if (*out_json) { free_c_string(*out_json); *out_json = nullptr; }
    // Every caller supplies a fixed, non-sensitive JSON literal.
    *out_json = strdup(message);
    return code;
}

struct IsolatedOTPLogging {
    bool saved = g_isolated_otp_logging_suppressed;
    IsolatedOTPLogging() { g_isolated_otp_logging_suppressed = true; }
    ~IsolatedOTPLogging() { g_isolated_otp_logging_suppressed = saved; }
};

#if !defined(_WIN32) && !defined(_MSC_VER)
struct IsolatedOTPFiles {
    std::string directory, temporary, blob;
    bool owns_directory = false;
    bool cleanup_reported = false;
    NativeOTPTrace &trace;

    IsolatedOTPFiles(const char *root, const uint8_t *identifier, NativeOTPTrace &owner)
        : directory(std::string(root) + "/" + format_uuid_string(identifier)),
          temporary(directory + "/.adi.pb.staging"), blob(directory + "/adi.pb"), trace(owner) {}

    bool cleanup() noexcept {
        if (!owns_directory) {
            if (!cleanup_reported) trace.add(NativeOTPStage::CleanupNotNeeded);
            cleanup_reported = true;
            return true;
        }
        bool okay = true;
        if (unlink(temporary.c_str()) != 0 && errno != ENOENT) okay = false;
        if (unlink(blob.c_str()) != 0 && errno != ENOENT) okay = false;
        if (rmdir(directory.c_str()) != 0 && errno != ENOENT) okay = false;
        if (okay) owns_directory = false;
        trace.add(okay ? NativeOTPStage::CleanupOK : NativeOTPStage::CleanupFailed);
        cleanup_reported = true;
        return okay;
    }
    ~IsolatedOTPFiles() { cleanup(); }

    bool stage(const char *root, const uint8_t *bytes, uint32_t length) {
        struct stat st;
        // The root must be a private directory belonging to this process's user.
        // Never reuse a pre-existing UUID child, including a symbolic link.
        if (lstat(root, &st) != 0 || !S_ISDIR(st.st_mode) ||
            st.st_uid != geteuid() || (st.st_mode & 0077) != 0) {
            trace.add(NativeOTPStage::RootFailed);
            return false;
        }
        trace.add(NativeOTPStage::RootOK);
        if (mkdir(directory.c_str(), 0700) != 0) {
            trace.add(errno == EEXIST ? NativeOTPStage::DirectoryExists : NativeOTPStage::DirectoryFailed);
            return false;
        }
        trace.add(NativeOTPStage::DirectoryCreated);
        owns_directory = true;
        int fd = open(temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
        trace.add(fd >= 0 ? NativeOTPStage::FileOpenOK : NativeOTPStage::FileOpenFailed);
        if (fd < 0) return false;
        FILE *file = fdopen(fd, "wb");
        trace.add(file ? NativeOTPStage::FileStreamOK : NativeOTPStage::FileStreamFailed);
        if (!file) {
            const int closed = close(fd);
            trace.add(closed == 0 ? NativeOTPStage::FileCloseOK : NativeOTPStage::FileCloseFailed);
            return false;
        }
        bool okay = fwrite(bytes, 1, length, file) == length;
        trace.add(okay ? NativeOTPStage::FileWriteOK : NativeOTPStage::FileWriteFailed);
        const int flushed = fflush(file);
        trace.add(flushed == 0 ? NativeOTPStage::FileFlushOK : NativeOTPStage::FileFlushFailed);
        if (flushed != 0) okay = false;
        const int closed = fclose(file);
        trace.add(closed == 0 ? NativeOTPStage::FileCloseOK : NativeOTPStage::FileCloseFailed);
        if (closed != 0) okay = false;
        if (!okay) return false;

        file = fopen(temporary.c_str(), "rb");
        trace.add(file ? NativeOTPStage::FileReadOpenOK : NativeOTPStage::FileReadOpenFailed);
        if (!file) return false;
        uint8_t chunk[4096];
        uint32_t offset = 0;
        while (okay && offset < length) {
            size_t count = std::min(sizeof(chunk), static_cast<size_t>(length - offset));
            if (fread(chunk, 1, count, file) != count || memcmp(chunk, bytes + offset, count) != 0) {
                okay = false;
            } else { offset += static_cast<uint32_t>(count); }
        }
        if (fgetc(file) != EOF || ferror(file)) okay = false;
        trace.add(okay ? NativeOTPStage::FileReadbackOK : NativeOTPStage::FileReadbackFailed);
        const int read_closed = fclose(file);
        trace.add(read_closed == 0 ? NativeOTPStage::FileReadCloseOK : NativeOTPStage::FileReadCloseFailed);
        if (read_closed != 0) okay = false;
        if (!okay) return false;
        const bool renamed = rename(temporary.c_str(), blob.c_str()) == 0;
        trace.add(renamed ? NativeOTPStage::FileRenameOK : NativeOTPStage::FileRenameFailed);
        return renamed;
    }
};

struct IsolatedOTPVM {
    EmulatorVM *saved_vm;
    std::string saved_path, saved_id;
    bool saved_initialized;
    std::unique_ptr<EmulatorVM> probe;

    IsolatedOTPVM()
        : saved_vm(g_shared_vm), saved_initialized(g_libraries_initialized),
          probe(new EmulatorVM(true)) {
        probe->read_only_filesystem = true;
        saved_path.swap(g_current_prov_path);
        saved_id.swap(g_current_android_id);
        g_shared_vm = probe.get();
        g_libraries_initialized = false;
    }
    ~IsolatedOTPVM() {
        g_shared_vm = saved_vm;
        g_current_prov_path.swap(saved_path);
        g_current_android_id.swap(saved_id);
        g_libraries_initialized = saved_initialized;
        // probe is destroyed after restoring the regular provider's state.
    }
};
#endif

int32_t get_anisette_headers_isolated_uc(
    const char *lib_dir, const char *provisioning_dir, const uint8_t *identifier,
    const uint8_t *adi_pb, uint32_t adi_pb_len, char **out_json
) {
    if (!out_json) return ANISETTE_ERR_INVALID_ARGUMENT;
    *out_json = nullptr;
    NativeOTPTrace trace(out_json);
    if (!lib_dir || !provisioning_dir || !identifier || !adi_pb ||
        !*lib_dir || !*provisioning_dir || adi_pb_len == 0 || adi_pb_len > 1048576) {
        trace.add(NativeOTPStage::ArgumentsFailed);
        return isolated_otp_error(out_json, -1, "{\"error\":\"Invalid isolated OTP argument\"}");
    }
    trace.add(NativeOTPStage::ArgumentsOK);
#if defined(_WIN32) || defined(_MSC_VER)
    return isolated_otp_error(out_json, -1, "{\"error\":\"Isolated OTP unsupported platform\"}");
#else
    // Covers setup, checked staging, OTP, VM destruction and cleanup. Normal
    // native calls use this same mutex and cannot observe swapped globals.
    std::lock_guard<std::mutex> lock(g_vm_mutex);
    IsolatedOTPLogging log_scope;
    try {
        IsolatedOTPFiles files(provisioning_dir, identifier, trace);
        if (!files.stage(provisioning_dir, adi_pb, adi_pb_len)) {
            return isolated_otp_error(out_json, -6, "{\"error\":\"Isolated OTP staging failed\"}");
        }
        int32_t result;
        {
            NativeOTPStageScope init_stage(&trace, NativeOTPStage::VMInitFailed);
            IsolatedOTPVM context;
            init_stage.finish(NativeOTPStage::VMInitOK);
            result = get_anisette_headers_uc_locked(lib_dir, provisioning_dir,
                identifier, adi_pb, adi_pb_len, out_json, true, trace);
        }
        if (!files.cleanup()) {
            return isolated_otp_error(out_json, -6, "{\"error\":\"Isolated OTP cleanup failed\"}");
        }
        if (!*out_json) {
            trace.add(NativeOTPStage::ResponseAllocationFailed);
            return isolated_otp_error(out_json, -7, "{\"error\":\"Isolated OTP allocation failed\"}");
        }
        return result;
    } catch (...) {
        return isolated_otp_error(out_json, -7, "{\"error\":\"Isolated OTP initialization failed\"}");
    }
#endif
}
