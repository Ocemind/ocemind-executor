# -*- coding: utf-8 -*-
"""界面静态资源的路径不变量。

这套界面有两个加载入口：FastAPI 自己挂在 /static，Tauri 直接把
`ocemind/web` 当 frontendDist 按相对路径取。两边都要求 index.html 里的
`static/...` 真的指向 `web/static/...`。资源一挪位置，网页版和桌面版会
同时变成一页没有样式的裸 HTML，且不会报错——所以钉住这条。
"""
import re
from pathlib import Path

from ocemind import paths

WEB = Path(paths.project_root()) / "ocemind" / "web"


def test_index_html_references_existing_assets():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:href|src)="(static/[^"]+)"', html)
    assert refs, "index.html 里应该引用了 static/ 下的资源"
    for ref in refs:
        assert (WEB / ref).is_file(), f"index.html 引用了不存在的资源：{ref}"


def test_window_title_is_plain_name():
    """窗口标题只写「内容执行器」，不带 Ocemind 前缀。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    title = re.search(r"<title>(.*?)</title>", html, re.S)
    assert title and title.group(1).strip() == "内容执行器"


def test_favicon_is_the_app_logo():
    """窗口图标必须是软件自己的标。指向别处或干脆不给，
    Chrome 就摆一个默认的"网页"图标，标题栏和任务栏那格是空的。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    icon = re.search(r'<link[^>]+rel="icon"[^>]*>', html)
    assert icon, "index.html 应该显式声明 favicon"
    href = re.search(r'href="([^"]+)"', icon.group(0)).group(1)
    assert href == "static/logo.png", href
    assert (WEB / href).is_file()


def test_title_carries_app_version():
    """版本号在运行时拼进标题：界面上看到的和接口报的是一个数。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "document.title = `内容执行器 ${version}`" in js


def test_logo_is_big_enough_for_a_sharp_render():
    """侧栏按 60px 画，高分屏要 120px 的源。拿一张 128 的小图来顶，
    窗口一放大就发虚——这条钉住 logo.png 的原图尺寸。"""
    png = (WEB / "static" / "logo.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "logo.png 应该是 PNG"
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    assert min(width, height) >= 128, f"logo.png 只有 {width}×{height}，放大就糊"


def test_brand_is_a_vertical_logo_lockup():
    """侧栏顶部是 LOGO 区：图标、Ocemind、内容执行器 从上到下排。
    顺序写反了或者名字被并回一行，这块就不像标了。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    brand = re.search(r'<div class="brand">(.*?)\n    </div>', html, re.S)
    assert brand, "index.html 里应该有一块 .brand"
    block = brand.group(1)
    order = [block.find(name) for name in
             ('class="brand-logo"', 'class="brand-name"', 'class="brand-sub"')]
    assert all(i >= 0 for i in order), block
    assert order == sorted(order), "LOGO 区应该依次是 图标 → Ocemind → 内容执行器"
    assert ">Ocemind<" in block and ">内容执行器<" in block
    assert (WEB / "static" / "logo.png").is_file()


def _sidebar(html):
    return html.split('<aside class="sidebar">')[1].split("</aside>")[0]


def _set_page(html, name):
    """截出设置页里某一页的 HTML（从自己的 data-page 到下一页/视图结束）。"""
    rest = html.split(f'data-page="{name}"', 1)[1]
    end = min(
        (rest.find(marker) for marker in ('data-page="', 'id="view-about"')
         if rest.find(marker) > 0),
        default=len(rest),
    )
    return rest[:end]


def test_footer_is_three_actions_plus_an_update_bar():
    """底栏就是三颗并排（设置 / 指南 / 深浅色）+ 通长的检测更新。
    顺序和数量写钉住，别又长回一堆版本号。"""
    foot = re.search(r'<div class="sidebar-foot">(.*?)\n    </div>',
                     (WEB / "index.html").read_text(encoding="utf-8"), re.S).group(1)
    assert len(re.findall(r'class="foot-act"', foot)) == 3
    for label in ("设置", "指南", "亮色"):
        assert re.search(rf"<span[^>]*>{label}</span>", foot), label
    assert 'id="btn-update"' in foot and "hidden" not in foot, "检测更新应该一直在"
    assert "检测更新" in foot
    # 图标在上、文字在下：每颗都是 svg + span
    for act in re.findall(r'<button class="foot-act".*?</button>', foot, re.S):
        assert "<svg" in act and "<span" in act


def test_tool_versions_live_in_settings_not_the_sidebar():
    """yt-dlp / ffmpeg / Chrome 那三行从侧栏底部挪进了设置页。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    sidebar = _sidebar(html)
    for tool in ("tool-ytdlp", "tool-ffmpeg", "tool-chrome"):
        assert tool not in sidebar, f"{tool} 不该再占侧栏地方"
    settings = html.split('id="view-settings"')[1].split('id="view-about"')[0]
    for tool in ("tool-ytdlp", "tool-ffmpeg", "tool-chrome"):
        assert tool in settings, f"{tool} 应该在本机工具卡里"


def test_every_nav_item_has_an_icon():
    """侧栏每一项都得有图标，二级项也不例外——一列只有文字会看着散。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    items = re.findall(r'<button class="nav-item[^"]*"[^>]*>(.*?)</button>',
                       _sidebar(html), re.S)
    assert items, "侧栏应该有一堆 nav-item"
    for item in items:
        assert "<svg" in item, item.strip()[:60]


def test_settings_is_five_separate_pages():
    """存储 / 引擎 / API / 接入 / 通用 各是一页，点哪个显示哪个，不是一页往下滚。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    settings = html.split('id="view-settings"')[1].split('id="view-about"')[0]
    pages = re.findall(r'data-page="([a-z]+)"', settings)
    assert pages == ["storage", "engine", "api", "connect", "general"], pages
    assert settings.count('class="set-page on"') == 1, "默认只开一页"
    # 左边导航只放前四页；通用设置归底栏那颗「设置」，导航里不再重复一项
    for sec in pages[:-1]:
        assert f'data-sec="{sec}"' in _sidebar(html), sec
    assert 'data-sec="general"' not in _sidebar(html)


def test_theme_defaults_to_light_and_remembers():
    """默认亮色；点过之后记住。深色样式还没做，但选择要先存住。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert 'localStorage.getItem("ocemind.theme") === "dark" ? "dark" : "light"' in js
    assert 'localStorage.setItem("ocemind.theme", state.theme)' in js
    assert 'dark ? "深色" : "亮色"' in js


def test_static_assets_live_under_static():
    """资源在 web/static 下，Tauri 的 frontendDist 和 FastAPI 的挂载点才对得上。"""
    for name in ("style.css", "app.js", "bridge.js"):
        assert (WEB / "static" / name).is_file(), name


def test_footer_settings_button_is_the_general_page():
    """底栏那颗「设置」就是设置里的通用设置，图标用滑杆、不另画齿轮。
    导航里不再重复放一项「通用设置」——一页两处入口，看着像两件事。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    foot = re.search(r'<div class="sidebar-foot">(.*?)\n    </div>', html, re.S).group(1)
    btn = re.search(r'<button[^>]*id="btn-settings".*?</button>', foot, re.S).group(0)
    assert ">设置<" in btn and ">通用设置<" not in btn
    # 滑杆：两条横线 + 两个旋钮；齿轮是 24 视角的齿圈，这里不该出现
    assert "M3 6.6h14M3 13.4h14" in btn
    assert ">通用设置<" not in _sidebar(html), "导航里不该再有「通用设置」这一项"
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    handler = re.search(r'\$\("#btn-settings"\)\.addEventListener\("click", \(\) => \{(.*?)\}\);',
                        js, re.S).group(1)
    assert 'state.section = "general"' in handler, "点底栏设置要落到通用设置那一页"
    assert 'setView("settings")' in handler


def test_feishu_config_lives_in_storage_page():
    """飞书只是产出的去处，跟默认目录、线程数是一件事，住在存储配置里。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    storage = _set_page(html, "storage")
    assert "<h2>飞书</h2>" in storage
    for field in ("fs-enabled", "fs-cli", "fs-base", "fs-table", "fs-folder"):
        assert field in storage, field
    general = _set_page(html, "general")
    assert "fs-base" not in general and "<h2>飞书</h2>" not in general
    assert "tool-ytdlp" in general, "本机工具还留在通用设置"


def test_window_opens_at_1280x820_and_clamps_at_1024x680():
    """打开固定 1280×820；最小 1024×680——CSS 和桌面壳写的是同一对数，
    对不上就会出现「窗口还能缩，布局已经散了」或者反过来一片空白。"""
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    app_rule = re.search(r"#app \{(.*?)\}", css, re.S).group(1)
    assert "min-width: 1024px" in app_rule
    assert "min-height: 680px" in app_rule
    # 正文到 1120 就不再拉宽，多出来的均分两边；页头留白读同一个变量，
    # 否则窗口一放大，标题就和正文左边缘错开
    content = re.search(r"\.content \{(.*?)\}", css, re.S).group(1)
    assert "max-width: var(--content-max)" in content and "margin-inline: auto" in content
    assert "--content-max: 1120px" in css
    assert "calc((100% - var(--content-max)) / 2 + 36px)" in css

    rust = (Path(paths.project_root()) / "desktop" / "src-tauri" / "src" / "lib.rs"
            ).read_text(encoding="utf-8")
    assert ".inner_size(1280.0, 820.0)" in rust
    assert ".min_inner_size(1024.0, 680.0)" in rust


def test_tool_page_has_one_way_back_to_all_tools():
    """「全部工具」只留页头右上那一颗。左边那列是同类工具，不是第二份出口。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert html.count("全部工具") == 1
    side = re.search(r'<aside class="tool-side"', html)
    assert side, "单个工具页左边那列还在"
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    body = re.search(r"function renderToolSide\(currentId\) \{(.*?)\n\}", js, re.S).group(1)
    assert "全部工具" not in body


def test_tool_side_and_empty_list_are_cards():
    """左边那列和「一条任务都没有」的空地都按统一配色做成白卡：
    白底、一像素描边、跟其他卡片同一个圆角和阴影。半张纸、半张灰底看着像没加载完。"""
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    side = re.search(r"\n\.tool-side \{(.*?)\}", css, re.S).group(1)
    for rule in ("background: var(--surface)", "border: 1px solid var(--line)",
                 "border-radius: var(--radius-card)", "box-shadow: var(--shadow-card)"):
        assert rule in side, rule
    empty = re.search(
        r"\n#view-tasks \.list-card > \.empty,\s*\n#view-secure \.list-card > \.empty \{(.*?)\}",
        css, re.S).group(1)
    for rule in ("background: var(--surface)", "border: 1px solid var(--line)",
                 "border-radius: var(--radius-card)", "box-shadow: var(--shadow-card)"):
        assert rule in empty, rule


def test_course_tool_is_blast_download_with_its_own_page():
    """课程回放叫「爆破下载」；它有自己的一页，左边跟别的工具页一样列着同类工具。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "课程回放" not in html and "课程回放" not in js
    secure = re.search(r'<section id="view-secure".*?\n    </section>', html, re.S).group(0)
    assert "<h1>爆破下载</h1>" in secure
    assert '<aside class="tool-side" id="tool-side-secure">' in secure
    assert 'class="content tool-layout secure-page"' in secure
    # 布局：登录态在左、粘链接在右同一行；下载选项单独一行；下面是任务区
    top = re.search(r'<div class="secure-top">(.*?)</div>\s*<!-- 下载选项', secure, re.S).group(1)
    assert top.index("登录态") < top.index("粘一个回放链接")
    assert 'class="card secure-options"' in secure
    assert '<div class="seg" id="secure-filter">' in secure
    assert 'id="secure-task-list"' in secure and 'id="secure-empty"' in secure
    assert 'if (tool.cap === "__course") { setView("secure"); return; }' in js
    # 左边那列两页共用一份渲染：两列长得一模一样，当前项高亮
    body = re.search(r"function renderToolSide\(currentId\) \{(.*?)\n\}", js, re.S).group(1)
    assert '$$(".tool-side")' in body, "两页的侧栏要一起填"
    # 登录提示那句话撤了，执行页也不再挂一个跳转按钮
    assert "需要登录" not in re.search(r'<section id="view-tasks".*?</section>', html, re.S).group(0)
    assert 'id="btn-secure"' not in html


def test_every_tool_has_a_line_icon():
    """18 个工具各有自己的 20×20 线框图标：只描边不填色，跟侧栏同一种画法。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    block = re.search(r"const TOOLS = \[(.*?)\n\];", js, re.S).group(1)
    entries = [(m.group(1), block[m.end():].split("\n  { id: ")[0])
               for m in re.finditer(r'\n  \{ id: "([^"]+)"', block)]
    assert len(entries) == 18, [e[0] for e in entries]
    for tool_id, body in entries:
        icon = re.search(r"icon: `(.*?)`", body, re.S)
        assert icon, f"{tool_id} 没有图标"
        assert re.search(r"<(path|rect|circle) ", icon.group(1)), f"{tool_id} 的图标是空的"
        assert not re.search(r"[一-鿿]", icon.group(1)), f"{tool_id} 的图标里还有字"
        assert "glyph:" not in body, f"{tool_id} 还留着字符图标"
    assert "viewBox=\"0 0 20 20\"" in js, "图标统一画在 20×20 的框里"


def test_action_bar_and_start_live_in_the_params_card():
    """动作条、「项目 / 序列 / 开始」和重复提示都收进「参数设置」那张卡：
    上面那块只负责收东西，不再跟着往下长一截。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    add = re.search(r'<section class="card add-card is-empty" id="add-card">(.*?)\n        </section>',
                    html, re.S).group(1)
    opts = re.search(r'<section class="card opts-card" id="opts-wrap">(.*?)\n        </section>',
                     html, re.S).group(1)
    for name in ('id="verb-bar"', 'id="add-foot"', 'id="btn-add"', 'id="dup-card"',
                 'id="project-input"', 'id="sequence-input"'):
        assert name not in add, f"{name} 不该还挂在输入卡里"
        assert name in opts, f"{name} 应该收进参数设置"
    # 常驻：不再等输入才浮出来
    assert '<div class="verb-bar" id="verb-bar"></div>' in opts
    assert '<div class="add-foot" id="add-foot">' in opts
    # 保存目录只在「保存到」那一格显示，输入区不再重复一行「存到 …」
    assert "dest-hint" not in html


def test_download_tasks_reach_the_download_tool_page():
    """下载器建的任务没有 capability 字段，只按 capability 比会把它们全筛掉——
    粘完链接说「已加入队列」，下面一条都看不见。要按 kind 认。
    换工具时当场重画列表，不能等下一轮轮询才换掉上个工具的活儿。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    body = re.search(r"function matchesTool\(t\) \{(.*?)\n\}", js, re.S).group(1)
    assert 'if (tool.cap === "download.platform")' in body
    assert 'return !t.capability && t.kind !== "course";' in body
    assert 'if (tool.cap === "__course") return t.kind === "course";' in body
    open_tool = re.search(r"function openTool\(toolId\) \{(.*?)\n\}", js, re.S).group(1)
    assert "render();" in open_tool


def test_capability_list_lives_in_the_guide():
    """能力清单是给 Agent 看的说明，不是设置项：它跟「这是什么」「三条规矩」
    一起放在指南里；接入页只管地址、令牌和配置片段，不再往下拖一长串。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    about = html.split('id="view-about"')[1].split('<section id="view-secure"')[0]
    assert 'id="cap-list"' in about and 'id="cap-count"' in about
    assert "Agent 能调什么" in about
    settings = html.split('id="view-settings"')[1].split('id="view-about"')[0]
    assert 'id="cap-list"' not in settings
    assert "能力清单" not in settings
    # 接入页留一句话指路，免得人在设置里找清单
    assert "清单在「指南」那一页" in settings


def test_tools_home_is_hot_cards_plus_a_column_index():
    """首页不是一长串一样大的卡：常用的摆成两列大卡，剩下的按分类
    一列一列排成索引——先认人，再认路，一屏扫得完。"""
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    home = re.search(r"function renderToolsHome\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert 'class="tool-group tool-hot"' in home
    assert 'class="tool-group tool-index"' in home
    assert "TOOLS.filter((t) => t.hot)" in home
    assert 'class="tool-cols"' in home and 'class="tool-col"' in home
    assert home.index("tool-hot") < home.index("tool-index"), "常用工具在上面"
    # 索引行只带图标和名字，点进工具再看说明
    link = re.search(r"function toolLink\(tool\) \{(.*?)\n\}", js, re.S).group(1)
    assert "tool-name" not in link and "tool-desc" not in link
    assert "toolIcon(tool" in link

    # 常用的九个，三列三行
    assert js.count("hot: true") == 9
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    grid = re.search(r"\n\.tool-grid \{(.*?)\}", css, re.S).group(1)
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in grid
    cols = re.search(r"\n\.tool-cols \{(.*?)\}", css, re.S).group(1)
    assert "grid-template-columns: repeat(6, minmax(0, 1fr))" in cols
    # 索引里的每一行也是一张白底小卡
    link = re.search(r"\n\.tool-link \{(.*?)\}", css, re.S).group(1)
    for rule in ("background: var(--surface)", "border: 1px solid var(--line)",
                 "border-radius: var(--radius-control)"):
        assert rule in link, rule
    # 窄窗口：索引六列变三列两行，六个分类都不许自己掉队成半排；
    # 这条断点必须写在基础规则后面，写在前面就被盖掉了
    narrow_cols = ".tool-cols { grid-template-columns: repeat(3, minmax(0, 1fr)); }"
    assert narrow_cols in css
    assert css.rindex(narrow_cols) > css.index("\n.tool-cols {"), "窄窗口规则写在了基础规则前面"


def test_tool_page_fills_the_window_height():
    """工具页底下不留一片灰：左右两列都拉到同一条底边，任务少的时候
    任务区吃满剩下的高度，空态内容在卡里居中。"""
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    layout = re.search(r"\n\.tool-layout \{(.*?)\}", css, re.S).group(1)
    assert "align-items: stretch" in layout
    assert "flex: 1 1 auto" in layout
    assert "position: sticky" not in re.search(r"\n\.tool-side \{(.*?)\}", css, re.S).group(1), \
        "左边那列要跟着拉到底，不是钉在顶上"
    listcard = re.search(
        r"\n#view-tasks \.list-card,\s*\n#view-secure \.list-card \{(.*?)\}", css, re.S).group(1)
    assert "flex: 1 1 auto" in listcard and "display: flex" in listcard
    empty = re.search(
        r"\n#view-tasks \.list-card > \.empty,\s*\n#view-secure \.list-card > \.empty \{(.*?)\}",
        css, re.S).group(1)
    assert "flex: 1 1 auto" in empty and "justify-content: center" in empty
    # 窄窗口改成上下排时，多出来的高度只给下面那行，别把工具卡拉高
    narrow = css.split("@media (max-width: 1080px)")[1]
    assert "grid-template-rows: auto minmax(0, 1fr)" in narrow


def test_no_breakpoints_below_the_clamp():
    """窗口最窄 1024，低于这个数的断点要么永远不触发（死代码），
    要么一触发就把侧栏压成图标条——等于把版面缩到看不全。"""
    css = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    widths = [int(w) for w in re.findall(r"@media \(max-width: (\d+)px\)", css)]
    assert widths, "style.css 里应该还有窄窗口断点"
    assert all(w >= 1024 for w in widths), f"这些断点落在窗口钳制之下：{widths}"


def test_images_are_requested_without_referer():
    """B 站封面按 Referer 防盗链：带 127.0.0.1 过去就是 403。

    整页声明 no-referrer，探测卡和任务行的缩略图才显示得出来。
    """
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<meta\s+name="referrer"\s+content="no-referrer"', html), \
        "index.html 需要 <meta name=\"referrer\" content=\"no-referrer\">"
