package com.bigjosh.mosfetscanner;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbManager;
import android.net.Uri;
import android.os.Build;
import android.provider.MediaStore;
import android.util.Base64;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import android.widget.Toast;

import com.hoho.android.usbserial.driver.UsbSerialDriver;
import com.hoho.android.usbserial.driver.UsbSerialPort;
import com.hoho.android.usbserial.driver.UsbSerialProber;
import com.hoho.android.usbserial.util.SerialInputOutputManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.OutputStream;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * window.AndroidSerial - the JS-facing USB serial bridge.
 *
 * JS API (all methods may be called from the page):
 *   list()                -> JSON array [{id, vid, pid, name, driver}]
 *   connect(id, baud)     -> async; events arrive via window.__androidSerialEvent
 *   write(base64) -> bool
 *   close()
 *   saveFile(name, base64)-> writes to the system Downloads folder
 *
 * Events pushed into the page: {type: 'connect'|'data'|'error'|'disconnect', data}
 * with 'data' payloads base64-encoded.
 */
public class SerialBridge implements SerialInputOutputManager.Listener {
    private static final String TAG = "SerialBridge";
    private static final String ACTION_USB_PERMISSION = "com.bigjosh.mosfetscanner.USB_PERMISSION";
    private static final int WRITE_TIMEOUT_MS = 2000;

    private final Activity activity;
    private final WebView webView;
    private final UsbManager usbManager;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    private UsbSerialPort port;
    private SerialInputOutputManager ioManager;
    private UsbDevice pendingDevice;
    private int pendingBaud;
    private boolean receiverRegistered = false;

    public SerialBridge(Activity activity, WebView webView) {
        this.activity = activity;
        this.webView = webView;
        this.usbManager = (UsbManager) activity.getSystemService(Context.USB_SERVICE);
    }

    private final BroadcastReceiver permissionReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (!ACTION_USB_PERMISSION.equals(intent.getAction())) return;
            boolean granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false);
            final UsbDevice device = pendingDevice;
            final int baud = pendingBaud;
            pendingDevice = null;
            if (device == null) return;
            if (granted) {
                executor.submit(() -> openPort(device, baud));
            } else {
                emit("error", "USB permission denied");
            }
        }
    };

    @JavascriptInterface
    public String list() {
        JSONArray out = new JSONArray();
        try {
            List<UsbSerialDriver> drivers = UsbSerialProber.getDefaultProber().findAllDrivers(usbManager);
            for (UsbSerialDriver d : drivers) {
                UsbDevice dev = d.getDevice();
                JSONObject o = new JSONObject();
                o.put("id", dev.getDeviceId());
                o.put("vid", dev.getVendorId());
                o.put("pid", dev.getProductId());
                o.put("driver", d.getClass().getSimpleName());
                String name = null;
                try { name = dev.getProductName(); } catch (Exception ignored) { }
                o.put("name", name == null ? "" : name);
                out.put(o);
            }
        } catch (Exception e) {
            Log.e(TAG, "list failed", e);
        }
        return out.toString();
    }

    @JavascriptInterface
    public void connect(final int deviceId, final int baud) {
        executor.submit(() -> {
            try {
                closeInternal();
                UsbSerialDriver driver = findDriver(deviceId);
                if (driver == null) {
                    emit("error", "USB device not found (unplugged?)");
                    return;
                }
                UsbDevice device = driver.getDevice();
                if (!usbManager.hasPermission(device)) {
                    pendingDevice = device;
                    pendingBaud = baud;
                    registerReceiverOnce();
                    Intent intent = new Intent(ACTION_USB_PERMISSION).setPackage(activity.getPackageName());
                    // Must be MUTABLE on Android 12+: the system writes the grant
                    // result into the intent extras. setPackage keeps it legal
                    // under targetSdk 34's implicit-mutable ban.
                    int piFlags = Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
                    PendingIntent pi = PendingIntent.getBroadcast(activity, 0, intent, piFlags);
                    usbManager.requestPermission(device, pi);
                    return;  // continues in permissionReceiver after the dialog
                }
                openPort(device, baud);
            } catch (Exception e) {
                emit("error", "connect failed: " + e.getMessage());
            }
        });
    }

    private UsbSerialDriver findDriver(int deviceId) {
        for (UsbSerialDriver d : UsbSerialProber.getDefaultProber().findAllDrivers(usbManager)) {
            if (d.getDevice().getDeviceId() == deviceId) return d;
        }
        return null;
    }

    private void openPort(UsbDevice device, int baud) {
        try {
            UsbSerialDriver driver = findDriver(device.getDeviceId());
            if (driver == null) {
                emit("error", "USB device disappeared");
                return;
            }
            UsbDeviceConnection connection = usbManager.openDevice(driver.getDevice());
            if (connection == null) {
                emit("error", "openDevice failed (permission?)");
                return;
            }
            port = driver.getPorts().get(0);
            port.open(connection);
            port.setParameters(baud, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE);
            try {
                port.setDTR(true);  // Uno auto-reset -> firmware banner follows
                port.setRTS(true);
            } catch (Exception e) {
                Log.w(TAG, "DTR/RTS unsupported on this adapter", e);
            }
            ioManager = new SerialInputOutputManager(port, this);
            ioManager.start();
            emit("connect", "");
        } catch (Exception e) {
            emit("error", "open failed: " + e.getMessage());
            closeInternal();
        }
    }

    @JavascriptInterface
    public boolean write(String base64) {
        try {
            UsbSerialPort p = port;
            if (p == null) return false;
            p.write(Base64.decode(base64, Base64.DEFAULT), WRITE_TIMEOUT_MS);
            return true;
        } catch (Exception e) {
            emit("error", "write failed: " + e.getMessage());
            return false;
        }
    }

    @JavascriptInterface
    public void close() {
        executor.submit(this::closeInternal);
    }

    @JavascriptInterface
    public void saveFile(String filename, String base64) {
        try {
            ContentValues v = new ContentValues();
            v.put(MediaStore.Downloads.DISPLAY_NAME, filename);
            v.put(MediaStore.Downloads.MIME_TYPE,
                    filename.endsWith(".csv") ? "text/csv" : "application/octet-stream");
            Uri uri = activity.getContentResolver()
                    .insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
            if (uri == null) throw new IllegalStateException("MediaStore insert failed");
            try (OutputStream os = activity.getContentResolver().openOutputStream(uri)) {
                os.write(Base64.decode(base64, Base64.DEFAULT));
            }
            final String msg = "Saved to Downloads: " + filename;
            activity.runOnUiThread(() ->
                    Toast.makeText(activity, msg, Toast.LENGTH_SHORT).show());
        } catch (Exception e) {
            emit("error", "save failed: " + e.getMessage());
        }
    }

    // ---- SerialInputOutputManager.Listener (runs on the IO thread) ----

    @Override
    public void onNewData(byte[] data) {
        emit("data", Base64.encodeToString(data, Base64.NO_WRAP));
    }

    @Override
    public void onRunError(Exception e) {
        emit("disconnect", e == null ? "" : String.valueOf(e.getMessage()));
        executor.submit(this::closeInternal);
    }

    // ---- internals ----

    private synchronized void closeInternal() {
        if (ioManager != null) {
            try {
                ioManager.setListener(null);
                ioManager.stop();
            } catch (Exception ignored) { }
            ioManager = null;
        }
        if (port != null) {
            try { port.close(); } catch (Exception ignored) { }
            port = null;
        }
    }

    public void shutdown() {
        closeInternal();
        if (receiverRegistered) {
            try { activity.unregisterReceiver(permissionReceiver); } catch (Exception ignored) { }
            receiverRegistered = false;
        }
        executor.shutdown();
    }

    private void registerReceiverOnce() {
        if (receiverRegistered) return;
        IntentFilter filter = new IntentFilter(ACTION_USB_PERMISSION);
        if (Build.VERSION.SDK_INT >= 33) {
            activity.registerReceiver(permissionReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            activity.registerReceiver(permissionReceiver, filter);
        }
        receiverRegistered = true;
    }

    private void emit(String type, String data) {
        try {
            JSONObject o = new JSONObject();
            o.put("type", type);
            o.put("data", data);
            final String js = "window.__androidSerialEvent && window.__androidSerialEvent(" + o + ");";
            activity.runOnUiThread(() -> webView.evaluateJavascript(js, null));
        } catch (Exception e) {
            Log.e(TAG, "emit failed", e);
        }
    }
}
