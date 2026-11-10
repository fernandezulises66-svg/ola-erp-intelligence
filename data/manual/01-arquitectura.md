---
doc_id: arquitectura
title: Arquitectura y despliegue del ERP
category: infraestructura
tags: [backend, frontend, despliegue, servidor]
---

# Arquitectura y despliegue del ERP de OLA Muebles

> Documentación técnica ficticia, creada con fines educativos para el curso de AI Engineering.

## Componentes

El ERP está dividido en dos aplicaciones que se comunican por una API REST:

- **Backend:** Node.js 22 con Express. Expone la API en `https://api.olamuebles.example/api`. Accede a la base de datos con consultas SQL escritas a mano mediante la librería `mysql2`, sin ORM.
- **Base de datos:** MySQL 8.0, con un esquema único llamado `ola_erp`. Las claves primarias se llaman siempre `id` y las foráneas siguen el patrón `<tabla>_id` (por ejemplo, `cliente_id`).
- **Frontend:** React 19 con Material UI. Los listados usan el componente DataGrid con la traducción al español activada.
- **Tienda online:** una tienda en Tiendanube que se sincroniza con el ERP por webhooks (ver el documento de integración con Tiendanube).

## Infraestructura

El backend corre en un servidor privado virtual (VPS) con Ubuntu 24.04. El proceso de Node se administra con **PM2**, que lo reinicia si se cae, y **Nginx** funciona como proxy inverso: recibe el tráfico HTTPS en el puerto 443 y lo reenvía al puerto interno 4000, donde escucha Express. El certificado TLS lo emite Let's Encrypt y se renueva solo con `certbot`.

El frontend se despliega en Vercel. Cada push a la rama `main` del repositorio del frontend genera un despliegue automático; no hace falta ninguna acción manual.

## Cómo desplegar un cambio en el backend

El backend **no** tiene despliegue automático. Para publicar un cambio:

1. Hacer push de los cambios a la rama `main` del repositorio del backend.
2. Conectarse al servidor por SSH con el usuario `deploy`.
3. Entrar a la carpeta `/srv/ola-erp-api` y ejecutar `git pull`.
4. Si cambiaron las dependencias, ejecutar `npm ci --omit=dev`.
5. Si hay migraciones nuevas, ejecutar `npm run migrate`.
6. Reiniciar el proceso con `pm2 restart ola-muebles-api`.
7. Verificar que el servicio respondió con `curl https://api.olamuebles.example/api/health`, que debe devolver `{"status":"ok"}`.

Si el reinicio falla, `pm2 logs ola-muebles-api --lines 100` muestra las últimas líneas del log. Para volver a la versión anterior se usa `git checkout <commit-anterior>` seguido de otro `pm2 restart`.

## Variables de entorno del backend

Las variables se cargan desde `/srv/ola-erp-api/.env`, que nunca se sube al repositorio:

| Variable | Uso |
|---|---|
| `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | Conexión a MySQL |
| `JWT_SECRET` | Firma de los tokens de sesión |
| `AFIP_CERT_PATH`, `AFIP_KEY_PATH` | Certificado digital para facturación electrónica |
| `TN_WEBHOOK_SECRET` | Validación de los webhooks de Tiendanube |
| `PORT` | Puerto interno de Express (por defecto 4000) |

## Problemas frecuentes de infraestructura

- **Puerto 3306 ocupado en desarrollo:** suele pasar cuando conviven dos instalaciones de MySQL (por ejemplo, la de XAMPP y una instalación independiente). Hay que detener una de las dos.
- **Error 502 Bad Gateway:** Nginx está activo pero el proceso de Node no responde. Revisar `pm2 status`; si el proceso figura como `errored`, ver los logs y reiniciarlo.
- **Cambios del backend que "no se ven":** casi siempre falta el `pm2 restart` después del `git pull`.
