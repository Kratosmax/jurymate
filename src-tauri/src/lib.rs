use std::net::TcpStream;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::Manager;

const BACKEND_PORT: u16 = 5090;

/// backend sidecar 进程句柄；退出时 kill
static BACKEND_CHILD: Mutex<Option<Child>> = Mutex::new(None);

fn backend_alive() -> bool {
    TcpStream::connect(("127.0.0.1", BACKEND_PORT)).is_ok()
}

/// sidecar 定位：打包后 = 资源目录 dist-backend/jury-backend.exe（bundle.resources
/// 随安装包分发）；开发模式 = 项目根 dist-backend/（PyInstaller 产物）或手动 Flask
fn backend_exe(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    if let Ok(d) = app.path().resource_dir() {
        let p = d.join("dist-backend/jury-backend.exe");
        if p.exists() {
            return Some(p);
        }
    }
    let dev = std::env::current_dir()
        .ok()?
        .ancestors()
        .map(|d| d.join("dist-backend/jury-backend.exe"))
        .find(|p| p.exists())?;
    Some(dev)
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
        .invoke_handler(tauri::generate_handler![greet, backend_port, backend_status])
        .setup(|app| {
            let handle = app.handle().clone();
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
