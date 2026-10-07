// DEBUG/TEMPORARY: remove after the Anisette failure is diagnosed.
// The build reads V3TemporaryAnisetteTrace.temporaryAnisetteTraceEnabled.
// This trace belongs to one native call; it never contains data from the call.
enum class NativeOTPStage {
    ArgumentsOK, ArgumentsFailed, RootOK, RootFailed,
    DirectoryCreated, DirectoryExists, DirectoryFailed,
    FileOpenOK, FileOpenFailed, FileStreamOK, FileStreamFailed,
    FileWriteOK, FileWriteFailed, FileFlushOK, FileFlushFailed, FileFlushUnchecked,
    FileCloseOK, FileCloseFailed, FileReadOpenOK, FileReadOpenFailed,
    FileReadbackOK, FileReadbackFailed, FileReadbackUnchecked,
    FileReadCloseOK, FileReadCloseFailed, FileRenameOK, FileRenameFailed,
    VMInitOK, VMInitFailed, VMReused, SetupBegin, SetupOK, SetupFailed,
    LibraryLoadOK, LibraryLoadFailed, LibraryCached, LibraryInitOK, LibraryInitFailed,
    ProvisioningPathOK, ProvisioningPathFailed, ProvisioningPathCached,
    AndroidIDOK, AndroidIDFailed, AndroidIDCached,
    NativeSymbolOK, NativeSymbolFailed, NativeOTPOK, NativeOTPFailed,
    NativeOutputOK, NativeOutputFailed, NativeOutputUnchecked,
    CleanupOK, CleanupFailed, CleanupNotNeeded, CleanupNotRequested, ResponseAllocationFailed, Truncated
};

struct NativeOTPTrace {
    // Reserve the final entry for a truncation marker. No dynamic allocation
    // occurs during recording, including cleanup and exceptional exits.
    static constexpr size_t max_events = 32, max_bytes = 1024;
    char value[max_bytes + 1] = {};
    size_t count = 0, length = 0;
    char **output;
    explicit NativeOTPTrace(char **out) : output(out) {}

    void add(NativeOTPStage stage) noexcept {
#if V3_TEMPORARY_ANISETTE_TRACE_ENABLED
        static const char *const tokens[] = {
            "arguments.ok", "arguments.failed", "root.ok", "root.failed",
            "uuid_dir.created", "uuid_dir.exists", "uuid_dir.failed",
            "file.open.ok", "file.open.failed", "file.stream.ok", "file.stream.failed",
            "file.write.ok", "file.write.failed", "file.flush.ok", "file.flush.failed", "file.flush.not_checked",
            "file.close.ok", "file.close.failed", "file.read_open.ok", "file.read_open.failed",
            "file.readback.ok", "file.readback.failed", "file.readback.not_checked",
            "file.read_close.ok", "file.read_close.failed", "file.rename.ok", "file.rename.failed",
            "vm.init.ok", "vm.init.failed", "vm.reused", "setup.begin", "setup.ok", "setup.failed",
            "library.load.ok", "library.load.failed", "library.cached", "library.init.ok", "library.init.failed",
            "provisioning_path.ok", "provisioning_path.failed", "provisioning_path.cached",
            "android_id.ok", "android_id.failed", "android_id.cached",
            "native.symbol.ok", "native.symbol.failed", "native.otp.ok", "native.otp.failed",
            "native.output.ok", "native.output.failed", "native.output.not_checked",
            "cleanup.ok", "cleanup.failed", "cleanup.not_needed", "cleanup.not_requested", "response.allocation.failed", "trace.truncated"
        };
        const size_t index = static_cast<size_t>(stage);
        if (index >= sizeof(tokens) / sizeof(tokens[0]) || count >= max_events) return;
        const char *token = tokens[index];
        if (count == max_events - 1 || length + strlen(token) + 1 > max_bytes - 16)
            token = "trace.truncated";
        const size_t size = strlen(token);
        if (length + size + (count ? 1 : 0) > max_bytes) return;
        if (count) value[length++] = ',';
        memcpy(value + length, token, size + 1);
        length += size;
        ++count;
        if (strcmp(token, "trace.truncated") == 0) count = max_events;
#else
        (void)stage;
#endif
    }

    ~NativeOTPTrace() noexcept {
#if V3_TEMPORARY_ANISETTE_TRACE_ENABLED
        if (!count || !output || !*output) return;
        const size_t original_length = strlen(*output);
        if (original_length < 2 || (*output)[0] != '{' || (*output)[original_length - 1] != '}') return;
        // This is private metadata, removed by Swift before constructing headers.
        // All trace bytes come from the finite literal table above.
        const char prefix[] = ",\"v3_native_trace\":\"";
        char *combined = static_cast<char *>(malloc(original_length + sizeof(prefix) + length + 3));
        if (!combined) return; // Diagnostics must not change the original result.
        memcpy(combined, *output, original_length - 1);
        size_t cursor = original_length - 1;
        memcpy(combined + cursor, prefix, sizeof(prefix) - 1); cursor += sizeof(prefix) - 1;
        memcpy(combined + cursor, value, length); cursor += length;
        memcpy(combined + cursor, "\"}", 3);
        free_c_string(*output);
        *output = combined;
#endif
    }
};

struct NativeOTPStageScope {
    NativeOTPTrace *trace;
    NativeOTPStage failure;
    bool completed = false;
    NativeOTPStageScope(NativeOTPTrace *owner, NativeOTPStage failed) : trace(owner), failure(failed) {}
    void finish(NativeOTPStage outcome) noexcept {
        if (trace) trace->add(outcome);
        completed = true;
    }
    ~NativeOTPStageScope() { if (!completed && trace) trace->add(failure); }
};
