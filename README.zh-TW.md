<a id="livecontainer--sidestore-auto-refresh"></a>

# LiveContainer + SideStore 自動續簽

<!-- README language selector -->
[![English (default)](https://img.shields.io/badge/lang-en-red.svg)](README.md)
[![简体中文](https://img.shields.io/badge/lang-zh--CN-orange.svg)](README.zh-CN.md)
[![繁體中文](https://img.shields.io/badge/lang-zh--TW-yellow.svg)](README.zh-TW.md)
[![Русский](https://img.shields.io/badge/lang-ru-blue.svg)](README.ru.md)
[![עברית](https://img.shields.io/badge/lang-he-purple.svg)](README.he.md)
<!-- /README language selector -->

![LiveContainer 和 SideStore 應用程式圖示由續簽循環連接，旁邊是專案名稱 Auto-Refresh](docs/assets/project-banner.webp)

[![組合版建置](https://github.com/NRG-Wardog/LiveContainer/actions/workflows/native-app-compile.yml/badge.svg)](https://github.com/NRG-Wardog/LiveContainer/actions/workflows/native-app-compile.yml)
[![獨立版建置](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/build-current.yml/badge.svg)](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/workflows/build-current.yml)
[![發佈版本](https://img.shields.io/github/v/release/NRG-Wardog/sidestore-auto-refresh)](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/latest)
[![許可證：MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

這是基於 **SideStore** 和 **LiveContainer** 的獨立開源建置，專注於可靠的本機續簽、清晰的排程安排、結果驗證，以及適合新手的設定流程。

<a id="current-status-v310-is-not-recommended"></a>

<a id="current-status-v60"></a>

## 當前狀態：v6.0 取代 v3

**v6.0 取代 v3，成為當前統一產品線。** 公開版 [v6.0.0](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.0) 直接發佈經過測試的同一 IPA，不重新建置或簽署。驗證僅限下述已確認流程，不涵蓋所有裝置、帳號或背景情境。

- **v6.0 DEVICE VERIFIED（實體裝置已驗證）：** 維護者裝置上已確認 Apple 帳號登入、IPA 安裝及應用程式執行、延伸功能選擇、新增 Sources 和 LiveContainer 客體應用程式、取消 Add Source、JITless 使用及客體應用程式背景行為。
- **驗證範圍：** 回報的測試中，手動續簽和續簽後開啟客體應用程式看起來正常。這不是長期執行或無人值守排程續簽的證明。來源更新（[#30](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/30)）狀態仍未知；偏好持久化（[#34](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/34)）和 Keep/Reuse（[#44](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/44)）尚未經實體裝置驗證。
- **歷史版本：** v3.1.0 及更早的 v3、組合版 v2 和獨立版 v1 保留原有發佈記錄。v3.1.0 的登入警告屬於歷史情況，不代表當前 v6.0 的登入結果；不推薦將舊版本視為已驗證的回退方案。
- **已有安裝：** 請保留當前設定並備份重要的客體應用程式資料。不要以猜測性的恢復措施刪除應用程式、重置帳號資料或替換配對記錄。

本獨立專案將 LiveContainer 和 SideStore 整合在同一介面中。首次安裝和配對需要電腦。設定完成後，預期的續簽路徑使用 **Wi-Fi + App Store 官方 LocalDevVPN**，無需保持電腦連接。僅使用行動網路進行續簽仍屬於實驗功能。

[發佈狀態](#current-status-v60) · [安裝參考](#installation) · [已知問題](#troubleshooting) · [驗證](#verification-status) · [從原始碼建置](#build-it-yourself)

> [!NOTE]
> 這不是 SideStore 或 LiveContainer 的官方發佈版。如需原版行為和上游支援，請使用官方 [SideStore](https://github.com/SideStore/SideStore) 和 [LiveContainer](https://github.com/LiveContainer/LiveContainer) 專案。


<a id="why-this-project-exists"></a>

## 為什麼建立這個專案

我建立此專案，是因為原版 SideStore 的續簽路徑在我的設定上不夠可靠。關鍵修復不是圍繞同一流程再新增一個快捷指令或觸發器，而是改變用於存取 iPhone 裝置服務的本機傳輸方式。

本專案預期使用的本機路徑如下：

```text
official LocalDevVPN -> Lockdown -> CoreDeviceProxy TLS -> CDTunnel -> RSD -> AFC / InstallationProxy
```

該路徑正常工作後，專案逐步發展為更完整的 LiveContainer + SideStore 體驗：明確的續簽排程、歷史記錄與驗證、按階段劃分的診斷、版面配置選項、客體應用程式返回控制項，以及統一的 v6.0 介面。

<a id="what-this-project-improves"></a>

### 本專案改進了什麼

| 方面 | 本專案提供的功能 |
| --- | --- |
| **可靠的本機傳輸** | 用本專案採用的 **LocalDevVPN -> CoreDevice -> RSD** 路徑替代在我的設定上失敗的 Lockdown 服務路徑 |
| **真正統一的 LiveContainer + SideStore 體驗** | v6.0 保留 LiveContainer 作為宿主，通過邊界明確的服務介面提供 SideStore 功能，使日常操作留在 **Home / Apps / Sources / Refresh / Settings** 中，而不是跳轉到單獨的內嵌 SideStore 流程 |
| **明確的自動續簽** | 由使用者控制的**每六小時、每天和每週**排程、偏好時間、通知、到期提醒及持久儲存的歷史記錄 |
| **經過驗證的續簽結果** | 將每次執行與其實際結果關聯，因此僅任務啟動、發生交接或發出請求本身不會被報告為續簽成功 |
| **有用的診斷** | 區分 VPN、CoreDevice、心跳、RSD、Lockdown、簽署和安裝失敗，不會將無關故障合併為一條籠統訊息 |
| **更好的應用程式瀏覽** | 增加 **List、Grid 和 Compact List**、持久儲存的版面配置偏好，以及可選的 Grid 標籤；本次比較所檢查的上游 LiveContainer 應用程式清單沒有這些本專案特有的版面配置控制項 |
| **更好的客體應用程式導覽** | 為全螢幕/直接執行的客體應用程式流程增加本專案特有的 **Return** 控制項、**Start Collapsed**、位置儲存，以及自訂圖示/背景顏色 |
| **設定完成後無需電腦的續簽執行方式** | 首次安裝和配對仍需使用電腦，但受支援的續簽路徑旨在通過 Wi-Fi + 官方 LocalDevVPN 在 iPhone 上執行，無需讓電腦一直保持連接 |

<details>
<summary><strong>與上游的技術比較</strong></summary>

上述產品行為所對應的原始碼級差異如下：

| 方面 | 已檢查的上游程式碼 | 本專案 |
| --- | --- | --- |
| Lockdown 服務路徑 | Lockdown 服務呼叫使用直接 TCP 提供器 | **CoreDeviceProxy TLS -> CDTunnel -> userspace IPv6 -> RSD** |
| iOS 26.4+ 上本地 VPN 的就緒條件 | Lockdown 的本地 VPN 就緒檢查要求存在 IKEv2/IPsec 介面 | CoreDevice 路徑使用官方 LocalDevVPN 的 `utun` 路徑，無需第二條 IKEv2/IPsec 隧道 |
| 複合配對檔案 | 在 Lockdown 金鑰之前檢查 RemotePairing 金鑰 | 優先使用有效的 **Lockdown** 資料，從而選擇 CoreDevice 路徑 |
| 組合版 LC + SS 導覽 | 當前 LiveContainer 程式碼通過 `openSideStore` / `builtinSideStore` 開啟/選擇內建 SideStore 流程 | v6.0 通過統一的宿主 UI 和服務橋接執行日常 SideStore 操作，而不把使用者切換到內嵌的 SideStore UI |

SideStore 自己的 README 已經介紹了定期背景續簽。這裡的改進並不是聲稱以前不存在背景續簽，而是將改變後的傳輸路徑與明確的排程、可觀測性、歷史記錄、驗證和故障處理結合起來。

_比較依據：上游 SideStore `develop` 的 `797e0d46c46491c7fba1192c789c016d24b35591`、minimuxer `20248550bbe014805460d4fa22ea69f146d338a0`，以及 2026 年 9 月 16 日檢查的 LiveContainer 原始碼。上游可能在此快照之後發生變化。_

</details>

---

<h2 align="center"><a href="#installation">安裝參考</a></h2>
<p align="center"><strong>安裝前請閱讀 v6.0 的驗證範圍和發佈狀態。保留現有資料，並使用確切匹配的安裝套件。</strong></p>
<p align="center"><a href="#what-this-project-improves">瞭解本專案的改進</a> · <a href="#installation">開始安裝</a></p>

---

<a id="installation"></a>

## 安裝

<a id="before-you-start"></a>

### 開始之前

**本指南適用於 v6.0 統一產品線。** 安裝前請閱讀[當前狀態](#current-status-v60)、[v6.0.0 發佈說明](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.0)及確切安裝套件的校驗碼。

安裝檔案為 `LiveContainer-SideStore-v6.0.0.ipa`。GitHub 原始碼壓縮檔不是可安裝的 IPA。請核對發佈附件、建置器修訂版本和校驗碼；僅憑顯示的版本號無法確認建置身分。

安裝前請檢查以下項目：

| 檢查項 | 所需條件 |
| --- | --- |
| **裝置** | 受支援的 iPhone 或 iPad，首次設定期間保持解鎖 |
| **電腦** | Windows、macOS 或 Linux，僅首次安裝和配對需要 |
| **安裝器** | [iLoader](https://github.com/nab138/iloader/releases/latest) **2.3.1 或更高版本**，或其他相容的 IPA 安裝器 |
| **Apple 帳號** | 用於簽署 IPA 的 Apple 帳號 / Personal Team（個人團隊） |
| **開發者模式** | 安裝後啟用 |
| **配對** | 為正在使用的**那一台實體裝置**產生的配對資料 |
| **網路** | Wi-Fi，並準備好 iPhone 當前的 **IP Address（IP 位址）和 Subnet Mask（子網路遮罩）** |
| **VPN** | App Store 上由 **Coxson Engineering LLC** 發佈的官方 **LocalDevVPN** |
| **應用程式延伸功能** | 保留組合版建置所需的擴展，包括 **LiveProcess** |
| **已有安裝** | 升級時，使用相同的簽署身分覆蓋安裝到匹配的現有 LiveContainer 建置上；不要將先刪除應用程式作為常規步驟 |

如想先瞭解差異，請查看[本專案改進了什麼](#what-this-project-improves)。否則，請按順序完成以下步驟。

<a id="step-1-download-the-correct-ipa"></a>

### 第 1 步：下載正確的 IPA

從 v6.0.0 發佈頁下載當前統一版 v6.0 安裝套件：

**[LiveContainer-SideStore-v6.0.0.ipa](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v6.0.0/LiveContainer-SideStore-v6.0.0.ipa)** · [發佈說明與驗證](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.0)

如果你需要本專案的修改版建置，請不要使用 iLoader 內建的原版 SideStore 或原版 LiveContainer + SideStore 安裝器。請使用 **Import IPA**，選擇從本儲存庫下載的 IPA。

其他產品線：

- **獨立版 SideStore：** [v1.0.4](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.4)
- **舊組合版介面：** [v2.1.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.1)
- **歷史統一版建置：** [v3.0.2](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.0.2)

<a id="step-2-install-with-iloader"></a>

### 第 2 步：使用 iLoader 安裝

1. 安裝/開啟 [iLoader](https://github.com/nab138/iloader/releases/latest)。
2. 在 Windows 上，確保已安裝 Apple 裝置驅動程式。
3. 通過 USB 連接 iPhone，解鎖，並在出現提示時點一下 **Trust（信任）**。
4. 在 iLoader 中選擇已連接的裝置。
5. 登入你希望用於簽署的 Apple 帳號。
6. 開啟 **Installers -> Import IPA**。
7. 選擇 `LiveContainer-SideStore-v6.0.0.ipa`。
8. 保留所需的應用程式延伸功能，包括 **LiveProcess**。不要移除所有擴展。
9. 讓 iLoader 簽署並安裝 IPA。安裝完成之前，請保持裝置連接並解鎖。

<a id="step-3-trust-the-app-and-enable-developer-mode"></a>

### 第 3 步：信任應用程式並啟用開發者模式

如果 iOS 顯示 **Untrusted Developer（不受信任的開發者）**：

`Settings -> General -> VPN & Device Management -> Developer App -> Trust`

然後啟用：

`Settings -> Privacy & Security -> Developer Mode`

裝置可能會重新啟動。如果重新啟動後出現提示，請確認 **Turn On（開啟）**。

<a id="step-4-place-the-pairing-file"></a>

### 第 4 步：放置配對檔案

保持裝置通過 USB 連接並處於解鎖狀態。

在 iLoader 中：

1. 開啟 **Management**。
2. 開啟 **Manage Pairing File**。
3. 如果未列出應用程式，請使用 **Rescan Installed Apps**。
4. 找到你剛安裝的、相容 SideStore 的應用程式。
5. 對該應用程式點選 **Place**；只有在你明確希望對所有應用程式操作時，才使用 **Place In All Apps**。
6. 等待成功訊息後再繼續。

> [!CAUTION]
> 配對檔案包含裝置的私密憑據。切勿將其上傳到 GitHub、Discord、Issue、公共檔案托管服務，也不要讓其出現在截圖中。

傳輸、RSD 或服務錯誤**不**自動意味著配對檔案有問題。只有診斷明確指出故障發生在配對階段時，才替換配對資料。

<a id="step-5-install-the-correct-localdevvpn"></a>

### 第 5 步：安裝正確的 LocalDevVPN

從 App Store 安裝官方 **[LocalDevVPN](https://apps.apple.com/us/app/localdevvpn/id6755608044)**，並核實開發者為 **Coxson Engineering LLC**。

受支援的 CoreDevice 路徑無需第二個 IKEv2/IPsec VPN。

<a id="step-6-find-the-iphones-wi-fi-subnet"></a>

### 第 6 步：查找 iPhone 的 Wi-Fi 子網路

在 iPhone 上開啟：

`Settings -> Wi-Fi -> ⓘ next to the connected network`

記下：

- **IP Address（IP 位址）**
- **Subnet Mask（子網路遮罩）**

示例：

```text
IP Address:  192.168.1.50
Subnet Mask: 255.255.255.0
```

該示例屬於 `192.168.1.x/24` 子網路。

> [!WARNING]
> 不要盲目照抄示例位址。請使用屬於**你當前 Wi-Fi 子網路**的位址。

<a id="step-7-choose-two-unused-localdevvpn-addresses"></a>

### 第 7 步：選擇兩個未使用的 LocalDevVPN 位址

選擇**兩個不同且未被使用的 IPv4 位址，它們必須與 iPhone 當前的 Wi-Fi 連接處於同一子網路**。

以下僅為 `192.168.1.x/24` 的示例：

```text
Tunnel IP: 192.168.1.240/32
Device IP: 192.168.1.241/32
```

**不要**使用：

- iPhone 實際的 Wi-Fi IP
- 路由器/預設閘道的 IP
- 電腦的 IP
- 已分配給其他裝置的 IP
- 當前 Wi-Fi 子網路之外的 IP
最穩妥的檢查方法是查看路由器的 DHCP 租約或已連接用戶端清單。僅憑 ping 失敗不能證明位址未被使用。

<a id="step-8-configure-localdevvpn"></a>

### 第 8 步：設定 LocalDevVPN

開啟：

`LocalDevVPN -> Settings -> Network Configuration`

然後：

1. 輸入選定的 **Tunnel IP**，帶上 `/32`。
2. 輸入選定的 **Device IP**，帶上 `/32`。
3. 啟用 **Allow Intermediate Addresses**。
4. 點一下 **Done**。
5. 點一下 **Save & Apply**。
6. 連接 LocalDevVPN。
7. 開啟 **Session Details**，確認自訂值已保留。

> [!IMPORTANT]
> 如果切換到子網路不同的 Wi-Fi 網路，請在再次續簽之前，將 Tunnel IP 和 Device IP 更新為適用於該網路的位址。

<a id="step-9-open-v3-and-complete-account--signing"></a>

### 第 9 步：開啟 v6.0 並完成帳號與簽署設定

**v6.0 DEVICE VERIFIED：** 維護者已在實體裝置上完成帳號登入。如您的登入流程失敗，請記錄確切建置及脫敏錯誤；不要以猜測性的恢復措施刪除應用程式、帳號或配對資料。

開啟已安裝的應用程式。v6.0 的日常操作位於 **Home / Apps / Sources / Refresh / Settings** 中。

前往：

`Settings -> Account and Signing -> Sign In / Authenticate`

在 Wi-Fi 和 LocalDevVPN 已連接時完成帳號流程。

<a id="step-10-run-one-manual-refresh-before-enabling-automation"></a>

### 第 10 步：啟用自動化前先手動續簽一次

開啟 **Refresh**，先執行一次手動續簽。

繼續之前，請驗證：

- LocalDevVPN 已連接
- 傳輸狀態顯示為可達/就緒
- 續簽已進入簽署/安裝管線
- 續簽歷史/驗證中記錄的最終結果為成功

僅有任務啟動或發生交接還不夠。請確認最終續簽結果。

如果失敗，請先閱讀所報告的故障階段，再進行任何更改。診斷旨在區分 VPN、CoreDevice、心跳、RSD、Lockdown、簽署、安裝和配對故障。

<a id="step-11-enable-automatic-refresh"></a>

### 第 11 步：啟用自動續簽

僅在手動續簽成功後：

1. 開啟 **Refresh** 分頁/設定。
2. 啟用自動續簽。
3. 在可用時選擇 **Every Six Hours（每六小時）**、**Daily（每天）** 或 **Weekly（每週）**。
4. 設定偏好時間/日期。
5. 優先選擇 iPhone 通常連接到已為 LocalDevVPN 設定好的 Wi-Fi 網路的時間。
6. 如需開始、失敗和到期提醒，請允許通知。

> [!NOTE]
> iOS 背景執行只能盡力而為。所選時間不是精確鬧鐘，iOS 可能稍後才執行任務。請在簽署有效期臨近結束之前檢查最近一次經過驗證的續簽。

<a id="step-12-confirm-pc-free-runtime"></a>

### 第 12 步：確認無需電腦即可執行

首次安裝和配對設定完成後，受支援的續簽執行方式旨在使用：

```text
iPhone/iPad + Wi-Fi + official LocalDevVPN
```

正常續簽過程中，應當無需讓電腦和 USB 線保持連接。

<a id="updating-without-losing-your-setup"></a>

### 在不丟失設定的情況下更新

對於 v6.0/v3/v2 組合版建置，使用相同的 Apple 帳號 / Personal Team 和匹配的識別碼，將匹配的新 IPA **覆蓋安裝到現有 LiveContainer 安裝上**。

**不要**將先刪除應用程式作為常規更新步驟。更新前請備份重要的客體應用程式資料。

對於獨立版 SideStore，請將新的獨立版建置覆蓋安裝到匹配的現有 SideStore 安裝上。不要將獨立版 SideStore IPA 覆蓋安裝到 LiveContainer 上。

<a id="if-something-does-not-work"></a>

### 如果出現問題

請先按順序檢查：

1. 確切的發佈版本/建置和 IPA 檔案名。
2. 開發者模式和信任狀態。
3. 配對資料是否屬於當前這台裝置。
4. LocalDevVPN 是否為 App Store 官方建置，並且已連接。
5. Tunnel IP 和 Device IP 是否為**同一 Wi-Fi 子網路中未被使用的位址**，而非 iPhone、路由器或電腦的 IP。
6. 是否啟用 **Allow Intermediate Addresses**，並使用過 **Save & Apply**。
7. 帳號/簽署認證是否完成。
8. 重置配對/帳號資料之前，先閱讀確切的診斷故障階段。
9. 如果 Wi-Fi 已更改，請為新子網路重新設定 LocalDevVPN。
10. 如果自動續簽沒有執行，請先測試手動續簽；iOS 不保證精確的背景執行時間。

如需進一步排查，請查看[故障排查](#troubleshooting)和[驗證](docs/VERIFICATION.md)。

<a id="what-is-this"></a>

## 這是什麼？

**v6.0 將 Home、Apps、Sources、Refresh 和 Settings 整合為一體化的應用程式體驗。** 日常操作不再需要在 LiveContainer 和舊的內嵌 SideStore 介面之間切換。LocalDevVPN 仍是續簽傳輸所使用的獨立應用程式。


本專案是經過修改的 SideStore / LiveContainer 建置，使本機續簽更易操作，也更易驗證。

它增加了：

- 直接在 iPhone 上手動續簽
- 每六小時、每天和每週的續簽排程
- 偏好續簽時間控制項
- 持久儲存的續簽歷史
- 有界的重試與恢復邏輯
- 明確的續簽驗證
- 本機 **LocalDevVPN + CoreDevice** 傳輸路徑
- 已內嵌修改版 SideStore 的 LiveContainer 組合建置
- 組合版中的客體應用程式 Return 控制項
- 組合版 v2.1.0 中的 **Start Collapsed** 和**自訂 Return 按鈕顏色**

目標很簡單：首次安裝和配對設定完成後，正常續簽應在 iPhone 上進行，無需讓電腦一直連接。

<a id="preview"></a>

## 預覽

以下截圖是歷史 v2 和獨立版預覽，並非新的統一版 v6.0 介面截圖。保留版本標注是為了避免混淆。

<a id="combined-v210"></a>

### 組合版 v2.1.0

這些是 **LiveContainer + 內嵌 SideStore v2.1.0** 的實際截圖。

<table>
<tr>
<td align="center"><img src="docs/screenshots/WhatsApp%20Image%202026-09-10%20at%2016.31.46.jpeg" width="220" alt="組合版 v2.1.0 續簽狀態"><br><strong>續簽狀態</strong></td>
<td align="center"><img src="docs/screenshots/WhatsApp%20Image%202026-09-10%20at%2016.31.45%20(1).jpeg" width="220" alt="組合版 v2.1.0 續簽排程"><br><strong>續簽排程</strong></td>
<td align="center"><img src="docs/screenshots/WhatsApp%20Image%202026-09-10%20at%2016.31.45.jpeg" width="220" alt="組合版 v2.1.0 續簽歷史"><br><strong>續簽歷史</strong></td>
<td align="center"><img src="docs/screenshots/WhatsApp%20Image%202026-09-10%20at%2016.31.46%20(1).jpeg" width="220" alt="組合版 v2.1.0 客體應用程式控制項"><br><strong>客體應用程式控制項</strong></td>
</tr>
</table>

v2.1.0 的 Guest Controls 頁麵包括 **Show Return Button**、**Start Collapsed**、**Use Custom Colors**、**Icon Color** 和 **Button Background**。

<a id="standalone-preview-v102-screenshots"></a>

### 獨立版預覽（v1.0.2 截圖）

這些截圖展示的是較早的獨立版 SideStore v1.0.2 介面。當前可下載的獨立版為 v1.0.4，基於 SideStore 0.7.0 nightly。

<table>
<tr>
<td align="center"><img src="docs/screenshots/settings-refreshing-apps.png" width="230" alt="SideStore 應用程式續簽設定"><br><strong>應用程式續簽</strong></td>
<td align="center"><img src="docs/screenshots/refresh-schedule-main.png" width="230" alt="SideStore 續簽排程"><br><strong>續簽排程</strong></td>
<td align="center"><img src="docs/screenshots/refresh-history.png" width="230" alt="SideStore 續簽歷史"><br><strong>續簽歷史</strong></td>
</tr>
</table>

<a id="quick-navigation"></a>

## 快速導覽

- [為什麼建立這個專案](#why-this-project-exists)
- [選擇建置](#which-version-should-i-download)
- [與原始項目有何不同？](#how-this-project-differs-from-the-original-projects)
- [安裝](#installation)
- [舊產品線](#previous-product-lines)
- [故障排查](#troubleshooting)
- [技術架構](#technical-architecture)

<a id="which-version-should-i-download"></a>

## 我應該下載哪個版本？

| 你的需求 | 使用版本 | 下載 |
| --- | --- | --- |
| 統一 LiveContainer + SideStore 介面 | **v6.0（公開版 v6.0.0）** | [下載 IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v6.0.0/LiveContainer-SideStore-v6.0.0.ipa) · [發佈說明](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.0) |
| 舊組合版介面 | **組合版 v2.1.1（舊版本）** | [舊 v2 發佈版](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.1) |
| 只使用 SideStore，側載應用程式按常規方式單獨安裝 | **獨立版 v1.0.4** | **[下載獨立版 IPA](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/download/v1.0.4/SideStore.ipa)** |

統一建置包含 SideStore，無需另行安裝 SideStore。v6.0 取代 v3 成為當前統一產品線。舊產品線具有不同的介面和驗證歷史，不作為已驗證的替代解決方案推薦。

**當前統一產品線：** [v6.0.0 發佈版](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v6.0.0)。

**之前的統一版建置：** [v3.0.2 發佈版](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.0.2)（仍可下載）

**舊組合版產品線：** [v2.1.1 發佈版](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.1)

**獨立版：** [v1.0.4 發佈版](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.4)

<a id="whats-confirmed-for-v60"></a>

## v6.0 已確認哪些內容？

**已測試建置來源：** 建置器 [`177ac31e90499231191b25f761014c17d9f2ba7b`](https://github.com/NRG-Wardog/LiveContainer/commit/177ac31e90499231191b25f761014c17d9f2ba7b)、原生建置 [40102842840](https://github.com/NRG-Wardog/LiveContainer/actions/runs/40102842840)、打包執行 [40104361491](https://github.com/NRG-Wardog/LiveContainer/actions/runs/40104361491)。本次文件及版本號更新不在此測試來源之外增加功能變更。版本名稱或 CI 成功本身並不等於實體裝置驗收。

**發佈不變的產物，而非重新建置：** v6.0.0 保留已通過裝置測試的 IPA 原始位元組，不重新建置或簽署。CI 的 `build-label.json` 仍記錄產品版本 `6.0`、管道 `experimental` 和標籤 `6.0 pairing and deferred setup experimental`。這並未重寫應用程式 bundle 版本：LiveContainer 仍為 `3.8.11`，SideStore 仍為 `0.7.0`。內部標籤不代表另一個 IPA。請使用建置器修訂版本和校驗碼識別公開發佈套件。

- IPA SHA-256: `86db9a6bf4accf7e886d88563f1474063c85865682131a5ba0b13ed55a33f321`

**繼承的簽署問題：** 仍揭露 CydiaSubstrate 第 0 頁簽署不匹配。尚未證明這是 v6.0 引入的新損壞，重新簽署也不是保證有效的修復。解讀簽署警告時請保留這一區別。

<a id="whats-new-in-v310"></a>

## v3.1.0 有哪些新內容？

**以下是歷史發佈內容，不代表對穩定性的認可。** 後續出現的登入/預配失敗在 [Issue #42](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/42) 中追蹤。

已發佈的 IPA 使用建置器 **651587433eb7142089509e558dcfba9eb69d0470**，來自成功的 macOS CI [37158730837](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/37158730837)。應用程式內診斷保留了原有的 `Combined LC+SS v3.0.3-rc` 標籤。公開發佈版為 v3.1.0；它不包含後續開發更改。

- **更小的無介面後端：** 移除了舊 SideStore UI/資源，同時保留上游後端操作。原始 IPA 大小為 **25,122,692 位元組 / 23.958866 MiB**，相比 v3.0.2 減少 **35.14%**。這是產物大小的測量結果，並非執行速度方面的聲明。
- **穩定化：** 包括從直接根視圖展示/重複使用本地 IPA、以權威狀態為準的來源新增持久化、類型化的認證/提示歸屬、已簽署 App Group/執行階段儲存處理、操作階段/進度、LC 原生 JIT-Less 觀察，以及針對性的上遊客體應用程式背景行為回移植。
- **明確的驗收限制：** 某些 iOS 27.0.1 裝置仍無法啟動內嵌 LiveProcess；已發佈的 IPA 在缺少請求 UUID 時仍會帶有合成的 Cocoa 3587 標記。其實體裝置上的成因尚未解決。替代的 App ID 數量限制錯誤碼 9120 映射和改進的啟動診斷屬於後續開發更改，不包含在已發佈的 v3.1.0 IPA 中。這些發佈時的限制保留用於追溯；後續診斷工作不會改變已發佈的 IPA。
- **仍需裝置測試：** 真實 Apple 認證/2FA、持續/排程續簽、受影響裝置上的客體應用程式生命週期/偏好、來源目錄/重新啟動，以及最新的 JIT-Less 就緒/簽署行為。請查看[完整發佈說明、校驗碼及大小表](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.1.0)。不要以猜測性的啟動恢復措施刪除資料或重置配對。

SHA-256：`2337e08a71b2af4fee930b59ccd28522563acd81e908069e56f6d27d1636daf4`。

<a id="whats-new-in-v302"></a>

## v3.0.2 有哪些新內容？

v3.0.2 是統一 LiveContainer + SideStore v3 產品線的上一版維護發布。

- **Setup Assistant（設定助理）：** 由宿主負責的初始設定清單，涵蓋裝置、配對、Apple 帳號、網路、背景 App 重新整理、排程，以及經過驗證的測試續簽。Setup Complete 要求已登入團隊、網路和隧道滿足條件、背景 App 重新整理可用、排程已啟用，並且在當前會話中完成過經過驗證的續簽。
- **Start Dock Collapsed：** 新增可持久儲存的多任務偏好。Dock 可以初始折疊；此後始終以手動展開/折疊操作為準，版面配置改變或旋轉不會重置它。它與 Hide Collapsed Dock 和 Guest Controls 相互獨立。
- **續簽目標崩潰修復：** 在未繼承 store 的情況下渲染目標應用程式續簽區域時，不再觸發陷阱崩潰。
- **首次啟動通知提示：** 使用通俗語言請求權限，以便發送續簽開始/完成/失敗橫幅。
- **可見的請求合併：** 當手動續簽點選落在已執行中的續簽任務上時，現在會給出反饋，不再保持靜默。

<a id="whats-new-in-v301"></a>

## v3.0.1 有哪些新內容？

v3.0.1 曾是統一 LiveContainer + SideStore v3 產品線的上一版維護發布。它保留可用的 v3 架構，著重讓 SideStore 成為後端實現細節，而由 LiveContainer 負責可見的使用者體驗。

- **無介面 SideStore 後端：** 常規 v3 流程不再依賴遠端 SideStore UI 的展示。面向使用者的導覽、表單、彈出面板、提醒、確認、進度和錯誤展示均由 LiveContainer 負責。
- **引導式登入和 2FA：** 帳號認證由統一 UI 驅動，提供更清晰的兩步 2FA 流程：先選擇受信任裝置或手機接收方式，再輸入驗證碼。
- **宿主管理頁面：** 憑證、開發者資料、配對、源、設定、診斷及相關流程使用 v3 服務/DTO 命令模型，而不是直接呈現 SideStore 頁面。
- **Home 可直接操作：** 關鍵狀態行現在可以直接跳轉到相關的 Apps、Sign In、Developer Services、Certificates、Pairing 或 Refresh 位置。
- **設定整理：** 按相關性排序各區域，將 Guest Runtime 放在最後，並將 Build Candidate 資訊移至底部。
- **減少無路可走的頁面：** 當沒有可用帳號時，依賴帳號的目標頁面會跳轉到 Sign In。
- **2FA 手機選擇修復：** 選擇手機號碼後會請求將驗證碼發送到該號碼，而不是取消流程。
- **發佈/執行階段穩定性：** 此發佈保留現有的續簽、傳輸、簽署、持久化、客體執行階段和 LiveProcess 行為，不引入無關重構。

已發佈的 v3.0.1 IPA 基於同一套經過測試的 v3 行為建置，隨後應用發佈/版本資訊和證據更新。已有 v3 使用者應使用相同簽署身分，將其覆蓋安裝到匹配的 LiveContainer 安裝上，而不是先刪除應用程式。

SideStore 繼續負責自己的資料庫、Keychain、認證邏輯、簽署、預配、源和安裝。LiveContainer 繼續負責客體應用程式及其執行。v3 服務橋接連接這些職責主體，而不重複儲存持久狀態。

受支援的續簽路徑仍需要 Wi-Fi 和官方 LocalDevVPN 應用程式。背景執行時間由 iOS 控制；排程並不是精確鬧鐘。Issue #18 仍單獨追蹤，因為無法僅憑 CI 或一台裝置，就普遍證明某位受影響使用者的 Apple 認證結果。

<a id="whats-new-in-v104-and-v211"></a>

## v1.0.4 和 v2.1.1 有哪些新內容？

Issue 17 在 **Interface** 下增加了可儲存的應用程式版面配置選項：

- **List** 保留詳細的卡片版面配置。
- **Grid** 顯示以圖示為主的應用程式網格。可以隱藏可見標籤，同時讓 VoiceOver 仍能讀取每個應用程式名稱。
- **Compact List** 使用更矮的行，以便更密集地瀏覽。

所選版面配置和標籤偏好會在重新啟動後保留。更改任一設定都會立即刷新 Apps 頁面。網格操作繼續使用現有的啟動、多任務、快顯選單、確認和錯誤處理路徑。
<a id="whats-new-in-standalone-v103"></a>

## 獨立版 v1.0.3 有哪些新內容？

獨立版 v1.0.3 將應用程式更新為 **SideStore 0.7.0 nightly**，包括較新的 **SideSign 認證路徑**。

- 保留 LocalDevVPN -> Lockdown/CoreDevice -> RSD 傳輸、手動續簽、排程續簽、歷史記錄、重試和驗證。
- 複合配對記錄優先使用 Lockdown/CoreDevice；此路徑與 LocalDevVPN 配合使用，無需額外的 IKEv2/IPsec 介面。
- 將簽署監測和背景資料庫啟動調適到 SideStore 0.7 API。
- 只有在資料庫更新成功後，才記錄自續簽狀態協調成功。

IPA 建置、自動化檢查和已發佈下載檔案的驗證均已通過。該確切 v1.0.3 建置的裝置測試仍待完成；較早的獨立版裝置證據在下方單獨列出。確切的原始碼修訂版本和驗證詳情請參閱[發佈說明](docs/RELEASE_NOTES_v1.0.3.md)。

<a id="whats-new-in-v210"></a>

## v2.1.0 有哪些新內容？

組合版 v2.1.0 保留現有 v2 續簽技術棧，並增加新的客體應用程式 Return 外觀控制項：

- **Start Collapsed：** 客體應用程式開啟時，Return 控制項可以已經折疊為邊緣標籤。
- **Use Custom Colors：** 為 Return 控制項啟用已儲存的自訂顏色。
- **Icon Color：** 更改 Return 圖示和折疊標籤圖示的顏色。
- **Button Background：** 更改展開後的 Return 按鈕背景。
- 客體應用程式控制項的外觀偏好會在重新啟動後保留。
- 仍可手動長按折疊、點一下展開。
- 折疊標籤在使用所選圖示顏色的同時，背景保持透明。

此發佈仍包含手動續簽、排程續簽、續簽歷史、驗證、LocalDevVPN/CoreDevice 傳輸，以及內嵌 SideStore 整合。

<a id="how-this-project-differs-from-the-original-projects"></a>

## 本專案與原始項目有何不同

SideStore 和 LiveContainer 是上游基礎；本儲存庫針對這一使用場景，將它們變為不同的端到端體驗。主要優勢是 **CoreDevice/RSD 續簽路徑**、**統一的 v6.0 LiveContainer + SideStore 工作流程**、明確的排程與歷史記錄、執行級驗證、結構化診斷，以及上文介紹的額外應用程式/客體 UI 控制項。

如需瞭解確切的原始碼級差異，請展開本 README 頂部附近的**與上游的技術比較**。

<a id="what-this-project-does-not-change"></a>

### 本專案沒有改變什麼

- 它**不會**消除常規 Personal Team 簽署到期限制，而是在到期前續簽。
- 它**不保證**精確的背景執行時間。任務排程由 iOS 控制。
- 它**尚未**使僅行動網路續簽成為穩定功能。
- 受支援的 Wi-Fi 路徑**不需要**越獄。
- 它**不會**讓 LiveContainer 客體應用程式等同於單獨安裝的 iOS 應用程式。
- 它**不保證** iOS 會讓每個客體行程持續存活。

由於組合版建置修改了 LiveContainer，且包含內嵌 SideStore 程式碼，請採用與其他修改版 LiveContainer 建置相同的信任模型。本儲存庫開源；如果你傾向於親自驗證建置路徑，可以[自行建置](#build-it-yourself)。

<a id="previous-product-lines"></a>

## 舊產品線

主要[安裝](#installation)指南適用於 v6.0。以下舊產品線保留用於參考，不作為推薦替代品。

<a id="combined-v211"></a>

### 組合版 v2.1.1

只有當你明確需要舊組合版介面時，才使用 [v2.1.1 發佈版](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.1)。開啟 LiveContainer，進入內嵌 SideStore 流程，先手動續簽一次，並且僅在該次手動續簽成功後啟用自動化。

<a id="standalone-sidestore-v104"></a>

### 獨立版 SideStore v1.0.4

如果你需要不包含 LiveContainer 的 SideStore，請使用 [v1.0.4 發佈版](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.4)。將它覆蓋安裝到匹配的現有獨立版 SideStore 安裝上，確保 LocalDevVPN 按當前 Wi-Fi 子網路設定，先手動續簽一次，再啟用其續簽排程。

<a id="normal-daily-use"></a>

## 日常使用

設定正常工作後：

- 保持 Wi-Fi 可用
- 盡可能保持 LocalDevVPN 正確設定並連接
- 保持背景 App 重新整理開啟
- 偶爾檢查續簽歷史，確認執行成功
- 切換到不同的 Wi-Fi 子網路時，更新 LocalDevVPN 位址

首次設定後，正常續簽執行期間應當無需電腦。

僅行動網路續簽仍屬於實驗功能。

<a id="updating-without-losing-setup-data"></a>

## 在不丟失設定資料的情況下更新

<a id="unified-v3-and-combined-v2"></a>

### 統一版 v6.0 和組合版 v2

使用相同的 Apple 帳號 / Personal Team 和匹配的識別碼，將新的組合版 IPA **覆蓋安裝到現有 LiveContainer 安裝上**。

**不要先刪除 LiveContainer。** 更新前請備份重要的客體應用程式資料。

<a id="standalone-v1"></a>

### 獨立版 v1

將新的獨立版 SideStore 建置覆蓋安裝到匹配的現有 SideStore 安裝上。

**不要先刪除 SideStore。** 覆蓋安裝有助於保留配對資料、Apple 帳號狀態和 SideStore 資料庫。

不要將獨立版 IPA 覆蓋安裝到 LiveContainer 上。不提供從獨立版到組合版的資料遷移。

<a id="troubleshooting"></a>

## 故障排查

| 問題 | 首先檢查 |
| --- | --- |
| Windows 上 iLoader 無法識別 iPhone | 確認已安裝 Apple 裝置驅動程式，重新連接 USB，解鎖手機，並點一下 **Trust** |
| iLoader 提示未選擇裝置 | 在執行 **Import IPA** 或配對操作前，先在 iLoader 中選擇 iPhone |
| 應用程式顯示不受信任的開發者 | `Settings -> General -> VPN & Device Management -> Developer App -> Trust` |
| 應用程式無法開啟 | 確認已在 `Settings -> Privacy & Security` 中啟用開發者模式 |
| 已確認配對資料缺失/無效 | 重新連接 USB，開啟 **Manage Pairing File**，使用 **Rescan Installed Apps**，然後再次對當前這台裝置執行 **Place** |
| 帳號登入或預配失敗 | 記錄確切的 v6.0 建置器版本和脫敏錯誤。v3.1.0 歷史記錄見 [Issue #42](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/42)；不要以猜測性修復措施刪除應用程式或重置帳號/配對資料 |
| v6.0 服務啟動或傳輸錯誤 | 閱讀診斷中的階段和錯誤碼。不要僅因服務或網路連接失敗就重置帳號或配對資料 |
| LocalDevVPN 無法連接 | 重新檢查 Network Configuration、兩個 `/32` 端點和 **Allow Intermediate Addresses** |
| LocalDevVPN 已連接但續簽失敗 | 確認位址位於 iPhone 當前 Wi-Fi 子網路內，且未被佔用 |
| 更換 Wi-Fi 後續簽停止 | 為新子網路重新設定 Tunnel IP 和 Device IP |
| 手動續簽失敗 | 確認 Wi-Fi、LocalDevVPN、配對、SideStore 登入，以及記錄的續簽結果/歷史 |
| 錯過排程續簽 | 確認手動續簽正常且背景 App 重新整理已啟用。所選時間並不是精確的喚醒時間 |
| App ID 數量不足 | 免費 Apple 帳號有註冊數量限制。不要反覆刪除/重新安裝建置。等待註冊到期，或在支援的情況下重複使用匹配的識別碼 |
| 客體應用程式簽署警告 | 此警告與已驗證的續簽狀態相互獨立。開啟警告中指明的客體應用程式，並報告它是否能實際啟動 |
| 僅行動網路續簽失敗 | 僅行動網路傳輸屬於實驗功能，不在受支援的 Wi-Fi 路徑範圍內 |

如需深入調查，請查看[驗證](docs/VERIFICATION.md)、[相容性](docs/COMPATIBILITY.md)和 [Issue #1](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/1)。

<a id="features"></a>

## 功能

<a id="shared-refresh-features"></a>

### 共有續簽功能

- 本機 LocalDevVPN + CoreDevice 續簽路徑
- 支援免費 Apple 帳號 / Personal Team
- 手動續簽
- 每六小時、每天和每週排程
- 偏好續簽時間控制項
- 持久儲存的續簽歷史
- 有界的重試與恢復邏輯
- 到期時間和通知支援
- 用於故障排查的有界診斷
- 首次設定後，正常續簽執行期間無需電腦

<a id="combined-features-retained-from-v2"></a>

### 從 v2 保留的組合版功能

- 內嵌本儲存庫修改版 SideStore 的 LiveContainer
- 宿主級續簽協調與交接處理
- 與執行關聯的驗證結果橋接
- 內嵌 SideStore 的啟動和認證修復
- 共享 Keychain 遷移與宿主身分處理
- 客體應用程式 Return 控制項
- 建議性的客體應用程式簽署檢查不會覆蓋經過驗證的成功續簽結果
- **v2.1.0：** Start Collapsed
- **v2.1.0：** 自訂 Return 圖示和背景顏色

<a id="guest-return-behavior"></a>

## 客體應用程式 Return 行為

| 執行模式 | Return 行為 |
| --- | --- |
| 視窗化 LiveProcess 多任務 | 隱藏懸浮 Return 按鈕。請使用普通視窗控制項。 |
| 全螢幕或最大化 LiveProcess | Return 會啟用宿主或將客體最小化以返回宿主，而不會有意終止客體應用程式。 |
| 重新開啟保留的客體應用程式 | 如果現有實例仍存活，則重複使用它。冷啟動前會清理過期實例。 |
| 直接在宿主行程中執行的客體應用程式 | 使用現有的重新啟動返回路徑。不保留客體記憶體。 |

長按 Return 按鈕可將其折疊為邊緣標籤。點一下標籤可恢復。其位置會被儲存，並在尺寸改變後限制在有效範圍內。

v2.1.0 在 **LiveContainer Settings > Guest Controls** 中增加以下選項：

- **Start Collapsed：** 新客體應用程式以邊緣標籤形式開啟。點一下展開，再點一下 Return 返回。使用 Return 會再次折疊控制項，包括重新開啟保留的客體應用程式時。此選項預設關閉。
- **Use Custom Colors：** 選擇 **Icon Color** 和 **Button Background**。圖示顏色也應用於折疊標籤；標籤背景保持透明。關閉此選項可恢復系統顏色。所選顏色會儲存以供以後使用。

這些偏好同時適用於全螢幕 LiveProcess 和直接在宿主行程中執行的客體應用程式。**Show Return Button** 仍控制可見性，視窗化多任務仍使用普通視窗控制項。

<a id="more-screenshots"></a>

## 更多截圖

以下補充截圖展示獨立版 SideStore 的續簽 UI。

<table>
<tr>
<td align="center"><img src="docs/screenshots/refresh-schedule-options.png" width="220" alt="每六小時、每天和每週續簽排程選項"><br><strong>排程選項</strong></td>
<td align="center"><img src="docs/screenshots/refresh-preferred-time-picker.png" width="220" alt="SideStore 偏好續簽時間選擇器"><br><strong>偏好時間</strong></td>
<td align="center"><img src="docs/screenshots/refresh-skipped.jpeg" width="220" alt="已跳過的 SideStore 續簽"><br><strong>已跳過續簽</strong></td>
</tr>
</table>

<a id="technical-architecture"></a>

## 技術架構

當前 v6.0 的原始碼依賴圖和建置邊界見[固定版本建置說明](#build-it-yourself)。以下 v3 文件僅是歷史架構參考，並非 v6.0 的確切原始碼快照。

確切的 v3 實現和職責邊界，請閱讀 [v3.0.2 標籤中的架構說明](https://github.com/NRG-Wardog/sidestore-auto-refresh/blob/v3.0.2/docs/V3_UNIFIED_ARCHITECTURE.md)。以下共享續簽傳輸仍與統一 UI 和服務生命週期分離。

```text
LiveContainer triggers / embedded SideStore
    -> refresh coordination and eligibility
    -> Wi-Fi / LocalDevVPN preflight
    -> signing and refresh pipeline
    -> Lockdown -> CoreDeviceProxy TLS -> CDTunnel
    -> userspace adapter -> RSD
    -> AFC staging / InstallationProxy and profile operations
    -> correlated verification results and history
```

CoreDevice 路徑保留服務 TLS、連續 CDTunnel 寫入、傳輸操作期間的心跳、封包大小和流控制修復，以及修正後的 FFI 所有權。實驗性的行動網路工作不屬於受支援的 Wi-Fi 路徑。

<a id="app-layout-architecture-issue-17"></a>

### 應用程式版面配置架構（Issue #17）

Issue #17 有意設計為**展示層功能**。它不得修改續簽傳輸、簽署、認證、背景排程、資料庫狀態協調或 CoreDevice 管線。

```text
Interface settings
    -> persisted AppLayoutPreferences
        -> App Layout: list | grid | compactList
        -> Show app labels: true | false
    -> Apps screen presentation switch
        -> List renderer
        -> Grid renderer
        -> Compact List renderer
    -> shared app model + shared actions
        -> open / refresh / activate / deactivate / context menu
```

實現約定如下：

- 新增一個小型、可持久儲存的版面配置模型，例如具有 `list`、`grid` 和 `compactList` 分支的 `AppLayoutStyle: String`。
- 保持 **List** 為預設值，使現有安裝保留當前行為。
- 在現有共享偏好中保留組合版鍵 `LCAppLayoutStyle` 和 `LCShowAppLabels`。獨立版使用其現有的偏好名稱。不要為 UI 偏好新增資料庫。
- 擴展現有的 **Interface -> App Layout** 設定，而不是建立第二個版面配置控制項。僅在選擇 Grid 時顯示 **Show app labels**。
- 將當前清單/卡片渲染器保留為 List 路徑。不要僅為共享程式碼而重寫已經正常工作的清單行為。
- 將 Grid 實現為以圖示為主的自適應網格，可選擇在每個圖示下方顯示應用程式名稱。
- Compact List 使用與 List 相同的應用程式資料和操作，但減小圖示尺寸、行高和垂直內距。
- 三種渲染器均通過現有的同一應用程式模型和操作處理器執行操作。版面配置專用視圖不得複製簽署、續簽、啟用、安裝或快顯選單的業務邏輯。
- 隱藏 Grid 標籤只影響視覺顯示。應用程式名稱必須仍可作為無障礙標籤使用。
- 僅將 [SideloadLabs/AppNest](https://github.com/SideloadLabs/AppNest) 作為視覺參考。除非另行審查其程式碼和許可證，否則不要將其實現作為第三方原始碼納入或複製。

<a id="patch-strategy"></a>

#### 補丁策略

本儲存庫應通過確定性的建置時補丁實現此功能，與專案其他部分保持一致。預期實現為專用的語義補丁，例如 `scripts/patch_app_layout.py`，面向固定版本的 SideStore 原始碼，而不是整體納入上游檔案。

獨立版和組合版當前使用不同的 SideStore 原始碼分支。UI 約定應保持一致，但當視圖結構不同時，可以使用原始碼特定的錨點/轉接器。不要僅為交付此 UI 功能，就將 LiveContainer 內嵌的 SideStore 升級到官方 SideStore 0.7。

補丁必須具有冪等性，並在上游錨點變化時安全停止。它只應修改此功能所需的最小設定/偏好和 Apps 頁面展示範圍。

<a id="verification-gates"></a>

#### 驗證門檻

在將 Issue #17 視為完成之前：

1. 儲存庫測試證明補丁具有冪等性，並會拒絕已更改或存在歧義的上游錨點。
2. 產生的 Swift 成功通過語法解析，所有獲得此功能的受支援目標均完成建置。
3. 可以從設定存取 List、Grid、不顯示可見標籤的 Grid，以及 Compact List。
4. 版面配置和標籤偏好在重新啟動後仍保留。
5. 在每種版面配置中，開啟、續簽、啟用/停用、狀態顯示和快顯選單操作仍使用相同的底層處理器。
6. 隱藏 Grid 標籤時，無障礙功能仍能讀取應用程式名稱。
7. 此功能不修改傳輸、簽署、認證、排程器或 CoreDevice 原始碼。
8. 發佈前，裝置截圖/驗收檢查覆蓋全部三種版面配置。

這種分離使 Issue #17 保持低風險：改變的是渲染器，而應用程式生命週期和續簽技術棧保持不變。

<a id="verification-status"></a>

## 驗證狀態

**v6.0 通過了限定範圍的實體裝置驗證，並非全面驗收。** 已確認流程和剩餘限制見[上文](#current-status-v60)。以下歷史自動化和裝置結果僅適用於各自的確切建置，不代表未測試的 v6.0 流程已通過。

**已測試建置來源：** 建置器 [`177ac31e90499231191b25f761014c17d9f2ba7b`](https://github.com/NRG-Wardog/LiveContainer/commit/177ac31e90499231191b25f761014c17d9f2ba7b)、原生建置 [40102842840](https://github.com/NRG-Wardog/LiveContainer/actions/runs/40102842840)、打包執行 [40104361491](https://github.com/NRG-Wardog/LiveContainer/actions/runs/40104361491)。本次文件及版本號更新不在此測試來源之外增加功能變更。版本名稱或 CI 成功本身並不等於實體裝置驗收。

| 範圍 | 當前證據 |
| --- | --- |
| 統一版 v3.1.0 建置與打包 | 確切建置器 65158743 / CI 37158730837 通過了 920 項 macOS 儲存庫測試，零跳過；並通過原生憑證檢查、產生原始碼/冪等性檢查、模擬器版面配置、宿主/後端建置及已上傳產物驗證。重新下載的發佈檔案與原始 IPA 校驗碼匹配。這並非該確切建置的實體裝置驗收 |
| 統一版 v3.0.2 建置與打包 | CI 35518324344 通過了 190 項儲存庫測試、512 項版面配置測量（0 失敗）、宿主 + 內嵌原始碼建置、傳輸檢查和 IPA 打包驗證。匹配的證據附於發佈版；這不是全面的實體裝置驗證 |
| 統一版 v3.0.1 建置與打包 | CI 35419528113 通過了 155 項儲存庫測試、512 項版面配置測量（0 失敗）、宿主 + 內嵌原始碼建置、傳輸檢查和 IPA 打包驗證。匹配的證據附於發佈版；這不是全面的實體裝置驗證 |
| 獨立版 v1.0.4 建置與打包 | CI 通過；已驗證發佈 IPA 的校驗碼、arm64 可執行檔案、背景任務設定和版面配置補丁語法解析 |
| 獨立版 v1.0.3 裝置續簽 | 此確切建置的裝置測試仍待完成 |
| 更早獨立版的手動 CoreDevice 續簽 | 已在 iPhone 12 / iOS 26.6.1 上驗證；早於 v1.0.3 |
| 更早獨立版在電腦斷開時的排程續簽 | 記錄的證明早於 v1.0.3，可在驗證報告中查看 |
| 組合版 v2.1.1 建置與打包 | 組合版 CI 執行成功完成，包括 LiveContainer 網格實現 |
| v2.1.0 Guest Controls | 已發佈組合版建置包含 Start Collapsed 和自訂顏色 |
| 背景排程 | 盡力執行。任務啟動時間由 iOS 控制 |
| 僅行動網路續簽 | 實驗功能，不在受支援的 Wi-Fi 路徑範圍內 |
| 客體行程保留 | 盡力保留。iOS 可能掛起或終止客體應用程式 |

建置完成、背景任務啟動或宿主發生交接，不會自動被視為簽署有效期已續期的證明。確切的證明模型請參閱 [docs/VERIFICATION.md](docs/VERIFICATION.md)。

<a id="v302-provenance"></a>

### v3.0.2 來源記錄

- 建置器/標籤提交：`35c6c28c98e7261afe6049a133a9ae542d666d57`
- 組合版 CI 執行：[35518324344](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/35518324344)
- IPA：`LiveContainer-SideStore-AutoRefresh.ipa`（38,736,104 位元組）
- SHA-256：`5AAB622EB91CA9007BD4A29E5AD8FEB90791AC8EBB1AB7393022A1C7474CA02E`
- LiveContainer：`12377cf3b91d51739a33f14a302e5f522b238593`
- 內嵌 SideStore：`ff25922e5c13ccfafd83bda5092910d848ebd409`
- SideSign：`a731c0d5a9a6617c7b385ae493e07ffb7f81cd5d`（GSA 修復 `35993d7` 已在 CI 中驗證為祖先提交）
- minimuxer：`98c3c79982f813878e922ab42f9545314a700f0c`
- 發佈版：[統一 LiveContainer + SideStore v3.0.2](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.0.2)

v3.0.2 是歷史統一版發佈，不是針對當前登入問題的已驗證回退方案。其來源記錄和下方的 v3.0.1 記錄保留用於復現。

<a id="v301-provenance"></a>

### v3.0.1 來源記錄

- 建置器/標籤提交：`6f0a144936c789bd7557d93ec1752888fddbb256`
- 組合版 CI 執行：[35419528113](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/35419528113)
- IPA：`LiveContainer-SideStore-AutoRefresh.ipa`（38,644,788 位元組）
- SHA-256：`A8A783D4FB6D229FAA449024872F1C56755351F9593349C6BC10B9CD76FE16311`
- LiveContainer：`12377cf3b91d51739a33f14a302e5f522b238593`
- 內嵌 SideStore：`ff25922e5c13ccfafd83bda5092910d848ebd409`
- SideSign：`a731c0d5a9a661c7b385ae493e07ffb7f81cd5d`（GSA 修復 `35993d7` 已在 CI 中驗證為祖先提交）
- minimuxer：`98c3c79982f813878e922ab42f9545314a700f0c`
- 發佈版：[統一 LiveContainer + SideStore v3.0.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.0.1)

v3.0.1 記錄在此保留，用於復現和歷史驗證。之後是此前發佈的 v3.0.0 來源記錄。

<a id="v300-provenance"></a>

### v3.0.0 來源記錄

- 建置器/標籤提交：`6bbddd55531ac2497707fce184e5370f76d7991f`
- 組合版 CI 執行：[34763923268](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34763923268)
- IPA：`LiveContainer-SideStore-AutoRefresh.ipa`
- SHA-256：`699f20bd839136b652e30c6c4de1d9237aa79b91437202daf4f8e59684358268`
- SideStoreSupport 和匹配的 dSYM UUID：`C59B7B1F-1CC8-3667-AE90-2514C2F11869`
- 發佈版：[統一 LiveContainer + SideStore v3.0.0](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v3.0.0)

此發佈將已驗證的 preview.2 IPA 提升為正式發佈，而不重新建置或重新簽署。其內部上游版本仍可能顯示 `3.8.9`；請通過發佈標籤和 **Settings > Build Candidate** 修訂版本識別套件。v2 和獨立版附件保留各自獨立的來源記錄。

<a id="v211-provenance"></a>

### v2.1.1 來源記錄

- 建置器/標籤提交：`3d4a7c49f9c202159397c25106149a3bad2b2902`
- 組合版 CI 執行：[34689847917](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34689847917)
- IPA：`LiveContainer-SideStore-AutoRefresh.ipa`
- SHA-256：`94a95f1b51066db0552c65ac5e4f3cf67efc3baa293adc2eca077228adabb583`
- 發佈版：[LiveContainer + SideStore 自動續簽 v2.1.1](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v2.1.1)

<a id="v104-provenance"></a>

### v1.0.4 來源記錄

- 建置器/標籤提交：`3d4a7c49f9c202159397c25106149a3bad2b2902`
- 獨立版 CI 執行：[34689425130](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34689425130)
- IPA：`SideStore.ipa`
- SHA-256：`f9641f559e922ea4de5099d9857438a7970e216f40536cf7fece8f21a1ee3692`
- 發佈版：[SideStore CoreDevice 自動續簽 v1.0.4](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.4)

<a id="v103-provenance"></a>

### v1.0.3 來源記錄

- 建置器/標籤提交：`07d52be7a49f6795b82f081ded2ec94eb44d50df`
- 獨立版 CI 執行：[34613911507](https://github.com/NRG-Wardog/sidestore-auto-refresh/actions/runs/34613911507)
- 上游應用程式版本：SideStore `0.7.0`，建置 `0700`
- IPA：`SideStore-CoreDevice-AutoRefresh-v1.0.3.ipa`
- SHA-256：`ab35772fe3209618c7bec302e315faea0a35b7ae280edfb9b5390d2aa62c8940`
- 發佈版：[SideStore CoreDevice 自動續簽 v1.0.3](https://github.com/NRG-Wardog/sidestore-auto-refresh/releases/tag/v1.0.3)
- 固定原始碼和驗證：[v1.0.3 發佈說明](docs/RELEASE_NOTES_v1.0.3.md)

<a id="build-it-yourself"></a>

## 自行建置

當前 v6.0 的建置定義位於 **NRG-Wardog/LiveContainer**，並非本儲存庫 `main` 分支上的舊組合建置工作流程。

1. 查看 `177ac31e90499231191b25f761014c17d9f2ba7b` 上的確切[原生編譯工作流程](https://github.com/NRG-Wardog/LiveContainer/blob/177ac31e90499231191b25f761014c17d9f2ba7b/.github/workflows/native-app-compile.yml)及其固定原始碼依賴圖。原生建置 [40102842840](https://github.com/NRG-Wardog/LiveContainer/actions/runs/40102842840) 使用 macOS 26、Xcode 26.4.1 和 Rust 1.98.1。
2. 查看 `0403c859f943ffe4f17a048ac1309dd009f1f17b` 上的確切[打包工作流程](https://github.com/NRG-Wardog/LiveContainer/blob/0403c859f943ffe4f17a048ac1309dd009f1f17b/.github/workflows/package-diagnostic.yml)及打包執行 [40104361491](https://github.com/NRG-Wardog/LiveContainer/actions/runs/40104361491)。打包使用保留的原生建置產物及經過獨立審核的驗證憑據和輸入雜湊，不重新建置應用程式。
3. 保留不可變原始碼修訂、工作流程限制、工具鏈和產物繫結。這些驗證工作流程限定特定儲存庫和分支身分，因此直接 fork 並原樣觸發不是通用的 v6.0 建置方法。新建置需要自己的已審核產物及證據，不會自動與已測試發佈套件逐位元組相同。
4. 安裝時，請使用自己的 Apple 帳號 / Personal Team 為 IPA 簽署。請根據記錄的建置器、確切發佈附件及校驗碼識別公開安裝套件，而不是根據保留的上游 bundle 版本。

以下舊 SAR 工作流程仍可用於各自的獨立版或歷史目標；它們**不能**重現 v6.0 安裝套件：

[歷史組合工作流程](.github/workflows/livecontainer-build.yml) | [獨立版工作流程](.github/workflows/build-current.yml)

<a id="development-checks"></a>

## 開發檢查

```bash
python -m unittest discover -s tests -v
git diff --check
```

依賴編譯器的測試需要相應工具鏈。原始碼和靜態檢查不能替代真實裝置驗證。

<a id="security-privacy-licensing-and-support"></a>

## 安全、隱私、許可和支援

**v6.0 的遠端 anisette 依賴：** 已測試建置使用外部 Mac anisette 提供者 `mac-ani.jkcoxson.com`，並透過 Keychain 重複使用其身分。這涉及第三方網路及隱私信任邊界，並非完全本機的認證路徑。登入前請評估此依賴；不要公開提供者身分資料、帳號憑證、配對記錄或私密日誌。

切勿公開配對檔案、Apple 憑據、私鑰、個人簽署 IPA、不必要的裝置識別碼或完整的私密裝置日誌。

組合版建置可以按照 LiveContainer 架構存取 LiveContainer 及其客體應用程式所使用的資料。如果你重視信任模型，請審查原始碼並自行建置 IPA。

除非另有說明，本儲存庫原創工作採用 MIT 許可證。上游程式碼及衍生二進制檔案保留其適用的上游許可證。

橫幅是借助 AI 創作的專案圖片，參考了上游 [LiveContainer](https://github.com/LiveContainer/LiveContainer) 和 [SideStore](https://github.com/SideStore/SideStore) 的應用程式圖示。它不是應用程式截圖，也不表示上游認可。

[貢獻](CONTRIBUTING.md) | [安全](SECURITY.md) | [許可證](LICENSE) | [第三方聲明](THIRD_PARTY_NOTICES.md)

報告相容性結果時，請提供確切的發佈標籤和建置器修訂版本、裝置型號、iOS 版本、產品線（v6.0 或歷史 v1/v2/v3）、下載來源、Wi-Fi 網路類別、LocalDevVPN 狀態，以及不含敏感資訊的續簽結果。不要附加秘密資訊或完整的私密日誌。相容性報告請提交至 [Issue #1](https://github.com/NRG-Wardog/sidestore-auto-refresh/issues/1)。
