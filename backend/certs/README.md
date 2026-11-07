# Сертификаты

`russian_trusted_root_ca.pem` — корневой сертификат удостоверяющего центра Минцифры (Russian Trusted Root CA). Им подписан сертификат API MAX (`platform-api2.max.ru`), а Python и Windows по умолчанию ему не доверяют.

Используется только в `app/bot/max_bot.py` для запросов к API MAX. Система и остальные HTTPS-запросы его не видят.

Источник: https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt (Госуслуги).
SHA-256: `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`. Действует до 27.02.2032.
