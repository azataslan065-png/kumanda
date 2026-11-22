package com.azat.kumanda;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.speech.RecognizerIntent;
import android.view.Gravity;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URLEncoder;
import java.util.ArrayList;

/*
 * Kumanda uygulaması: köprü (Kopru hizmeti) uygulamanın içinde çalışır, Termux gerekmez.
 * Sayfa köprünün kendi adresinden (http://127.0.0.1:8080) açılır; sesle komut Android'in konuşma tanımasıyla yapılır.
 */
public class Ana extends Activity {
    static final String ADRES = "http://127.0.0.1:8080/";
    private static final int SES = 21, BILDIRIM_IZNI = 22;
    private WebView w;
    private TextView yukleniyor;
    private String bekleyenPaylasim;

    @Override protected void onCreate(Bundle b) {
        super.onCreate(b);
        Kopru.baslat(this);
        FrameLayout kok = new FrameLayout(this);
        kok.setBackgroundColor(Color.parseColor("#0d0f14"));
        w = new WebView(this);
        w.setBackgroundColor(Color.parseColor("#0d0f14"));
        kok.addView(w);
        yukleniyor = new TextView(this);
        yukleniyor.setText("Kumanda başlatılıyor…");
        yukleniyor.setTextColor(Color.parseColor("#8a90a2"));
        yukleniyor.setTextSize(16);
        yukleniyor.setGravity(Gravity.CENTER);
        kok.addView(yukleniyor, new FrameLayout.LayoutParams(-1, -1));
        setContentView(kok);

        WebSettings s = w.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setTextZoom(100);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setUserAgentString(s.getUserAgentString() + " KumandaApp/" + BuildConfig.VERSION_CODE);
        w.setOverScrollMode(View.OVER_SCROLL_NEVER);
        w.addJavascriptInterface(new Kopruk(), "KumandaApp");
        w.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri u = r.getUrl();
                if ("127.0.0.1".equals(u.getHost()) || "localhost".equals(u.getHost())) return false;
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception ignored) { }
                return true;   // diğer bağlantılar (TOD, GitHub sayfası…) tarayıcıda açılır
            }
            @Override public void onPageFinished(WebView v, String url) { yukleniyor.setVisibility(View.GONE); }
        });
        paylasimAl(getIntent());
        sayfayiAc();
        izinler();
        Magaza.denetle(this);
    }

    /* Köprü hazır olunca (birkaç saniye) sayfayı açar. */
    private void sayfayiAc() {
        final String hedef = bekleyenPaylasim != null ? ADRES + "?metin=" + kodla(bekleyenPaylasim) : ADRES;
        bekleyenPaylasim = null;
        new Thread(() -> {
            long son = System.currentTimeMillis() + 40000;
            while (System.currentTimeMillis() < son) {
                try (Socket k = new Socket()) { k.connect(new InetSocketAddress("127.0.0.1", 8080), 500); break; }
                catch (Exception e) {
                    if (!Kopru.calisiyor && Kopru.hata != null) break;
                    try { Thread.sleep(300); } catch (InterruptedException ignored) { }
                }
            }
            runOnUiThread(() -> {
                if (Kopru.hata != null && !Kopru.calisiyor) yukleniyor.setText("Köprü başlatılamadı:\n" + Kopru.hata + "\n\nUygulamayı kapatıp yeniden aç.");
                w.loadUrl(hedef);
            });
        }).start();
    }

    private static String kodla(String s) { try { return URLEncoder.encode(s, "UTF-8"); } catch (Exception e) { return ""; } }

    private void paylasimAl(Intent i) {
        if (i != null && Intent.ACTION_SEND.equals(i.getAction())) {
            String t = i.getStringExtra(Intent.EXTRA_TEXT);
            if (t != null && !t.isEmpty()) bekleyenPaylasim = t;
        }
    }

    @Override protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        paylasimAl(i);
        if (bekleyenPaylasim != null) { w.loadUrl(ADRES + "?metin=" + kodla(bekleyenPaylasim)); bekleyenPaylasim = null; }
    }

    /* İlk açılışta: bildirim izni (hatırlatmalar için) ve pil kısıtlamasından muafiyet (bağlantı kopmasın). */
    private void izinler() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[]{ Manifest.permission.POST_NOTIFICATIONS }, BILDIRIM_IZNI);
        else pilMuafiyeti();
    }

    @Override public void onRequestPermissionsResult(int kod, String[] izin, int[] sonuc) {
        super.onRequestPermissionsResult(kod, izin, sonuc);
        if (kod == BILDIRIM_IZNI) { Kopru.baslat(this); pilMuafiyeti(); }
    }

    private void pilMuafiyeti() {
        try {
            PowerManager pm = getSystemService(PowerManager.class);
            SharedPreferences sp = getSharedPreferences("kumanda", MODE_PRIVATE);
            if (pm.isIgnoringBatteryOptimizations(getPackageName()) || sp.getBoolean("pil_soruldu", false)) return;
            sp.edit().putBoolean("pil_soruldu", true).apply();
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
        } catch (Exception ignored) { }
    }

    /* Sayfanın kullandığı yerel işlevler: window.KumandaApp */
    class Kopruk {
        @JavascriptInterface public void dinle() {
            runOnUiThread(() -> {
                Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                    .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "tr-TR")
                    .putExtra(RecognizerIntent.EXTRA_PROMPT, "Komutunu söyle")
                    .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3);
                try { startActivityForResult(i, SES); }
                catch (ActivityNotFoundException e) { sesSonucu("", "Bu telefonda konuşma tanıma yok (Google uygulaması gerekli)"); }
            });
        }
        @JavascriptInterface public int surum() { return BuildConfig.VERSION_CODE; }
        @JavascriptInterface public void magaza() { runOnUiThread(() -> Magaza.ac(Ana.this)); }
    }

    @Override protected void onActivityResult(int kod, int sonuc, Intent veri) {
        super.onActivityResult(kod, sonuc, veri);
        if (kod != SES) return;
        ArrayList<String> l = veri != null ? veri.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS) : null;
        if (sonuc == RESULT_OK && l != null && !l.isEmpty()) sesSonucu(l.get(0), "");
        else sesSonucu("", sonuc == RESULT_CANCELED ? "" : "Duyamadım");
    }

    private void sesSonucu(String metin, String hata) {
        w.evaluateJavascript("window.sesSonucu&&window.sesSonucu(" + JSONObject.quote(metin) + "," + JSONObject.quote(hata) + ")", null);
    }

    /* Geri: açık pencere/panel varsa sayfa kapatır; yoksa uygulama arka plana geçer (köprü çalışmaya devam eder). */
    @Override public void onBackPressed() {
        if (w.canGoBack()) w.goBack(); else moveTaskToBack(true);
    }

    @Override protected void onPause() { super.onPause(); w.onPause(); }
    @Override protected void onResume() {
        super.onResume(); w.onResume();
        if (!Kopru.calisiyor) Kopru.baslat(this);
        Magaza.denetle(this);
    }
    @Override protected void onDestroy() { if (w != null) w.destroy(); super.onDestroy(); }
}
