package sg.rajat.ledctl;

import android.app.Activity;
import android.bluetooth.*;
import android.bluetooth.le.*;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.audiofx.Visualizer;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.ParcelUuid;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedList;
import java.util.List;
import java.util.UUID;

// ledctl phone app: the same UI as the board's web page, talking to the ESP32 over Bluetooth LE.
public class MainActivity extends Activity {
    static final UUID SVC = UUID.fromString("6e1d0001-8c2a-4f0e-9b1a-1edc7a5e0001");
    static final UUID CMD = UUID.fromString("6e1d0002-8c2a-4f0e-9b1a-1edc7a5e0001");
    static final UUID STATE = UUID.fromString("6e1d0003-8c2a-4f0e-9b1a-1edc7a5e0001");
    static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");

    WebView web;
    final Handler ui = new Handler(Looper.getMainLooper());
    BluetoothAdapter adapter;
    BluetoothLeScanner scanner;
    BluetoothGatt gatt;
    BluetoothGattCharacteristic cmdChar;
    boolean scanning = false, connected = false, connecting = false, resumed = false;
    final LinkedList<String> queue = new LinkedList<>();
    boolean writing = false;
    String lastStatus = "";

    // ---------- phone audio: read what the phone is playing (e.g. Spotify) digitally via the Visualizer API ----------
    Visualizer vis;
    boolean phoneAudioWanted = false;
    long lastNonZero = 0, visStarted = 0;

    void startPhoneAudio() {
        phoneAudioWanted = true;
        if (checkSelfPermission("android.permission.RECORD_AUDIO") != PackageManager.PERMISSION_GRANTED) {
            phone("allow the microphone permission (needed to read the phone's audio)", false);
            requestPermissions(new String[]{"android.permission.RECORD_AUDIO"}, 3);
            return;
        }
        if (vis != null) return;
        try {
            vis = new Visualizer(0);                     // 0 = the phone's whole output mix
            vis.setEnabled(false);
            vis.setCaptureSize(Visualizer.getCaptureSizeRange()[1]);
            vis.setScalingMode(Visualizer.SCALING_MODE_NORMALIZED);
            vis.setDataCaptureListener(new Visualizer.OnDataCaptureListener() {
                public void onWaveFormDataCapture(Visualizer v, byte[] w, int rate) { }
                public void onFftDataCapture(Visualizer v, byte[] fft, int rateMilliHz) { onFft(fft, rateMilliHz / 1000); }
            }, Visualizer.getMaxCaptureRate(), false, true);
            vis.setEnabled(true);
            visStarted = System.currentTimeMillis();
            phone("listening to the phone's audio…", true);
        } catch (Throwable e) {
            vis = null;
            phone("this phone won't share its audio (" + e.getClass().getSimpleName() + ")", false);
        }
    }

    void stopPhoneAudio() {
        phoneAudioWanted = false;
        try { if (vis != null) { vis.setEnabled(false); vis.release(); } } catch (Throwable e) { }
        vis = null;
        phone("", true);
    }

    static double lg(double e) { return Math.max(0, Math.min(254, 4 * Math.log(1 + e) / Math.log(2))); }

    long lastPhoneMsg = 0;
    void onFft(byte[] fft, int fs) {
        int n = fft.length;                              // n real samples -> n/2 bins
        double eb = 0, em = 0, eh = 0;
        for (int k = 1; k < n / 2; k++) {
            double re = fft[2 * k], im = fft[2 * k + 1], e = re * re + im * im;
            double f = (double) k * fs / n;
            if (f < 150) eb += e; else if (f < 2000) em += e; else if (f < 12000) eh += e;
        }
        double et = eb + em + eh;
        long now = System.currentTimeMillis();
        if (et > 0) lastNonZero = now;
        String pkt = "pa=" + (int) lg(et * 4) + "," + (int) lg(eb * 8) + "," + (int) lg(em * 4) + "," + (int) lg(eh * 8);
        if (connected) sendAudio(pkt);
        if (now - lastPhoneMsg > 1000) {
            lastPhoneMsg = now;
            if (now - lastNonZero < 1500) phone("using the phone's audio ♪", true);
            else if (now - visStarted > 3000) phone("no sound from the phone – is music playing? (some phones block this)", false);
        }
    }

    void sendAudio(String pkt) {
        synchronized (queue) {
            // keep at most one audio packet waiting so we never lag behind the music
            for (int i = queue.size() - 1; i >= 0; i--) if (queue.get(i).startsWith("pa=")) queue.remove(i);
            queue.add(pkt);
        }
        ui.post(new Runnable() { public void run() { pump(); } });
    }

    void phone(final String text, final boolean ok) {
        ui.post(new Runnable() { public void run() { web.evaluateJavascript("window.onPhone && onPhone(" + quote(text) + "," + ok + ")", null); } });
    }

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().setStatusBarColor(Color.parseColor("#0b0b0f"));
        getWindow().setNavigationBarColor(Color.parseColor("#0b0b0f"));
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0b0b0f"));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        web.addJavascriptInterface(new Bridge(), "Android");
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView v, String url) { status(lastStatus.isEmpty() ? "searching…" : lastStatus, connected); }
        });
        web.loadUrl("file:///android_asset/index.html");
        setContentView(web);
        BluetoothManager bm = (BluetoothManager) getSystemService(BLUETOOTH_SERVICE);
        adapter = bm.getAdapter();
    }

    @Override protected void onResume() { super.onResume(); resumed = true; start(); }
    @Override protected void onPause() { super.onPause(); resumed = false; stopScan(); }
    @Override protected void onDestroy() { super.onDestroy(); stopPhoneAudio(); closeGatt(); }

    // ---------- permissions / start ----------
    String[] neededPerms() {
        if (Build.VERSION.SDK_INT >= 31)
            return new String[]{"android.permission.BLUETOOTH_SCAN", "android.permission.BLUETOOTH_CONNECT", "android.permission.ACCESS_FINE_LOCATION"};
        return new String[]{"android.permission.ACCESS_FINE_LOCATION"};
    }

    boolean hasPerm(String p) { return checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED; }

    void start() {
        if (adapter == null) { status("this phone has no Bluetooth", false); return; }
        List<String> missing = new ArrayList<>();
        for (String p : neededPerms()) if (!hasPerm(p)) missing.add(p);
        boolean core = Build.VERSION.SDK_INT >= 31
                ? hasPerm("android.permission.BLUETOOTH_CONNECT") && hasPerm("android.permission.BLUETOOTH_SCAN")
                : hasPerm("android.permission.ACCESS_FINE_LOCATION");
        if (!core) {
            status("allow Bluetooth permission", false);
            requestPermissions(missing.toArray(new String[0]), 1);
            return;
        }
        if (!adapter.isEnabled()) {
            status("turn Bluetooth on", false);
            try { startActivityForResult(new Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE), 2); } catch (Exception e) { }
            return;
        }
        if (!connected && !connecting) startScan();
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        if (code == 3) {
            if (res.length > 0 && res[0] == PackageManager.PERMISSION_GRANTED) startPhoneAudio();
            else phone("microphone permission denied", false);
            return;
        }
        ui.postDelayed(new Runnable() { public void run() { start(); } }, 200);
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        ui.postDelayed(new Runnable() { public void run() { start(); } }, 300);
    }

    // ---------- scan ----------
    final ScanCallback scanCb = new ScanCallback() {
        @Override public void onScanResult(int type, ScanResult r) { found(r.getDevice()); }
        @Override public void onBatchScanResults(List<ScanResult> rs) { for (ScanResult r : rs) found(r.getDevice()); }
        @Override public void onScanFailed(int err) { status("scan failed (" + err + ")", false); retryLater(3000); }
    };

    void startScan() {
        if (scanning) return;
        try {
            scanner = adapter.getBluetoothLeScanner();
            if (scanner == null) { retryLater(2000); return; }
            ScanFilter f = new ScanFilter.Builder().setServiceUuid(new ParcelUuid(SVC)).build();
            ScanSettings st = new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build();
            scanner.startScan(Collections.singletonList(f), st, scanCb);
            scanning = true;
            status("searching for the board…", false);
            ui.postDelayed(new Runnable() { public void run() { if (scanning && !connected && !connecting) { stopScan(); retryLater(500); } } }, 12000);
        } catch (SecurityException e) {
            status("Bluetooth permission needed", false);
        }
    }

    void stopScan() {
        if (!scanning) return;
        scanning = false;
        try { scanner.stopScan(scanCb); } catch (Exception e) { }
    }

    void found(BluetoothDevice d) {
        if (connecting || connected) return;
        stopScan();
        connecting = true;
        status("connecting…", false);
        try {
            gatt = d.connectGatt(this, false, gattCb, BluetoothDevice.TRANSPORT_LE);
        } catch (SecurityException e) { connecting = false; status("Bluetooth permission needed", false); }
    }

    void retryLater(long ms) {
        ui.postDelayed(new Runnable() { public void run() { if (resumed && !connected && !connecting) start(); } }, ms);
    }

    void closeGatt() {
        try { if (gatt != null) { gatt.disconnect(); gatt.close(); } } catch (Exception e) { }
        gatt = null; cmdChar = null; connected = false; connecting = false;
        synchronized (queue) { queue.clear(); writing = false; }
    }

    // ---------- GATT ----------
    final BluetoothGattCallback gattCb = new BluetoothGattCallback() {
        @Override
        public void onConnectionStateChange(BluetoothGatt g, int status, int newState) {
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                try { g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH); g.requestMtu(185); } catch (SecurityException e) { }
            } else {
                ui.post(new Runnable() { public void run() { closeGatt(); status("board not found – retrying…", false); retryLater(1500); } });
            }
        }

        @Override public void onMtuChanged(BluetoothGatt g, int mtu, int status) {
            try { g.discoverServices(); } catch (SecurityException e) { }
        }

        @Override
        public void onServicesDiscovered(BluetoothGatt g, int status) {
            BluetoothGattService s = g.getService(SVC);
            if (s == null) { ui.post(new Runnable() { public void run() { closeGatt(); retryLater(1500); } }); return; }
            cmdChar = s.getCharacteristic(CMD);
            BluetoothGattCharacteristic st = s.getCharacteristic(STATE);
            try {
                g.setCharacteristicNotification(st, true);
                BluetoothGattDescriptor d = st.getDescriptor(CCCD);
                if (d != null) {
                    d.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
                    synchronized (queue) { writing = true; }
                    g.writeDescriptor(d);
                }
            } catch (SecurityException e) { }
            connected = true; connecting = false;
            status("connected", true);
        }

        @Override public void onDescriptorWrite(BluetoothGatt g, BluetoothGattDescriptor d, int status) {
            synchronized (queue) { writing = false; }
            pump();
        }

        @Override public void onCharacteristicWrite(BluetoothGatt g, BluetoothGattCharacteristic c, int status) {
            synchronized (queue) { writing = false; }
            pump();
        }

        @Override public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic c) {
            byte[] v = c.getValue();
            if (v != null) deliver(v);
        }

        // Android 13+ calls this one instead (same signature as the API 33 method)
        public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic c, byte[] v) {
            if (v != null) deliver(v);
        }
    };

    void deliver(byte[] v) {
        final String json = new String(v, StandardCharsets.UTF_8);
        ui.post(new Runnable() { public void run() { web.evaluateJavascript("window.onState && onState(" + quote(json) + ")", null); } });
    }

    void pump() {
        String next;
        synchronized (queue) {
            if (writing || queue.isEmpty() || cmdChar == null || gatt == null) return;
            next = queue.removeFirst();
            // merge waiting commands into one packet (later keys win on the board)
            while (!queue.isEmpty() && next.length() + 1 + queue.peekFirst().length() <= 180)
                next = next + "&" + queue.removeFirst();
            writing = true;
        }
        try {
            cmdChar.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT);
            cmdChar.setValue(next.getBytes(StandardCharsets.UTF_8));
            if (!gatt.writeCharacteristic(cmdChar)) {
                synchronized (queue) { writing = false; queue.addFirst(next); }
                ui.postDelayed(new Runnable() { public void run() { pump(); } }, 50);
            }
        } catch (SecurityException e) {
            synchronized (queue) { writing = false; }
        }
    }

    class Bridge {
        @JavascriptInterface public void phoneAudio(final boolean on) {
            ui.post(new Runnable() { public void run() { if (on) startPhoneAudio(); else stopPhoneAudio(); } });
        }
        @JavascriptInterface public void send(String q) {
            if (q == null || q.isEmpty()) return;
            synchronized (queue) { queue.add(q); }
            ui.post(new Runnable() { public void run() { pump(); } });
        }
    }

    void status(final String text, final boolean ok) {
        lastStatus = text;
        ui.post(new Runnable() { public void run() { web.evaluateJavascript("window.onConn && onConn(" + quote(text) + "," + ok + ")", null); } });
    }

    static String quote(String s) {
        StringBuilder b = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
            else b.append(c);
        }
        return b.append('"').toString();
    }
}
