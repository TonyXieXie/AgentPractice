use std::{
    net::TcpListener,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
};

use tauri::{Manager, RunEvent, WindowEvent};

// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

struct BackendChild(Mutex<Option<Child>>);
struct BackendState {
    port: u16,
}

#[tauri::command]
fn get_backend_base_url(state: tauri::State<BackendState>) -> String {
    if let Ok(value) = std::env::var("VITE_API_BASE_URL") {
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            return trimmed.to_string();
        }
    }
    format!("http://127.0.0.1:{}", state.port)
}

fn log_sandbox_status() {
    #[cfg(target_os = "macos")]
    {
        let home = std::env::var("HOME").unwrap_or_default();
        let home_in_container = home.contains("/Library/Containers/");
        let sandbox_id = std::env::var("APP_SANDBOX_CONTAINER_ID").unwrap_or_default();
        let sandbox_label = if sandbox_id.is_empty() { "(none)" } else { &sandbox_id };
        eprintln!(
            "[Sandbox] macos home_in_container={} app_sandbox_id={}",
            home_in_container, sandbox_label
        );
    }
}

fn resolve_backend_path<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> Result<PathBuf, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|_| "Failed to resolve resource directory.".to_string())?;
    let exe_name = if cfg!(windows) {
        "tauri-agent-backend.exe"
    } else {
        "tauri-agent-backend"
    };
    let candidate = resource_dir.join(exe_name);
    if candidate.exists() {
        return Ok(candidate);
    }
    let fallback = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.join(exe_name)));
    if let Some(path) = fallback {
        if path.exists() {
            return Ok(path);
        }
    }
    Err(format!(
        "Backend sidecar not found at {}.",
        candidate.display()
    ))
}

fn pick_backend_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|err| format!("Failed to bind to an ephemeral port: {err}"))?;
    let port = listener
        .local_addr()
        .map_err(|err| format!("Failed to read ephemeral port: {err}"))?
        .port();
    drop(listener);
    Ok(port)
}

fn spawn_backend<R: tauri::Runtime>(
    app: &tauri::AppHandle<R>,
    port: u16,
) -> Result<Child, String> {
    if std::env::var("TAURI_AGENT_EXTERNAL_BACKEND")
        .map(|value| value == "1" || value.eq_ignore_ascii_case("true"))
        .unwrap_or(false)
    {
        eprintln!("[Backend] External backend enabled; skipping sidecar spawn.");
        return Err("External backend enabled; skipping sidecar spawn.".to_string());
    }
    eprintln!("[Backend] Spawning sidecar backend.");
    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|_| "Failed to resolve app data directory.".to_string())?;
    std::fs::create_dir_all(&app_data_dir)
        .map_err(|err| format!("Failed to create app data directory: {err}"))?;

    let db_path = std::env::var("TAURI_AGENT_DB_PATH")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            if tauri::is_dev() {
                let dev_candidate = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("..")
                    .join("python-backend")
                    .join("chat_app.db");
                if dev_candidate.exists() {
                    return dev_candidate;
                }
            }
            app_data_dir.join("chat_app.db")
        });
    let app_config_path = app_data_dir.join("app_config.json");
    let tools_config_path = app_data_dir.join("tools_config.json");
    let backend_path = resolve_backend_path(app)?;

    let mut command = Command::new(backend_path);
    command
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string());
    command.env("TAURI_AGENT_DATA_DIR", &app_data_dir);
    command.env("TAURI_AGENT_DB_PATH", &db_path);
    command.env("APP_CONFIG_PATH", &app_config_path);
    command.env("TOOLS_CONFIG_PATH", &tools_config_path);
    if tauri::is_dev() {
        command.env("TAURI_AGENT_DEV", "1");
    }
    command.current_dir(&app_data_dir);
    if !tauri::is_dev() {
        command.stdout(Stdio::null()).stderr(Stdio::null());
    }

    command
        .spawn()
        .map_err(|err| format!("Failed to spawn backend sidecar: {err}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let context = tauri::generate_context!();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![greet, get_backend_base_url])
        .setup(|app| {
            log_sandbox_status();
            let mut backend_port = 8000;
            let external_backend = std::env::var("TAURI_AGENT_EXTERNAL_BACKEND")
                .map(|value| value == "1" || value.eq_ignore_ascii_case("true"))
                .unwrap_or(false);
            if !external_backend {
                if let Ok(selected) = pick_backend_port() {
                    backend_port = selected;
                }
            }
            match spawn_backend(&app.handle(), backend_port) {
                Ok(child) => {
                    app.manage(BackendChild(Mutex::new(Some(child))));
                }
                Err(err) => {
                    eprintln!("{err}");
                    if !tauri::is_dev()
                        && !err.contains("External backend enabled")
                    {
                        return Err(err.into());
                    }
                }
            }
            app.manage(BackendState { port: backend_port });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    window.app_handle().exit(0);
                }
            }
        })
        .build(context)
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Some(state) = app_handle.try_state::<BackendChild>() {
                if let Ok(mut guard) = state.0.lock() {
                    if let Some(mut child) = guard.take() {
                        let _ = child.kill();
                    }
                }
            }
        }
    });
}
