# Uygulamanın içindeki köprü: kumanda.py'yi Termux yerine Android hizmetinin içinde çalıştırır.
import os, sys

def baslat(ev, site, android, port=8080):
    os.environ['HOME'] = ev
    os.environ['KUMANDA_PORT'] = str(port)                    # ayarlar, eşleşme anahtarları, yayın akışı burada saklanır
    os.environ['KUMANDA_SITE'] = site          # kumanda sayfası (APK'dan çıkarılan kopya)
    # Python'un https doğrulaması için Android'in kendi sertifika deposu
    if os.path.isdir('/system/etc/security/cacerts'):
        os.environ.setdefault('SSL_CERT_DIR', '/system/etc/security/cacerts')
    sys.argv = ['kumanda']
    import kumanda
    kumanda.ANDROID = android                  # bildirimler Android'in kendi bildirimiyle gösterilir
    kumanda.main()
