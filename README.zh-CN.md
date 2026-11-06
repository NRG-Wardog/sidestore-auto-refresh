# LiveContainer + SideStore Auto-Refresh

<!-- README language selector -->
[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [Русский](README.ru.md) · [עברית](README.he.md)
<!-- /README language selector -->


独立的 LiveContainer + SideStore 构建，在统一界面中管理客体应用、签名、安装和设备端续签。

日常操作使用 **Home / Apps / Sources / Refresh / Settings**。首次安装和配对需要电脑。受支持的续签方式使用 **Wi-Fi + 官方 LocalDevVPN**，不承诺仅使用蜂窝网络即可续签。

这不是 [LiveContainer](https://github.com/LiveContainer/LiveContainer) 或 [SideStore](https://github.com/SideStore/SideStore) 的官方发行版。

<a id="verification-status"></a>
## 下载

**[下载 v6.0.1 IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v6.0.1/LiveContainer-SideStore-v6.0.1.ipa)** · [发行说明与限制](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.1)

文件：`LiveContainer-SideStore-v6.0.1.ipa`

SHA-256：`eaca3f28b32b4a14acc0b1bf16237348f528de18a6eed37552b80d65fb9f84d3`

GitHub 源码压缩包不是可安装的 IPA。请核对文件名和校验值，不要只看应用内显示的版本。具体构建的验证范围和已知限制见发行说明；构建成功不代表所有真机流程均已通过验证。

<a id="installation"></a>
## 安装

需要受支持的 iPhone 或 iPad、电脑、用于签名的 Apple Account、当前设备的配对数据，以及 [iLoader 2.3.1 或更新版本](https://github.com/nab138/iloader/releases/latest)或兼容的 IPA 安装工具。

1. 下载上方 IPA。通过 USB 连接设备，解锁并信任电脑。Windows 用户如有需要，请安装 Apple 设备驱动。
2. 在 iLoader 中选择设备，登录用于签名的 Apple Account，然后选择 **Installers → Import IPA**。选择本项目的 IPA，不要使用安装工具自带的原版 SideStore 或 LiveContainer 包。
3. 保留必需的扩展，包括 **LiveProcess**。安装完成前保持设备连接并解锁。
4. 如有提示，在 iOS **设置 → 通用 → VPN 与设备管理**中信任开发者。开启**隐私与安全性 → 开发者模式**，重启后确认。
5. 在 iLoader 中打开 **Management → Manage Pairing File**，必要时重新扫描已安装应用，然后对刚安装的应用选择 **Place**。配对数据必须属于这台设备。

**配对文件包含私密的设备访问凭据。不要将其放入问题报告、公开日志或截图。**

### 配置 LocalDevVPN

安装 **Coxson Engineering LLC** 开发的官方 **[LocalDevVPN](https://apps.apple.com/us/app/localdevvpn/id6755608044)**。此方式不需要额外的 IKEv2/IPsec VPN。

1. 在 iOS **设置 → Wi-Fi → ⓘ** 中查看当前 **IP 地址**和**子网掩码**。
2. 选择**当前 Wi-Fi 同一子网内两个不同且未使用的 IPv4 地址**。不要使用手机、路由器、电脑或其他设备的地址。请查看路由器的 DHCP 租约或客户端列表；ping 无响应不能单独证明地址空闲。
3. 在 **LocalDevVPN → Settings → Network Configuration** 中填写 **Tunnel IP** 和 **Device IP**，两者都使用 **`/32`**。
4. 开启 **Allow Intermediate Addresses**，点击 **Done**，再点击 **Save & Apply**。
5. 连接 LocalDevVPN，在 **Session Details** 中确认地址已保存。

示例仅适用于掩码为 `255.255.255.0` 的 `192.168.1.x` 网络：如果地址空闲，可以使用 `192.168.1.240/32` 和 `192.168.1.241/32`。请先核对自己的网络，不要直接照抄。

**切换到不同的 Wi-Fi 子网后，请先更新两个地址，再续签。**

### 登录并续签

认证使用外部 Mac anisette 服务 `mac-ani.jkcoxson.com`，并在 Keychain 中保留其身份信息。这涉及对第三方的网络与隐私信任，并非完全本地认证。登录前请评估这项依赖，不要公开服务提供方的身份数据。

1. 连接 Wi-Fi 和 LocalDevVPN 后，在应用中打开 **Settings → Account and Signing → Sign In / Authenticate**。
2. 打开 **Refresh**，先执行一次手动续签。
3. 在续签历史和验证结果中确认最终成功。任务开始或控制权交接并不代表续签成功。
4. 手动续签成功后，再在 **Refresh** 中开启自动续签，选择可用的每六小时、每天或每周计划。
5. 在 iOS 中开启后台 App 刷新；如需续签和到期提醒，请允许通知。

完成首次设置后，受支持的续签方式通常无需保持电脑连接。**后台执行尽力而为，不保证准时运行：**实际运行时间由 iOS 决定。请在签名到期前检查最近一次已验证的续签结果，设定时间不是精确闹钟。

<a id="updating-without-losing-your-setup"></a>
## 安全升级

- 先备份重要的客体应用数据，并阅读[升级说明](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.1)。
- 使用**相同的签名身份（Apple Account / Personal Team）和匹配的标识符**，覆盖安装到对应的现有 **LiveContainer** 应用。
- 保留必需扩展，包括 **LiveProcess**。不要把卸载旧应用作为常规升级步骤。
- 独立 SideStore IPA 不能代替合并版 LiveContainer IPA。
- 不要为了尝试修复问题而盲目重置账户、应用或配对数据。

<a id="troubleshooting"></a>
## 故障排查

修改数据前，请先检查：

- 准确的 IPA 和构建版本、开发者信任、开发者模式及必需扩展。
- 官方 LocalDevVPN 已连接，使用同一子网内两个空闲的 `/32` 地址，并已开启 **Allow Intermediate Addresses**、执行 **Save & Apply**。
- 配对数据属于当前设备，且 **Account and Signing** 已完成登录认证。
- 报错所指的阶段：网络/VPN、CoreDevice/RSD、配对、认证、签名或安装。
- 自动续签未运行时，先测试手动续签，并检查 iOS 后台 App 刷新设置。

在 [Issues](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues) 中提供构建版本、设备型号与 iOS 版本、失败操作及已脱敏的简短错误。不要包含配对记录、凭据、私钥或未脱敏的设备日志。

<a id="build-it-yourself"></a>
## 开发

本仓库维护合并应用及其依赖所需的构建脚本、补丁和测试。

- [文档](docs/README.md)
- [构建与测试指南](ci/README.md)
- [贡献指南](CONTRIBUTING.md) · [安全](SECURITY.md)

具体检查及其前提条件见[贡献指南](CONTRIBUTING.md)，并运行 `git diff --check`。完整验证需要外部源码检出和原生工具链；原生 iOS 构建使用构建指南中说明的 macOS/Xcode 工作流程。

## 许可证

除另有说明外，仓库原创内容采用 [MIT](LICENSE) 许可证。上游组件保留各自的许可证，参见[第三方声明](THIRD_PARTY_NOTICES.md)。
