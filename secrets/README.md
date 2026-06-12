# Credenciales Firebase (no commitear)

Proyecto Firebase: **si2-p2-862ad**

## 1. Backend — push FCM (HTTP v1)

1. [Firebase Console](https://console.firebase.google.com/) → **si2-p2-862ad**
2. Configuración del proyecto → **Cuentas de servicio**
3. **Generar nueva clave privada** → guardar como:

```
backend/secrets/firebase-service-account.json
```

4. Reinicia el backend:

```powershell
docker compose up -d --build backend
```

Docker ya monta `./backend/secrets` en `/app/secrets` y usa:

```
FCM_SERVICE_ACCOUNT_PATH=/app/secrets/firebase-service-account.json
```

Si corres el backend **sin Docker**, en `backend/.env`:

```env
FCM_SERVICE_ACCOUNT_PATH=secrets/firebase-service-account.json
```

## 2. Mobile Android

Ya configurado en:

- `mobile/android/app/google-services.json`
- `mobile/lib/firebase_options.dart`

## 3. Mobile Web (Chrome) — opcional

En Firebase Console añade una **app Web** y copia el `appId`.

Ejecuta Flutter con:

```powershell
flutter run -d chrome `
  --dart-define=API_URL=http://localhost:8000 `
  --dart-define=WS_URL=ws://localhost:8000 `
  --dart-define=FIREBASE_WEB_APP_ID=1:904373067369:web:TU_APP_ID `
  --dart-define=FIREBASE_VAPID_KEY=TU_VAPID_KEY
```

La clave VAPID está en: Firebase → Cloud Messaging → **Certificados push web**.

Sin esas variables, en web siguen funcionando las **notificaciones in-app** (polling cada 6 s).

## Alternativa legacy

Si tu proyecto aún tiene Server key:

```env
FCM_SERVER_KEY=AAAA...
```
