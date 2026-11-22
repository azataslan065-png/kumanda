package com.azat.kumanda;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
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

/* ZORUNLU GÜNCELLEME — YALNIZCA UYGULAMA MERKEZİ ÜZERİNDEN (2026-11-22, kullanıcı: "Zorunlu güncelleme sadece Uygulama Merkezi
   üzerinden yapılıyor ... Uygulama Merkezi ilgili cihazda yoksa güncelleme indirilemez, bu nedenle ilgili uyarıyı versin").
   denetle(): mağazanın sürüm dosyasında bu uygulama varsa ve GitHub'daki APK telefondaki kurulumdan yeniyse kapatılamayan
   "Güncelleme gerekli" penceresi çıkar; uygulama güncellenmeden kullanılamaz. "Sonra" yoktur.
   - Uygulama Merkezi kuruluysa: "Uygulama Merkezi'nde güncelle" → mağazada bu uygulamanın sayfası (uygulamamerkezi://paket/<paket>).
   - Kurulu değilse: "Uygulama Merkezi bu cihazda kurulu değil" uyarısı + "Uygulama Merkezi'ni indir".
   Uygulama içinde indirme/kurma YOKTUR. İnternet yoksa ya da mağazaya ulaşılamazsa uygulama engellenmez.
   Her onResume'da çağrılır: mağazadan dönünce güncelleme yapılmadıysa pencere yeniden çıkar. */
final class Magaza {
    static final String SITE = "https://azataslan065-png.github.io/uygulama-merkezi/";
    static final String MAGAZA_PAKET = "com.azat.uygulamamerkezi";
    static final String MAGAZA_APK = "https://github.com/azataslan065-png/uygulama-merkezi/releases/download/apk/Uygulama-Merkezi.apk";
    private static long son;
    private static boolean acik;
    private static String bekleyen;      // gerekli güncellemenin açıklaması (bulunduysa); güncelleme kurulunca süreç yeniden başlar, sıfırlanır
    private static AlertDialog pencere;
    private static boolean pencereKurulu;

    static boolean magazaKurulu(Activity a) {
        try { a.getPackageManager().getPackageInfo(MAGAZA_PAKET, 0); return true; } catch (PackageManager.NameNotFoundException e) { }
        Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse("uygulamamerkezi://paket/" + a.getPackageName()));
        return i.resolveActivity(a.getPackageManager()) != null;
    }

    /* Mağazada bu uygulamanın sayfası. Uygulama Merkezi kurulu değilse güncelleme indirilemez: uyarı + "Uygulama Merkezi'ni indir". */
    static void ac(Activity a) {
        if (!magazaKurulu(a)) { kuruluDegil(a); return; }
        try { a.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("uygulamamerkezi://paket/" + a.getPackageName())).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); }
        catch (Exception e) { kuruluDegil(a); }
    }

    static void kuruluDegil(final Activity a) {
        if (a.isFinishing()) return;
        new AlertDialog.Builder(a, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Uygulama Merkezi kurulu değil")
            .setMessage("Güncelleme yalnızca Uygulama Merkezi'nden indirilir ve bu cihazda Uygulama Merkezi kurulu değil. Önce Uygulama Merkezi'ni kur, sonra bu uygulamayı oradan güncelle.")
            .setPositiveButton("Uygulama Merkezi'ni indir", (d, w) -> magazayiIndir(a))
            .setNegativeButton("Tamam", null)
            .show();
    }

    static void magazayiIndir(Activity a) {
        try { a.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(MAGAZA_APK)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); } catch (Exception ignored) { }
    }

    static void denetle(final Activity a) {
        if (bekleyen != null) { goster(a); return; }
        long t = System.currentTimeMillis();
        if (t - son < 5L * 60 * 1000) return;
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
                    try {
                        JSONObject r = new JSONObject(oku("https://api.github.com/repos/" + m.group(1) + "/" + m.group(2) + "/releases/tags/" + m.group(3)));
                        for (int i = 0; r.optJSONArray("assets") != null && i < r.optJSONArray("assets").length(); i++) {
                            JSONObject as = r.optJSONArray("assets").optJSONObject(i);
                            if (as != null && Uri.decode(m.group(4)).equals(as.optString("name"))) { long z = iso(as.optString("updated_at")); if (z > 0) apk = z; }
                        }
                    } catch (Exception ignored) { }                            // GitHub sınırı: mağazanın kaydettiği zamanla devam
                }
                PackageInfo pi = a.getPackageManager().getPackageInfo(a.getPackageName(), 0);
                if (apk <= 0 || pi.lastUpdateTime >= apk - 120000) return;     // güncel
                String ad = ben.optString("name", ""), v = ben.optString("version", "");
                bekleyen = (ad.isEmpty() ? "Bu uygulamanın" : ad) + (v.isEmpty() ? " yeni sürümü" : " v" + v);
                h.post(() -> goster(a));
            } catch (Exception ignored) { }
        }).start();
    }

    private static void goster(final Activity a) {
        if (a.isFinishing() || bekleyen == null) return;
        final boolean kurulu = magazaKurulu(a);
        if (acik && pencere != null && pencere.isShowing()) {
            if (kurulu == pencereKurulu) return;
            try { pencere.setOnDismissListener(null); pencere.dismiss(); } catch (Exception ignored) { }   // bu arada Uygulama Merkezi kuruldu: pencere ona göre yenilenir
        }
        pencereKurulu = kurulu;
        AlertDialog.Builder b = new AlertDialog.Builder(a, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Güncelleme gerekli")
            .setCancelable(false);
        if (kurulu) {
            b.setMessage(bekleyen + " çıktı. Devam etmek için güncellemen gerekiyor.\n\nGüncelleme yalnızca Uygulama Merkezi'nden yapılır.")
             .setPositiveButton("Uygulama Merkezi'nde güncelle", null);
        } else {
            b.setMessage(bekleyen + " çıktı. Devam etmek için güncellemen gerekiyor.\n\nUygulama Merkezi bu cihazda kurulu değil, bu yüzden güncelleme indirilemez. Önce Uygulama Merkezi'ni kur, sonra bu uygulamayı oradan güncelle.")
             .setPositiveButton("Uygulama Merkezi'ni indir", null);
        }
        b.setOnDismissListener(d -> acik = false);
        acik = true;
        pencere = b.show();
        // düğmeye basınca pencere kapanmaz: mağazadan güncellemeden dönülürse uygulama yine kullanılamaz
        pencere.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> { if (kurulu) ac(a); else magazayiIndir(a); });
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
