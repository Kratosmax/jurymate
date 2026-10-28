use std::net::TcpStream;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};

const BACKEND_PORT: u16 = 5090;

/// backend sidecar 进程句柄；退出时 kill
static BACKEND_CHILD: Mutex<Option<Child>> = Mutex::new(None);
static APP_HANDLE: std::sync::OnceLock<tauri::AppHandle> = std::sync::OnceLock::new();

fn backend_alive() -> bool {
    TcpStream::connect(("127.0.0.1", BACKEND_PORT)).is_ok()
}

/// sidecar 定位：打包后 = 资源目录 dist-backend/jury-backend.exe（bundle.resources
/// 随安装包分发）；开发模式 = 项目根 dist-backend/（PyInstaller 产物）或手动 Flask
/// sidecar 选择：settings.json 的 debug=true → 带*控制台的 debug 版
/// （用户需求：正式版无控制台，debug 模式在设置里开启）；默认无控制台版。
fn backend_exe(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    // debug 版只是可选的调试辅助：安装包里只保证正式版存在，
    // 因此按 [debug, 正式] 的顺序找，debug 缺失时静默回退到正式版。
    let names: &[&str] = if debug_enabled(app) {
        &["jury-backend-debug.exe", "jury-backend.exe"]
    } else {
        &["jury-backend.exe"]
    };
    if let Ok(d) = app.path().resource_dir() {
        for name in names {
            let p = d.join(format!("dist-backend/{}", name));
            if p.exists() {
                return Some(p);
            }
        }
    }
    for name in names {
        let found = std::env::current_dir()
            .ok()?
            .ancestors()
            .map(|d| d.join(format!("dist-backend/{}", name)))
            .find(|p| p.exists());
        if found.is_some() {
            return found;
        }
    }
    None
}

/// 解析当前生效的数据根目录，与 backend/paths.py 的优先级保持一致：
/// 1. 环境变量 JURYMATE_DATA_DIR
/// 2. %LOCALAPPDATA%/JuryMate/paths.json 里的 data_root
/// 3. %LOCALAPPDATA%/JuryMate
///
/// 为什么 Rust 也要算：sidecar 是 PyInstaller onefile，启动时把全部依赖
/// 解压到 %TEMP%\_MEI*（约 1.1GB）。只有在我们 **spawn 它之前** 把 TEMP
/// 指到数据目录，这个解压才会落在用户指定的位置，而不是系统盘。
fn data_root() -> std::path::PathBuf {
    if let Ok(v) = std::env::var("JURYMATE_DATA_DIR") {
        if !v.trim().is_empty() {
            return std::path::PathBuf::from(v);
        }
    }
    let base = std::env::var("LOCALAPPDATA")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| std::env::temp_dir());
    let root = base.join("JuryMate");
    let conf = root.join("paths.json");
    if let Ok(s) = std::fs::read_to_string(&conf) {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&s) {
            if let Some(p) = v.get("data_root").and_then(|x| x.as_str()) {
                if !p.trim().is_empty() {
                    return std::path::PathBuf::from(p);
                }
            }
        }
    }
    root
}

/// 壳自己的配置也放数据根下，跟着用户的「存储位置」走。
///
/// 不用 Tauri 的 app_config_dir()：那个位置固定在 %LOCALAPPDATA%/com.jurymate.app，
/// 用户把数据目录换到别的盘后，这里仍留在系统盘，与"所有存储位置可自定义"矛盾。
fn app_settings_path() -> std::path::PathBuf {
    data_root().join("settings.json")
}

/// debug 开关持久化在 <数据根>/settings.json
fn debug_enabled(_app: &tauri::AppHandle) -> bool {
    std::fs::read_to_string(app_settings_path())
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.get("debug").and_then(|d| d.as_bool()))
        .unwrap_or(false)
}

#[tauri::command]
fn set_debug(enabled: bool) -> Result<(), String> {
    let p = app_settings_path();
    if let Some(dir) = p.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let mut cfg: serde_json::Value = std::fs::read_to_string(&p).ok()
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_else(|| serde_json::json!({}));
    cfg["debug"] = serde_json::json!(enabled);
    std::fs::write(&p, serde_json::to_string_pretty(&cfg).unwrap())
        .map_err(|e| e.to_string())
}

fn spawn_backend(app: &tauri::AppHandle) -> Result<(), String> {
    if backend_alive() {
        return Ok(()); // 已有实例（如开发者手动起的 Flask）
    }
    let Some(exe) = backend_exe(app) else {
        return Err(format!(
            "未找到后端程序 dist-backend/jury-backend.exe。开发模式下请手动运行 backend/app.py（端口 {}）",
            BACKEND_PORT
        ));
    };
    // 把壳的版本传给 sidecar：打包后的 Python 程序读不到仓库里的
    // tauri.conf.json，没有这一步前端就拿不到后端版本、无法提示不一致。
    // 数据根下的 tmp，作为 sidecar 的临时目录（见 data_root 的注释）
    let root = data_root();
    let tmp = root.join("tmp");
    let _ = std::fs::create_dir_all(&tmp);

    let child = Command::new(&exe)
        .arg("--port")
        .arg(BACKEND_PORT.to_string())
        .arg("--no-browser")
        .env("JURYMATE_VERSION", env!("CARGO_PKG_VERSION"))
        // 把临时目录钉到数据根下的 tmp：PyInstaller onefile 会在此解压
        // 约 1.1GB 的运行时，被强杀时不会自删，放系统盘会越积越多。
        .env("TEMP", &tmp)
        .env("TMP", &tmp)
        .spawn()
        .map_err(|e| format!("启动后端失败: {}", e))?;
    *BACKEND_CHILD.lock().unwrap() = Some(child);
    // 健康等待：onefile 冷启动自解压可达 30 秒
    let deadline = Instant::now() + Duration::from_secs(90);
    while Instant::now() < deadline {
        if backend_alive() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    Err("后端启动超时（90 秒内未监听端口）".into())
}

fn kill_backend() {
    if let Some(mut c) = BACKEND_CHILD.lock().unwrap().take() {
        let _ = c.kill();
        let _ = c.wait(); // 回收句柄，确保进程真的没了（否则文件仍被锁）
    }
}

/// 按可执行文件名扫掉残留 sidecar。
///
/// 只靠子进程句柄不够：进程可能是上一次会话遗留的、或句柄已丢失，
/// 它们照样锁着 dist-backend/*.exe，让安装器覆盖失败。
#[cfg(windows)]
fn kill_backend_by_name() {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let _ = Command::new("taskkill")
        .args(["/F", "/IM", "jury-backend.exe", "/T"])
        .creation_flags(CREATE_NO_WINDOW)
        .status();
    // debug 版同名规则不同，单独清
    let _ = Command::new("taskkill")
        .args(["/F", "/IM", "jury-backend-debug.exe", "/T"])
        .creation_flags(CREATE_NO_WINDOW)
        .status();
}

#[cfg(not(windows))]
fn kill_backend_by_name() {}

#[tauri::command]
fn backend_port() -> u16 {
    BACKEND_PORT
}

#[tauri::command]
fn backend_status() -> String {
    if backend_alive() { "up" } else { "down" }.into()
}

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

/// 安装更新前主动停掉后端。
///
/// 为什么不能只靠 app 退出时清理：更新器（tauri-plugin-updater）的执行顺序是
///   on_before_exit 钩子 → **spawn 安装器** → 应用退出
/// 安装器进程起来时应用还活着，sidecar 也还活着，安装器于是报
/// "Error opening file for writing: dist-backend\jury-backend.exe" 并卡住。
/// RunEvent::Exit 要到后面才触发，那时已经晚了。
///
/// 所以由前端在 downloadAndInstall 之前先调这个命令，确保文件已释放。
#[tauri::command]
fn prepare_update() -> String {
    kill_backend();
    kill_backend_by_name();
    // 给 Windows 一点时间释放文件句柄，否则安装器可能仍撞到占用
    for _ in 0..20 {
        if backend_file_free() {
            return "ok".into();
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    "busy".into()
}

/// 探测 dist-backend/jury-backend.exe 当前能否独占打开。
#[cfg(windows)]
fn backend_file_free() -> bool {
    let Some(app) = APP_HANDLE.get() else { return true };
    let Some(p) = backend_exe(app) else { return true };
    match std::fs::OpenOptions::new().read(true).write(true).open(&p) {
        Ok(_) => true,
        Err(_) => false,
    }
}

#[cfg(not(windows))]
fn backend_file_free() -> bool {
    true
}

/// 在资源管理器中定位文件，优先复用已经打开该目录的窗口。
///
/// 为什么不用 `explorer /select,`：那样每点一次就多开一个窗口。
/// Windows 原生做法是枚举 Shell 窗口（IShellWindows），找到已打开目标
/// 目录的那个，对它 Navigate2 到文件路径——同一个窗口内直接定位并选中。
/// 只有确实没有已打开窗口时才新建。
#[cfg(windows)]
#[tauri::command]
fn reveal_in_explorer(path: String) -> Result<String, String> {
    use windows::Win32::System::Com::{CoInitializeEx, CLSCTX_ALL, COINIT_APARTMENTTHREADED};

    let target = std::path::PathBuf::from(&path);
    if !target.exists() {
        return Err(format!("文件不存在：{path}"));
    }
    let parent = target.parent().ok_or_else(|| "取不到父目录".to_string())?.to_path_buf();

    // SHOpenFolderAndSelectItems(pidlFolder, cidl, apidl, flags) 的**第一个参数
    // 必须是文件夹的 PIDL**，apidl 才是相对它解析的子项。之前把文件自己的 PIDL
    // 当成 pidlFolder 传进去，Shell 就按"打开这个文件"处理——用户实测直接
    // 启动了看图程序。这里分别解析父目录与文件，取文件的最后一级作为子项。
    let folder_w: Vec<u16> = parent.to_string_lossy().encode_utf16().chain(std::iter::once(0)).collect();
    let file_w: Vec<u16> = path.encode_utf16().chain(std::iter::once(0)).collect();

    unsafe {
        // SHParseDisplayName / SHOpenFolderAndSelectItems 要求当前线程已初始化 COM
        let _ = CoInitializeEx(None, COINIT_APARTMENTTHREADED);
        use windows::Win32::UI::Shell::{ILFindLastID, SHOpenFolderAndSelectItems, SHParseDisplayName};
        use windows::core::PCWSTR;

        let mut folder_pidl = std::ptr::null_mut();
        let mut file_pidl = std::ptr::null_mut();
        let mut attrs = 0u32;
        SHParseDisplayName(PCWSTR(folder_w.as_ptr()), None, &mut folder_pidl, 0, Some(&mut attrs))
            .map_err(|e| format!("解析目录失败: {e}"))?;
        if SHParseDisplayName(PCWSTR(file_w.as_ptr()), None, &mut file_pidl, 0, Some(&mut attrs)).is_err() {
            windows::Win32::System::Com::CoTaskMemFree(Some(folder_pidl as *const _));
            return Err("解析文件路径失败".into());
        }
        let child = ILFindLastID(file_pidl);
        let r = SHOpenFolderAndSelectItems(folder_pidl, Some(&[child]), 0);
        windows::Win32::System::Com::CoTaskMemFree(Some(folder_pidl as *const _));
        windows::Win32::System::Com::CoTaskMemFree(Some(file_pidl as *const _));
        if r.is_err() {
            // 兜底：交给 explorer 的 /select 语法（仍是系统原生命令）
            let _ = std::process::Command::new("explorer")
                .arg(format!("/select,{}", path))
                .spawn();
        }
    }
    Ok("opened".into())
}

#[cfg(windows)]
fn percent_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            if let (Some(h), Some(l)) = (hex_val(b[i + 1]), hex_val(b[i + 2])) {
                out.push(h * 16 + l);
                i += 3;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).to_string()
}

#[cfg(windows)]
fn hex_val(c: u8) -> Option<u8> {
    match c {
        b'0'..=b'9' => Some(c - b'0'),
        b'a'..=b'f' => Some(c - b'a' + 10),
        b'A'..=b'F' => Some(c - b'A' + 10),
        _ => None,
    }
}

#[cfg(not(windows))]
#[tauri::command]
fn reveal_in_explorer(path: String) -> Result<String, String> {
    let _ = path;
    Err("仅支持 Windows".into())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![greet, backend_port, backend_status, set_debug, prepare_update, reveal_in_explorer, updater_check, updater_install])
        .setup(|app| {
            let handle = app.handle().clone();
            let _ = APP_HANDLE.set(handle.clone());
            // 先清上一次会话可能残留的 sidecar：它们占着端口会让
            // spawn_backend 直接复用旧进程（版本可能已不匹配）
            kill_backend_by_name();
            std::thread::spawn(move || {
                if let Err(e) = spawn_backend(&handle) {
                    eprintln!("[jurymate] backend: {}", e);
                }
            });
            Ok(())
        })
        .on_window_event(|_window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                kill_backend();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app, event| {
            // 关键：Updater 用 relaunch() 重启时窗口不一定会走 Destroyed，
            // sidecar 会残留并锁住 dist-backend/*.exe，导致新版本安装器
            // 报 "Error opening file for writing" 而无法覆盖。
            // 在任何退出路径上都清理后端进程，并按名字兜底清残留。
            if let tauri::RunEvent::Exit = event {
                kill_backend();
                kill_backend_by_name();
            }
        });
}

// ============================================================
// 更新器（镜像优先）
// ============================================================
// 为什么不用 tauri-plugin-updater 的 JS check()：它的 endpoint 是编译期写死的
// github.com，运行时无法替换；而被墙的用户只有镜像线路能用。这里用
// `updater_builder().endpoints(...)` 在运行时注入候选线路，取清单、下载、
// 校验、安装全部由同一个 Updater 完成，签名与哈希校验沿用插件实现。
use tauri_plugin_updater::UpdaterExt;

#[derive(serde::Serialize)]
pub struct UpdateInfo {
    pub available: bool,
    pub current_version: String,
    pub version: Option<String>,
    pub notes: Option<String>,
}

fn build_updater(
    app: &tauri::AppHandle,
    endpoints: &[String],
    proxy: Option<&str>,
) -> Result<tauri_plugin_updater::Updater, String> {
    let urls: Vec<tauri::Url> = endpoints
        .iter()
        .map(|e| e.trim())
        .filter(|e| !e.is_empty())
        .filter_map(|e| tauri::Url::parse(e).ok())
        .collect();
    if urls.is_empty() {
        return Err("没有可用的更新线路（请在网络设置里启用镜像或保留直连）".into());
    }
    let mut b = app.updater_builder().endpoints(urls).map_err(|e| e.to_string())?;
    if let Some(p) = proxy.map(str::trim).filter(|p| !p.is_empty()) {
        let u = tauri::Url::parse(p).map_err(|e| format!("代理地址不合法：{e}"))?;
        b = b.proxy(u);
    }
    b.build().map_err(|e| e.to_string())
}

#[tauri::command]
async fn updater_check(
    app: tauri::AppHandle,
    endpoints: Vec<String>,
    proxy: Option<String>,
) -> Result<UpdateInfo, String> {
    let updater = build_updater(&app, &endpoints, proxy.as_deref())?;
    let current = updater
        .check()
        .await
        .map_err(|e| e.to_string())?;
    match current {
        Some(u) => Ok(UpdateInfo {
            available: true,
            current_version: u.current_version.clone(),
            version: Some(u.version.clone()),
            notes: u.body.clone(),
        }),
        None => Ok(UpdateInfo {
            available: false,
            current_version: app.package_info().version.to_string(),
            version: None,
            notes: None,
        }),
    }
}

#[tauri::command]
async fn updater_install(
    app: tauri::AppHandle,
    endpoints: Vec<String>,
    prefixes: Vec<String>,
    proxy: Option<String>,
) -> Result<(), String> {
    // 先用插件 check 一次：拿到带签名的 Update（签名在清单里，对应安装包字节）
    let updater = build_updater(&app, &endpoints, proxy.as_deref())?;
    let update = updater
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "已是最新版本".to_string())?;

    // 再用自己的 HTTP 客户端按同一批线路取清单，目的是拿到原始下载地址，
    // 并按「命中的那条线路」改写成镜像地址 —— 插件做不到这点，它只照清单里
    // 的 github.com 地址下载，被墙时就是 0 字节卡死。
    // 注意：不能设 client 级别的 timeout()，那是对整个请求计时——472MB 在慢镜像上
    // 必然超过 120 秒会被中断（实测断在 2%）。这里只限制建连与单次读取的空闲。
    let mut cb = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(20))
        .read_timeout(Duration::from_secs(60));
    if let Some(p) = proxy.as_deref().map(str::trim).filter(|p| !p.is_empty()) {
        cb = cb.proxy(reqwest::Proxy::all(p).map_err(|e| format!("代理地址不合法：{e}"))?);
    }
    let client = cb.build().map_err(|e| e.to_string())?;

    let mut last_err = String::new();
    let mut picked: Option<(String, serde_json::Value)> = None;
    for (i, ep) in endpoints.iter().enumerate() {
        match client.get(ep).send().await {
            Ok(r) if r.status().is_success() => match r.json::<serde_json::Value>().await {
                Ok(v) => {
                    picked = Some((prefixes.get(i).cloned().unwrap_or_default(), v));
                    break;
                }
                Err(e) => last_err = format!("{ep}: {e}"),
            },
            Ok(r) => last_err = format!("{ep}: HTTP {}", r.status()),
            Err(e) => last_err = format!("{ep}: {e}"),
        }
    }
    let (prefix, manifest) =
        picked.ok_or_else(|| format!("所有线路都无法获取清单：{last_err}"))?;
    let original = manifest["platforms"]["windows-x86_64"]["url"]
        .as_str()
        .ok_or_else(|| "清单缺少 windows-x86_64 下载地址".to_string())?
        .to_string();
    let dl = if prefix.is_empty() {
        original
    } else {
        format!("{}/{}", prefix.trim_end_matches('/'), original)
    };

    let mut resp = client
        .get(&dl)
        .send()
        .await
        .map_err(|e| format!("下载失败：{e}"))?;
    if !resp.status().is_success() {
        return Err(format!("下载失败：HTTP {}", resp.status()));
    }
    let total = resp.content_length();
    let mut buf: Vec<u8> = Vec::with_capacity(total.unwrap_or(0) as usize);
    let mut last = std::time::Instant::now();
    while let Some(chunk) = resp.chunk().await.map_err(|e| format!("下载中断：{e}"))? {
        buf.extend_from_slice(&chunk);
        if last.elapsed() >= Duration::from_millis(200) {
            last = std::time::Instant::now();
            let _ = app.emit("updater://progress", (buf.len(), total));
        }
    }
    let _ = app.emit("updater://progress", (buf.len(), total));

    // install 内部会再验一次签名（minisign + 公钥），不通过绝不执行安装器
    update
        .install(&buf)
        .map_err(|e| format!("校验或安装失败：{e}"))?;
    Ok(())
}
