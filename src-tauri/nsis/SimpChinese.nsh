; Tauri 安装器自定义文案的简体中文翻译
;
; 说明：
; - NSIS 通用界面（按钮、页面标题等）由 MUI 的 SimpChinese.nsh 提供，
;   这里只覆盖 Tauri 自己新增的那些 LangString
; - ${LANG_SIMPCHINESE} 是 NSIS 内置语言常量
; - ${PRODUCTNAME} / $R4 / $0 / $1 / {{product_name}} 是运行时占位符，
;   必须原样保留，否则安装器会显示成字面量或报错

LangString addOrReinstall ${LANG_SIMPCHINESE} "添加/重新安装组件"
LangString alreadyInstalled ${LANG_SIMPCHINESE} "已安装"
LangString alreadyInstalledLong ${LANG_SIMPCHINESE} "${PRODUCTNAME} ${VERSION} 已安装。请选择要执行的操作，然后点击下一步继续。"
LangString appRunning ${LANG_SIMPCHINESE} "{{product_name}} 正在运行！请先关闭它再重试。"
LangString appRunningOkKill ${LANG_SIMPCHINESE} "{{product_name}} 正在运行！$\n点击「确定」结束它"
LangString chooseMaintenanceOption ${LANG_SIMPCHINESE} "请选择要执行的维护操作。"
LangString chooseHowToInstall ${LANG_SIMPCHINESE} "请选择 ${PRODUCTNAME} 的安装方式。"
LangString createDesktop ${LANG_SIMPCHINESE} "创建桌面快捷方式"
LangString dontUninstall ${LANG_SIMPCHINESE} "不卸载"
LangString dontUninstallDowngrade ${LANG_SIMPCHINESE} "不卸载（本安装程序不允许不卸载就降级）"
LangString failedToKillApp ${LANG_SIMPCHINESE} "无法结束 {{product_name}}。请先手动关闭它再重试"
LangString installingWebview2 ${LANG_SIMPCHINESE} "正在安装 WebView2…"
LangString newerVersionInstalled ${LANG_SIMPCHINESE} "系统中已安装了更新版本的 ${PRODUCTNAME}！不建议再安装旧版本。如果确实要安装这个旧版本，建议先卸载当前版本。请选择要执行的操作，然后点击下一步继续。"
LangString older ${LANG_SIMPCHINESE} "更旧"
LangString olderOrUnknownVersionInstalled ${LANG_SIMPCHINESE} "系统中安装的是 $R4 版本的 ${PRODUCTNAME}。建议安装前先卸载当前版本。请选择要执行的操作，然后点击下一步继续。"
LangString silentDowngrades ${LANG_SIMPCHINESE} "本安装程序不允许降级，无法继续静默安装，请改用图形界面安装。$\n"
LangString unableToUninstall ${LANG_SIMPCHINESE} "无法卸载！"
LangString uninstallApp ${LANG_SIMPCHINESE} "卸载 ${PRODUCTNAME}"
LangString uninstallBeforeInstalling ${LANG_SIMPCHINESE} "安装前先卸载"
LangString unknown ${LANG_SIMPCHINESE} "未知"
LangString webview2AbortError ${LANG_SIMPCHINESE} "WebView2 安装失败！没有它程序无法运行。请重新启动安装程序再试一次。"
LangString webview2DownloadError ${LANG_SIMPCHINESE} "错误：下载 WebView2 失败 - $0"
LangString webview2DownloadSuccess ${LANG_SIMPCHINESE} "WebView2 引导程序下载完成"
LangString webview2Downloading ${LANG_SIMPCHINESE} "正在下载 WebView2 引导程序…"
LangString webview2InstallError ${LANG_SIMPCHINESE} "错误：WebView2 安装失败，退出码 $1"
LangString webview2InstallSuccess ${LANG_SIMPCHINESE} "WebView2 安装完成"
LangString deleteAppData ${LANG_SIMPCHINESE} "删除应用数据"
