#!/usr/bin/env python3
"""Create the SwiftProxy Android project from pinned MIT-licensed SocksTun.
The engine is open source; the new app uses a separate package and signing key.
"""
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "swiftproxy-android"
UPSTREAM = "https://github.com/heiher/sockstun.git"
COMMIT = "f8d9e25cb3ebd712c1e092c619d539820fd1c8e0"

def edit(path, old, new):
    file = OUT / path
    s = file.read_text(encoding="utf-8")
    count = s.count(old)
    if count != 1:
        raise RuntimeError(f"Unexpected upstream source for {path}: {count} matches")
    file.write_text(s.replace(old, new, 1), encoding="utf-8")

if OUT.exists():
    raise RuntimeError("Remove swiftproxy-android before regenerating it")

subprocess.run(["git", "clone", "--no-checkout", UPSTREAM, str(OUT)], check=True)
subprocess.run(["git", "-C", str(OUT), "checkout", "--detach", COMMIT], check=True)
subprocess.run(["git", "-C", str(OUT), "submodule", "update", "--init", "--recursive"], check=True)

edit("app/build.gradle", 'applicationId "hev.sockstun"', 'applicationId "com.swiftproxy.light"')
edit("app/build.gradle", 'versionCode 12', 'versionCode 101')
edit("app/build.gradle", 'versionName "8.0"', 'versionName "0.2.0"')
edit("app/build.gradle", """\t\tdebug {
\t\t\tminifyEnabled false
\t\t\tsigningConfig signingConfigs.release
\t\t}""", """\t\tdebug {
            minifyEnabled false
            signingConfig signingConfigs.debug
        }""")
edit("app/src/main/res/values/strings.xml", '<string name="app_name">SocksTun</string>', '<string name="app_name">SwiftProxy</string>')
edit("app/src/main/res/values/strings.xml", 'Profile limit reached (13)', 'Profile limit reached (200)')
edit("app/src/main/java/hev/sockstun/Preferences.java", "public static final int MAX_PROFILES = 13;", "public static final int MAX_PROFILES = 200;")
edit("app/src/main/java/hev/sockstun/Preferences.java", "return prefs.getBoolean(key(GLOBAL), false);", "return prefs.getBoolean(key(GLOBAL), true);")

manifest = "app/src/main/AndroidManifest.xml"
for cls in ("MainActivity", "TProxyService", "QSTileService", "AppListActivity", "ServiceReceiver"):
    edit(manifest, 'android:name=".' + cls + '"', 'android:name="hev.sockstun.' + cls + '"')

activity = "app/src/main/java/hev/sockstun/MainActivity.java"
edit(activity, "import android.os.Bundle;", "import android.os.Bundle;\nimport android.os.Handler;\nimport android.os.Looper;\nimport android.os.Build;")
edit(activity, "\tprivate Preferences prefs;", "\tprivate Preferences prefs;\n    private boolean switching = false;\n    private final Handler switchHandler = new Handler(Looper.getMainLooper());")
edit(activity, "\t\tbutton_profile_prev.setOnClickListener(this);", """        textview_profile_name.setOnClickListener(v -> showProfilePicker());
        button_profile_prev.setOnClickListener(this);""")
old_switch = """\tprivate void switchProfile(int direction) {
\t\tif (prefs.getEnable())
\t\t  return;
\t\tint count = prefs.getProfileCount();
\t\tprefs.setSelected((prefs.getSelected() + direction + count) % count);
\t\tupdateUI();
\t}"""
new_switch = """    private void switchProfile(int direction) {
        if (switching || prefs.getProfileCount() < 2) return;
        int count = prefs.getProfileCount();
        selectProfile((prefs.getSelected() + direction + count) % count);
    }

    private void showProfilePicker() {
        if (switching) return;
        android.content.SharedPreferences sp =
            getSharedPreferences(Preferences.PREFS_NAME, MODE_PRIVATE);
        String[] names = new String[prefs.getProfileCount()];
        for (int i = 0; i < names.length; i++)
            names[i] = sp.getString("P" + i + "." + Preferences.NAME, "Proxy " + (i+1));
        new AlertDialog.Builder(this).setTitle("Switch proxy")
            .setSingleChoiceItems(names, prefs.getSelected(), (dialog, position) -> {
                dialog.dismiss();
                selectProfile(position);
            }).show();
    }

    private void selectProfile(int index) {
        if (switching || prefs.getSelected() == index) return;
        boolean wasConnected = prefs.getEnable();
        if (!wasConnected) {
            try { savePrefs(); }
            catch (Exception e) {
                Toast.makeText(this, "Please check port", Toast.LENGTH_SHORT).show();
                return;
            }
        }
        prefs.setSelected(index);
        updateUI();
        if (!wasConnected) return;

        switching = true;
        updateControlState();
        Toast.makeText(this, "Switching proxy...", Toast.LENGTH_SHORT).show();
        Intent disconnect = new Intent(this, TProxyService.class);
        startService(disconnect.setAction(TProxyService.ACTION_DISCONNECT));
        // Upstream tunnel engine terminates its isolated :native process on stop.
        // Start a fresh process so native state is not reused.
        switchHandler.postDelayed(() -> {
            Intent connect = new Intent(this, TProxyService.class)
                .setAction(TProxyService.ACTION_CONNECT);
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                    startForegroundService(connect);
                else startService(connect);
            } catch (Exception e) {
                Toast.makeText(this, "Could not restart VPN", Toast.LENGTH_LONG).show();
            }
            switchHandler.postDelayed(() -> {
                switching = false;
                updateControlState();
            }, 1800);
        }, 1700);
    }"""
edit(activity, old_switch, new_switch)
edit(activity, 'button_profile_prev.setEnabled(editable);', 'button_profile_prev.setEnabled(!switching);')
edit(activity, 'button_profile_next.setEnabled(editable);', 'button_profile_next.setEnabled(!switching);')
edit(activity, 'textview_profile_name.setEnabled(editable);', 'textview_profile_name.setEnabled(!switching);')
edit(activity, 'button_control.setEnabled(editable);', 'button_control.setEnabled(!switching);') if False else None
# A VPN permission check still takes place in the original MainActivity.
# Report native failures correctly rather than showing an active tunnel.
service = "app/src/main/java/hev/sockstun/TProxyService.java"
edit(service, """\t\tTProxyStartService(tproxy_file.getAbsolutePath(), tunFd.getFd());
\t\tprefs.setEnable(true);""", """        if (!TProxyStartService(tproxy_file.getAbsolutePath(), tunFd.getFd())) {
            try { tunFd.close(); } catch (IOException ignored) { }
            tunFd = null;
            prefs.setEnable(false);
            stopSelf();
            return;
        }
        prefs.setEnable(true);""")
for name in ("store.properties", "debug.keystore"):
    (OUT / name).unlink(missing_ok=True)
print("SwiftProxy ready. Build with ./gradlew assembleDebug in swiftproxy-android")
