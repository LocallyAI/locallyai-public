// LocallyAI Workspace — minimal macOS desktop wrapper around the
// firm's Workspace. Replaces "open the URL in Safari" with a real
// app icon a non-technical user can double-click.
//
// The app is a thin WKWebView pointed at the firm's deployment URL.
// The URL is resolved in this order:
//   1. LOCALLYAI_WORKSPACE_URL environment variable (runtime override)
//   2. UserDefaults key "WorkspaceURL" (set once via first-run dialog)
//   3. Compile-time default baked via -DDEFAULT_URL=<url>
//
// Self-signed TLS is accepted at the navigation-delegate level — every
// LocallyAI firm runs a self-signed cert from install.sh. In a future
// hardened build this would compare to a pinned fingerprint instead
// of blindly trusting.

import Cocoa
import WebKit

// Compile-time default; swiftc -DDEFAULT_URL=https://… to bake at build.
#if DEFAULT_URL_EXISTS
let COMPILE_TIME_DEFAULT_URL = "DEFAULT_URL_LITERAL"
#else
let COMPILE_TIME_DEFAULT_URL = "http://localhost:5174"
#endif

final class WindowController: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    let window: NSWindow
    let webView: WKWebView

    init(url: URL) {
        let config = WKWebViewConfiguration()
        // Lawyers paste their admin key into the LoginGate; that key
        // lands in localStorage. Persistent data store = key survives
        // restarts (no "sign in every morning" UX).
        config.websiteDataStore = WKWebsiteDataStore.default()

        webView = WKWebView(frame: .zero, configuration: config)
        webView.allowsBackForwardNavigationGestures = true

        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let w: CGFloat = min(1280, screen.width - 80)
        let h: CGFloat = min(840, screen.height - 80)
        let x = screen.midX - w / 2
        let y = screen.midY - h / 2

        window = NSWindow(
            contentRect: NSRect(x: x, y: y, width: w, height: h),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "LocallyAI Workspace"
        window.contentView = webView
        window.tabbingMode = .disallowed
        window.minSize = NSSize(width: 800, height: 600)

        super.init()
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.load(URLRequest(url: url))
        window.makeKeyAndOrderFront(nil)
    }

    // Accept the deployment's self-signed cert. WKWebView refuses
    // self-signed by default; every LocallyAI install runs a
    // self-signed CN-matched cert generated at install time.
    func webView(_ webView: WKWebView,
                 didReceive challenge: URLAuthenticationChallenge,
                 completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        if challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
           let trust = challenge.protectionSpace.serverTrust {
            completionHandler(.useCredential, URLCredential(trust: trust))
            return
        }
        completionHandler(.performDefaultHandling, nil)
    }

    // Friendly error page if the deployment can't be reached.
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        renderErrorPage(error: error)
    }

    // ── window.open handling ────────────────────────────────────────────────
    // The Workspace's "Open document" button calls window.open(blobUrl) so
    // a cited PDF renders in a new window. WKWebView's default behaviour
    // is to silently drop window.open with no targetFrame — the call
    // returns null and nothing happens. Implementing
    // createWebViewWithConfiguration tells WebKit to ROUTE the new-window
    // request to a fresh WKWebView we own; we open it in a regular NSWindow
    // so the user sees the document.
    //
    // For blob: URLs (the typical case for "Open document"), the new
    // WKWebView shares the website data store with the parent — blob
    // identity is preserved. PDFs render inline; #page=N anchors are
    // honoured by WebKit's PDF viewer.
    private var _childWindows: [NSWindow] = []  // hold strong refs so child windows don't dealloc

    func webView(_ webView: WKWebView,
                 createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        // Same data store as the parent so blob: URLs resolve.
        configuration.websiteDataStore = webView.configuration.websiteDataStore
        let newWebView = WKWebView(frame: NSRect(x: 0, y: 0, width: 1100, height: 800),
                                   configuration: configuration)
        newWebView.navigationDelegate = self
        newWebView.uiDelegate = self
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let w: CGFloat = 1100
        let h: CGFloat = 800
        let win = NSWindow(
            contentRect: NSRect(x: screen.midX - w / 2, y: screen.midY - h / 2, width: w, height: h),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false
        )
        win.title = navigationAction.request.url?.lastPathComponent ?? "Document"
        win.contentView = newWebView
        win.makeKeyAndOrderFront(nil)
        // Hold a strong ref so the window survives once this function returns.
        _childWindows.append(win)
        // Clean up the ref when the user closes the window.
        NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification,
                                                object: win, queue: nil) { [weak self] _ in
            self?._childWindows.removeAll { $0 === win }
        }
        return newWebView
    }

    // ── Download handling ────────────────────────────────────────────────────
    // Without these delegate methods WKWebView silently drops file downloads —
    // a `<a download="…">` click does nothing and a Content-Disposition:
    // attachment response navigates to a blank page. Both are common in the
    // Workspace (compliance snapshot, audit CSV, installer zips).
    //
    // Strategy: when the navigation looks downloadable (download attribute,
    // attachment disposition, or non-renderable MIME type), tell WKWebView
    // to convert the navigation into a download. Files land in ~/Downloads
    // with their server-suggested name, then Finder opens to reveal them.

    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if navigationAction.shouldPerformDownload {
            decisionHandler(.download)
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let resp = navigationResponse.response
        let isAttachment: Bool = {
            if let http = resp as? HTTPURLResponse,
               let cd = http.value(forHTTPHeaderField: "Content-Disposition"),
               cd.lowercased().contains("attachment") {
                return true
            }
            return false
        }()
        if isAttachment || !navigationResponse.canShowMIMEType {
            decisionHandler(.download)
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView,
                 navigationAction: WKNavigationAction,
                 didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView,
                 navigationResponse: WKNavigationResponse,
                 didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload,
                  decideDestinationUsing response: URLResponse,
                  suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let downloads = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Downloads")
        let target = _uniqueFilename(in: downloads, basename: suggestedFilename)
        completionHandler(target)
    }

    func downloadDidFinish(_ download: WKDownload) {
        guard let url = _lastDownloadedURL else { return }
        // Reveal in Finder so the user can see what just landed.
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    func download(_ download: WKDownload, didFailWithError error: Error,
                  resumeData: Data?) {
        let alert = NSAlert()
        alert.messageText = "Download failed"
        alert.informativeText = error.localizedDescription
        alert.runModal()
    }

    private var _lastDownloadedURL: URL?

    private func _uniqueFilename(in dir: URL, basename: String) -> URL {
        let fm = FileManager.default
        var candidate = dir.appendingPathComponent(basename)
        let nameOnly = (basename as NSString).deletingPathExtension
        let ext = (basename as NSString).pathExtension
        var n = 1
        while fm.fileExists(atPath: candidate.path) {
            let suffix = ext.isEmpty ? " (\(n))" : " (\(n)).\(ext)"
            candidate = dir.appendingPathComponent("\(nameOnly)\(suffix)")
            n += 1
        }
        _lastDownloadedURL = candidate
        return candidate
    }

    private func renderErrorPage(error: Error) {
        let target = WorkspaceURL.resolve()
        let html = """
        <html><head><style>
        body { font: 14px -apple-system, BlinkMacSystemFont, sans-serif; max-width: 520px;
               margin: 80px auto; color: #18181b; padding: 0 24px; }
        h1 { font-size: 20px; }
        code { background: #f4f4f5; padding: 2px 6px; border-radius: 3px;
               font: 12px ui-monospace, "SF Mono", monospace; }
        .meta { color: #71717a; margin-top: 24px; }
        button { font: 13px -apple-system; padding: 6px 14px; border-radius: 6px;
                 border: 1px solid #d4d4d8; background: white; cursor: pointer; }
        </style></head><body>
        <h1>Can't reach the LocallyAI office Mac.</h1>
        <p>The Manager is configured to connect to:</p>
        <p><code>\(target.absoluteString)</code></p>
        <p>Check that:</p>
        <ul>
            <li>You're on the office Wi-Fi (or Tailscale is on).</li>
            <li>The office Mac is awake and running LocallyAI.</li>
            <li>The hostname is reachable: try <code>ping \(target.host ?? "?")</code> in Terminal.</li>
        </ul>
        <p><button onclick="location.reload()">Retry</button></p>
        <p class="meta">Error: \(error.localizedDescription)</p>
        </body></html>
        """
        webView.loadHTMLString(html, baseURL: nil)
    }
}

enum WorkspaceURL {
    static func resolve() -> URL {
        // 1. env var (developer / debug override)
        if let raw = ProcessInfo.processInfo.environment["LOCALLYAI_WORKSPACE_URL"],
           let url = URL(string: raw), url.scheme != nil {
            return url
        }
        // 2. UserDefaults (set by first-run dialog or operator)
        if let raw = UserDefaults.standard.string(forKey: "WorkspaceURL"),
           let url = URL(string: raw), url.scheme != nil {
            return url
        }
        // 3. compile-time default
        return URL(string: COMPILE_TIME_DEFAULT_URL)!
    }

    static func set(_ urlString: String) {
        UserDefaults.standard.set(urlString, forKey: "WorkspaceURL")
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var controller: WindowController?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // First-run check — if no URL has ever been set AND no
        // compile-time default looks office-shaped, ask.
        let url = WorkspaceURL.resolve()
        controller = WindowController(url: url)
        installMenu()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    private func installMenu() {
        let mainMenu = NSMenu()

        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About LocallyAI Workspace", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Set Workspace URL…", action: #selector(promptForURL), keyEquivalent: ",").target = self
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "Hide LocallyAI Workspace", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)

        let editMenuItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu
        mainMenu.addItem(editMenuItem)

        let viewMenuItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(withTitle: "Reload", action: #selector(reloadPage), keyEquivalent: "r").target = self
        viewMenuItem.submenu = viewMenu
        mainMenu.addItem(viewMenuItem)

        NSApplication.shared.mainMenu = mainMenu
    }

    @objc func promptForURL() {
        let alert = NSAlert()
        alert.messageText = "LocallyAI Workspace URL"
        alert.informativeText = "Enter your firm's office Mac URL. Your IT administrator can give you this."
        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 400, height: 24))
        input.stringValue = WorkspaceURL.resolve().absoluteString
        alert.accessoryView = input
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn {
            let raw = input.stringValue.trimmingCharacters(in: .whitespaces)
            if let url = URL(string: raw), url.scheme != nil {
                WorkspaceURL.set(raw)
                controller?.webView.load(URLRequest(url: url))
            }
        }
    }

    @objc func reloadPage() {
        controller?.webView.reload()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.activate(ignoringOtherApps: true)
app.run()
