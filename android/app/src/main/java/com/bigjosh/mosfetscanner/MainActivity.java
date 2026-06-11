package com.bigjosh.mosfetscanner;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;
import android.view.WindowManager;
import android.webkit.ConsoleMessage;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Thin shell: a fullscreen WebView pointed at the GitHub Pages app, plus a
 * native USB-serial bridge (window.AndroidSerial) that bypasses Chrome's
 * WebUSB/Web Serial restrictions. All app logic lives in the web app, so
 * features update via git push without touching this APK.
 */
public class MainActivity extends Activity {
    private static final String APP_HOST = "bigjosh.github.io";
    private static final String APP_URL = "https://" + APP_HOST + "/scan-mosfet/";

    private WebView web;
    private SerialBridge bridge;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setUserAgentString(s.getUserAgentString() + " MosfetScannerShell/1.0");

        bridge = new SerialBridge(this, web);
        web.addJavascriptInterface(bridge, "AndroidSerial");

        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onConsoleMessage(ConsoleMessage m) {
                Log.d("WebConsole", m.message() + " @" + m.sourceId() + ":" + m.lineNumber());
                return true;
            }
        });
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                if (APP_HOST.equals(u.getHost())) return false;  // stay in-app
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));  // external links -> browser
                } catch (Exception ignored) { }
                return true;
            }
        });

        setContentView(web);
        web.loadUrl(APP_URL);
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (bridge != null) bridge.shutdown();
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
