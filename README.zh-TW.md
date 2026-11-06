# LiveContainer + SideStore Auto-Refresh

<!-- README language selector -->
[English](README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [Русский](README.ru.md) · [עברית](README.he.md)
<!-- /README language selector -->


獨立的 LiveContainer + SideStore 建置版本，在統一介面中管理客體 App、簽署、安裝和裝置端重新簽署。

日常操作使用 **Home / Apps / Sources / Refresh / Settings**。首次安裝和配對需要電腦。支援的重新簽署方式使用 **Wi-Fi + 官方 LocalDevVPN**，不承諾只透過行動網路即可重新簽署。

這不是 [LiveContainer](https://github.com/LiveContainer/LiveContainer) 或 [SideStore](https://github.com/SideStore/SideStore) 的官方版本。

<a id="verification-status"></a>
## 下載

**[下載 v6.0.1 IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v6.0.1/LiveContainer-SideStore-v6.0.1.ipa)** · [版本說明與限制](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.1)

檔案：`LiveContainer-SideStore-v6.0.1.ipa`

SHA-256：`eaca3f28b32b4a14acc0b1bf16237348f528de18a6eed37552b80d65fb9f84d3`

GitHub 原始碼壓縮檔不是可安裝的 IPA。請核對檔名和校驗值，不要只看 App 內顯示的版本。特定建置版本的驗證範圍和已知限制請見版本說明；建置成功不代表所有實機流程均已通過驗證。

<a id="installation"></a>
## 安裝

需要受支援的 iPhone 或 iPad、電腦、用於簽署的 Apple Account、目前裝置的配對資料，以及 [iLoader 2.3.1 或更新版本](https://github.com/nab138/iloader/releases/latest)或相容的 IPA 安裝工具。

1. 下載上方 IPA。透過 USB 連接裝置，解鎖並信任電腦。Windows 使用者如有需要，請安裝 Apple 裝置驅動程式。
2. 在 iLoader 中選擇裝置，登入用於簽署的 Apple Account，然後選擇 **Installers → Import IPA**。選擇本專案的 IPA，不要使用安裝工具內建的原版 SideStore 或 LiveContainer 套件。
3. 保留必要的延伸功能，包括 **LiveProcess**。安裝完成前保持裝置連接並解鎖。
4. 如有提示，在 iOS **設定 → 一般 → VPN 與裝置管理**中信任開發者。開啟**隱私權與安全性 → 開發者模式**，重新啟動後確認。
5. 在 iLoader 中開啟 **Management → Manage Pairing File**，必要時重新掃描已安裝 App，然後對剛安裝的 App 選擇 **Place**。配對資料必須屬於這台裝置。

**配對檔案包含私密的裝置存取憑證。請勿將其放入問題回報、公開記錄或截圖。**

### 設定 LocalDevVPN

安裝 **Coxson Engineering LLC** 開發的官方 **[LocalDevVPN](https://apps.apple.com/us/app/localdevvpn/id6755608044)**。此方式不需要額外的 IKEv2/IPsec VPN。

1. 在 iOS **設定 → Wi-Fi → ⓘ** 中查看目前的 **IP 位址**和**子網路遮罩**。
2. 選擇**目前 Wi-Fi 同一子網路內兩個不同且未使用的 IPv4 位址**。不要使用手機、路由器、電腦或其他裝置的位址。請查看路由器的 DHCP 租約或用戶端清單；ping 沒有回應不能單獨證明位址未被使用。
3. 在 **LocalDevVPN → Settings → Network Configuration** 中填入 **Tunnel IP** 和 **Device IP**，兩者都使用 **`/32`**。
4. 開啟 **Allow Intermediate Addresses**，點選 **Done**，再點選 **Save & Apply**。
5. 連接 LocalDevVPN，在 **Session Details** 中確認位址已儲存。

範例僅適用於遮罩為 `255.255.255.0` 的 `192.168.1.x` 網路：如果位址未被使用，可以使用 `192.168.1.240/32` 和 `192.168.1.241/32`。請先核對自己的網路，不要直接照抄。

**切換到不同的 Wi-Fi 子網路後，請先更新兩個位址，再重新簽署。**

### 登入並重新簽署

驗證使用外部 Mac anisette 服務 `mac-ani.jkcoxson.com`，並在 Keychain 中保留其身分資訊。這涉及對第三方的網路與隱私信任，並非完全本機驗證。登入前請評估這項相依性，不要公開服務提供者的身分資料。

1. 連接 Wi-Fi 和 LocalDevVPN 後，在 App 中開啟 **Settings → Account and Signing → Sign In / Authenticate**。
2. 開啟 **Refresh**，先執行一次手動重新簽署。
3. 在重新簽署歷史和驗證結果中確認最終成功。工作開始或控制權交接並不代表重新簽署成功。
4. 手動重新簽署成功後，再在 **Refresh** 中啟用自動重新簽署，選擇可用的每六小時、每天或每週排程。
5. 在 iOS 中開啟背景 App 重新整理；如需重新簽署和到期提醒，請允許通知。

完成首次設定後，支援的重新簽署方式通常不需要保持電腦連接。**背景執行會盡力完成，但不保證準時：**實際執行時間由 iOS 決定。請在簽章到期前檢查最近一次已驗證的重新簽署結果，設定時間不是精確鬧鐘。

<a id="updating-without-losing-your-setup"></a>
## 安全更新

- 先備份重要的客體 App 資料，並閱讀[升級說明](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.1)。
- 使用**相同的簽署身分（Apple Account / Personal Team）和相符的識別碼**，覆蓋安裝到對應的現有 **LiveContainer** App。
- 保留必要的延伸功能，包括 **LiveProcess**。不要把刪除舊 App 當成一般更新步驟。
- 獨立 SideStore IPA 不能代替合併版 LiveContainer IPA。
- 不要為了嘗試修復問題而盲目重設帳號、App 或配對資料。

<a id="troubleshooting"></a>
## 疑難排解

修改資料前，請先檢查：

- 正確的 IPA 和建置版本、開發者信任、開發者模式及必要的延伸功能。
- 官方 LocalDevVPN 已連接，使用同一子網路內兩個未使用的 `/32` 位址，並已開啟 **Allow Intermediate Addresses**、執行 **Save & Apply**。
- 配對資料屬於目前裝置，且 **Account and Signing** 已完成登入驗證。
- 錯誤所指的階段：網路/VPN、CoreDevice/RSD、配對、驗證、簽署或安裝。
- 自動重新簽署未執行時，先測試手動重新簽署，並檢查 iOS 背景 App 重新整理設定。

在 [Issues](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues) 中提供建置版本、裝置型號與 iOS 版本、失敗操作及已移除私人資料的簡短錯誤。不要包含配對記錄、憑證、私密金鑰或未移除私人資料的裝置記錄。

<a id="build-it-yourself"></a>
## 開發

本儲存庫維護合併 App 及其相依元件所需的建置指令碼、修補程式和測試。

- [文件](docs/README.md)
- [建置與測試指南](ci/README.md)
- [貢獻指南](CONTRIBUTING.md) · [安全](SECURITY.md)

各項檢查及其先決條件請見[貢獻指南](CONTRIBUTING.md)，並執行 `git diff --check`。完整驗證需要外部原始碼和原生工具鏈；原生 iOS 建置使用建置指南中說明的 macOS/Xcode 工作流程。

## 授權條款

除另有說明外，儲存庫原創內容採用 [MIT](LICENSE) 授權。上游元件保留各自的授權條款，請見[第三方聲明](THIRD_PARTY_NOTICES.md)。
