// The production feature set validates existing records without enabling Pair.
fn cellular_pairing_error_status(error: &IdeviceError) -> i32 {
    match error {
        IdeviceError::InvalidHostID => 2,
        #[cfg(feature = "pair")]
        IdeviceError::UserDeniedPairing => 2,
        _ => 3,
    }
}

/// Read-only authenticated validation for the embedded diagnostic.
/// The provider is BORROWED on every return path. No secret values leave Rust.
/// status: 0 accepted, 1 connect/read failure, 2 explicit pairing rejection,
/// 3 session/TLS failure (not necessarily bad pairing), 4 identity mismatch,
/// 5 timeout, 6 invalid argument. error_code contains only the numeric category.
///
/// # Safety
/// provider and error_code must be valid, non-null pointers for the entire call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn cellular_lockdown_validate(
    provider: *mut IdeviceProviderHandle,
    error_code: *mut i32,
) -> i32 {
    if provider.is_null() || error_code.is_null() {
        return 6;
    }
    unsafe { *error_code = 0 };
    let result = run_sync_local(async {
        let provider_ref: &dyn IdeviceProvider = unsafe { &*(*provider).0 };
        tokio::time::timeout(std::time::Duration::from_secs(20), async {
            let pairing = provider_ref.get_pairing_file().await.map_err(|e| (1, e.code()))?;
            let mut client = LockdownClient::connect(provider_ref).await.map_err(|e| (1, e.code()))?;
            client.start_session(&pairing).await.map_err(|e| {
                (cellular_pairing_error_status(&e), e.code())
            })?;
            let actual = client.get_value(Some("UniqueDeviceID"), None).await
                .map_err(|e| (1, e.code()))?;
            let expected = pairing.udid.as_deref().filter(|s| !s.is_empty());
            if expected.is_none() || actual.as_string() != expected {
                return Err((4, 0));
            }
            Ok::<(), (i32, i32)>(())
        }).await
    });
    match result {
        Ok(Ok(())) => 0,
        Ok(Err((status, code))) => { unsafe { *error_code = code }; status }
        Err(_) => 5,
    }
}
