use std::{
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

fn spawn_backend<R: tauri::Runtime>(app: &tauri::AppHandle<R>) -> Result<Child, String> {
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
    command.arg("--host").arg("127.0.0.1").arg("--port").arg("8000");
    command.env("TAURI_AGENT_DATA_DIR", &app_data_dir);
    command.env("TAURI_AGENT_DB_PATH", &db_path);
    command.env("APP_CONFIG_PATH", &app_config_path);
    command.env("TOOLS_CONFIG_PATH", &tools_config_path);
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
        .invoke_handler(tauri::generate_handler![greet])
        .setup(|app| {
            log_sandbox_status();
            match spawn_backend(&app.handle()) {
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
