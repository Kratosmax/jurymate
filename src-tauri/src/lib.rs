use std::net::TcpStream;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::Manager;

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
    let debug = debug_enabled(app);
    let name = if debug { "jury-backend-debug.exe" } else { "jury-backend.exe" };
    if let Ok(d) = app.path().resource_dir() {
        let p = d.join(format!("dist-backend/{}", name));
        if p.exists() {
            return Some(p);
        }
    }
    let dev = std::env::current_dir()
        .ok()?
        .ancestors()
        .map(|d| d.join(format!("dist-backend/{}", name)))
        .find(|p| p.exists())?;
    Some(dev)
}

/// debug 开关持久化在 %LOCALAPPDATA%/JuryMate/settings.json
fn debug_enabled(app: &tauri::AppHandle) -> bool {
    let dir = app
        .path()
        .app_config_dir()
        .unwrap_or_else(|_| std::env::current_dir().unwrap_or_default());
    let p = dir.join("settings.json");
    std::fs::read_to_string(p)
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.get("debug").and_then(|d| d.as_bool()))
        .unwrap_or(false)
}

#[tauri::command]
fn set_debug(enabled: bool) -> Result<(), String> {
    let dir = crate::APP_HANDLE
        .get()
        .ok_or("app not ready")?
        .path()
        .app_config_dir()
        .map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let p = dir.join("settings.json");
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
    let child = Command::new(&exe)
        .arg("--port")
        .arg(BACKEND_PORT.to_string())
        .arg("--no-browser")
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
    }
}

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![greet, backend_port, backend_status, set_debug])
        .setup(|app| {
            let handle = app.handle().clone();
            let _ = APP_HANDLE.set(handle.clone());
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
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
