// LocallyAI Manager — Tauri shell.
//
// Architecture: this app is a thin native window over the firm's
// office LocallyAI server. On first launch it shows a config screen
// that asks for the office server URL, persists it to the OS-standard
// app config dir, then loads <server>/worker on every subsequent
// launch.
//
// Why a thin shell rather than bundling the React UI: the firm's
// office Mac Studio is the source of truth for both UI and data. A
// staff laptop only needs the client shell; the UI HTML+JS is served
// fresh from the office server every time, so a UI update on the
// office server reaches every laptop without an installer push.
//
// Two Tauri commands are exposed to the front-end JavaScript:
//   load_server_url()  → reads the persisted server URL ("" if none)
//   save_server_url(s) → writes it to disk
// Both are gated to the OS-managed app-config directory so the file
// can't be smuggled outside that scope.

use std::fs;
use std::path::PathBuf;
use serde::{Serialize, Deserialize};
use tauri::Manager;

#[derive(Serialize, Deserialize, Default)]
struct AppConfig {
    server_url: String,
}

fn config_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let dir = app.path()
        .app_config_dir()
        .map_err(|e| format!("Could not resolve config dir: {}", e))?;
    fs::create_dir_all(&dir).map_err(|e| format!("Could not create config dir: {}", e))?;
    Ok(dir.join("config.json"))
}

#[tauri::command]
fn load_server_url(app: tauri::AppHandle) -> Result<String, String> {
    let p = config_path(&app)?;
    if !p.exists() {
        return Ok(String::new());
    }
    let raw = fs::read_to_string(&p).map_err(|e| format!("Could not read config: {}", e))?;
    let cfg: AppConfig = serde_json::from_str(&raw).unwrap_or_default();
    Ok(cfg.server_url)
}

#[tauri::command]
fn save_server_url(app: tauri::AppHandle, url: String) -> Result<(), String> {
    let p = config_path(&app)?;
    let cfg = AppConfig { server_url: url };
    let raw = serde_json::to_string_pretty(&cfg)
        .map_err(|e| format!("Could not serialise config: {}", e))?;
    fs::write(&p, raw).map_err(|e| format!("Could not write config: {}", e))?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![load_server_url, save_server_url])
        .run(tauri::generate_context!())
        .expect("error while running LocallyAI Manager");
}
