package com.azat.kumanda;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/* Köprü (Python) program hatırlatmasını bu nesne üzerinden telefona bildirim olarak gösterir. */
public class Bildirici {
    private final Context c;
    Bildirici(Context c) { this.c = c.getApplicationContext(); }

    public void bildir(String baslik, String metin) {
        PendingIntent ac = PendingIntent.getActivity(c, 2, new Intent(c, Ana.class), PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(c, Kopru.KANAL_HATIRLAT) : new Notification.Builder(c);
        b.setSmallIcon(R.drawable.bildirim).setContentTitle(baslik).setContentText(metin)
         .setStyle(new Notification.BigTextStyle().bigText(metin)).setContentIntent(ac).setAutoCancel(true);
        if (Build.VERSION.SDK_INT < 26) b.setPriority(Notification.PRIORITY_HIGH).setDefaults(Notification.DEFAULT_ALL);
        c.getSystemService(NotificationManager.class).notify((baslik + metin).hashCode(), b.build());
    }
}
