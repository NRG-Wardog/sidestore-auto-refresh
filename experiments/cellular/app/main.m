#import <UIKit/UIKit.h>
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#import <Network/Network.h>
#import <os/log.h>
#include <arpa/inet.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <string.h>
#include "idevice.h"
#include "ProbePolicy.h"

@interface ProbeViewController : UIViewController <UIDocumentPickerDelegate>
@property(nonatomic, strong) UITextField *peer;
@property(nonatomic, strong) UISegmentedControl *mode;
@property(nonatomic, strong) UITextView *output;
@property(nonatomic, strong) UIButton *runButton;
@property(nonatomic, strong) UIButton *importButton;
@property(nonatomic) BOOL running;
@property(atomic) BOOL interrupted;
@property(nonatomic, copy) NSString *runID;
@property(nonatomic, strong) NSMutableArray<NSString *> *lines;
@property(nonatomic, strong) dispatch_queue_t worker;
- (void)record:(NSString *)event;
- (UIButton *)button:(NSString *)title icon:(NSString *)icon action:(SEL)action;
- (BOOL)probeAddress:(struct in_addr)address pairing:(NSData *)pairing;
- (void)saveReport;
@end

static __weak ProbeViewController *currentProbe;

static NSURL *PairingURL(void) {
    NSURL *base = [NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory
                                                     inDomains:NSUserDomainMask].firstObject;
    return [base URLByAppendingPathComponent:@"pairing.plist"];
}

// Never forward arbitrary Rust messages: service errors can contain private data.
static void TransportLog(const char *raw) {
    @autoreleasepool {
        NSString *message = raw ? [NSString stringWithUTF8String:raw] : nil;
        if (!message) return;
        for (NSString *stage in @[@"HEARTBEAT_CONNECT_PASS", @"HEARTBEAT_CONNECT_FAIL",
                                  @"TUNNEL_COREDEVICE_CONNECT_START", @"TUNNEL_COREDEVICE_CONNECT_PASS",
                                  @"TUNNEL_RSD_HANDSHAKE_PASS", @"HEARTBEAT_STOPPED"]) {
            if ([message containsString:stage]) {
                [currentProbe record:stage];
                break;
            }
        }
    }
}

static BOOL CheckError(ProbeViewController *view, IdeviceFfiError *error, NSString *stage) {
    if (!error) return YES;
    [view record:[NSString stringWithFormat:@"FAIL stage=%@ domain=idevice code=%d sub_code=%d",
                  stage, error->code, error->sub_code]];
    idevice_error_free(error);
    return NO;
}

// One bounded observation, not a persistent monitor or a periodic probe.
static int SamplePath(nw_interface_type_t type) {
    nw_path_monitor_t monitor = nw_path_monitor_create_with_type(type);
    dispatch_queue_t queue = dispatch_queue_create("cellular.probe.path", DISPATCH_QUEUE_SERIAL);
    dispatch_semaphore_t ready = dispatch_semaphore_create(0);
    __block int result = -1;
    __block BOOL received = NO;
    nw_path_monitor_set_queue(monitor, queue);
    nw_path_monitor_set_update_handler(monitor, ^(nw_path_t path) {
        if (received) return;
        received = YES;
        result = nw_path_get_status(path) == nw_path_status_satisfied && nw_path_uses_interface_type(path, type);
        dispatch_semaphore_signal(ready);
    });
    nw_path_monitor_start(monitor);
    long timedOut = dispatch_semaphore_wait(ready, dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC));
    dispatch_sync(queue, ^{
        received = YES;
        nw_path_monitor_cancel(monitor);
        if (timedOut) result = -1;
    });
    return result;
}

static void LogInterfaces(ProbeViewController *view) {
    struct ifaddrs *list = NULL;
    BOOL tunnel = NO, ipsec = NO, cellular = NO;
    if (getifaddrs(&list) != 0) { [view record:@"INTERFACES_UNKNOWN"]; return; }
    for (struct ifaddrs *p = list; p; p = p->ifa_next) {
        if (!p->ifa_name || !(p->ifa_flags & IFF_UP)) continue;
        tunnel |= strncmp(p->ifa_name, "utun", 4) == 0;
        ipsec |= strncmp(p->ifa_name, "ipsec", 5) == 0;
        cellular |= strncmp(p->ifa_name, "pdp_ip", 6) == 0;
    }
    freeifaddrs(list);
    [view record:[NSString stringWithFormat:@"INTERFACES utun=%d ipsec=%d cellular=%d provider_identity=unknown", tunnel, ipsec, cellular]];
}

@implementation ProbeViewController
- (void)viewDidLoad {
    [super viewDidLoad];
    self.title = @"Cellular Probe";
    self.view.backgroundColor = UIColor.systemBackgroundColor;
    self.lines = [NSMutableArray array];
    self.runID = @"idle";
    self.worker = dispatch_queue_create("cellular.probe.ffi", DISPATCH_QUEUE_SERIAL);
    currentProbe = self;
    idevice_set_transport_log_callback(TransportLog);
    self.navigationItem.rightBarButtonItem = [[UIBarButtonItem alloc] initWithBarButtonSystemItem:UIBarButtonSystemItemAction target:self action:@selector(share)];
    self.peer = [UITextField new];
    self.peer.placeholder = @"LocalDevVPN Device / Peer IPv4";
    self.peer.borderStyle = UITextBorderStyleRoundedRect;
    self.peer.keyboardType = UIKeyboardTypeNumbersAndPunctuation;
    self.peer.autocorrectionType = UITextAutocorrectionTypeNo;
    self.peer.text = [NSUserDefaults.standardUserDefaults stringForKey:@"probe.peer"];
    self.mode = [[UISegmentedControl alloc] initWithItems:@[@"Wi-Fi baseline", @"Cellular"]];
    self.mode.selectedSegmentIndex = 0;
    self.importButton = [self button:@"Import Pairing" icon:@"doc.badge.plus" action:@selector(importPairing)];
    self.runButton = [self button:@"Run Read-Only Test" icon:@"play.fill" action:@selector(run)];
    UIStackView *controls = [[UIStackView alloc] initWithArrangedSubviews:@[self.peer, self.mode, self.importButton, self.runButton]];
    controls.axis = UILayoutConstraintAxisVertical;
    controls.spacing = 12;
    controls.translatesAutoresizingMaskIntoConstraints = NO;
    self.output = [UITextView new];
    self.output.editable = NO;
    self.output.font = [UIFont monospacedSystemFontOfSize:12 weight:UIFontWeightRegular];
    self.output.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:controls];
    [self.view addSubview:self.output];
    UILayoutGuide *safe = self.view.safeAreaLayoutGuide;
    [NSLayoutConstraint activateConstraints:@[
        [controls.topAnchor constraintEqualToAnchor:safe.topAnchor constant:12],
        [controls.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:16],
        [controls.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-16],
        [self.peer.heightAnchor constraintEqualToConstant:44],
        [self.importButton.heightAnchor constraintEqualToConstant:44],
        [self.runButton.heightAnchor constraintEqualToConstant:44],
        [self.output.topAnchor constraintEqualToAnchor:controls.bottomAnchor constant:12],
        [self.output.leadingAnchor constraintEqualToAnchor:safe.leadingAnchor constant:12],
        [self.output.trailingAnchor constraintEqualToAnchor:safe.trailingAnchor constant:-12],
        [self.output.bottomAnchor constraintEqualToAnchor:self.view.keyboardLayoutGuide.topAnchor]
    ]];
    [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(backgrounded) name:UIApplicationDidEnterBackgroundNotification object:nil];
    [self record:[NSString stringWithFormat:@"READY build=%@ os=%@ pairing_imported=%d",
        [NSBundle.mainBundle objectForInfoDictionaryKey:@"ProbeBuilderCommit"] ?: @"local",
        UIDevice.currentDevice.systemVersion, [NSFileManager.defaultManager fileExistsAtPath:PairingURL().path]]];
}
- (UIButton *)button:(NSString *)title icon:(NSString *)icon action:(SEL)action {
    UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem];
    UIButtonConfiguration *config = [UIButtonConfiguration tintedButtonConfiguration];
    config.title = title;
    config.image = [UIImage systemImageNamed:icon];
    config.imagePadding = 8;
    button.configuration = config;
    [button addTarget:self action:action forControlEvents:UIControlEventTouchUpInside];
    return button;
}
- (void)record:(NSString *)event {
    if (!NSThread.isMainThread) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self record:event]; });
        return;
    }
    NSString *line = [NSString stringWithFormat:@"%.3f run=%@ %@", NSDate.date.timeIntervalSince1970, self.runID, event];
    os_log_with_type(OS_LOG_DEFAULT, OS_LOG_TYPE_DEFAULT, "[CELLULAR_PROBE] %{public}s", line.UTF8String);
    [self.lines addObject:line];
    if (self.lines.count > 200) [self.lines removeObjectAtIndex:0];
    self.output.text = [self.lines componentsJoinedByString:@"\n"];
    [self.output scrollRangeToVisible:NSMakeRange(self.output.text.length, 0)];
}
- (void)backgrounded {
    if (self.running) {
        self.interrupted = YES;
        [self record:@"RUN_INTERRUPTED reason=app_backgrounded result_will_be_inconclusive"];
    }
}
- (void)importPairing {
    if (self.running) return;
    UIDocumentPickerViewController *picker = [[UIDocumentPickerViewController alloc] initForOpeningContentTypes:@[UTTypeData] asCopy:YES];
    picker.delegate = self;
    [self presentViewController:picker animated:YES completion:nil];
}
- (void)documentPicker:(UIDocumentPickerViewController *)controller didPickDocumentsAtURLs:(NSArray<NSURL *> *)urls {
    NSURL *url = urls.firstObject;
    if (!url || self.running) return;
    BOOL scoped = [url startAccessingSecurityScopedResource];
    NSError *error = nil;
    NSNumber *size = nil;
    [url getResourceValue:&size forKey:NSURLFileSizeKey error:&error];
    NSData *data = size && size.unsignedLongLongValue <= 1024 * 1024 ? [NSData dataWithContentsOfURL:url options:0 error:&error] : nil;
    if (scoped) [url stopAccessingSecurityScopedResource];
    id plist = data ? [NSPropertyListSerialization propertyListWithData:data options:0 format:NULL error:&error] : nil;
    if (![plist isKindOfClass:NSDictionary.class] || !plist[@"HostPrivateKey"] || !plist[@"HostCertificate"] || !plist[@"HostID"]) {
        [self record:@"PAIRING_IMPORT_FAIL reason=invalid_lockdown_record_or_size"];
        return;
    }
    NSURL *target = PairingURL();
    if (![NSFileManager.defaultManager createDirectoryAtURL:target.URLByDeletingLastPathComponent withIntermediateDirectories:YES attributes:@{NSFileProtectionKey:NSFileProtectionComplete} error:&error] ||
        ![target.URLByDeletingLastPathComponent setResourceValue:@YES forKey:NSURLIsExcludedFromBackupKey error:&error] ||
        ![data writeToURL:target options:NSDataWritingAtomic | NSDataWritingFileProtectionComplete error:&error]) {
        [self record:[NSString stringWithFormat:@"PAIRING_IMPORT_FAIL domain=%@ code=%ld", error.domain, (long)error.code]];
        return;
    }
    [self record:@"PAIRING_IMPORT_PASS stored=protected_app_support secrets_logged=false"];
}
- (void)run {
    if (self.running || UIApplication.sharedApplication.applicationState != UIApplicationStateActive) return;
    NSString *peer = [self.peer.text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    struct in_addr address;
    if (peer.length == 0 || inet_pton(AF_INET, peer.UTF8String, &address) != 1) { [self record:@"INPUT_FAIL reason=peer_must_be_ipv4"]; return; }
    NSData *pairing = [NSData dataWithContentsOfURL:PairingURL()];
    if (!pairing) { [self record:@"INPUT_FAIL reason=import_pairing_first"]; return; }
    [self.view endEditing:YES];
    self.running = YES;
    self.interrupted = NO;
    self.runID = NSUUID.UUID.UUIDString;
    self.runButton.enabled = self.importButton.enabled = self.peer.enabled = self.mode.enabled = NO;
    BOOL cellular = self.mode.selectedSegmentIndex == 1;
    [NSUserDefaults.standardUserDefaults setObject:peer forKey:@"probe.peer"];
    [self record:[NSString stringWithFormat:@"PROBE_BEGIN mode=%@ fresh_transport=true", cellular ? @"cellular" : @"wifi_baseline"]];
    dispatch_async(self.worker, ^{
        @autoreleasepool {
            int wifi = SamplePath(nw_interface_type_wifi), mobile = SamplePath(nw_interface_type_cellular);
            [self record:[NSString stringWithFormat:@"PATH_BEFORE wifi=%d cellular=%d", wifi, mobile]];
            LogInterfaces(self);
            BOOL before = probe_path_allowed(cellular, wifi, mobile);
            BOOL browse = NO;
            if (!before || self.interrupted) [self record:@"PREFLIGHT_FAIL reason=path_mismatch_unknown_or_interrupted no_coredevice=true"];
            else browse = [self probeAddress:address pairing:pairing];
            int afterWifi = SamplePath(nw_interface_type_wifi), afterMobile = SamplePath(nw_interface_type_cellular);
            [self record:[NSString stringWithFormat:@"PATH_AFTER wifi=%d cellular=%d", afterWifi, afterMobile]];
            BOOL after = probe_path_allowed(cellular, afterWifi, afterMobile);
            dispatch_async(dispatch_get_main_queue(), ^{
                BOOL valid = probe_result_valid(browse, before, after, self.interrupted);
                [self record:[NSString stringWithFormat:@"PROBE_COMPLETE browse=%d path_snapshots_match=%d interrupted=%d result=%@ refresh_performed=false",
                    browse, before && after, self.interrupted, valid ? @"READ_ONLY_PASS" : @"FAILED_OR_INCONCLUSIVE"]];
                [self record:@"SCOPE snapshots_only_not_continuous_route_proof; no_signing_or_installation_tested"];
                self.running = NO;
                self.runButton.enabled = self.importButton.enabled = self.peer.enabled = self.mode.enabled = YES;
                [self saveReport];
            });
        }
    });
}
- (BOOL)probeAddress:(struct in_addr)address pairing:(NSData *)pairing {
    struct IdevicePairingFile *pf = NULL;
    struct IdeviceProviderHandle *provider = NULL;
    struct AdapterHandle *adapter = NULL;
    struct RsdHandshakeHandle *rsd = NULL;
    struct InstallationProxyClientHandle *client = NULL;
    void *apps = NULL;
    size_t count = 0;
    BOOL success = NO;
    struct sockaddr_in socketAddress = {0};
    socketAddress.sin_len = sizeof(socketAddress);
    socketAddress.sin_family = AF_INET;
    socketAddress.sin_port = htons(62078);
    socketAddress.sin_addr = address;
    [self record:@"PAIRING_PARSE_BEGIN"];
    if (!CheckError(self, idevice_pairing_file_from_bytes(pairing.bytes, pairing.length, &pf), @"PAIRING_PARSE")) goto cleanup;
    if (!pf) { [self record:@"FAIL stage=PAIRING_PARSE reason=missing_handle"]; goto cleanup; }
    [self record:@"PAIRING_PARSE_PASS"];
    if (!CheckError(self, idevice_tcp_provider_new((const idevice_sockaddr *)&socketAddress, pf, "CellularProbe", &provider), @"PROVIDER")) goto cleanup;
    pf = NULL; // The TCP provider consumes the pairing handle on success.
    if (!provider) { [self record:@"FAIL stage=PROVIDER reason=missing_handle"]; goto cleanup; }
    [self record:@"COREDEVICE_RSD_BEGIN transport=TCP_PROVIDER port=62078"];
    // Despite its upstream name, this uses our TCP provider, NOT USB/usbmuxd.
    if (!CheckError(self, tunnel_create_usb(provider, &adapter, &rsd), @"COREDEVICE_RSD")) goto cleanup;
    if (!adapter || !rsd) { [self record:@"FAIL stage=COREDEVICE_RSD reason=missing_handle"]; goto cleanup; }
    [self record:@"COREDEVICE_RSD_PASS"];
    [self record:@"INSTALL_PROXY_CONNECT_BEGIN"];
    if (!CheckError(self, installation_proxy_connect_rsd(adapter, rsd, &client), @"INSTALL_PROXY_CONNECT")) goto cleanup;
    if (!client) { [self record:@"FAIL stage=INSTALL_PROXY_CONNECT reason=missing_handle"]; goto cleanup; }
    [self record:@"INSTALL_PROXY_CONNECT_PASS"];
    [self record:@"BROWSE_BEGIN"];
    if (!CheckError(self, installation_proxy_get_apps(client, NULL, NULL, 0, &apps, &count), @"BROWSE")) goto cleanup;
    success = apps != NULL && count > 0;
    [self record:[NSString stringWithFormat:@"%@ count=%zu", success ? @"BROWSE_PASS" : @"BROWSE_EMPTY_INCONCLUSIVE", count]];
cleanup:
    if (apps) idevice_plist_array_free((plist_t *)apps, count);
    if (client) installation_proxy_client_free(client);
    if (rsd) rsd_handshake_free(rsd);
    if (adapter) adapter_free(adapter);
    tunnel_heartbeat_stop();
    if (provider) idevice_provider_free(provider);
    if (pf) idevice_pairing_file_free(pf);
    [self record:@"RESOURCES_RELEASED"];
    return success;
}
- (void)saveReport {
    NSURL *folder = [NSFileManager.defaultManager URLsForDirectory:NSDocumentDirectory inDomains:NSUserDomainMask].firstObject;
    NSError *error = nil;
    BOOL saved = [self.output.text writeToURL:[folder URLByAppendingPathComponent:@"latest-probe.txt"] atomically:YES encoding:NSUTF8StringEncoding error:&error];
    if (!saved) [self record:[NSString stringWithFormat:@"REPORT_SAVE_FAIL domain=%@ code=%ld", error.domain, (long)error.code]];
}
- (void)share {
    UIActivityViewController *share = [[UIActivityViewController alloc] initWithActivityItems:@[self.output.text ?: @""] applicationActivities:nil];
    share.popoverPresentationController.barButtonItem = self.navigationItem.rightBarButtonItem;
    [self presentViewController:share animated:YES completion:nil];
}
@end

@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@end
@implementation AppDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    self.window.rootViewController = [[UINavigationController alloc] initWithRootViewController:[ProbeViewController new]];
    [self.window makeKeyAndVisible];
    return YES;
}
@end
int main(int argc, char *argv[]) {
    @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass(AppDelegate.class)); }
}
