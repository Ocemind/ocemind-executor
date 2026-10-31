//! Tauri 壳：一个原生窗口 + 一个 Python sidecar。
//!
//! 界面仍然由 Python 的 FastAPI 提供（`xiaoe_dl/web`），壳层只负责：
//! 选一个空闲端口、把 Python 拉起来、等 `/api/health` 通了再开窗口、
//! 退出时先请求服务自己收尾、再把整个进程树收掉。
//!
//! 这样 Web / 源码运行 / 桌面安装包用的是同一套界面和同一套接口，
//! 不会出现「装了桌面版反而少功能」。

use std::io::{BufRead, BufReader, Read};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_updater::UpdaterExt;

/// 端口被别的进程抢走时最多重试几次。实际几乎不会发生，但比让用户
/// 看到一个空窗口强。
const START_ATTEMPTS: usize = 3;
const READY_TIMEOUT: Duration = Duration::from_secs(60);

struct Sidecar {
    child: Option<Child>,
    port: u16,
    token: String,
    exiting: bool,
    #[cfg(windows)]
    job: Option<usize>,
}

struct SidecarState(Mutex<Sidecar>);

// ---------------------------------------------------------------- 端口 / 令牌

fn free_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    listener.local_addr().map(|addr| addr.port())
}

fn random_token() -> String {
    use std::hash::{BuildHasher, Hasher};

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let mut out = String::new();
    for salt in 0..2u128 {
        let mut hasher = std::collections::hash_map::RandomState::new().build_hasher();
        hasher.write_u128(now);
        hasher.write_u128(salt);
        out.push_str(&format!("{:016x}", hasher.finish()));
    }
    out
}

// ---------------------------------------------------------------- 启动 sidecar

struct SidecarCmd {
    program: PathBuf,
    args: Vec<String>,
    cwd: PathBuf,
}

fn sidecar_command(app: &tauri::AppHandle, port: u16) -> Result<SidecarCmd, String> {
    // debug 构建默认跑源码，改 Python 不用重打包；这台机器 SAC 只放行
    // debug 构建，所以要验证「安装包里的冻结 sidecar」得用
    // OCEMIND_SIDECAR_FROZEN=1 把 debug 构建也按发布路径走一遍。
    let dev = std::env::var("OCEMIND_SIDECAR_DEV").is_ok()
        || (cfg!(debug_assertions) && std::env::var("OCEMIND_SIDECAR_FROZEN").is_err());
    let port_arg = port.to_string();

    if dev {
        // 源码开发：直接用当前解释器跑 run.py，改了 Python 不用重新打包。
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
            .canonicalize()
            .map_err(|e| format!("定位项目根目录失败: {e}"))?;
        let python = std::env::var("OCEMIND_PYTHON").unwrap_or_else(|_| "python".into());
        return Ok(SidecarCmd {
            program: PathBuf::from(python),
            args: vec![
                "run.py".into(),
                "--serve".into(),
                "--port".into(),
                port_arg,
            ],
            cwd: root,
        });
    }

    // 安装包：sidecar 和主程序放在一起（Windows MSI / Linux deb 都是这样）。
    let exe_dir = std::env::current_exe()
        .map_err(|e| format!("定位自身路径失败: {e}"))?
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| "自身路径没有上级目录".to_string())?;
    let name = if cfg!(windows) {
        "ocemind-sidecar.exe"
    } else {
        "ocemind-sidecar"
    };
    let candidates = [
        exe_dir.join(name),
        exe_dir.join("binaries").join(name),
        app.path()
            .resource_dir()
            .unwrap_or_else(|_| exe_dir.clone())
            .join("binaries")
            .join(name),
    ];
    let program = candidates
        .into_iter()
        .find(|p| p.is_file())
        .ok_or_else(|| format!("找不到 sidecar：{}", exe_dir.join(name).display()))?;

    Ok(SidecarCmd {
        program,
        args: vec!["--serve".into(), "--port".into(), port_arg],
        cwd: exe_dir,
    })
}

/// 便携版靠 exe 旁边的 `portable.ini` 认亲：数据和浏览器 profile 都放在
/// 解压目录的 `data/` 里，不写用户目录。安装版没有这个文件，行为不变。
fn portable_data_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let dir = exe.parent()?;
    if dir.join("portable.ini").is_file() {
        Some(dir.join("data"))
    } else {
        None
    }
}

fn pump<R: Read + Send + 'static>(reader: R, label: &'static str) {
    std::thread::spawn(move || {
        for line in BufReader::new(reader).lines().map_while(Result::ok) {
            log::info!("[sidecar:{}] {}", label, line);
        }
    });
}

fn spawn(app: &tauri::AppHandle, port: u16, token: &str) -> Result<Child, String> {
    let spec = sidecar_command(app, port)?;
    let log_dir = app
        .path()
        .app_log_dir()
        .or_else(|_| app.path().app_data_dir())
        .ok();
    if let Some(dir) = log_dir.as_ref() {
        let _ = std::fs::create_dir_all(dir);
    }

    let mut cmd = Command::new(&spec.program);
    cmd.args(&spec.args)
        .current_dir(&spec.cwd)
        .env("OCEMIND_PORT", port.to_string())
        .env("OCEMIND_SHUTDOWN_TOKEN", token)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    if let Some(root) = portable_data_dir() {
        let local = root.join("local");
        let _ = std::fs::create_dir_all(&local);
        cmd.env("XIAOE_DL_HOME", &root)
            .env("XIAOE_DL_LOCAL_HOME", &local)
            .env("OCEMIND_PORTABLE", "1");
    }

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("启动 sidecar 失败：{} ({e})", spec.program.display()))?;
    if let Some(out) = child.stdout.take() {
        pump(out, "out");
    }
    if let Some(err) = child.stderr.take() {
        pump(err, "err");
    }
    Ok(child)
}

fn wait_ready(child: &mut Child, port: u16) -> bool {
    let url = format!("http://127.0.0.1:{port}/api/health");
    let started = Instant::now();
    while started.elapsed() < READY_TIMEOUT {
        // 先看进程还在不在：Python 崩了就别再傻等满 60 秒
        match child.try_wait() {
            Ok(Some(status)) => {
                log::error!("sidecar 提前退出：{status}");
                return false;
            }
            Ok(None) => {}
            Err(e) => log::warn!("查询 sidecar 状态失败：{e}"),
        }
        if let Ok(resp) = ureq::get(&url).timeout(Duration::from_secs(2)).call() {
            if resp.status() == 200 {
                return true;
            }
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    log::error!("等待 sidecar 就绪超时（{READY_TIMEOUT:?}）");
    false
}

#[cfg(windows)]
fn attach_job(pid: u32) -> Option<usize> {
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::{
        OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
    };

    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return None;
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let set_ok = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const std::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid);
        if process.is_null() {
            CloseHandle(job);
            return None;
        }
        let assigned = if set_ok != 0 {
            AssignProcessToJobObject(job, process)
        } else {
            0
        };
        CloseHandle(process);
        if assigned == 0 {
            CloseHandle(job);
            return None;
        }
        Some(job as usize)
    }
}

// ---------------------------------------------------------------- 收尾

fn stop(state: &SidecarState) {
    let mut guard = match state.0.lock() {
        Ok(g) => g,
        Err(poisoned) => poisoned.into_inner(),
    };
    if guard.exiting {
        return;
    }
    guard.exiting = true;
    let port = guard.port;
    let token = guard.token.clone();

    // 先请服务自己退出：uvicorn 收尾会关账号浏览器、停任务队列，
    // 比直接 TerminateProcess 干净。
    let _ = ureq::post(&format!("http://127.0.0.1:{port}/api/shutdown"))
        .set("X-Ocemind-Shutdown", &token)
        .timeout(Duration::from_secs(3))
        .call();

    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        let done = match guard.child.as_mut() {
            None => true,
            Some(child) => match child.try_wait() {
                Ok(Some(_)) => true,
                Ok(None) => false,
                Err(_) => true,
            },
        };
        if done || Instant::now() >= deadline {
            break;
        }
        drop(guard);
        std::thread::sleep(Duration::from_millis(100));
        guard = match state.0.lock() {
            Ok(g) => g,
            Err(poisoned) => poisoned.into_inner(),
        };
    }

    if let Some(mut child) = guard.child.take() {
        if matches!(child.try_wait(), Ok(None)) {
            #[cfg(unix)]
            unsafe {
                // 请求退出没成功就发 SIGTERM，再不行才 kill
                libc::kill(child.id() as i32, libc::SIGTERM);
            }
            let _ = child.kill();
        }
        let _ = child.wait();
    }

    #[cfg(windows)]
    if let Some(job) = guard.job.take() {
        use windows_sys::Win32::Foundation::CloseHandle;
        unsafe {
            // KILL_ON_JOB_CLOSE：关掉 job 句柄，ffmpeg / Chrome 这些
            // 孙进程也一起收掉，不会留在后台。
            CloseHandle(job as *mut std::ffi::c_void);
        }
    }
}

// ---------------------------------------------------------------- 文件对话框

fn as_path(value: tauri_plugin_dialog::FilePath) -> Option<String> {
    value
        .into_path()
        .ok()
        .map(|p| p.to_string_lossy().into_owned())
}

fn start_dir(current: &str) -> Option<PathBuf> {
    if current.is_empty() {
        return None;
    }
    let path = Path::new(current);
    if path.is_dir() {
        Some(path.to_path_buf())
    } else {
        path.parent().map(Path::to_path_buf)
    }
}

#[tauri::command]
async fn pick_file(app: tauri::AppHandle, current: String) -> Option<String> {
    let mut dialog = app.dialog().file().add_filter("所有文件", &["*"]);
    if let Some(dir) = start_dir(&current) {
        dialog = dialog.set_directory(dir);
    }
    dialog.blocking_pick_file().and_then(as_path)
}

#[tauri::command]
async fn pick_cookie_file(app: tauri::AppHandle, current: String) -> Option<String> {
    let mut dialog = app
        .dialog()
        .file()
        .add_filter("Cookies", &["txt"])
        .add_filter("所有文件", &["*"]);
    if let Some(dir) = start_dir(&current) {
        dialog = dialog.set_directory(dir);
    }
    dialog.blocking_pick_file().and_then(as_path)
}

#[tauri::command]
async fn pick_folder(app: tauri::AppHandle, current: String) -> Option<String> {
    let mut dialog = app.dialog().file();
    if let Some(dir) = start_dir(&current) {
        dialog = dialog.set_directory(dir);
    }
    dialog.blocking_pick_folder().and_then(as_path)
}

#[tauri::command]
fn reveal_path(path: String) -> bool {
    let path = PathBuf::from(path);
    if !path.exists() {
        return false;
    }
    opener::reveal(&path).is_ok()
}

#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    let lowered = url.to_ascii_lowercase();
    if !(lowered.starts_with("http://") || lowered.starts_with("https://")) {
        return Err("只允许打开 http(s) 链接".into());
    }
    opener::open(url).map_err(|e| e.to_string())
}

// ---------------------------------------------------------------- 自动更新

/// 仓库是私有的，release 匿名读不到，所以「查更新」和「下载安装包」
/// 都要带只读令牌。令牌只在构建时通过 `OCEMIND_UPDATE_TOKEN` 注入：
/// 本地从 `.updater-token` 读（不入库），CI 从 Secrets 读。
/// 没有令牌就退化成匿名请求——仓库转公开后删掉即可。
fn updater_plugin(
) -> tauri::plugin::TauriPlugin<tauri::Wry, tauri_plugin_updater::Config> {
    let mut builder = tauri_plugin_updater::Builder::new();
    if let Some(token) = option_env!("OCEMIND_UPDATE_TOKEN").filter(|t| !t.is_empty()) {
        let mut headers = tauri::http::HeaderMap::new();
        if let Ok(value) = tauri::http::HeaderValue::from_str(&format!("Bearer {token}")) {
            headers.insert(tauri::http::header::AUTHORIZATION, value);
            builder = builder.headers(headers);
        }
    }
    builder.build()
}

/// 只查版本，不下载。返回 `None` 表示已是最新。
///
/// 更新源和公钥在 tauri.conf.json 的 `plugins.updater` 里；安装包由
/// GitHub Actions 用私钥签名，本地明文私钥不进仓库。
#[tauri::command]
async fn check_update(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let updater = app.updater().map_err(|e| e.to_string())?;
    let update = updater.check().await.map_err(|e| e.to_string())?;
    Ok(update.map(|u| u.version))
}

/// 下载并安装。Windows 上 msiexec 会接管并结束当前进程（安装器负责
/// 重新拉起），所以正常路径不会走到后面的 restart；留着是给还返回的
/// 平台兜底。失败时把错误原样交给界面显示。
#[tauri::command]
async fn install_update(app: tauri::AppHandle) -> Result<(), String> {
    let updater = app.updater().map_err(|e| e.to_string())?;
    let update = updater
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "没有可用更新".to_string())?;
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|e| e.to_string())?;
    app.restart();
}

// ---------------------------------------------------------------- 入口

pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(
            tauri_plugin_log::Builder::new()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(updater_plugin())
        .invoke_handler(tauri::generate_handler![
            pick_file,
            pick_folder,
            pick_cookie_file,
            reveal_path,
            open_url,
            check_update,
            install_update
        ])
        .setup(|app| {
            let mut last_error = String::new();

            for attempt in 1..=START_ATTEMPTS {
                let port = free_port()?;
                let token = random_token();
                let mut child = match spawn(app.handle(), port, &token) {
                    Ok(child) => child,
                    Err(e) => {
                        last_error = e;
                        continue;
                    }
                };
                #[cfg(windows)]
                let job = attach_job(child.id());

                if wait_ready(&mut child, port) {
                    let state = SidecarState(Mutex::new(Sidecar {
                        child: Some(child),
                        port,
                        token,
                        exiting: false,
                        #[cfg(windows)]
                        job,
                    }));
                    app.manage(state);

                    // 便携版没有安装流程，应用内更新会把 MSI 装到用户目录，
                    // 和「所有数据都在解压目录」的约定冲突；Linux 上只有
                    // AppImage 能被 updater 就地替换（源码跑、deb 装都不行），
                    // 所以用 APPIMAGE 环境变量判断。其余情况界面藏掉入口。
                    let portable = portable_data_dir().is_some();
                    let updater = !portable
                        && (cfg!(windows)
                            || (cfg!(target_os = "linux")
                                && std::env::var_os("APPIMAGE").is_some()));
                    let init = format!(
                        "window.__OCEMIND_PORT__ = {port}; window.__OCEMIND_VERSION__ = {:?}; \
                         window.__OCEMIND_PORTABLE__ = {}; window.__OCEMIND_UPDATER__ = {};",
                        app.package_info().version.to_string(),
                        portable,
                        updater,
                    );
                    let window = WebviewWindowBuilder::new(
                        app,
                        "main",
                        WebviewUrl::App("index.html".into()),
                    )
                    .title("Ocemind 内容执行器")
                    .inner_size(1280.0, 820.0)
                    .min_inner_size(980.0, 640.0)
                    .disable_drag_drop_handler()
                    .initialization_script(&init)
                    .build()?;
                    let _ = window.show();
                    log::info!("界面已启动，sidecar 端口 {port}");
                    return Ok(());
                }

                log::warn!("第 {attempt} 次启动 sidecar 失败，重试");
                let _ = child.kill();
                let _ = child.wait();
            }

            let msg = if last_error.is_empty() {
                "Python 服务没有在预期时间内就绪。请查看日志目录里的 ocemind 日志。"
                    .to_string()
            } else {
                last_error
            };
            app.dialog()
                .message(msg)
                .title("Ocemind 启动失败")
                .blocking_show();
            Err("sidecar failed".into())
        });

    let app = builder
        .build(tauri::generate_context!())
        .expect("初始化 Tauri 应用失败");

    app.run(|handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            if let Some(state) = handle.try_state::<SidecarState>() {
                stop(&state);
            }
        }
    });
}
