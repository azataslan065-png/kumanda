package com.azat.kumanda;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Iterator;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/* UYGULAMA MERKEZİ ÜZERİNDEN GÜNCELLEME (2026-11-21, kullanıcı: "güncelleme geldiğinde kullanıcı uygulama içinde güncellemeyi
   kuramayacak; çıkan güncelleme ekranıyla mağazaya yönlendirilecek, güncelleme sadece uygulama mağazasında gerçekleştirilecek").
   ac(): Uygulama Merkezi uygulamasında bu uygulamanın sayfasını açar (uygulamamerkezi://paket/<paket>); mağaza uygulaması kurulu
   değilse aynı sayfa tarayıcıda açılır (oradan mağaza uygulaması indirilebilir).
   denetle(): mağazanın sürüm dosyasında bu uygulama varsa, GitHub'daki APK'nın yüklenme zamanı telefondaki kurulumdan yeniyse
   "Güncelleme var" penceresi gösterir. Uygulama içinde indirme/kurma YOKTUR. */
final class Magaza {
    static final String SITE = "https://azataslan065-png.github.io/uygulama-merkezi/";
    private static long son;
    private static boolean acik;

    static void ac(Activity a) {
        String pk = a.getPackageName();
        try { a.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("uygulamamerkezi://paket/" + pk)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); }
        catch (ActivityNotFoundException e) {
            try { a.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(SITE + "#/paket/" + pk)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); } catch (Exception ignored) { }
        }
    }

    static void denetle(final Activity a) {
        long t = System.currentTimeMillis();
        if (acik || t - son < 30L * 60 * 1000) return;
        son = t;
        final Handler h = new Handler(Looper.getMainLooper());
        new Thread(() -> {
            try {
                JSONObject apps = new JSONObject(oku(SITE + "guncelleme/surumler.json?t=" + System.currentTimeMillis())).optJSONObject("apps");
                if (apps == null) return;
                JSONObject ben = null; Iterator<String> it = apps.keys();
                while (it.hasNext()) { JSONObject x = apps.optJSONObject(it.next()); if (x != null && a.getPackageName().equals(x.optString("paket"))) { ben = x; break; } }
                if (ben == null) return;                                       // bu uygulama mağazada değil
                long apk = ben.optLong("apkMs", 0);
                Matcher m = Pattern.compile("github\\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/?#]+)").matcher(ben.optString("url"));
                if (m.find()) {                                                // GitHub'daki gerçek APK zamanı (mağaza da buna bakar)
                    JSONObject r = new JSONObject(oku("https://api.github.com/repos/" + m.group(1) + "/" + m.group(2) + "/releases/tags/" + m.group(3)));
                    for (int i = 0; r.optJSONArray("assets") != null && i < r.optJSONArray("assets").length(); i++) {
                        JSONObject as = r.optJSONArray("assets").optJSONObject(i);
                        if (as != null && Uri.decode(m.group(4)).equals(as.optString("name"))) { long z = iso(as.optString("updated_at")); if (z > 0) apk = z; }
                    }
                }
                PackageInfo pi = a.getPackageManager().getPackageInfo(a.getPackageName(), 0);
                if (apk <= 0 || pi.lastUpdateTime >= apk - 120000) return;     // güncel
                final SharedPreferences sp = a.getSharedPreferences("magaza", Context.MODE_PRIVATE);
                final String anahtar = "sonra" + apk;
                if (System.currentTimeMillis() < sp.getLong(anahtar, 0)) return;
                final String ad = ben.optString("name", ""), v = ben.optString("version", "");
                h.post(() -> {
                    if (a.isFinishing() || acik) return;
                    acik = true;
                    new AlertDialog.Builder(a, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                        .setTitle("Güncelleme var")
                        .setMessage((ad.isEmpty() ? "Bu uygulamanın" : ad) + (v.isEmpty() ? "" : " v" + v) + " yeni sürümü Uygulama Merkezi'nde hazır.\n\nGüncelleme yalnızca Uygulama Merkezi'nden yapılır.")
                        .setPositiveButton("Mağazada güncelle", (d, w) -> ac(a))
                        .setNegativeButton("Sonra", (d, w) -> sp.edit().putLong(anahtar, System.currentTimeMillis() + 24L * 60 * 60 * 1000).apply())
                        .setOnDismissListener(d -> acik = false)
                        .show();
                });
            } catch (Exception ignored) { }
        }).start();
    }

    private static long iso(String s) {
        try { java.text.SimpleDateFormat f = new java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US); f.setTimeZone(java.util.TimeZone.getTimeZone("UTC")); return f.parse(s).getTime(); }
        catch (Exception e) { return 0; }
    }

    private static String oku(String adres) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(adres).openConnection();
        c.setConnectTimeout(8000); c.setReadTimeout(10000); c.setRequestProperty("Cache-Control", "no-cache"); c.setRequestProperty("Accept", "application/vnd.github+json");
        if (c.getResponseCode() != 200) throw new Exception("HTTP " + c.getResponseCode());
        StringBuilder s = new StringBuilder();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), "UTF-8"))) { String l; while ((l = r.readLine()) != null) s.append(l); }
        return s.toString();
    }
}
