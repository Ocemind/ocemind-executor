// 正式构建时不弹控制台窗口；调试构建保留，方便看 panic 和日志。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    ocemind_desktop_lib::run()
}
