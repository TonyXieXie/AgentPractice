#[tauri::command]
fn get_backend_base_url() -> String {
    if let Ok(value) = std::env::var("VITE_API_BASE_URL") {
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            return trimmed.trim_end_matches('/').to_string();
        }
    }
    "http://127.0.0.1:8000".to_string()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![get_backend_base_url])
        .run(tauri::generate_context!())
        .expect("error while running tauri-agent-next");
}
