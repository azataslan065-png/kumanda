package com.azat.kumanda;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/* Telefon açılınca ya da uygulama güncellenince köprüyü başlatır: hatırlatmalar ve izleme süresi kesintisiz çalışır. */
public class Acilis extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent i) {
        try { Kopru.baslat(c); } catch (Exception ignored) { }
    }
}
