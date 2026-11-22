package com.azat.kumanda;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.content.res.AssetManager;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.Process;
import android.util.Log;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;

/*
 * Köprü hizmeti: eskiden Termux'ta çalışan kumanda.py artık bu hizmetin içinde çalışır.
 * Ön plan hizmeti olduğu için (bildirim çubuğunda "Kumanda çalışıyor") Android onu arka planda kapatmaz;
 * telefon açılınca da kendiliğinden başlar (bkz. Acilis). Sayfa http://127.0.0.1:8080 adresinden bu köprüye bağlanır.
 */
public class Kopru extends Service {
    static final String KAPAT = "com.azat.kumanda.KAPAT";
    static final String KANAL = "kopru", KANAL_HATIRLAT = "hatirlatma";
    static volatile boolean calisiyor, hazir;
    static volatile String hata;
    private WifiManager.MulticastLock ml;

    static void baslat(Context c) {
        Intent i = new Intent(c, Kopru.class);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i); else c.startService(i);
    }

    @Override public void onCreate() {
        super.onCreate();
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel k = new NotificationChannel(KANAL, "Kumanda bağlantısı", NotificationManager.IMPORTANCE_MIN);
            k.setDescription("Kumandanın TV'lerle bağlantısını açık tutar");
            k.setShowBadge(false);
            nm.createNotificationChannel(k);
            NotificationChannel h = new NotificationChannel(KANAL_HATIRLAT, "Program hatırlatmaları", NotificationManager.IMPORTANCE_HIGH);
            h.setDescription("Hatırlatma kurduğun program başlamak üzere");
            h.enableVibration(true);
            nm.createNotificationChannel(h);
        }
        Notification n = bildirim();
        if (Build.VERSION.SDK_INT >= 29) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE);
        else startForeground(1, n);
        try {   // ağdaki TV'leri bulmak için (SSDP) çok noktaya yayın paketlerini almak gerekir
            ml = ((WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE)).createMulticastLock(getPackageName());
            ml.setReferenceCounted(false);
            ml.acquire();
        } catch (Exception ignored) { }
    }

    private Notification bildirim() {
        PendingIntent ac = PendingIntent.getActivity(this, 0, new Intent(this, Ana.class), PendingIntent.FLAG_IMMUTABLE);
        PendingIntent kapat = PendingIntent.getService(this, 1, new Intent(this, Kopru.class).setAction(KAPAT), PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, KANAL) : new Notification.Builder(this);
        return b.setSmallIcon(R.drawable.bildirim)
            .setContentTitle(BuildConfig.TEST ? "Kumanda Test çalışıyor" : "Kumanda çalışıyor")
            .setContentText("TV'lerle bağlantı açık, hatırlatmalar zamanında gelir")
            .setContentIntent(ac)
            .setOngoing(true)
            .addAction(new Notification.Action.Builder(null, "Kapat", kapat).build())
            .build();
    }

    @Override public int onStartCommand(Intent i, int f, int id) {
        if (i != null && KAPAT.equals(i.getAction())) {
            stopForeground(true);
            stopSelf();
            Process.killProcess(Process.myPid());   // Python'un sunucusu süreçle birlikte kapanır
            return START_NOT_STICKY;
        }
        if (!calisiyor) {
            calisiyor = true;
            new Thread(this::calistir, "kopru").start();
        }
        return START_STICKY;
    }

    private void calistir() {
        try {
            File site = new File(getFilesDir(), "site");
            siteCikar(site);
            if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
            hazir = true;
            Python.getInstance().getModule("android_kopru").callAttr("baslat",
                getFilesDir().getAbsolutePath(), site.getAbsolutePath(), new Bildirici(this), BuildConfig.PORT);
            hata = "Köprü durdu";
        } catch (Throwable t) {
            hata = String.valueOf(t);
            Log.e("Kumanda", "köprü", t);
        } finally {
            calisiyor = false;
        }
    }

    /* Sayfa dosyaları her açılışta APK'dan yeniden çıkarılır: uygulama güncellenince sayfa da güncellenir. */
    private void siteCikar(File hedef) throws Exception {
        hedef.mkdirs();
        AssetManager am = getAssets();
        String[] adlar = am.list("site");
        if (adlar == null) return;
        byte[] tampon = new byte[65536];
        for (String ad : adlar) {
            try (InputStream in = am.open("site/" + ad); OutputStream out = new FileOutputStream(new File(hedef, ad))) {
                int n; while ((n = in.read(tampon)) > 0) out.write(tampon, 0, n);
            }
        }
    }

    @Override public void onDestroy() {
        try { if (ml != null) ml.release(); } catch (Exception ignored) { }
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent i) { return null; }
}
